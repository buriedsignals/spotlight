#!/usr/bin/env python3
"""The one writer for data/source-expressions.json.

The investigator and the fact-checker propose passages; this script merges
them by expression id and publishes the file atomically under the case lock,
so an interrupted or concurrent write can never leave a half-written or
lost-update file. It computes every fingerprint and hash itself; a proposal
never supplies them.

Usage:
  python3 scripts/write-source-expressions.py {CASE_DIR} --actor investigator --cycle 2 < proposal.json
  python3 scripts/write-source-expressions.py {CASE_DIR} --fingerprint-findings

Proposal (JSON on stdin, or --file):
  {"expressions": [{
      "id": "SX3",
      "text": "exact passage as stored",
      "anchor_ref": {"path": "research/x.md", "line_start": 4, "line_end": 4},
      "original_evidence_bundle_id": "E2",
      "language": "fr", "direct_quote": true, "attribution": "...",      (optional)
      "derived_from_expression_id": "SX1", "derivative_type": "translation",  (optional)
      "finding_links": [{"finding_id": "F1", "relation": "supports"}]
   }],
   "lifecycle": [{"expression_id": "SX1", "event": "superseded",
                  "successor_expression_id": "SX3", "reason": "..."}]}

Merging rules:
- a new id is created with an `activated` lifecycle event by --actor;
- an existing id must keep its passage core byte for byte (a changed passage
  is a new expression that supersedes the old one); its finding links are
  unioned, never removed, and its author is kept;
- lifecycle events are appended, never rewritten.

The merged file is validated with the case validator before it is published;
any error refuses the whole write and leaves the file as it was.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_expression_contract import canonical_fingerprint, canonical_json_bytes, passage_core  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("validate_case", SCRIPT_DIR / "validate-case.py")
VALIDATOR = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(VALIDATOR)

TARGET = "data/source-expressions.json"
ACTORS = ("investigator", "fact-checker", "human")
PROPOSED_CORE = ("text", "anchor_ref", "original_evidence_bundle_id", "language",
                 "attribution", "direct_quote", "derived_from_expression_id", "derivative_type")


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


def _build_expression(case: Path, proposal: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    unknown = set(proposal) - set(PROPOSED_CORE) - {"id", "finding_links", "supersedes_expression_id"}
    if unknown:
        raise WriteError(f"expression {proposal.get('id')!r}: unknown or computed fields {sorted(unknown)}; the writer computes hashes and fingerprints")
    anchor = proposal.get("anchor_ref")
    if not isinstance(anchor, dict) or not isinstance(anchor.get("path"), str):
        raise WriteError(f"expression {proposal.get('id')!r}: anchor_ref with a case-local path is required")
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


def merge(case: Path, current: dict[str, Any] | None, proposal: dict[str, Any], actor: str, cycle: int) -> dict[str, Any]:
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
        built = _build_expression(case, raw, bundle)
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
                                 "a changed passage is a new expression that supersedes it")
            target = existing
        for link in raw.get("finding_links", []):
            finding_id, relation = link.get("finding_id"), link.get("relation")
            if finding_id not in findings:
                raise WriteError(f"expression {raw['id']!r}: finding {finding_id!r} does not exist")
            if relation not in ("supports", "contradicts", "context"):
                raise WriteError(f"expression {raw['id']!r}: relation must be supports, contradicts or context")
            built_link = _link(target, findings[finding_id], finding_id, relation)
            if not any(l.get("finding_id") == finding_id for l in target["finding_links"]):
                target["finding_links"].append(built_link)
            elif not any(l.get("link_fingerprint") == built_link["link_fingerprint"] for l in target["finding_links"]):
                raise WriteError(f"expression {raw['id']!r} already links {finding_id!r} with another relation")
    for event in proposal.get("lifecycle", []):
        expression = by_id.get(event.get("expression_id"))
        if expression is None:
            raise WriteError(f"lifecycle event for unknown expression {event.get('expression_id')!r}")
        entry = {"event": event.get("event"), "timestamp": _now(), "actor": actor, "reason": event.get("reason")}
        if event.get("successor_expression_id"):
            entry["successor_expression_id"] = event["successor_expression_id"]
        expression["lifecycle_events"].append(entry)
    return doc


def write(case: Path, proposal: dict[str, Any], actor: str, cycle: int) -> dict[str, Any]:
    if actor not in ACTORS:
        raise WriteError(f"--actor must be one of {', '.join(ACTORS)}")
    with transaction(case) as descriptor:
        current = _load(case / TARGET)
        doc = merge(case, current, proposal, actor, cycle)
        findings = _load(case / "data" / "findings.json", {})
        fact_check = _load(case / "data" / "fact-check.json")
        bundle = _load(case / "data" / "evidence-bundle.json")
        errors = VALIDATOR.validate_source_expressions(case, doc, findings, fact_check, bundle, enforce_positive_verdicts=False)
        if errors:
            raise WriteError("the merged passages are not valid: " + "; ".join(errors[:8]))
        atomic_write_files(case, {TARGET: canonical_json_bytes(doc) + b"\n"}, data_descriptor=descriptor)
    return doc


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
        outputs = {"data/findings.json": (json.dumps(findings_doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")}
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
    args = parser.parse_args(argv)
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
        doc = write(Path(args.case_dir), proposal, args.actor, args.cycle)
    except (WriteError, OrchestrationError, json.JSONDecodeError, OSError) as exc:
        print(f"source-expressions: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "expressions": len(doc["expressions"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
