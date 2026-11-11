#!/usr/bin/env python3
"""Prepare a newsroom case's approved batch, and record what the Editorial MCP answered.

  python3 scripts/newsroom-package.py register {CASE_DIR}          < reference JSON
  python3 scripts/newsroom-package.py request {CASE_DIR} --case-id ID --batch-id ID --out FILE
  python3 scripts/newsroom-package.py begin {CASE_DIR} --package-sha256 SHA --batch-id ID
  python3 scripts/newsroom-package.py pending {CASE_DIR}
  python3 scripts/newsroom-package.py blocked {CASE_DIR} --package-sha256 SHA  < errors JSON
  python3 scripts/newsroom-package.py complete {CASE_DIR} --package-sha256 SHA --batch-id ID  < receipts JSON

Newsroom edition only (``knowledge_destination.mode`` is ``newsroom``), for an
activated case whose ingestion was requested on the ingest page. The Pi
launcher and harness/pi/src/newsroom-submit.ts run these around the Editorial
MCP's ``start_case_review``; the contract is docs/newsroom-editorial-mcp.md.

``request`` writes one JSON object to FILE: ``request`` (the approved batch as
the service's input, scripts/newsroom_batch.py) and ``request_sha256``.

Every other command is the only writer of a newsroom case file the model may
not write (data/editorial-case.json, data/newsroom-submission.json,
data/newsroom-ids.json, receipts/, and the ingest marker), and writes it
under the case lock, with what the Editorial MCP answered already cleared of
the sign-in token and of the review address.

Submit is three steps so a crash can neither lose nor repeat a submission:
``begin`` records the intent (the batch id the service issued and the sha256
of the request) before start_case_review is called, and refuses unless the
member requested ingestion on the ingest page; ``blocked`` or ``complete``
then resolves that intent. The service stores a batch id at most once, so a
pending intent left by a crash is resolved by asking the service whether it
holds that batch. ``complete`` writes the receipts, the ids the service
assigned, the submission state and the completed ingest marker (destination
``newsroom``); the phase then seals it.

Exit codes: 0 = done; 2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))
import newsroom_batch  # noqa: E402
from spotlight_orchestration import review  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.resolver import load_ingestion_marker, status_for  # noqa: E402
from spotlight_orchestration.storage import load_state, read_data_bytes, transaction  # noqa: E402

CASE_REFERENCE = "data/editorial-case.json"
SUBMISSION = "data/newsroom-submission.json"
SERVER_IDS = "data/newsroom-ids.json"
RECEIPTS = "receipts"
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
RECEIPT_NAME = re.compile(r"^(submission|review-status|review)-[A-Za-z0-9._-]{1,200}\.json$")

class Refused(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load(case: Path, rel: str, required: bool = True) -> Any:
    """A case file as a JSON object; None only when an optional one is missing."""
    path = case / rel
    if path.is_symlink():
        raise Refused(f"{rel} is a symlink")
    if not path.exists():
        if required:
            raise Refused(f"{rel} is missing")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{rel} is unreadable: {exc}") from None
    if not isinstance(value, dict):
        raise Refused(f"{rel} is not a JSON object")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_data(case: Path, descriptor: int, rel: str) -> dict[str, Any] | None:
    """Case state: None only when the file is missing."""
    content = read_data_bytes(case, rel.removeprefix("data/"), descriptor)
    if content is None:
        return None
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict):
        raise Refused(f"{rel} is not a readable JSON object")
    return value


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
    """Keeps the case id the service issued, then marks it confirmed once the
    service answered register_case. A stored reference stands; only its
    ``confirmed`` mark changes."""
    case_ref = payload.get("case_ref")
    if not isinstance(case_ref, str) or not SAFE_ID.match(case_ref):
        raise Refused("the newsroom issued no usable case id")
    reference = {key: payload.get(key) for key in ("case_ref", "title", "editorial_origin", "client_case_id")}
    if not all(isinstance(value, str) and value for value in reference.values()):
        raise Refused("the case reference needs case_ref, title, editorial_origin and client_case_id")
    confirmed = payload.get("confirmed") is True
    with transaction(case) as descriptor:
        stored = _read_data(case, descriptor, CASE_REFERENCE)
        if stored and stored.get("case_ref"):
            if stored.get("case_ref") != case_ref or stored.get("editorial_origin") != reference["editorial_origin"]:
                raise Refused("this case already holds another newsroom reference")
            if stored.get("confirmed") is True or not confirmed:
                return {"ok": True, "already_recorded": True, "case_ref": case_ref}
            record = {**stored, "confirmed": True, "registered_at": _now()}
        else:
            record = {"schema_version": "1.1", **reference, "confirmed": confirmed, "kept_at": _now()}
            if confirmed:
                record["registered_at"] = _now()
        atomic_write_files(case, {CASE_REFERENCE: _json_bytes(record)}, data_descriptor=descriptor)
    return {"ok": True, "already_recorded": False, "case_ref": case_ref}


def _server_ids(case: Path, descriptor: int) -> list[dict[str, Any]]:
    """The ids the service assigned in this case's earlier submissions."""
    stored = _read_data(case, descriptor, SERVER_IDS) or {}
    return [entry for entry in stored.get("assigned_ids") or [] if isinstance(entry, dict)]


