#!/usr/bin/env python3
"""The one writer for data/source-expressions.json and data/source-positions.json.

The investigator and the fact-checker propose passages; this script merges
them by expression id and publishes the file atomically under the case lock,
so an interrupted or concurrent write can never leave a half-written or
lost-update file. It computes every fingerprint and hash itself; a proposal
never supplies them.

Usage:
  python3 scripts/write-source-expressions.py {CASE_DIR} --actor investigator --cycle 2 < proposal.json
  python3 scripts/write-source-expressions.py {CASE_DIR} --fingerprint-findings
  python3 scripts/write-source-expressions.py {CASE_DIR} --resolve-fact-check-refs

Proposal (JSON on stdin, or --file):
  {"expressions": [{
      "id": "SX3",
      "text": "exact passage as stored",
      "anchor_ref": {"path": "research/x.md", "line_start": 4, "line_end": 4},
      "original_evidence_bundle_id": "E2",
      "language": "fr",                                                     (required, BCP 47)
      "direct_quote": true, "attribution": "...",                           (optional)
      "derived_from_expression_id": "SX1", "derivative_type": "translation",  (optional)
      "finding_links": [{"finding_id": "F1", "relation": "supports"}]
   }],
   "lifecycle": [{"expression_id": "SX1", "event": "superseded",
                  "successor_expression_id": "SX3", "reason": "..."}],
   "positions": [{"id": "SP1", "finding_id": "F1", "actor": {"label": "Ministry of Health"},
                  "stance": "asserts", "source_expression_ids": ["SX3"],
                  "attribution_verbatim": "the ministry said"}],
   "withdraw_positions": [{"id": "SP0", "reason": "..."}]}

A source position records who asserts or disputes a finding: one for every
attributed finding, and one for every materially different actor position.
Its attribution_verbatim must appear exactly in one of the cited passages,
which must be linked to the finding; at least one must be active. When a
cited passage is superseded, re-propose the position with the successor in the
same write; a position that no longer stands is withdrawn with a reason.

Merging rules:
- a new id is created with an `activated` lifecycle event by --actor;
- an existing id must keep its passage core byte for byte (a changed passage
  is a new expression that supersedes the old one); its finding links are
  unioned, never removed, and its author is kept;
- lifecycle events are appended, never rewritten;
- an existing position keeps its finding, actor, stance and attribution; its
  passages are unioned (a changed position is a new id).

The merged file is validated with the case validator before it is published;
any error refuses the whole write and leaves the file as it was.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_anchors import normalized, selected_source_texts, source_lines  # noqa: E402
from source_expression_contract import canonical_fingerprint, canonical_json_bytes, passage_core  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("validate_case", SCRIPT_DIR / "validate-case.py")
VALIDATOR = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(VALIDATOR)

TARGET = "data/source-expressions.json"
POSITIONS = "data/source-positions.json"
POSITION_CORE = ("finding_id", "actor", "stance", "attribution_verbatim")
ACTORS = ("investigator", "fact-checker", "human")
# Same rule as build-knowledge-batch.py, which refuses a passage without one at Ingest.
LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
PROPOSED_CORE = ("text", "anchor_ref", "original_evidence_bundle_id", "language",
                 "attribution", "direct_quote", "derived_from_expression_id", "derivative_type")
ALLOWED = (*PROPOSED_CORE, "id", "finding_links", "supersedes_expression_id")
# Fields the writer sets itself: a proposal carrying them loses them, with a note.
COMPUTED = {"anchor_sha256", "original_artifact_sha256", "expression_fingerprint", "lifecycle_events",
            "created_by", "cycle", "sha256"}
# The bundle fields naming an item's own text files, as validate-case reads
# them (a screenshot holds no text to find).
BUNDLE_FILES = ("raw_path", "downloaded_document_path", "path")


class WriteError(Exception):
    """A proposal that cannot be merged; nothing is written."""


def _load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriteError(f"cannot read {path.name}: {exc}") from exc


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_case_file(case: Path, relative: str) -> str:
    target = (case / relative).resolve()
    if case.resolve() not in target.parents or not target.is_file():
        raise WriteError(f"anchor {relative!r} is not a case-local file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _bundle_hash(bundle: dict[str, Any], bundle_id: str) -> str:
    for item in bundle.get("items", []) if isinstance(bundle, dict) else []:
        if isinstance(item, dict) and item.get("id") == bundle_id:
            digest = item.get("sha256")
            if isinstance(digest, str) and len(digest) == 64:
                return digest
            raise WriteError(f"evidence bundle item {bundle_id!r} has no sha256 for its original artifact")
    raise WriteError(f"evidence bundle item {bundle_id!r} does not exist")


def _bundle_item(bundle: dict[str, Any], bundle_id: Any) -> dict[str, Any] | None:
    items = bundle.get("items", []) if isinstance(bundle, dict) else []
    return next((item for item in items if isinstance(item, dict) and item.get("id") == bundle_id), None)


def _item_files(item: dict[str, Any]) -> list[str]:
    derivatives = item.get("text_derivatives") if isinstance(item.get("text_derivatives"), list) else []
    paths = [item.get(key) for key in BUNDLE_FILES] + [d.get("path") for d in derivatives if isinstance(d, dict)]
    return list(dict.fromkeys(path for path in paths if isinstance(path, str)))


def _occurrences(lines: list[str], needle: str, normalize: bool) -> list[tuple[int, int]]:
    """The (line_start, line_end) of every place `needle` occurs in the lines,
    exactly or, with `normalize`, on validate-case's NFKC and whitespace rule."""
    pieces = [(number, normalized(line) if normalize else line) for number, line in enumerate(lines, 1)]
    if normalize:
        pieces = [(number, line) for number, line in pieces if line]
        needle = normalized(needle)
    if not needle:
        return []
    starts, offset = [], 0
    for _number, line in pieces:
        starts.append(offset)
        offset += len(line) + 1
    full = (" " if normalize else "\n").join(line for _number, line in pieces)
    found, at = [], full.find(needle)
    while at >= 0:
        first = max(i for i, start in enumerate(starts) if start <= at)
        last = max(i for i, start in enumerate(starts) if start <= at + len(needle) - 1)
        found.append((pieces[first][0], pieces[last][0]))
        at = full.find(needle, at + 1)
    return sorted(set(found))


