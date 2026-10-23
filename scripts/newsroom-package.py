#!/usr/bin/env python3
"""Build the newsroom Submit package, and record a staged submission.

  python3 scripts/newsroom-package.py build {CASE_DIR}
  python3 scripts/newsroom-package.py complete {CASE_DIR} --package-sha256 SHA \
      --submission-id ID --review-id ID

Newsroom edition only (``knowledge_destination.mode`` is ``newsroom``), for an
activated case whose ingestion was requested on the ingest page. The Pi
harness runs both steps itself around the Editorial MCP's
``submit_case_review``; the contract is docs/newsroom-editorial-mcp.md.

``build`` prints one JSON object: ``package`` (the structured case), ``files``
(the cited evidence files, base64) and ``package_sha256`` (the idempotency
key). The package holds the findings, the fact-check, the passages, the
source positions, the evidence manifest with hashes, the decision flags and
dispositions so far, and the journalist-input units. Only cited evidence
files travel: those a passage linked to a finding anchors in, and the
original artifacts and text derivatives of their evidence items, each with
its role (supporting, contradicting, contextual). Raw research stays behind:
nothing uncited, nothing under research/swarm/, no leads or clues. A cited
file that cannot travel is a declared omission with its reason. A file whose
bytes no longer match a declared hash stops the build.

``complete`` records the staged submission in data/ingestion.json (status
``completed``, destination ``newsroom``) under the case lock; the phase then
seals it. It refuses unless ingestion was requested.

Exit codes: 0 = done; 2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))
from source_expression_contract import lifecycle_state  # noqa: E402
from spotlight_orchestration import review  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.resolver import load_ingestion_marker, status_for  # noqa: E402
from spotlight_orchestration.storage import load_state, transaction  # noqa: E402

PACKAGE_VERSION = "spotlight-case-package/1.0"
ROLES = {"supports": "supporting", "contradicts": "contradicting", "context": "contextual"}
DIRECT_PATHS = ("raw_path", "downloaded_document_path", "screenshot_path")
# Research-phase material never travels, even when something cites it.
STAYS_BEHIND = ("research/swarm/", "data/leads.json", "data/leads.jsonl", "data/clues.json")
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PACKAGE_BYTES = 200 * 1024 * 1024


class Refused(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load(case: Path, rel: str, required: bool = True) -> Any:
    path = case / rel
    if path.is_symlink() or not path.is_file():
        if required:
            raise Refused(f"{rel} is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{rel} is unreadable: {exc}") from None


def journalist_input_units(case: Path) -> list[dict[str, Any]]:
    """Extension point for the newsroom edition's journalist input.

    The newsroom edition records interviews, source emails, notes and
    documents at case intake (kind, source or protected pseudonym,
    attribution, consent, rights, sensitivity) and contributes the units a
    journalist marked for review here. Open-source Spotlight has no
    journalist-input intake, so it contributes none. Whatever an edition
    returns travels only to the newsroom's own Editorial MCP, never to a
    third-party model route.
    """
    return []


def _case_file(case: Path, rel: object) -> tuple[str, Path | None, str | None]:
    """The case-relative path, its real file, or why it cannot travel."""
    if not isinstance(rel, str) or not rel:
        return str(rel), None, "no path recorded"
    text = rel.replace("\\", "/").removeprefix("{CASE_DIR}/")
    if text.startswith("/") or ".." in text.split("/"):
        return text, None, "the path leads outside the case"
    if any(text == p or text.startswith(p) for p in STAYS_BEHIND):
        return text, None, "research-phase material stays on this computer"
    path = case / text
    if path.is_symlink():
        return text, None, "the path is a symlink"
    if not path.is_file():
        return text, None, "the file is missing"
    try:
        path.resolve().relative_to(case.resolve())
    except ValueError:
        return text, None, "the path leads outside the case"
    if path.stat().st_size > MAX_FILE_BYTES:
        return text, None, f"the file is larger than {MAX_FILE_BYTES} bytes"
    return text, path, None


def build(case: Path) -> dict[str, Any]:
    if not review.activated_case(case):
        raise Refused("only an activated case (findings 1.1) is submitted to the newsroom")
    if review.ingest_mode() != "newsroom":
        raise Refused("this install is not in newsroom mode (knowledge_destination.mode)")
    findings = _load(case, "data/findings.json")
    fact_check = _load(case, "data/fact-check.json")
    bundle = _load(case, "data/evidence-bundle.json")
    passages = _load(case, "data/source-expressions.json", required=False) or {"expressions": []}
    positions = _load(case, "data/source-positions.json", required=False)
    checks = _load(case, "data/newsroom-checks.json", required=False)
    items = {str(item.get("id")): item for item in bundle.get("items", []) if isinstance(item, dict)}

    entries: dict[str, dict[str, Any]] = {}
    omissions: list[dict[str, Any]] = []
    problems: list[str] = []

    def cite(rel: object, role: str, expression_id: str, finding_id: str, evidence_id: str | None,
             kind: str, declared: str | None) -> None:
        text, path, why = _case_file(case, rel)
        if path is None:
            omissions.append({"path": text, "expression_id": expression_id, "reason": why})
            return
        data = path.read_bytes()
        digest = _sha(data)
        if declared and declared.lower() != digest:
            problems.append(f"{text} no longer matches its declared sha256 ({kind})")
            return
        entry = entries.setdefault(text, {
            "path": text, "sha256": digest, "bytes": len(data),
            "media_type": mimetypes.guess_type(text)[0] or "application/octet-stream",
            "kinds": [], "roles": [], "finding_ids": [], "expression_ids": [], "evidence_ids": [],
        })
        for key, value in (("kinds", kind), ("roles", role), ("finding_ids", finding_id),
                           ("expression_ids", expression_id), ("evidence_ids", evidence_id)):
            if value and value not in entry[key]:
                entry[key].append(value)

    for expression in passages.get("expressions", []):
        if not isinstance(expression, dict) or lifecycle_state(expression) != "activated":
            continue
        expression_id = str(expression.get("id"))
        item = items.get(str(expression.get("original_evidence_bundle_id")))
        for link in expression.get("finding_links", []) or []:
            role = ROLES.get(link.get("relation"), "contextual")
            finding_id = str(link.get("finding_id"))
            cite((expression.get("anchor_ref") or {}).get("path"), role, expression_id, finding_id,
                 item.get("id") if item else None, "anchor", None)
            if item is None:
                omissions.append({"path": None, "expression_id": expression_id,
                                  "reason": "its evidence item is not in the evidence bundle"})
                continue
            original = expression.get("original_artifact_sha256")
            for key in DIRECT_PATHS:
                if item.get(key):
                    # The original artifact's declared hash is the item's; a
                    # screenshot is a capture beside it and is hashed as found.
                    declared = original if key != "screenshot_path" and _first_original(item) == key else None
                    cite(item[key], role, expression_id, finding_id, item.get("id"), key, declared)
            for derivative in item.get("text_derivatives", []) or []:
                if isinstance(derivative, dict) and derivative.get("path"):
                    cite(derivative["path"], role, expression_id, finding_id, item.get("id"),
                         "text_derivative", derivative.get("sha256"))
    if problems:
        raise Refused("; ".join(sorted(set(problems))[:10]))

    manifest = sorted(entries.values(), key=lambda entry: entry["path"])
    reviews = {}
    for gate in ("investigation_gate", "report"):
        record = _load(case, f"data/{review.review_file(gate)}", required=False)
        if record is not None:
            reviews[gate] = record
    package = {
        "package_version": PACKAGE_VERSION,
        "case_id": case.name,
        "findings": findings,
        "fact_check": fact_check,
        "passages": passages,
        "source_positions": positions,
        "evidence_manifest": {"files": manifest, "omissions": omissions},
        "decision_flags": (checks or {}).get("checks", {}),
        "dispositions": reviews,
        "journalist_input_units": journalist_input_units(case),
    }
    files = []
    total = 0
    for entry in manifest:
        data = (case / entry["path"]).read_bytes()
        total += len(data)
        if total > MAX_PACKAGE_BYTES:
            raise Refused(f"the cited evidence is larger than {MAX_PACKAGE_BYTES} bytes")
        files.append({"path": entry["path"], "sha256": entry["sha256"], "media_type": entry["media_type"],
                      "content_base64": base64.b64encode(data).decode("ascii")})
    return {"package": package, "files": files,
            "package_sha256": _sha(_canonical({"package": package, "file_sha256": [f["sha256"] for f in files]}))}


def _first_original(item: dict[str, Any]) -> str | None:
    for key in ("downloaded_document_path", "raw_path"):
        if item.get(key):
            return key
    return None


def complete(case: Path, package_sha256: str, submission_id: str, review_id: str) -> dict[str, Any]:
    if review.ingest_mode() != "newsroom":
        raise Refused("this install is not in newsroom mode (knowledge_destination.mode)")
    with transaction(case) as descriptor:
        state = load_state(case, descriptor)
        status = status_for(case, state, descriptor)
        detail = status.get("ingest") or {}
        if status.get("next_phase") != "ingest" or detail.get("resume_at") not in ("ingest", "seal"):
            raise Refused("ingestion has not been requested")
        marker = load_ingestion_marker(case, descriptor)
        if marker.get("status") == "completed":
            if marker.get("package_sha256") == package_sha256:
                return {"ok": True, "already_recorded": True}
            raise Refused("a different submission is already recorded for this case")
        marker.update({"status": "completed", "destination": "newsroom", "package_sha256": package_sha256,
                       "submission_id": submission_id, "review_id": review_id, "completed_at": _now()})
        atomic_write_files(case, {
            "data/ingestion.json": (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        }, data_descriptor=descriptor)
    return {"ok": True, "already_recorded": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    built = sub.add_parser("build")
    built.add_argument("case_dir")
    done = sub.add_parser("complete")
    done.add_argument("case_dir")
    done.add_argument("--package-sha256", required=True)
    done.add_argument("--submission-id", required=True)
    done.add_argument("--review-id", required=True)
    args = parser.parse_args(argv)
    case = Path(args.case_dir).resolve()
    try:
        if args.command == "build":
            result = build(case)
        else:
            result = complete(case, args.package_sha256, args.submission_id, args.review_id)
    except (Refused, OrchestrationError, OSError) as exc:
        print(f"newsroom-package: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