def request(case: Path, case_id: str, batch_id: str) -> dict[str, Any]:
    """The approved batch as the service's start_case_review input."""
    _require_newsroom()
    if not SAFE_ID.match(case_id) or not SAFE_ID.match(batch_id):
        raise Refused("the case and batch ids must be the service's ids")
    batch = _load(case, review.INGEST_BATCH)
    with transaction(case) as descriptor:
        known = {(str(entry.get("type")), str(entry.get("client_id"))): str(entry.get("uuid"))
                 for entry in _server_ids(case, descriptor)}
    try:
        converted = newsroom_batch.start_case_review_input(batch, case_id, batch_id, known)
    except newsroom_batch.Refused as exc:
        raise Refused(str(exc)) from None
    return {"request": converted, "request_sha256": _sha(_canonical(converted))}


def _require_key(package_sha256: str) -> None:
    if not SHA256.match(package_sha256):
        raise Refused("the package sha256 must be 64 lower-case hex characters")


def begin(case: Path, package_sha256: str, batch_id: str) -> dict[str, Any]:
    """Records the submission intent, with the batch id the service issued,
    before start_case_review is called."""
    _require_newsroom()
    _require_key(package_sha256)
    if not SAFE_ID.match(batch_id):
        raise Refused("the batch id must be the service's id")
    with transaction(case) as descriptor:
        _require_requested(case, descriptor)
        current = _read_data(case, descriptor, SUBMISSION) or {}
        if current.get("state") == "pending":
            if current.get("idempotency_key") != package_sha256 or current.get("batch_id") != batch_id:
                raise Refused("a submission of a different version of this case is pending; it must be "
                              "resolved before another is sent")
            return {"ok": True, "resumed": True}
        intent = {"schema_version": "1.1", "state": "pending", "idempotency_key": package_sha256,
                  "package_sha256": package_sha256, "batch_id": batch_id, "at": _now()}
        atomic_write_files(case, {SUBMISSION: _json_bytes(intent)}, data_descriptor=descriptor)
    return {"ok": True, "resumed": False}


def pending(case: Path) -> dict[str, Any]:
    """The pending submission intent, if a crash left one."""
    with transaction(case) as descriptor:
        current = _read_data(case, descriptor, SUBMISSION) or {}
    if current.get("state") != "pending":
        return {"pending": False}
    key = current.get("idempotency_key")
    if not (isinstance(key, str) and SHA256.match(key) and current.get("package_sha256") == key):
        raise Refused(f"{SUBMISSION} records a pending submission without a valid key")
    batch_id = current.get("batch_id")
    if not (isinstance(batch_id, str) and SAFE_ID.match(batch_id)):
        raise Refused(f"{SUBMISSION} records a pending submission without a batch id")
    return {"pending": True, "idempotency_key": key, "batch_id": batch_id}


