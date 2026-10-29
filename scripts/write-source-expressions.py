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
import difflib
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

from evidence_anchors import case_evidence_path, locate_quote, normalized, selected_source_texts, source_lines, validate_source_ref  # noqa: E402
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


# Marks a bundle hash the writer took itself, not one recorded at acquisition.
HASH_AT_PASSAGE = "computed_at_passage_write"


def _bundle_hash(case: Path, bundle: dict[str, Any], bundle_id: str, anchor_path: str, notes: list[str]) -> str:
    for item in bundle.get("items", []) if isinstance(bundle, dict) else []:
        if isinstance(item, dict) and item.get("id") == bundle_id:
            digest = item.get("sha256")
            if isinstance(digest, str) and len(digest) == 64:
                return digest
            # An item with no hash whose own research/ file holds the passage is
            # pinned now, to the bytes the passage was just checked against, and
            # marked so (12B r3: both cases that reached passages stopped here).
            if anchor_path.startswith("research/") and anchor_path in (item.get(key) for key in BUNDLE_FILES):
                item["sha256"] = _sha256_case_file(case, anchor_path)
                item["sha256_source"] = HASH_AT_PASSAGE
                notes.append(f"evidence item {bundle_id}: sha256 of {anchor_path} recorded now ({HASH_AT_PASSAGE})")
                return item["sha256"]
            raise WriteError(f"evidence bundle item {bundle_id!r} has no sha256 for its original artifact, and the passage "
                             "is not in that item's own file under research/; record the item's sha256 in data/evidence-bundle.json")
    raise WriteError(f"evidence bundle item {bundle_id!r} does not exist in data/evidence-bundle.json yet. "
                     "Write the evidence item first, then record its passages.")


def _bundle_item(bundle: dict[str, Any], bundle_id: Any) -> dict[str, Any] | None:
    items = bundle.get("items", []) if isinstance(bundle, dict) else []
    return next((item for item in items if isinstance(item, dict) and item.get("id") == bundle_id), None)


def _item_files(item: dict[str, Any]) -> list[str]:
    derivatives = item.get("text_derivatives") if isinstance(item.get("text_derivatives"), list) else []
    paths = [item.get(key) for key in BUNDLE_FILES] + [d.get("path") for d in derivatives if isinstance(d, dict)]
    return list(dict.fromkeys(path for path in paths if isinstance(path, str)))


# The honest exit when no acquired source holds a supporting passage
# (12B r1: it recorded its own conclusions and placeholders as passages).
ESCAPE = ("If no source you acquired contains a passage that supports the finding, do not record one: "
          "set the verdict to unverified (or use missing_source_gate for a source that was requested and not returned).")

# How a quote may differ from the file and still be the file's own words:
# exactly; on validate-case's NFKC whitespace rule; and with markdown markers
# (**, __, backticks, a leading "> ") and wrapping quotes also ignored.
def _markdown_free(text: str) -> str:
    return normalized(re.sub(r"\*\*|__|`", "", re.sub(r"(?m)^\s*>\s?", "", text)))


MATCH_RULES = (("exact", lambda text: text), ("spacing", normalized), ("markdown", _markdown_free))


def _occurrences(lines: list[str], needle: str, rule: str) -> list[tuple[int, int]]:
    """The (line_start, line_end) of every place `needle` occurs in the lines under one of MATCH_RULES."""
    transform = dict(MATCH_RULES)[rule]
    pieces = [(number, transform(line)) for number, line in enumerate(lines, 1)]
    if rule != "exact":
        pieces = [(number, line) for number, line in pieces if line]
        needle = transform(needle.strip().strip("\"'\u201c\u201d\u2018\u2019") if rule == "markdown" else needle)
    if not needle:
        return []
    starts, offset = [], 0
    for _number, line in pieces:
        starts.append(offset)
        offset += len(line) + 1
    full = ("\n" if rule == "exact" else " ").join(line for _number, line in pieces)
    found, at = [], full.find(needle)
    while at >= 0:
        first = max(i for i, start in enumerate(starts) if start <= at)
        last = max(i for i, start in enumerate(starts) if start <= at + len(needle) - 1)
        found.append((pieces[first][0], pieces[last][0]))
        at = full.find(needle, at + 1)
    return sorted(set(found))


# U3 census (84674fd9): fact-checkers resubmitted a paraphrase of a passage
# up to 33 times. A refused quote is shown the lines of the searched files
# closest to it, at most three, verbatim with their line numbers. Whether one
# states what the finding claims stays the model's judgment.
CLOSEST_LINES = 3
CLOSEST_MIN_RATIO = 0.5
CLOSEST_MAX_CHARS = 600


