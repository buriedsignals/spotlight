#!/usr/bin/env python3
"""Build the newsroom Submit package, and record what the Editorial MCP answered.

  python3 scripts/newsroom-package.py build {CASE_DIR}
  python3 scripts/newsroom-package.py register {CASE_DIR}          < reference JSON
  python3 scripts/newsroom-package.py checks {CASE_DIR}            < check record JSON
  python3 scripts/newsroom-package.py begin {CASE_DIR} --package-sha256 SHA
  python3 scripts/newsroom-package.py blocked {CASE_DIR} --package-sha256 SHA  < failures JSON
  python3 scripts/newsroom-package.py complete {CASE_DIR} --package-sha256 SHA \
      --submission-id ID --review-id ID                            < receipts JSON

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

Every other command is the only writer of a newsroom case file the model may
not write (data/editorial-case.json, data/newsroom-checks.json,
data/newsroom-submission.json, receipts/, and the ingest marker), and writes
it under the case lock. The Pi launcher calls them with what the Editorial MCP
answered, already cleared of the sign-in token and of the review address.

Submit is three steps so a crash can neither lose nor repeat a submission:
``begin`` records the intent (the package sha256, which is also the
idempotency key) before submit_case_review is called, and refuses unless the
member requested ingestion on the ingest page; ``blocked`` or ``complete``
then resolves that intent. A pending intent left by a crash is resubmitted
with the same key, which the Editorial MCP answers with the submission it
already staged. ``complete`` writes the receipts, the submission state and
the completed ingest marker (destination ``newsroom``); the phase then seals
it.

Exit codes: 0 = done; 2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))
from evidence_anchors import case_evidence_path  # noqa: E402
from source_expression_contract import lifecycle_state  # noqa: E402
from spotlight_orchestration import review  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.resolver import load_ingestion_marker, status_for  # noqa: E402
from spotlight_orchestration.storage import load_state, read_data_bytes, transaction  # noqa: E402

PACKAGE_VERSION = "spotlight-case-package/1.0"
ROLES = {"supports": "supporting", "contradicts": "contradicting", "context": "contextual"}
DIRECT_PATHS = ("raw_path", "downloaded_document_path", "screenshot_path")
DIRECT_PATHS_BY_PRECEDENCE = ("downloaded_document_path", "raw_path", "screenshot_path")
CASE_REFERENCE = "data/editorial-case.json"
CHECKS = "data/newsroom-checks.json"
SUBMISSION = "data/newsroom-submission.json"
RECEIPTS = "receipts"
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
RECEIPT_NAME = re.compile(r"^(submission|review-status|review)-[A-Za-z0-9._-]{1,200}\.json$")
# Research-phase material never travels, even when something cites it.
STAYS_BEHIND = ("research/swarm/",)
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
    if text.startswith("/") or re.match(r"[A-Za-z]:", text) or ".." in text.split("/"):
        return text, None, "the path leads outside the case"
    # No component may be a symlink: a linked research/ or evidence/ (or a
    # folder in them) would lead into data/ or out of the case.
    parts = text.split("/")
    if any(case.joinpath(*parts[: i + 1]).is_symlink() for i in range(len(parts))):
        return text, None, "the path passes through a symlink"
    # Only evidence locations travel (research/ and evidence/, on the real
    # path); data/, receipts/ and review state never do. Compared without
    # case, so a case variant cannot slip past on a case-insensitive disk.
    path = case_evidence_path(case, text)
    if path is None:
        return text, None, "the file is missing or is not in an evidence location"
    real = path.relative_to(case.resolve()).as_posix().lower()
    if any(real.startswith(p) or text.lower().startswith(p) for p in STAYS_BEHIND):
        return text, None, "research-phase material stays on this computer"
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
    checks = _load(case, CHECKS, required=False)
    items = {str(item.get("id")): item for item in bundle.get("items", []) if isinstance(item, dict)}

    entries: dict[str, dict[str, Any]] = {}
    contents: dict[str, bytes] = {}  # each cited file's bytes, read once from its validated path
    omissions: list[dict[str, Any]] = []
    problems: list[str] = []

    def cite(rel: object, role: str, expression_id: str, finding_id: str, evidence_id: str | None,
             kind: str, declared: str | None) -> None:
        text, path, why = _case_file(case, rel)
        if path is None:
            omissions.append({"path": text, "expression_id": expression_id, "reason": why})
            return
        data = contents.get(text)
        if data is None:
            # The cap holds before a file is buffered, not only after all are.
            if sum(map(len, contents.values())) + path.stat().st_size > MAX_PACKAGE_BYTES:
                raise Refused(f"the cited evidence is larger than {MAX_PACKAGE_BYTES} bytes")
            data = contents[text] = path.read_bytes()
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
                 item.get("id") if item else None, "anchor", expression.get("anchor_sha256"))
            if item is None:
                omissions.append({"path": None, "expression_id": expression_id,
                                  "reason": "its evidence item is not in the evidence bundle"})
                continue
            original = expression.get("original_artifact_sha256")
            for key in DIRECT_PATHS:
                if item.get(key):
                    # The passage declares its original artifact's hash: the
                    # downloaded document, else the raw capture, else (when it
                    # is the only artifact) the screenshot. A screenshot beside
                    # another original is a capture and is hashed as found.
                    declared = original if _first_original(item) == key else None
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
        data = contents[entry["path"]]
        total += len(data)
        if total > MAX_PACKAGE_BYTES:
            raise Refused(f"the cited evidence is larger than {MAX_PACKAGE_BYTES} bytes")
        files.append({"path": entry["path"], "sha256": entry["sha256"], "media_type": entry["media_type"],
                      "content_base64": base64.b64encode(data).decode("ascii")})
    return {"package": package, "files": files,
            "package_sha256": _sha(_canonical({"package": package, "file_sha256": [f["sha256"] for f in files]}))}


def _first_original(item: dict[str, Any]) -> str | None:
    for key in DIRECT_PATHS_BY_PRECEDENCE:
        if item.get(key):
            return key
    return None


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_data(case: Path, descriptor: int, rel: str) -> dict[str, Any] | None:
    content = read_data_bytes(case, rel.removeprefix("data/"), descriptor)
    if content is None:
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _require_newsroom() -> None:
    if review.ingest_mode() != "newsroom":
        raise Refused("this install is not in newsroom mode (knowledge_destination.mode)")


def _require_requested(case: Path, descriptor: int) -> None:
    """The member's Ingest decision (the ingest page's nonce flow) comes first."""
    status = status_for(case, load_state(case, descriptor), descriptor)
    detail = status.get("ingest") or {}
    if status.get("next_phase") != "ingest" or detail.get("resume_at") != "ingest":
        raise Refused("ingestion has not been requested")


def _object(raw: str, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError:
        raise Refused(f"{what} is not JSON") from None
    if not isinstance(value, dict):
        raise Refused(f"{what} must be a JSON object")
    return value


def register(case: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Stores the Editorial MCP's case reference once; a stored one stands."""
    case_ref = payload.get("case_ref")
    if not isinstance(case_ref, str) or not SAFE_ID.match(case_ref):
        raise Refused("register_case returned no usable case_ref")
    reference = {key: payload.get(key) for key in ("case_ref", "title", "custody_mode", "editorial_origin", "client_case_id")}
    if not all(isinstance(value, str) and value for value in reference.values()):
        raise Refused("the case reference needs case_ref, title, custody_mode, editorial_origin and client_case_id")
    with transaction(case) as descriptor:
        stored = _read_data(case, descriptor, CASE_REFERENCE)
        if stored and stored.get("case_ref") and stored.get("editorial_origin") == reference["editorial_origin"]:
            return {"ok": True, "already_recorded": True, "case_ref": stored["case_ref"]}
        record = {"schema_version": "1.0", **reference, "registered_at": _now()}
        atomic_write_files(case, {CASE_REFERENCE: _json_bytes(record)}, data_descriptor=descriptor)
    return {"ok": True, "already_recorded": False, "case_ref": case_ref}


