"""Durable Spotlight state transitions; callers serialize them with a transaction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .contract import (
    ATTEMPT_LIMITS,
    GATE1_FINALIZATION_OUTPUTS,
    METHODOLOGY_INPUTS,
    OrchestrationError,
    SCRIPT_DIR,
)
from .dependencies import (
    approval_matches,
    current_hashes,
    gate1_approval_matches,
    require_gate1_digest,
    sha256,
    validate_approval,
)
from .resolver import load_ingestion_marker, status_for
from .case_writer import restore_files_on_error
from .storage import atomic_write_json, case_path, load_state



def approve(
    case: Path,
    data_descriptor: int,
    gate: str,
    approved_by: str,
    approved_at: str,
) -> None:
    if gate not in {"methodology", "gate1"}:
        raise OrchestrationError("gate must be methodology or gate1")
    validate_approval(approved_by, approved_at)
    state = load_state(case, data_descriptor)
    expected_phase = "methodology_approval" if gate == "methodology" else "gate1_approval"
    if status_for(case, state, data_descriptor)["next_phase"] != expected_phase:
        raise OrchestrationError(f"case is not awaiting {gate} approval")

    previous = state["approvals"].get(gate)
    if gate == "methodology":
        hashes = current_hashes(case, METHODOLOGY_INPUTS)
        state["approvals"][gate] = {
            "approved_by": approved_by,
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
            "approved_by": approved_by,
            "approved_at": approved_at,
            "dependency_digest": dependency_digest,
        }
        review_path = case_path(case, "review.html")
        if review_path.is_file():
            gate1_receipt["review_sha256_at_approval"] = sha256(review_path)
        state["approvals"][gate] = gate1_receipt
        if not gate1_approval_matches(previous, dependency_digest):
            state["decisions"].clear()
            state.pop("gate1_finalization", None)
        state.pop("follow_up", None)
    atomic_write_json(
        case, "data/orchestration.json", state, data_descriptor=data_descriptor
    )


def record_attempt(case: Path, data_descriptor: int, kind: str, gap: str) -> None:
    if kind not in ATTEMPT_LIMITS:
        raise OrchestrationError(f"unsupported attempt kind: {kind}")
    if not gap.strip():
        raise OrchestrationError("gap must describe the unresolved gap")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] != "execution":
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
    atomic_write_json(
        case, "data/orchestration.json", state, data_descriptor=data_descriptor
    )


def request_follow_up(
    case: Path, data_descriptor: int, instructions: str
) -> None:
    if not instructions.strip():
        raise OrchestrationError("instructions must describe the requested follow-up")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] not in {
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
    atomic_write_json(
        case, "data/orchestration.json", state, data_descriptor=data_descriptor
    )


def seal_gate1(case: Path, data_descriptor: int) -> None:
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    if status.get("next_phase") != "gate1_finalization" or status.get("gate1", {}).get(
        "resume_at"
    ) != "seal":
        raise OrchestrationError("Gate 1 finalization outputs are not ready to seal")
    state["gate1_finalization"] = {
        "dependency_digest": require_gate1_digest(case),
        "output_sha256": current_hashes(case, GATE1_FINALIZATION_OUTPUTS),
    }
    state["decisions"].clear()
    atomic_write_json(
        case, "data/orchestration.json", state, data_descriptor=data_descriptor
    )


def _run_helper(script: str, case: Path) -> None:
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / script), str(case)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise OrchestrationError(f"cannot run {script}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip() or f"{script} failed"
        raise OrchestrationError(detail)


def decide_report(case: Path, data_descriptor: int, decision: str) -> None:
    if decision not in {"completed", "declined"}:
        raise OrchestrationError("report decision must be completed or declined")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] != "report":
        raise OrchestrationError("case is not awaiting a report decision")
    if decision == "declined":
        helper = "decline-report.py"
        outputs = ("data/report-declined.json",)
        recovery = restore_files_on_error(
            case, outputs, data_descriptor=data_descriptor
        )
    else:
        helper = "finalize-report.py"
        outputs = ("report.html", "findings-report.md", "evidence-map.json")
        recovery = restore_files_on_error(case, outputs)

    with recovery:
        _run_helper(helper, case)
        state["decisions"]["report"] = {
            "decision": decision,
            "dependency_digest": require_gate1_digest(case),
            "output_sha256": current_hashes(case, outputs),
        }
        state["decisions"].pop("ingest", None)
        atomic_write_json(
            case,
            "data/orchestration.json",
            state,
            data_descriptor=data_descriptor,
        )


def decide_ingest(case: Path, data_descriptor: int, decision: str) -> None:
    if decision not in {"requested", "completed", "declined"}:
        raise OrchestrationError("ingest decision must be requested, completed, or declined")
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    if status["next_phase"] != "ingest":
        raise OrchestrationError("case is not awaiting an ingestion decision")
    detail = status.get("ingest", {})
    if decision in {"requested", "declined"} and detail.get("resume_at") != "decision":
        raise OrchestrationError("the ingestion decision is already durable")
    if decision == "completed" and detail != {"state": "completed", "resume_at": "seal"}:
        raise OrchestrationError("ingestion has not produced a completed receipt to seal")

    dependency_digest = require_gate1_digest(case)
    marker = load_ingestion_marker(case, data_descriptor)
    marker["status"] = decision
    marker["dependency_digest"] = dependency_digest
    atomic_write_json(
        case, "data/ingestion.json", marker, data_descriptor=data_descriptor
    )
    state["decisions"]["ingest"] = {
        "decision": decision,
        "dependency_digest": dependency_digest,
        "output_sha256": current_hashes(case, ("data/ingestion.json",)),
    }
    atomic_write_json(
        case, "data/orchestration.json", state, data_descriptor=data_descriptor
    )