def _closest_lines(case: Path, text: str, paths: list[str]) -> str:
    wanted = " ".join(text.split()).lower()
    scored: list[tuple[float, str, int, str]] = []
    for relative in paths:
        target = (case / relative).resolve()
        if case.resolve() not in target.parents or not target.is_file():
            continue
        for number, line in enumerate(source_lines(target), start=1):
            shown = line.strip()
            if not shown or len(shown) > CLOSEST_MAX_CHARS:
                continue
            matcher = difflib.SequenceMatcher(None, wanted, " ".join(shown.split()).lower())
            if matcher.real_quick_ratio() < CLOSEST_MIN_RATIO or matcher.quick_ratio() < CLOSEST_MIN_RATIO:
                continue
            ratio = matcher.ratio()
            if ratio >= CLOSEST_MIN_RATIO:
                scored.append((ratio, relative, number, shown))
    if not scored:
        return ""
    best = sorted(scored, key=lambda item: -item[0])[:CLOSEST_LINES]
    listed = "\n".join(f"{relative} line {number}: {shown}" for _ratio, relative, number, shown in best)
    return ("\nThe lines closest to it in the files searched are below. A line counts only if it states what the finding "
            "claims; then quote it exactly as written. If none does, set the verdict to unverified.\n" + listed)


def _matches(case: Path, text: str, paths: list[str]) -> list[tuple[str, int, int]]:
    """Every place `text` occurs in the case-local files among `paths`, under
    the first rule that finds it anywhere."""
    files = [(relative, (case / relative).resolve()) for relative in paths]
    files = [(relative, target) for relative, target in files if case.resolve() in target.parents and target.is_file()]
    # A line break written as the two characters \n (12B r3 cc) is a line
    # break, tried only after the text as sent; what is stored is the file's lines.
    for needle in [text] + ([text.replace("\\n", "\n")] if "\\n" in text else []):
        for rule, _transform in MATCH_RULES:
            found = [(relative, start, end) for relative, target in files for start, end in _occurrences(source_lines(target), needle, rule)]
            if found:
                return found
    return []


def _free_id(taken_id: str, by_id: dict[str, Any]) -> str:
    """The next unused id in `taken_id`'s numbering (FC-P1 -> FC-P4 when up to
    FC-P3 is taken); an id with no trailing number gets "-2", "-3"."""
    match = re.match(r"^(.*?)(\d+)$", taken_id)
    prefix, number = (match.group(1), int(match.group(2))) if match else (f"{taken_id}-", 2)
    while f"{prefix}{number}" in by_id or f"{prefix}{number}" == taken_id:
        number += 1
    return f"{prefix}{number}"


