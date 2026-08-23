#!/usr/bin/env python3
"""Persist and derive one hash-bound Spotlight case orchestration state."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

STATE_VERSION = "spotlight-orchestration/v1"
STATUS_VERSION = "spotlight-orchestration-status/v1"
DEPENDENCY_STATUS_VERSION = "spotlight-gate1-dependencies/v1"
METHODOLOGY_INPUTS = ("brief-directions.txt", "data/methodology.json")
GATE1_FINALIZATION_OUTPUTS = ("data/provenance-manifest.json", "review.html")
ATTEMPT_LIMITS = {
    "execution-cycle": 5,
    "fact-check-evidence-repair": 1,
    "structural-correction": 2,
}
SCRIPT_DIR = Path(__file__).resolve().parent


class OrchestrationError(ValueError):
    """A case or transition does not satisfy the orchestration contract."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def resolve_case(value: str) -> Path:
    case = Path(value).resolve()
    if not case.is_dir() or not (case / "data").is_dir():
        raise OrchestrationError(f"case directory or data directory not found: {case}")
    return case


@contextmanager
def state_lock(case: Path, *, exclusive: bool) -> Iterator[None]:
    lock_path = case / "data/.orchestration.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def new_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_VERSION,
        "approvals": {},
        "attempts": {},
        "decisions": {},
    }


def load_state(case: Path) -> dict[str, Any]:
    path = case / "data/orchestration.json"
    if not path.is_file():
        return new_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/orchestration.json: {exc}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != STATE_VERSION:
        raise OrchestrationError("data/orchestration.json has an unsupported schema")
    for field in ("approvals", "attempts", "decisions"):
        if not isinstance(state.get(field), dict):
            raise OrchestrationError(f"data/orchestration.json field {field!r} must be an object")
    if any(
        kind not in ATTEMPT_LIMITS
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or count > ATTEMPT_LIMITS[kind]
        for kind, count in state["attempts"].items()
    ):
        raise OrchestrationError("data/orchestration.json contains invalid attempt counts")
    for field in ("blocked", "follow_up", "gate1_finalization"):
        if field in state and not isinstance(state[field], dict):
            raise OrchestrationError(f"data/orchestration.json field {field!r} must be an object")
    return state


def current_hashes(case: Path, inputs: tuple[str, ...]) -> dict[str, str]:
    missing = [relative for relative in inputs if not (case / relative).is_file()]
    if missing:
        raise OrchestrationError(f"required case inputs are missing: {', '.join(missing)}")
    return {relative: sha256(case / relative) for relative in inputs}


def receipt_matches(receipt: object, hashes: dict[str, str]) -> bool:
    return isinstance(receipt, dict) and receipt.get("input_sha256") == hashes


def digest_receipt_matches(receipt: object, dependency_digest: str) -> bool:
    return isinstance(receipt, dict) and receipt.get("dependency_digest") == dependency_digest


def approval_matches(receipt: object, hashes: dict[str, str]) -> bool:
    if not receipt_matches(receipt, hashes) or not isinstance(receipt, dict):
        return False
    return attributable_approval(receipt)


def gate1_approval_matches(receipt: object, dependency_digest: str) -> bool:
    if not digest_receipt_matches(receipt, dependency_digest) or not isinstance(receipt, dict):
        return False
    return attributable_approval(receipt)


def attributable_approval(receipt: dict[str, Any]) -> bool:
    actor = receipt.get("approved_by")
    approved_at = receipt.get("approved_at")
    if not isinstance(actor, str) or not isinstance(approved_at, str):
        return False
    try:
        validate_approval(actor, approved_at)
    except OrchestrationError:
        return False
    return True


def outputs_match(case: Path, receipt: object) -> bool:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("output_sha256"), dict):
        return False
    outputs = receipt["output_sha256"]
    return bool(outputs) and all(
        isinstance(relative, str)
        and isinstance(expected, str)
        and (case / relative).is_file()
        and sha256(case / relative) == expected
        for relative, expected in outputs.items()
    )


def gate1_dependencies(case: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "build-provenance-manifest.py"),
                str(case),
                "--dependency-digest",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise OrchestrationError(f"cannot hash Gate 1 dependencies: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "dependency builder failed"
        raise OrchestrationError(detail)
    try:
        snapshot = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OrchestrationError("dependency builder returned invalid JSON") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != DEPENDENCY_STATUS_VERSION
        or not isinstance(snapshot.get("ready"), bool)
        or not isinstance(snapshot.get("missing"), list)
        or not isinstance(snapshot.get("dependency_digest"), str)
    ):
        raise OrchestrationError("dependency builder returned an unsupported contract")
    return snapshot