def _locate(case: Path, label: str, text: str, paths: list[str]) -> tuple[str, int, int]:
    """The one place `text` occurs in the named files: an exact match first,
    then a whitespace-normalised one. Anything else is refused."""
    if not paths:
        raise WriteError(f"expression {label}: name the file the passage is in, as anchor_ref {{\"path\": \"research/page.md.raw\"}}")
    for normalize in (False, True):
        matches = []
        for relative in paths:
            target = (case / relative).resolve()
            if case.resolve() not in target.parents or not target.is_file():
                raise WriteError(f"expression {label}: not found: {relative} is not a case-local file")
            matches += [(relative, start, end) for start, end in _occurrences(source_lines(target), text, normalize)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            ranges = ", ".join(f"{path} lines {start}-{end}" for path, start, end in matches[:8])
            raise WriteError(f"expression {label}: the text occurs {len(matches)} times ({ranges}); give anchor_ref with the line_start and line_end you mean")
    raise WriteError(f"expression {label}: the text is not found in {', '.join(paths)}; quote it exactly as the file has it")


def _build_expression(case: Path, proposal: dict[str, Any], bundle: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    label = repr(proposal.get("id"))
    proposal = dict(proposal)
    dropped = sorted(COMPUTED & set(proposal))
    for key in dropped:
        del proposal[key]
    if dropped:
        notes.append(f"expression {label}: ignored {dropped}; the writer computes them")
    # `lang` is the model's own tag under a short key, not a guess.
    if "lang" in proposal:
        lang = proposal.pop("lang")
        if "language" in proposal and proposal["language"] != lang:
            raise WriteError(f"expression {label}: lang {lang!r} and language {proposal['language']!r} differ; send only language")
        if "language" not in proposal:
            proposal["language"] = lang
            notes.append(f"expression {label}: read lang as language")
    unknown = set(proposal) - set(ALLOWED)
    if unknown:
        raise WriteError(f"expression {label}: unknown fields {sorted(unknown)}; the allowed fields are {', '.join(ALLOWED)}")
    # Ingest builds claims only from passages with a language, so it is asked for here.
    if not isinstance(proposal.get("language"), str) or not LANGUAGE.match(proposal["language"]):
        raise WriteError(f"expression {label}: language is required, a BCP 47 tag of the passage's original language (for example \"en\" or \"fr-CH\")")
    if not isinstance(proposal.get("text"), str) or not proposal["text"].strip():
        raise WriteError(f"expression {label}: text is required, the exact passage as the file has it")
    anchor = proposal.get("anchor_ref")
    # A path sent as a string, with or without a ":12-14" line suffix, is that path.
    if isinstance(anchor, str):
        anchor = {"path": re.sub(r":\d+(-\d+)?$", "", anchor)}
    if anchor is not None and not (isinstance(anchor, dict) and isinstance(anchor.get("path"), str)):
        raise WriteError(f"expression {label}: anchor_ref must be an object with a case-local path, "
                         f'for example {{"path": "research/page.md.raw"}}, not {json.dumps(anchor)}')
    anchor = dict(anchor) if anchor else None
    # A line number sent as a string of digits ("65") is that number.
    for field in ("line_start", "line_end"):
        if anchor and isinstance(anchor.get(field), str) and anchor[field].isdigit():
            anchor[field] = int(anchor[field])
    target = (case / anchor["path"]).resolve() if anchor else None
    reproduces = (anchor is not None and target is not None and case.resolve() in target.parents and target.is_file()
                  and proposal["text"] in selected_source_texts(target, anchor)[0])
    if not reproduces:
        # Without an anchor that selects the text, the writer finds it: in the
        # named file, or else in the evidence item's own files.
        item = _bundle_item(bundle, proposal.get("original_evidence_bundle_id")) or {}
        paths = [anchor["path"]] if anchor else _item_files(item)
        path, start, end = _locate(case, label, proposal["text"], paths)
        anchor = {"path": path, "line_start": start, "line_end": end}
        stored = "\n".join(source_lines((case / path).resolve())[start - 1:end])
        notes.append(f"expression {label}: anchored at {path} lines {start}-{end}"
                     + ("" if stored == proposal["text"] else "; text set to those lines as the file has them"))
        proposal["text"] = stored
    proposal["anchor_ref"] = anchor
    expression = {"id": proposal["id"]}
    expression.update({key: copy.deepcopy(proposal[key]) for key in PROPOSED_CORE if key in proposal})
    expression["anchor_sha256"] = _sha256_case_file(case, anchor["path"])
    expression["original_artifact_sha256"] = _bundle_hash(bundle, str(proposal.get("original_evidence_bundle_id")))
    expression["expression_fingerprint"] = canonical_fingerprint(passage_core(expression))
    if "supersedes_expression_id" in proposal:
        expression["supersedes_expression_id"] = proposal["supersedes_expression_id"]
    return expression


def _link(expression: dict[str, Any], finding: dict[str, Any], finding_id: str, relation: str) -> dict[str, Any]:
    finding_fp = canonical_fingerprint({"claim": finding.get("claim")})
    link = {"finding_id": finding_id, "finding_fingerprint": finding_fp, "relation": relation}
    link["link_fingerprint"] = canonical_fingerprint({
        "expression_fingerprint": expression["expression_fingerprint"],
        "finding_fingerprint": finding_fp,
        "finding_id": finding_id,
        "relation": relation,
    })
    return link


LIFECYCLE_EXAMPLE = ('{"expression_id": "P1", "event": "superseded", '
                     '"successor_expression_id": "P1b", "reason": "..."}')


def merge(case: Path, current: dict[str, Any] | None, proposal: dict[str, Any], actor: str, cycle: int,
          notes: list[str] | None = None) -> dict[str, Any]:
    """Return the merged document; raise WriteError and change nothing on any problem."""
    findings_doc = _load(case / "data" / "findings.json", {})
    findings = {f.get("id"): f for f in findings_doc.get("findings", []) if isinstance(f, dict)}
    bundle = _load(case / "data" / "evidence-bundle.json", {})
    doc = copy.deepcopy(current) if current else {
        "schema_version": "1.0",
        "project": findings_doc.get("project"),
        "created_at": _now(),
        "expressions": [],
    }
    by_id = {e.get("id"): e for e in doc.get("expressions", []) if isinstance(e, dict)}
    for raw in proposal.get("expressions", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise WriteError("every proposed expression needs a non-empty id")
        built = _build_expression(case, raw, bundle, notes if notes is not None else [])
        existing = by_id.get(raw["id"])
        if existing is None:
            built.update({"finding_links": [], "created_by": actor, "cycle": cycle,
                          "lifecycle_events": [{"event": "activated", "timestamp": _now(), "actor": actor,
                                                "reason": "passage captured from a stored artifact"}]})
            doc["expressions"].append(built)
            by_id[raw["id"]] = built
            target = built
        else:
            if existing.get("expression_fingerprint") != built["expression_fingerprint"]:
                raise WriteError(f"expression {raw['id']!r} already exists with a different passage; "
                                 "propose the changed passage under a new id and supersede the old one with "
                                 f"{LIFECYCLE_EXAMPLE}")
            target = existing
        links = raw.get("finding_links", [])
        if not isinstance(links, list) or not all(isinstance(link, dict) for link in links):
            raise WriteError(f"expression {raw['id']!r}: finding_links must be a list of objects")
        for link in links:
            finding_id, relation = link.get("finding_id"), link.get("relation")
            if finding_id not in findings:
                raise WriteError(f"expression {raw['id']!r}: finding {finding_id!r} does not exist")
            if relation not in ("supports", "contradicts", "context"):
                raise WriteError(f"expression {raw['id']!r}: relation must be supports, contradicts or context")
            built_link = _link(target, findings[finding_id], finding_id, relation)
            existing_link = next((l for l in target["finding_links"] if l.get("finding_id") == finding_id), None)
            if existing_link is None:
                target["finding_links"].append(built_link)
            elif existing_link.get("relation") != relation:
                raise WriteError(f"expression {raw['id']!r} already links {finding_id!r} as {existing_link.get('relation')}")
            elif existing_link.get("link_fingerprint") != built_link["link_fingerprint"]:
                raise WriteError(f"expression {raw['id']!r}: the link to {finding_id!r} is stale because the claim changed; run --fingerprint-findings")
    lifecycle = proposal.get("lifecycle", [])
    if not isinstance(lifecycle, list) or not all(isinstance(event, dict) for event in lifecycle):
        raise WriteError("lifecycle must be a list of objects")
    for event in lifecycle:
        expression = by_id.get(event.get("expression_id"))
        if expression is None:
            raise WriteError(f"a lifecycle event needs the expression_id of an existing expression, for example "
                             f"{LIFECYCLE_EXAMPLE}; got {json.dumps(event)}")
        entry = {"event": event.get("event"), "timestamp": _now(), "actor": actor, "reason": event.get("reason")}
        if event.get("successor_expression_id"):
            entry["successor_expression_id"] = event["successor_expression_id"]
        expression["lifecycle_events"].append(entry)
    return doc


def merge_positions(current: dict[str, Any] | None, proposal: dict[str, Any], project: Any,
                    actor: str, cycle: int) -> dict[str, Any] | None:
    """Return the merged positions document (None when there are none);
    raise WriteError on a conflicting or malformed position."""
    proposed = proposal.get("positions", [])
    withdrawals = proposal.get("withdraw_positions", [])
    for name, value in (("positions", proposed), ("withdraw_positions", withdrawals)):
        if not isinstance(value, list) or not all(isinstance(p, dict) for p in value):
            raise WriteError(f"{name} must be a list of objects")
    if not proposed and not withdrawals:
        return current
    doc = copy.deepcopy(current) if current else {
        "schema_version": "1.0", "project": project, "created_at": _now(), "positions": [],
    }
    by_id = {p.get("id"): p for p in doc.get("positions", []) if isinstance(p, dict)}
    for raw in proposed:
        unknown = set(raw) - set(POSITION_CORE) - {"id", "source_expression_ids"}
        if unknown:
            raise WriteError(f"position {raw.get('id')!r}: unknown fields {sorted(unknown)}")
        if not isinstance(raw.get("id"), str) or not raw["id"]:
            raise WriteError("every proposed position needs a non-empty id")
        ids = raw.get("source_expression_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise WriteError(f"position {raw['id']!r}: source_expression_ids must be a non-empty list of ids")
        existing = by_id.get(raw["id"])
        if existing is None:
            built = {key: copy.deepcopy(raw.get(key)) for key in ("id",) + POSITION_CORE}
            built.update({"source_expression_ids": list(dict.fromkeys(ids)), "created_by": actor, "cycle": cycle})
            doc["positions"].append(built)
            by_id[raw["id"]] = built
            continue
        if "withdrawn" in existing:
            raise WriteError(f"position {raw['id']!r} was withdrawn; record a changed position under a new id")
        if any(existing.get(key) != raw.get(key) for key in POSITION_CORE):
            raise WriteError(f"position {raw['id']!r} already exists with a different finding, actor, stance "
                             "or attribution; a changed position is a new id")
        # Passages are unioned, never dropped: a superseded passage stays as
        # history and its successor is added beside it.
        existing["source_expression_ids"] = list(dict.fromkeys(existing["source_expression_ids"] + ids))
    for raw in withdrawals:
        position = by_id.get(raw.get("id"))
        reason = raw.get("reason")
        if position is None:
            raise WriteError(f"cannot withdraw unknown position {raw.get('id')!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise WriteError(f"withdrawing position {raw.get('id')!r} needs a reason")
        if "withdrawn" not in position:
            position["withdrawn"] = {"timestamp": _now(), "actor": actor, "reason": reason}
    return doc


def write(case: Path, proposal: dict[str, Any], actor: str, cycle: int, notes: list[str] | None = None) -> dict[str, Any]:
    if actor not in ACTORS:
        raise WriteError(f"--actor must be one of {', '.join(ACTORS)}")
    with transaction(case) as descriptor:
        current = _load(case / TARGET)
        doc = merge(case, current, proposal, actor, cycle, notes)
        findings = _load(case / "data" / "findings.json", {})
        bundle = _load(case / "data" / "evidence-bundle.json")
        # The passages are checked, not the fact-check citing them: when a
        # claim changes between cycles, the previous fact-check's refs go
        # stale and the next fact-check replaces them, so they must not block
        # the investigator's passages (validate-case reports them as the
        # fact-checker's).
        errors = VALIDATOR.validate_source_expressions(case, doc, findings, None, bundle, enforce_positive_verdicts=False)
        if errors:
            raise WriteError("the merged passages are not valid: " + "; ".join(errors[:8]))
        outputs = {TARGET: canonical_json_bytes(doc) + b"\n"}
        current_positions = _load(case / POSITIONS)
        positions = merge_positions(current_positions, proposal, doc.get("project"), actor, cycle)
        # Positions are re-checked on every write, so a passage change that
        # would leave a position citing no active passage is refused here.
        if positions is not None:
            errors = VALIDATOR.validate_source_positions(positions, findings, doc)
            if errors:
                raise WriteError("the source positions would not be valid: " + "; ".join(errors[:8])
                                 + " (re-propose the position with the successor passage, or withdraw it)")
            if positions is not current_positions:
                outputs[POSITIONS] = canonical_json_bytes(positions) + b"\n"
        # Both files publish together or not at all.
        atomic_write_files(case, outputs, data_descriptor=descriptor)
    return doc


def proposal_refs(doc: dict[str, Any], proposal: dict[str, Any]) -> dict[str, dict[str, list[dict[str, str]]]]:
    """The fact-check ref objects for the proposed passages, by finding and
    relation: the fingerprints a producer must copy and never computes."""
    proposed = {item.get("id") for item in proposal.get("expressions", []) if isinstance(item, dict)}
    refs: dict[str, dict[str, list[dict[str, str]]]] = {}
    for expression in doc["expressions"]:
        if expression.get("id") not in proposed:
            continue
        for link in expression.get("finding_links", []):
            refs.setdefault(link["finding_id"], {}).setdefault(link["relation"], []).append(_ref(expression, link))
    return refs


def _ref(expression: dict[str, Any], link: dict[str, Any]) -> dict[str, str]:
    return {
        "expression_id": expression["id"],
        "expression_fingerprint": expression["expression_fingerprint"],
        "finding_fingerprint": link["finding_fingerprint"],
        "link_fingerprint": link["link_fingerprint"],
    }


def resolve_fact_check_refs(case: Path) -> int:
    """Replace each bare expression id (or id-only ref object) in
    data/fact-check.json's source_expression_refs with the full ref for that claim's finding, exactly
    what a write returns, in one locked write. The stored refs still pin the
    fingerprints of that moment; an id with no link to the finding is left for
    validate-case to report."""
    with transaction(case) as descriptor:
        fact_check = _load(case / "data" / "fact-check.json")
        expressions = _load(case / TARGET)
        if not isinstance(fact_check, dict) or not isinstance(expressions, dict):
            return 0
        by_id = {item.get("id"): item for item in expressions.get("expressions", []) if isinstance(item, dict)}
        resolved = 0
        claims = fact_check.get("claims")
        for claim in claims if isinstance(claims, list) else []:
            if not isinstance(claim, dict):
                continue
            for field in ("evidence_for", "evidence_against"):
                items = claim.get(field)
                for item in items if isinstance(items, list) else []:
                    refs = item.get("source_expression_refs") if isinstance(item, dict) else None
                    for index, ref in enumerate(refs if isinstance(refs, list) else []):
                        # A bare id, or an object naming only expression_id
                        # (and perhaps relation) with none of the fingerprints.
                        if isinstance(ref, dict) and not {"expression_fingerprint", "finding_fingerprint", "link_fingerprint"} & ref.keys():
                            ref = ref.get("expression_id")
                        expression = by_id.get(ref) if isinstance(ref, str) else None
                        link = next((l for l in (expression or {}).get("finding_links", [])
                                     if isinstance(l, dict) and l.get("finding_id") == claim.get("finding_id")), None)
                        if link is not None:
                            refs[index] = _ref(expression, link)
                            resolved += 1
        if resolved:
            atomic_write_files(case, {"data/fact-check.json": (json.dumps(fact_check, indent=2, ensure_ascii=False) + "\n").encode("utf-8")},
                               data_descriptor=descriptor)
    return resolved


def fingerprint_findings(case: Path) -> int:
    """Set every finding's finding_fingerprint from its claim, and refresh the
    passage links of any finding whose claim changed, in one locked write."""
    with transaction(case) as descriptor:
        findings_doc = _load(case / "data" / "findings.json")
        if not isinstance(findings_doc, dict) or not isinstance(findings_doc.get("findings"), list):
            raise WriteError("data/findings.json has no findings to fingerprint")
        changed: dict[str, dict[str, Any]] = {}
        current: dict[str, dict[str, Any]] = {}
        for finding in findings_doc["findings"]:
            if not isinstance(finding, dict):
                continue
            fp = canonical_fingerprint({"claim": finding.get("claim")})
            current[str(finding.get("id"))] = finding
            if finding.get("finding_fingerprint") != fp:
                finding["finding_fingerprint"] = fp
                changed[str(finding.get("id"))] = finding
        # Rewrite findings.json only when a fingerprint changed, so an idle
        # run never moves the file's hash.
        outputs = {}
        if changed:
            outputs["data/findings.json"] = (json.dumps(findings_doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        expressions = _load(case / TARGET)
        relinked = False
        if isinstance(expressions, dict):
            # Any link whose finding fingerprint no longer matches the claim
            # is rebuilt, whether or not the stored fingerprint just changed.
            for expression in expressions.get("expressions", []):
                links = expression.get("finding_links", []) if isinstance(expression, dict) else []
                for index, link in enumerate(links):
                    finding = current.get(link.get("finding_id")) if isinstance(link, dict) else None
                    if finding is not None and link.get("finding_fingerprint") != finding["finding_fingerprint"]:
                        links[index] = _link(expression, finding, link["finding_id"], link["relation"])
                        relinked = True
            if relinked:
                outputs[TARGET] = canonical_json_bytes(expressions) + b"\n"
        if outputs:
            atomic_write_files(case, outputs, data_descriptor=descriptor)
    return len(changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir")
    parser.add_argument("--actor", choices=ACTORS)
    parser.add_argument("--cycle", type=int)
    parser.add_argument("--file", help="proposal JSON; default stdin")
    parser.add_argument("--fingerprint-findings", action="store_true",
                        help="set finding_fingerprint on every finding (run after writing findings.json)")
    parser.add_argument("--resolve-fact-check-refs", action="store_true",
                        help="replace bare expression ids in fact-check.json refs with full refs (the harness runs it after the fact-checker)")
    args = parser.parse_args(argv)
    if args.resolve_fact_check_refs:
        try:
            count = resolve_fact_check_refs(Path(args.case_dir))
        except (WriteError, OrchestrationError, OSError) as exc:
            print(f"source-expressions: refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "refs_resolved": count}))
        return 0
    if args.fingerprint_findings:
        try:
            count = fingerprint_findings(Path(args.case_dir))
        except (WriteError, OrchestrationError, OSError) as exc:
            print(f"source-expressions: refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "findings_changed": count}))
        return 0
    if not args.actor or args.cycle is None:
        parser.error("--actor and --cycle are required to write passages")
    try:
        raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        proposal = json.loads(raw)
        if not isinstance(proposal, dict):
            raise WriteError("the proposal must be a JSON object")
        notes: list[str] = []
        doc = write(Path(args.case_dir), proposal, args.actor, args.cycle, notes)
    except (WriteError, OrchestrationError, json.JSONDecodeError, OSError) as exc:
        print(f"source-expressions: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "ok": True,
        **({"notes": notes} if notes else {}),
        "expressions": len(doc["expressions"]),
        "source_expression_refs": proposal_refs(doc, proposal),
        "next": "These are the refs per finding and relation for the passages just written. In data/fact-check.json, "
                "copy them into source_expression_refs of the claim's evidence_for item (supports) or evidence_against "
                "item (contradicts); a verified claim needs at least one supports ref.",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