def _owned_files(bundle: dict[str, Any], readable: set[str] | None) -> dict[str, list[str]]:
    """Each bundle-owned text file (that this role may read) and the items owning it."""
    owners: dict[str, list[str]] = {}
    for item in bundle.get("items", []) if isinstance(bundle, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            for path in _item_files(item):
                if readable is None or path in readable:
                    owners.setdefault(path, []).append(item["id"])
    return owners


def _build_expression(case: Path, proposal: dict[str, Any], bundle: dict[str, Any], notes: list[str],
                      readable: set[str] | None = None) -> dict[str, Any]:
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
    item = _bundle_item(bundle, proposal.get("original_evidence_bundle_id")) or {}
    owners = _owned_files(bundle, None)

    def cite(path: str) -> bool:
        """Cite the evidence item that owns `path`: the named one, else the
        first that lists it (u3 p1 eu-ai-act: three items listed one page, and
        a quote anchored there was refused). False when no item owns it."""
        if path in _item_files(item):
            return True
        ids = owners.get(path, [])
        if not ids:
            return False
        proposal["original_evidence_bundle_id"] = ids[0]
        also = f" (also listed by {', '.join(ids[1:])})" if len(ids) > 1 else ""
        notes.append(f"expression {label}: the text is in {path}, so it cites evidence item {ids[0]}{also}")
        return True

    if reproduces and cite(anchor["path"]):
        found = None
    else:
        # The writer finds the text: in the named file (or the evidence item's
        # own files), else in the one evidence file that holds it.
        named = [anchor["path"]] if anchor else [path for path in _item_files(item) if readable is None or path in readable]
        found = _matches(case, proposal["text"], named)
        searchable = [path for path in owners if readable is None or path in readable]
        # The named file decides the place. rc3 john-lewis (52bf6183) and
        # incr-test2 sp98: a quote in a file no evidence item lists was refused
        # as "not in" it, or sent to other files the model never named.
        unlisted = sorted({path for path, _start, _end in found if not owners.get(path)})
        if found and len(unlisted) == len({path for path, _start, _end in found}):
            elsewhere = sorted({path for path, _start, _end in _matches(case, proposal["text"], searchable)})
            alternative = (f" Or anchor it to {', '.join(elsewhere[:3])}, which data/evidence-bundle.json lists and which "
                           f"{'hold' if len(elsewhere) > 1 else 'holds'} the same text." if elsewhere else "")
            named_id = proposal.get("original_evidence_bundle_id")
            missing = f" ({named_id} is not in it yet)" if isinstance(named_id, str) and not item else ""
            raise WriteError(f"expression {label}: the text is in {unlisted[0]}, but no item in data/evidence-bundle.json lists that file, "
                             f"so it cannot be cited yet. Add the evidence item for {unlisted[0]} to data/evidence-bundle.json{missing}, "
                             f"then record the passage again.{alternative}")
        if len(found) == 1 and not cite(found[0][0]):
            found = []
        if not found:
            found = _matches(case, proposal["text"], searchable)
            if len(found) == 1:
                cite(found[0][0])
        if not found:
            searched = ", ".join(named) or "no file named"
            raise WriteError(f"expression {label}: the text is not in {searched} nor in any other file of data/evidence-bundle.json, "
                             f"so it is not from a source you acquired. {ESCAPE}"
                             + _closest_lines(case, proposal["text"], named or [path for path in owners if readable is None or path in readable]))
        # The same whole line repeated in one file is one passage: a reader
        # checking the first sees exactly the quoted text (12B r3 cc: an EIN
        # on sixteen identical lines).
        if len(found) > 1 and len({path for path, _start, _end in found}) == 1:
            lines = source_lines((case / found[0][0]).resolve())
            if len({"\n".join(lines[start - 1:end]) for _path, start, end in found}) == 1:
                notes.append(f"expression {label}: the text is on {len(found)} identical lines of {found[0][0]}; anchored the first")
                found = found[:1]
        if len(found) > 1:
            ranges = ", ".join(f"{path} lines {start}-{end}" for path, start, end in found[:8])
            raise WriteError(f"expression {label}: the text appears in {len(found)} places ({ranges}); quote a longer span that is unique, "
                             "or give anchor_ref with the path, line_start and line_end you mean")
        path, start, end = found[0]
        anchor = {"path": path, "line_start": start, "line_end": end}
        stored = "\n".join(source_lines((case / path).resolve())[start - 1:end])
        note = f"expression {label}: anchored at {path} lines {start}-{end}"
        if stored != proposal["text"]:
            note += "; text set to those lines as the file has them"
        if len(stored) > 3 * len(proposal["text"]):
            note += f" ({len(stored)} characters, wider than your quote)"
        notes.append(note)
        proposal["text"] = stored
    proposal["anchor_ref"] = anchor
    expression = {"id": proposal["id"]}
    expression.update({key: copy.deepcopy(proposal[key]) for key in PROPOSED_CORE if key in proposal})
    expression["anchor_sha256"] = _sha256_case_file(case, anchor["path"])
    expression["original_artifact_sha256"] = _bundle_hash(case, bundle, str(proposal.get("original_evidence_bundle_id")), anchor["path"], notes)
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
          notes: list[str] | None = None, readable: set[str] | None = None,
          bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the merged document; raise WriteError and change nothing on any
    problem. A bundle item hash the writer records is set on `bundle`."""
    findings_doc = _load(case / "data" / "findings.json", {})
    findings = {f.get("id"): f for f in findings_doc.get("findings", []) if isinstance(f, dict)}
    if bundle is None:
        bundle = _load(case / "data" / "evidence-bundle.json", {})
    doc = copy.deepcopy(current) if current else {
        "schema_version": "1.0",
        "project": findings_doc.get("project"),
        "created_at": _now(),
        "expressions": [],
    }
    by_id = {e.get("id"): e for e in doc.get("expressions", []) if isinstance(e, dict)}
    # A passage names its evidence item, so a bundle with no items list leaves
    # nothing to cite (12B r3 cc: its items were under "evidence_bundle").
    if proposal.get("expressions") and not (isinstance(bundle, dict) and isinstance(bundle.get("items"), list)):
        raise WriteError('data/evidence-bundle.json has no "items" list, so no passage can name its evidence item; '
                         'the bundle must keep its items under "items" before a passage is recorded')
    for raw in proposal.get("expressions", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            raise WriteError("every proposed expression needs a non-empty id")
        built = _build_expression(case, raw, bundle, notes if notes is not None else [], readable)
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
                # fc-stall scan: 45 refusals in 18 runs, mostly a new passage
                # given an id already taken; name a free one.
                raise WriteError(f"expression {raw['id']!r} already exists with a different passage. For a new passage, "
                                 f"give it a free id such as {_free_id(raw['id'], by_id)!r}. To change {raw['id']!r}, "
                                 "propose the changed passage under a new id and supersede the old one with "
                                 f"{LIFECYCLE_EXAMPLE}")
            target = existing
        links = raw.get("finding_links", [])
        if not isinstance(links, list) or not all(isinstance(link, dict) for link in links):
            raise WriteError(f"expression {raw['id']!r}: finding_links must be a list of objects")
        for link in links:
            finding_id, relation = link.get("finding_id"), link.get("relation")
            if finding_id not in findings:
                # fc-stall scan: investigators recorded passages before writing
                # the finding they link to.
                raise WriteError(f"expression {raw['id']!r}: finding {finding_id!r} does not exist in data/findings.json yet. "
                                 "Write the finding first, then record its passages; the passage file is unchanged.")
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


def write(case: Path, proposal: dict[str, Any], actor: str, cycle: int, notes: list[str] | None = None,
          readable: set[str] | None = None) -> dict[str, Any]:
    if actor not in ACTORS:
        raise WriteError(f"--actor must be one of {', '.join(ACTORS)}")
    with transaction(case) as descriptor:
        current = _load(case / TARGET)
        findings = _load(case / "data" / "findings.json", {})
        # Findings not yet fingerprinted are, first: the validator refuses a
        # link to an unfingerprinted finding (12B r3 ia: eight refusals).
        refingerprinted, _relinked = _refingerprint(findings, current)
        bundle = _load(case / "data" / "evidence-bundle.json", {})
        bundle_before = json.dumps(bundle, sort_keys=True)
        doc = merge(case, current, proposal, actor, cycle, notes, readable, bundle)
        # The passages are checked, not the fact-check citing them: when a
        # claim changes between cycles, the previous fact-check's refs go
        # stale and the next fact-check replaces them, so they must not block
        # the investigator's passages (validate-case reports them as the
        # fact-checker's).
        errors = VALIDATOR.validate_source_expressions(case, doc, findings, None, bundle, enforce_positive_verdicts=False)
        if errors:
            raise WriteError("the merged passages are not valid: " + "; ".join(errors[:8]))
        outputs = {TARGET: canonical_json_bytes(doc) + b"\n"}
        if refingerprinted:
            outputs["data/findings.json"] = _findings_bytes(findings)
        if json.dumps(bundle, sort_keys=True) != bundle_before:
            outputs["data/evidence-bundle.json"] = (json.dumps(bundle, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
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
        # The files publish together or not at all.
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


def repair_locators(case: Path, fact_check: dict[str, Any]) -> list[dict[str, Any]]:
    """Correct, in place, the line range of each evidence item whose quote is
    not in its cited lines but occurs exactly once in that same cited case
    file (as written, or with each literal \\n read as a line break). A model
    counts the lines of read_file text itself and miscounts (gold sens4 calder:
    two lines short on a saved wiki note). The quote, the file, the verdict and
    every ref stay as they are; the old range is kept in
    locator_repaired_from, so a reviewer sees that code moved it."""
    repairs = []
    claims = fact_check.get("claims")
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict):
            continue
        for field in ("evidence_for", "evidence_against"):
            items = claim.get(field)
            for index, item in enumerate(items if isinstance(items, list) else []):
                ref = item.get("source_ref") if isinstance(item, dict) else None
                if not isinstance(ref, dict) or "json_pointer" in ref or not isinstance(item.get("quote"), str):
                    continue
                path = case_evidence_path(case, ref.get("path"))
                if path is None or not validate_source_ref(case, ref, item["quote"])[1]:
                    continue
                located = locate_quote(path, item["quote"])
                if located is None:
                    continue
                old = [ref.get("line_start"), ref.get("line_end")]
                ref["line_start"], ref["line_end"] = located
                item["locator_repaired_from"] = old
                repairs.append({"claim": claim.get("id", claim.get("claim_id")), "item": f"{field}[{index}]",
                                "path": ref.get("path"), "from": old, "to": list(located)})
    return repairs


def resolve_fact_check_refs(case: Path) -> tuple[int, list[dict[str, Any]]]:
    """In one locked write of data/fact-check.json: repair the evidence
    locators (repair_locators), then replace each bare expression id (or
    id-only ref object) in source_expression_refs with the full ref for that
    claim's finding, exactly what a write returns. The stored refs still pin
    the fingerprints of that moment; an id with no link to the finding is left
    for validate-case to report. Returns the refs resolved and the repairs."""
    if not (case / "data" / "fact-check.json").is_file():
        return 0, []
    with transaction(case) as descriptor:
        fact_check = _load(case / "data" / "fact-check.json")
        if not isinstance(fact_check, dict):
            return 0, []
        repairs = repair_locators(case, fact_check)
        expressions = _load(case / TARGET)
        expressions = expressions if isinstance(expressions, dict) else {}
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
        if resolved or repairs:
            atomic_write_files(case, {"data/fact-check.json": (json.dumps(fact_check, indent=2, ensure_ascii=False) + "\n").encode("utf-8")},
                               data_descriptor=descriptor)
    return resolved, repairs


def _refingerprint(findings_doc: Any, expressions: Any) -> tuple[int, bool]:
    """Set every finding's finding_fingerprint from its claim and rebuild every
    passage link whose finding fingerprint no longer matches, in place; return
    how many findings changed and whether any link did."""
    changed = 0
    current: dict[str, dict[str, Any]] = {}
    for finding in findings_doc.get("findings", []) if isinstance(findings_doc, dict) else []:
        if not isinstance(finding, dict):
            continue
        fp = canonical_fingerprint({"claim": finding.get("claim")})
        current[str(finding.get("id"))] = finding
        if finding.get("finding_fingerprint") != fp:
            finding["finding_fingerprint"] = fp
            changed += 1
    relinked = False
    for expression in expressions.get("expressions", []) if isinstance(expressions, dict) else []:
        links = expression.get("finding_links", []) if isinstance(expression, dict) else []
        for index, link in enumerate(links):
            finding = current.get(link.get("finding_id")) if isinstance(link, dict) else None
            if finding is not None and link.get("finding_fingerprint") != finding["finding_fingerprint"]:
                links[index] = _link(expression, finding, link["finding_id"], link["relation"])
                relinked = True
    return changed, relinked


def _findings_bytes(findings_doc: dict[str, Any]) -> bytes:
    return (json.dumps(findings_doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def fingerprint_findings(case: Path) -> int:
    """Set every finding's finding_fingerprint from its claim, and refresh the
    passage links of any finding whose claim changed, in one locked write."""
    with transaction(case) as descriptor:
        findings_doc = _load(case / "data" / "findings.json")
        if not isinstance(findings_doc, dict) or not isinstance(findings_doc.get("findings"), list):
            raise WriteError("data/findings.json has no findings to fingerprint")
        expressions = _load(case / TARGET)
        changed, relinked = _refingerprint(findings_doc, expressions)
        # Rewrite a file only when it changed, so an idle run never moves its hash.
        outputs = {}
        if changed:
            outputs["data/findings.json"] = _findings_bytes(findings_doc)
        if relinked:
            outputs[TARGET] = canonical_json_bytes(expressions) + b"\n"
        if outputs:
            atomic_write_files(case, outputs, data_descriptor=descriptor)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir")
    parser.add_argument("--actor", choices=ACTORS)
    parser.add_argument("--cycle", type=int)
    parser.add_argument("--file", help="proposal JSON; default stdin")
    parser.add_argument("--readable", action="append",
                        help="an evidence file the writer may search for an unanchored passage (repeat); default every evidence file")
    parser.add_argument("--fingerprint-findings", action="store_true",
                        help="set finding_fingerprint on every finding (run after writing findings.json)")
    parser.add_argument("--resolve-fact-check-refs", action="store_true",
                        help="repair evidence line ranges and replace bare expression ids in fact-check.json refs with full refs (the harness runs it after the fact-checker)")
    args = parser.parse_args(argv)
    if args.resolve_fact_check_refs:
        try:
            count, repairs = resolve_fact_check_refs(Path(args.case_dir))
        except (WriteError, OrchestrationError, OSError) as exc:
            print(f"source-expressions: refused: {exc}", file=sys.stderr)
            return 2
        for repair in repairs:
            print(f"locator repaired by code: claim {repair['claim']} {repair['item']} in {repair['path']}: "
                  f"lines {repair['from'][0]}-{repair['from'][1]} -> {repair['to'][0]}-{repair['to'][1]} (the quote occurs once, there)")
        print(json.dumps({"ok": True, "refs_resolved": count, "locators_repaired": len(repairs)}))
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
        doc = write(Path(args.case_dir), proposal, args.actor, args.cycle, notes,
                    set(args.readable) if args.readable is not None else None)
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