def require_gate1_digest(case: Path) -> str:
    snapshot = gate1_dependencies(case)
    if not snapshot["ready"]:
        raise OrchestrationError(
            f"required Gate 1 inputs are missing: {', '.join(snapshot['missing'])}"
        )
    return str(snapshot["dependency_digest"])


def provenance_matches(case: Path, dependency_digest: str) -> bool:
    path = case / "data/provenance-manifest.json"
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("input_set_hash") == dependency_digest

def review_is_fresh(case: Path, approval: object) -> bool:
    path = case / "review.html"
    if not path.is_file():
        return False
    prior_hash = (
        approval.get("review_sha256_at_approval")
        if isinstance(approval, dict)
        else None
    )
    return not isinstance(prior_hash, str) or sha256(path) != prior_hash


def execution_status(
    status: dict[str, Any], state: dict[str, Any], follow_up: object = None
) -> dict[str, Any]:
    value = {
        **status,
        "status": "active",
        "next_phase": "execution",
        "attempts": dict(state["attempts"]),
    }
    if isinstance(follow_up, dict):
        value["follow_up"] = {"instructions": follow_up["instructions"]}
    return value


def status_for(case: Path, state: dict[str, Any]) -> dict[str, Any]:
    status = {"schema_version": STATUS_VERSION, "status": "pending", "next_phase": "brief"}
    if not (case / "brief-directions.txt").is_file():
        return status
    if not (case / "data/methodology.json").is_file():
        return {**status, "next_phase": "methodology"}
    methodology_hashes = current_hashes(case, METHODOLOGY_INPUTS)
    if not approval_matches(state["approvals"].get("methodology"), methodology_hashes):
        return {**status, "next_phase": "methodology_approval"}
    if "blocked" in state:
        return {**status, "status": "blocked", "next_phase": "blocked", "blocked": state["blocked"]}

    dependencies = gate1_dependencies(case)
    dependency_digest = str(dependencies["dependency_digest"])
    follow_up = state.get("follow_up")
    if not dependencies["ready"]:
        return execution_status(status, state)
    if (
        digest_receipt_matches(follow_up, dependency_digest)
        and isinstance(follow_up.get("instructions"), str)
    ):
        return execution_status(status, state, follow_up)
    gate1_approval = state["approvals"].get("gate1")
    if not gate1_approval_matches(gate1_approval, dependency_digest):
        return {**status, "next_phase": "gate1_approval"}

    finalization = state.get("gate1_finalization")
    if not digest_receipt_matches(finalization, dependency_digest) or not outputs_match(
        case, finalization
    ):
        if not provenance_matches(case, dependency_digest):
            resume_at = "provenance"
        elif not review_is_fresh(case, gate1_approval):
            resume_at = "review"
        else:
            resume_at = "seal"
        return {
            **status,
            "status": "active",
            "next_phase": "gate1_finalization",
            "gate1": {"state": "approved", "resume_at": resume_at},
        }

    report = state["decisions"].get("report")
    if (
        not isinstance(report, dict)
        or report.get("decision") not in {"completed", "declined"}
        or not digest_receipt_matches(report, dependency_digest)
        or not outputs_match(case, report)
    ):
        return {**status, "next_phase": "report"}

    marker = load_ingestion_marker(case)
    marker_status = marker.get("status")
    ingest = state["decisions"].get("ingest")
    if not digest_receipt_matches(ingest, dependency_digest):
        if (
            digest_receipt_matches(marker, dependency_digest)
            and marker_status in {"requested", "completed"}
        ):
            detail = (
                {"state": "completed", "resume_at": "seal"}
                if marker_status == "completed"
                else {"state": "requested", "resume_at": "ingest"}
            )
            return {**status, "status": "active", "next_phase": "ingest", "ingest": detail}
        return {
            **status,
            "next_phase": "ingest",
            "ingest": {"state": "pending", "resume_at": "decision"},
        }
    decision = ingest.get("decision")
    if decision == "requested":
        detail = (
            {"state": "completed", "resume_at": "seal"}
            if marker_status == "completed"
            else {"state": "requested", "resume_at": "ingest"}
        )
        return {**status, "status": "active", "next_phase": "ingest", "ingest": detail}
    if decision == "completed" and outputs_match(case, ingest):
        return {
            "schema_version": STATUS_VERSION,
            "status": "completed",
            "next_phase": "complete",
            "ingest": {"state": "completed", "resume_at": "complete"},
        }
    if decision == "declined" and outputs_match(case, ingest):
        return {
            "schema_version": STATUS_VERSION,
            "status": "completed",
            "next_phase": "complete",
            "ingest": {"state": "declined", "resume_at": "complete"},
        }
    if decision == "completed" and marker_status == "completed":
        return {
            **status,
            "status": "active",
            "next_phase": "ingest",
            "ingest": {"state": "completed", "resume_at": "seal"},
        }
    return {
        **status,
        "next_phase": "ingest",
        "ingest": {"state": "pending", "resume_at": "decision"},
    }


