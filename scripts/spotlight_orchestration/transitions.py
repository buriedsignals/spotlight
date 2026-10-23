"""Durable Spotlight state transitions; callers serialize them with a transaction."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .contract import (
    ATTEMPT_LIMITS,
    INVESTIGATION_GATE_FINALIZATION_OUTPUTS,
    METHODOLOGY_INPUTS,
    OrchestrationError,
    SCRIPT_DIR,
)
from .dependencies import (
    approval_matches,
    current_hashes,
    investigation_gate_approval_matches,
    require_investigation_gate_digest,
    sha256,
    validate_approval,
)
from .resolver import load_ingestion_marker, status_for
from .case_contract import bind as bind_case_contract
from .case_writer import atomic_write_files, restore_files_on_error
from . import review
from .review import LEDGER_FILE, consume_nonce, ledger_bytes, load_ledger, record_decision
from .storage import (
    LEGACY_LEDGER_KEY,
    case_path,
    load_state,
)


def _state_bytes(state: dict) -> bytes:
    return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _publish(
    case: Path,
    data_descriptor: int,
    state: dict,
    ledger: dict | None = None,
    extra: dict | None = None,
) -> None:
    """Write the state in one atomic publication with the ledger entries it needs.

    A spent nonce is published with the decision it backs. For a state upgraded
    from v1, this first v2 write also records the carried-over decisions'
    legacy entries, so they stay backed once the file is v2.
    """
    legacy = state.pop(LEGACY_LEDGER_KEY, None)
    outputs: dict[str, bytes] = {}
    if legacy or ledger is not None:
        if ledger is None:
            ledger = load_ledger(case, data_descriptor)
        ledger["nonces"].update(legacy or {})
        outputs[f"data/{LEDGER_FILE}"] = ledger_bytes(ledger)
    outputs.update(extra or {})
    outputs["data/orchestration.json"] = _state_bytes(state)
    atomic_write_files(case, outputs, data_descriptor=data_descriptor)


def approve(
    case: Path,
    data_descriptor: int,
    gate: str,
    approved_by: str,
    approved_at: str,
    nonce: str | None = None,
    digest: str | None = None,
) -> None:
    if gate not in {"methodology", "investigation_gate"}:
        raise OrchestrationError("gate must be methodology or investigation_gate")
    validate_approval(approved_by, approved_at)
    state = load_state(case, data_descriptor)
    expected_phase = (
        "methodology_approval" if gate == "methodology" else "investigation_gate_approval"
    )
    if status_for(case, state, data_descriptor)["next_phase"] != expected_phase:
        raise OrchestrationError(f"case is not awaiting {gate} approval")
    ledger, binding = consume_nonce(case, data_descriptor, gate, nonce, digest)

    previous = state["approvals"].get(gate)
    if gate == "methodology":
        hashes = current_hashes(case, METHODOLOGY_INPUTS)
        state["approvals"][gate] = {
            "approved_by": approved_by,
            "approved_at": approved_at,
            "input_sha256": hashes,
            "review": binding,
        }
        record_decision(ledger, binding, "approved")
        if not approval_matches(previous, hashes):
            state["approvals"].pop("investigation_gate", None)
            state["decisions"].clear()
            state["attempts"].clear()
            state.pop("blocked", None)
            state.pop("follow_up", None)
            state.pop("investigation_gate_finalization", None)
    else:
        # Bind the activated artifacts this approval covers before computing
        # the digest, so the binding itself is part of what was approved.
        # A failure anywhere up to the state write puts the contract back.
        with restore_files_on_error(case, ("data/case-contract.json",), data_descriptor=data_descriptor):
            bind_case_contract(case, data_descriptor)
            dependency_digest = require_investigation_gate_digest(case)
            gate_receipt = {
                "approved_by": approved_by,
                "approved_at": approved_at,
                "dependency_digest": dependency_digest,
                "review": binding,
            }
            review_path = case_path(case, "review.html")
            if review_path.is_file():
                gate_receipt["review_sha256_at_approval"] = sha256(review_path)
            state["approvals"][gate] = gate_receipt
            record_decision(ledger, binding, "approved", dependency_digest)
            if not investigation_gate_approval_matches(previous, dependency_digest):
                state["decisions"].clear()
                state.pop("investigation_gate_finalization", None)
            state.pop("follow_up", None)
            _publish(case, data_descriptor, state, ledger)
        return
    _publish(case, data_descriptor, state, ledger)


def record_attempt(case: Path, data_descriptor: int, kind: str, gap: str) -> None:
    if kind not in ATTEMPT_LIMITS:
        raise OrchestrationError(f"unsupported attempt kind: {kind}")
    if not gap.strip():
        raise OrchestrationError("gap must describe the unresolved gap")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] != "investigation":
        raise OrchestrationError("attempts can only be recorded during investigation")
    attempts = state["attempts"]
    attempts[kind] = attempts.get(kind, 0) + 1
    if attempts[kind] >= ATTEMPT_LIMITS[kind]:
        state["blocked"] = {
            "phase": "investigation",
            "exhausted_attempt": kind,
            "gap": gap,
            "attempts": dict(attempts),
        }
    _publish(case, data_descriptor, state)


def request_follow_up(
    case: Path, data_descriptor: int, instructions: str
) -> None:
    if not instructions.strip():
        raise OrchestrationError("instructions must describe the requested follow-up")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] not in {
        "investigation_gate_approval",
        "investigation_gate_finalization",
        "report",
    }:
        raise OrchestrationError("follow-up can only be requested from the Investigation gate")
    state["follow_up"] = {
        "instructions": instructions,
        "dependency_digest": require_investigation_gate_digest(case),
    }
    state["approvals"].pop("investigation_gate", None)
    state.pop("investigation_gate_finalization", None)
    state["decisions"].clear()
    _publish(case, data_descriptor, state)


def seal_investigation_gate(case: Path, data_descriptor: int) -> None:
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    if status.get("next_phase") != "investigation_gate_finalization" or status.get(
        "investigation_gate", {}
    ).get("resume_at") != "seal":
        raise OrchestrationError(
            "Investigation gate finalization outputs are not ready to seal"
        )
    state["investigation_gate_finalization"] = {
        "dependency_digest": require_investigation_gate_digest(case),
        "output_sha256": current_hashes(case, INVESTIGATION_GATE_FINALIZATION_OUTPUTS),
    }
    state["decisions"].clear()
    _publish(case, data_descriptor, state)


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


def decide_report(
    case: Path,
    data_descriptor: int,
    decision: str,
    nonce: str | None = None,
    digest: str | None = None,
) -> None:
    if decision not in {"completed", "declined"}:
        raise OrchestrationError("report decision must be completed or declined")
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor)["next_phase"] != "report":
        raise OrchestrationError("case is not awaiting a report decision")
    ledger, binding = consume_nonce(case, data_descriptor, "report", nonce, digest)
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
        report_digest = require_investigation_gate_digest(case)
        state["decisions"]["report"] = {
            "decision": decision,
            "dependency_digest": report_digest,
            "output_sha256": current_hashes(case, outputs),
            "review": binding,
        }
        record_decision(ledger, binding, decision, report_digest)
        state["decisions"].pop("ingest", None)
        _publish(case, data_descriptor, state, ledger)


def decide_ingest(
    case: Path,
    data_descriptor: int,
    decision: str,
    nonce: str | None = None,
    digest: str | None = None,
) -> None:
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

    dependency_digest = require_investigation_gate_digest(case)
    marker = load_ingestion_marker(case, data_descriptor)
    if decision == "completed":
        # Sealing the projection receipt is model-side; it carries forward the
        # member's earlier requested decision instead of taking a new nonce.
        if nonce is not None or digest is not None:
            raise OrchestrationError("sealing a completed ingestion takes no review nonce")
        prior = state["decisions"].get("ingest")
        binding = prior.get("review") if isinstance(prior, dict) else None
        if not isinstance(binding, dict):
            # The resolver reached the seal step through the marker only when
            # its binding is a ledger entry bound to this request and digest.
            binding = marker.get("review")
        if not isinstance(binding, dict):
            raise OrchestrationError("the requested ingestion decision has no review receipt")
        ledger = None
    else:
        # A decline needs no finished batch; a solo request on an activated
        # case does, on every channel (the page also blocks without one).
        if decision == "requested" and review.activated_case(case) and review.ingest_mode() == "solo":
            if not case_path(case, review.INGEST_BATCH).is_file():
                raise OrchestrationError("build and review the knowledge batch before requesting ingestion")
        ledger, binding = consume_nonce(case, data_descriptor, "ingest", nonce, digest,
                                        ready_check=decision == "requested")
        record_decision(ledger, binding, decision, dependency_digest)
    marker["status"] = decision
    marker["dependency_digest"] = dependency_digest
    marker["review"] = binding
    marker_bytes = (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8")
    state["decisions"]["ingest"] = {
        "decision": decision,
        "dependency_digest": dependency_digest,
        "output_sha256": {
            "data/ingestion.json": hashlib.sha256(marker_bytes).hexdigest()
        },
        "review": binding,
    }
    _publish(case, data_descriptor, state, ledger, {"data/ingestion.json": marker_bytes})