def _require_intent(case: Path, descriptor: int, package_sha256: str) -> None:
    current = _read_data(case, descriptor, SUBMISSION) or {}
    if current.get("state") != "pending" or current.get("idempotency_key") != package_sha256:
        raise Refused("no submission of this package is pending")


def blocked(case: Path, package_sha256: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_newsroom()
    _require_key(package_sha256)
    errors = payload.get("errors")
    if not isinstance(errors, list):
        raise Refused("errors must be a list")
    reasons = [{"check": "service", "reason": str(error)} for error in errors[:200]] or [
        {"check": "unknown", "reason": "the service rejected the batch without a reason"}]
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


def complete(case: Path, package_sha256: str, batch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_newsroom()
    _require_key(package_sha256)
    if not SAFE_ID.match(batch_id):
        raise Refused("the batch id must be the service's id")
    assigned = payload.get("assigned_ids")
    if assigned is not None and not (isinstance(assigned, list) and all(
            isinstance(entry, dict) and all(isinstance(entry.get(key), str) and entry[key] for key in ("type", "client_id", "uuid"))
            for entry in assigned)):
        raise Refused("assigned_ids must be a list of {type, client_id, uuid}, or null")
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
        # Receipts first, then the data state (two folders, two atomic writes):
        # a crash between them leaves the intent pending, and the resume
        # resubmits the same key and rewrites the same receipts.
        _ensure_receipts_folder(case)
        atomic_write_files(case, {f"{RECEIPTS}/{name}": _json_bytes(value) for name, value in receipts.items()})
        marker.update({"status": "completed", "destination": "newsroom", "package_sha256": package_sha256,
                       "batch_id": batch_id, "completed_at": _now()})
        submission = {"schema_version": "1.1", "state": "submitted", "idempotency_key": package_sha256,
                      "package_sha256": package_sha256, "batch_id": batch_id,
                      "review_state": review_state, "receipts": sorted(f"{RECEIPTS}/{name}" for name in receipts),
                      "at": _now()}
        files = {"data/ingestion.json": _json_bytes(marker), SUBMISSION: _json_bytes(submission)}
        # A later batch of this case names these records by the service's ids.
        # null: the service already held the batch after a crash, so its
        # answer, and the ids in it, were lost.
        if assigned is not None:
            ids = {(entry["type"], entry["client_id"]): entry for entry in [*_server_ids(case, descriptor), *assigned]}
            files[SERVER_IDS] = _json_bytes({"schema_version": "1.0", "assigned_ids": sorted(
                ({"type": e["type"], "client_id": e["client_id"], "uuid": e["uuid"]} for e in ids.values()),
                key=lambda e: (e["type"], e["client_id"]))})
        atomic_write_files(case, files, data_descriptor=descriptor)
    return {"ok": True, "already_recorded": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "pending", "request"):
        sub.add_parser(name).add_argument("case_dir")
    for flag in ("--case-id", "--batch-id", "--out"):
        sub.choices["request"].add_argument(flag, required=True)
    for name in ("begin", "blocked", "complete"):
        command = sub.add_parser(name)
        command.add_argument("case_dir")
        command.add_argument("--package-sha256", required=True)
        if name != "blocked":
            command.add_argument("--batch-id", required=True)
    args = parser.parse_args(argv)
    case = Path(args.case_dir).resolve()
    try:
        if args.command == "request":
            Path(args.out).write_text(json.dumps(request(case, args.case_id, args.batch_id)), encoding="utf-8")
            result = {"written": args.out}
        elif args.command == "register":
            result = register(case, _object(sys.stdin.read(), "the case reference"))
        elif args.command == "pending":
            result = pending(case)
        elif args.command == "begin":
            result = begin(case, args.package_sha256, args.batch_id)
        elif args.command == "blocked":
            result = blocked(case, args.package_sha256, _object(sys.stdin.read(), "the rejection"))
        else:
            result = complete(case, args.package_sha256, args.batch_id, _object(sys.stdin.read(), "the receipts"))
    except (Refused, OrchestrationError, OSError) as exc:
        print(f"newsroom-package: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