def record_checks(case: Path, record: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind")
    if kind not in ("findings", "report"):
        raise Refused("a check record's kind must be findings or report")
    if record.get("state") not in ("done", "failed", "unavailable"):
        raise Refused("a check record's state must be done, failed or unavailable")
    with transaction(case) as descriptor:
        existing = _read_data(case, descriptor, CHECKS) or {}
        checks = existing.get("checks") if isinstance(existing.get("checks"), dict) else {}
        checks = {**checks, kind: record}
        atomic_write_files(case, {CHECKS: _json_bytes({"schema_version": "1.0", "checks": checks})},
                           data_descriptor=descriptor)
    return {"ok": True}


def _require_key(package_sha256: str) -> None:
    if not SHA256.match(package_sha256):
        raise Refused("the package sha256 must be 64 lower-case hex characters")


def begin(case: Path, package_sha256: str) -> dict[str, Any]:
    """Records the submission intent before submit_case_review is called."""
    _require_newsroom()
    _require_key(package_sha256)
    with transaction(case) as descriptor:
        _require_requested(case, descriptor)
        current = _read_data(case, descriptor, SUBMISSION) or {}
        if current.get("state") == "pending":
            if current.get("idempotency_key") != package_sha256:
                raise Refused("a submission of a different version of this case is pending; it must be "
                              "resolved before another is sent")
            return {"ok": True, "resumed": True}
        intent = {"schema_version": "1.0", "state": "pending", "idempotency_key": package_sha256,
                  "package_sha256": package_sha256, "at": _now()}
        atomic_write_files(case, {SUBMISSION: _json_bytes(intent)}, data_descriptor=descriptor)
    return {"ok": True, "resumed": False}


def pending(case: Path) -> dict[str, Any]:
    """The pending submission intent, if a crash left one."""
    with transaction(case) as descriptor:
        current = _read_data(case, descriptor, SUBMISSION) or {}
    if current.get("state") == "pending" and isinstance(current.get("idempotency_key"), str):
        return {"pending": True, "idempotency_key": current["idempotency_key"]}
    return {"pending": False}


def _require_intent(case: Path, descriptor: int, package_sha256: str) -> None:
    current = _read_data(case, descriptor, SUBMISSION) or {}
    if current.get("state") != "pending" or current.get("idempotency_key") != package_sha256:
        raise Refused("no submission of this package is pending")


def blocked(case: Path, package_sha256: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_newsroom()
    _require_key(package_sha256)
    failures = payload.get("integrity_failures")
    if not isinstance(failures, list):
        raise Refused("integrity_failures must be a list")
    reasons = [failure for failure in failures if isinstance(failure, dict)][:200] or [
        {"check": "unknown", "reason": "the service rejected the package without a reason"}]
    with transaction(case) as descriptor:
        _require_intent(case, descriptor, package_sha256)
        record = {"schema_version": "1.0", "state": "blocked", "package_sha256": package_sha256,
                  "integrity_failures": reasons, "at": _now()}
        atomic_write_files(case, {SUBMISSION: _json_bytes(record)}, data_descriptor=descriptor)
    return {"ok": True}


def _ensure_receipts_folder(case: Path) -> None:
    descriptor = os.open(case, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.mkdir(RECEIPTS, 0o700, dir_fd=descriptor)
    except FileExistsError:
        pass
    finally:
        os.close(descriptor)


def complete(case: Path, package_sha256: str, submission_id: str, review_id: str,
             payload: dict[str, Any]) -> dict[str, Any]:
    _require_newsroom()
    _require_key(package_sha256)
    if not SAFE_ID.match(submission_id) or not SAFE_ID.match(review_id):
        raise Refused("submission and review ids must be plain identifiers")
    receipts = payload.get("receipts")
    if not isinstance(receipts, dict) or not receipts:
        raise Refused("receipts must be a non-empty object")
    for name, value in receipts.items():
        if not RECEIPT_NAME.match(name) or not isinstance(value, dict):
            raise Refused(f"refused receipt {name!r}")
    review_state = str(payload.get("review_state") or "pending")[:40]
    with transaction(case) as descriptor:
        marker = load_ingestion_marker(case, descriptor)
        current = _read_data(case, descriptor, SUBMISSION) or {}
        if marker.get("status") == "completed":
            if marker.get("package_sha256") == package_sha256 and current.get("state") == "submitted":
                return {"ok": True, "already_recorded": True}
            raise Refused("a different submission is already recorded for this case")
        _require_requested(case, descriptor)
        _require_intent(case, descriptor, package_sha256)
        _ensure_receipts_folder(case)
        atomic_write_files(case, {f"{RECEIPTS}/{name}": _json_bytes(value) for name, value in receipts.items()})
        marker.update({"status": "completed", "destination": "newsroom", "package_sha256": package_sha256,
                       "submission_id": submission_id, "review_id": review_id, "completed_at": _now()})
        submission = {"schema_version": "1.0", "state": "submitted", "idempotency_key": package_sha256,
                      "package_sha256": package_sha256, "submission_id": submission_id, "review_id": review_id,
                      "review_state": review_state, "receipts": sorted(f"{RECEIPTS}/{name}" for name in receipts),
                      "at": _now()}
        atomic_write_files(case, {
            "data/ingestion.json": _json_bytes(marker),
            SUBMISSION: _json_bytes(submission),
        }, data_descriptor=descriptor)
    return {"ok": True, "already_recorded": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "register", "checks", "pending"):
        sub.add_parser(name).add_argument("case_dir")
    for name in ("begin", "blocked", "complete"):
        command = sub.add_parser(name)
        command.add_argument("case_dir")
        command.add_argument("--package-sha256", required=True)
        if name == "complete":
            command.add_argument("--submission-id", required=True)
            command.add_argument("--review-id", required=True)
    args = parser.parse_args(argv)
    case = Path(args.case_dir).resolve()
    try:
        if args.command == "build":
            result = build(case)
        elif args.command == "register":
            result = register(case, _object(sys.stdin.read(), "the case reference"))
        elif args.command == "checks":
            result = record_checks(case, _object(sys.stdin.read(), "the check record"))
        elif args.command == "pending":
            result = pending(case)
        elif args.command == "begin":
            result = begin(case, args.package_sha256)
        elif args.command == "blocked":
            result = blocked(case, args.package_sha256, _object(sys.stdin.read(), "the rejection"))
        else:
            result = complete(case, args.package_sha256, args.submission_id, args.review_id,
                              _object(sys.stdin.read(), "the receipts"))
    except (Refused, OrchestrationError, OSError) as exc:
        print(f"newsroom-package: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