def validate_approval(actor: str, approved_at: str) -> None:
    if not actor.strip():
        raise OrchestrationError("--approved-by must name the approving human")
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OrchestrationError("--approved-at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OrchestrationError("--approved-at must include a timezone")


def approve(case: Path, gate: str, actor: str, approved_at: str) -> None:
    validate_approval(actor, approved_at)
    state = load_state(case)
    expected_phase = "methodology_approval" if gate == "methodology" else "gate1_approval"
    if status_for(case, state)["next_phase"] != expected_phase:
        raise OrchestrationError(f"case is not awaiting {gate} approval")

    previous = state["approvals"].get(gate)
    if gate == "methodology":
        hashes = current_hashes(case, METHODOLOGY_INPUTS)
        state["approvals"][gate] = {
            "approved_by": actor,
            "approved_at": approved_at,
            "input_sha256": hashes,
        }
        if not approval_matches(previous, hashes):
            state["approvals"].pop("gate1", None)
            state["decisions"].clear()
            state["attempts"].clear()
            state.pop("blocked", None)
            state.pop("follow_up", None)
            state.pop("gate1_finalization", None)
    else:
        dependency_digest = require_gate1_digest(case)
        gate1_receipt = {
            "approved_by": actor,
            "approved_at": approved_at,
            "dependency_digest": dependency_digest,
        }
        review_path = case / "review.html"
        if review_path.is_file():
            gate1_receipt["review_sha256_at_approval"] = sha256(review_path)
        state["approvals"][gate] = gate1_receipt
        if not gate1_approval_matches(previous, dependency_digest):
            state["decisions"].clear()
            state.pop("gate1_finalization", None)
        state.pop("follow_up", None)
    atomic_write_json(case / "data/orchestration.json", state)


def record_attempt(case: Path, kind: str, gap: str) -> None:
    if not gap.strip():
        raise OrchestrationError("--gap must describe the unresolved gap")
    state = load_state(case)
    if status_for(case, state)["next_phase"] != "execution":
        raise OrchestrationError("attempts can only be recorded during execution")
    attempts = state["attempts"]
    attempts[kind] = attempts.get(kind, 0) + 1
    if attempts[kind] >= ATTEMPT_LIMITS[kind]:
        state["blocked"] = {
            "phase": "execution",
            "exhausted_attempt": kind,
            "gap": gap,
            "attempts": dict(attempts),
        }
    atomic_write_json(case / "data/orchestration.json", state)


def request_follow_up(case: Path, instructions: str) -> None:
    if not instructions.strip():
        raise OrchestrationError("--instructions must describe the requested follow-up")
    state = load_state(case)
    if status_for(case, state)["next_phase"] not in {
        "gate1_approval",
        "gate1_finalization",
        "report",
    }:
        raise OrchestrationError("follow-up can only be requested from Gate 1")
    state["follow_up"] = {
        "instructions": instructions,
        "dependency_digest": require_gate1_digest(case),
    }
    state["approvals"].pop("gate1", None)
    state.pop("gate1_finalization", None)
    state["decisions"].clear()
    atomic_write_json(case / "data/orchestration.json", state)


def seal_gate1(case: Path) -> None:
    state = load_state(case)
    status = status_for(case, state)
    if status.get("next_phase") != "gate1_finalization" or status.get("gate1", {}).get(
        "resume_at"
    ) != "seal":
        raise OrchestrationError("Gate 1 finalization outputs are not ready to seal")
    state["gate1_finalization"] = {
        "dependency_digest": require_gate1_digest(case),
        "output_sha256": current_hashes(case, GATE1_FINALIZATION_OUTPUTS),
    }
    state["decisions"].clear()
    atomic_write_json(case / "data/orchestration.json", state)


def run_helper(script: str, case: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / script), str(case), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OrchestrationError(result.stdout.strip() or result.stderr.strip() or f"{script} failed")


def decide_report(case: Path, decision: str) -> None:
    state = load_state(case)
    if status_for(case, state)["next_phase"] != "report":
        raise OrchestrationError("case is not awaiting a report decision")
    if decision == "declined":
        run_helper("decline-report.py", case)
        outputs = ("data/report-declined.json",)
    else:
        run_helper("finalize-report.py", case)
        outputs = ("report.html", "findings-report.md", "evidence-map.json")
    state["decisions"]["report"] = {
        "decision": decision,
        "dependency_digest": require_gate1_digest(case),
        "output_sha256": current_hashes(case, outputs),
    }
    state["decisions"].pop("ingest", None)
    atomic_write_json(case / "data/orchestration.json", state)


def load_ingestion_marker(case: Path) -> dict[str, Any]:
    marker_path = case / "data/ingestion.json"
    marker: dict[str, Any] = {"schema_version": "1.0"}
    if not marker_path.is_file():
        return marker
    try:
        existing = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/ingestion.json: {exc}") from exc
    if not isinstance(existing, dict):
        raise OrchestrationError("data/ingestion.json must be an object")
    marker.update(existing)
    return marker


def decide_ingest(case: Path, decision: str) -> None:
    state = load_state(case)
    status = status_for(case, state)
    if status["next_phase"] != "ingest":
        raise OrchestrationError("case is not awaiting an ingestion decision")
    detail = status.get("ingest", {})
    if decision in {"requested", "declined"} and detail.get("resume_at") != "decision":
        raise OrchestrationError("the ingestion decision is already durable")
    if decision == "completed" and detail != {"state": "completed", "resume_at": "seal"}:
        raise OrchestrationError("ingestion has not produced a completed receipt to seal")

    dependency_digest = require_gate1_digest(case)
    marker = load_ingestion_marker(case)
    marker["status"] = decision
    marker["dependency_digest"] = dependency_digest
    marker_path = case / "data/ingestion.json"
    atomic_write_json(marker_path, marker)
    state["decisions"]["ingest"] = {
        "decision": decision,
        "dependency_digest": dependency_digest,
        "output_sha256": current_hashes(case, ("data/ingestion.json",)),
    }
    atomic_write_json(case / "data/orchestration.json", state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.add_argument("case_dir")
    approval = commands.add_parser("approve")
    approval.add_argument("gate", choices=("methodology", "gate1"))
    approval.add_argument("--approved-by", required=True)
    approval.add_argument("--approved-at", required=True)
    approval.add_argument("case_dir")
    attempt = commands.add_parser("record-attempt")
    attempt.add_argument("kind", choices=tuple(ATTEMPT_LIMITS))
    attempt.add_argument("--gap", required=True)
    attempt.add_argument("case_dir")
    follow_up = commands.add_parser("request-follow-up")
    follow_up.add_argument("--instructions", required=True)
    follow_up.add_argument("case_dir")
    seal = commands.add_parser("seal-gate1")
    seal.add_argument("case_dir")
    report = commands.add_parser("decide-report")
    report.add_argument("decision", choices=("completed", "declined"))
    report.add_argument("case_dir")
    ingest = commands.add_parser("decide-ingest")
    ingest.add_argument("decision", choices=("requested", "completed", "declined"))
    ingest.add_argument("case_dir")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        case = resolve_case(args.case_dir)
        with state_lock(case, exclusive=args.command != "status"):
            if args.command == "status":
                value = status_for(case, load_state(case))
                print(
                    json.dumps(value, sort_keys=True)
                    if args.json
                    else f"{value['status']}: {value['next_phase']}"
                )
            elif args.command == "approve":
                approve(case, args.gate, args.approved_by, args.approved_at)
            elif args.command == "record-attempt":
                record_attempt(case, args.kind, args.gap)
            elif args.command == "request-follow-up":
                request_follow_up(case, args.instructions)
            elif args.command == "seal-gate1":
                seal_gate1(case)
            elif args.command == "decide-report":
                decide_report(case, args.decision)
            else:
                decide_ingest(case, args.decision)
    except OrchestrationError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
