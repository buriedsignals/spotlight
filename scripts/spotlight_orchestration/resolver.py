"""Side-effect-free derivation of the next Spotlight phase owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import METHODOLOGY_INPUTS, STATUS_VERSION, OrchestrationError
from .dependencies import (
    approval_matches,
    current_hashes,
    digest_receipt_matches,
    investigation_gate_approval_matches,
    investigation_gate_dependencies,
    outputs_match,
    provenance_matches,
    review_is_fresh,
)
from .review import decision_backed, effective_ledger
from .storage import LEGACY_LEDGER_KEY
from .storage import case_path, read_data_bytes

_PHASE_OWNERS = {
    "brief": "phase-methodology",
    "methodology": "phase-methodology",
    "methodology_approval": "phase-methodology",
    "investigation": "phase-investigation",
    "blocked": "phase-investigation",
    "investigation_gate_approval": "phase-investigation-gate",
    "investigation_gate_finalization": "phase-investigation-gate",
    "report": "phase-report",
    "ingest": "phase-ingest",
    "complete": None,
}


def load_ingestion_marker(
    case: Path, data_descriptor: int | None = None
) -> dict[str, Any]:
    marker: dict[str, Any] = {"schema_version": "1.0"}
    content = read_data_bytes(case, "ingestion.json", data_descriptor)
    if content is None:
        return marker
    try:
        existing = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/ingestion.json: {exc}") from exc
    if not isinstance(existing, dict):
        raise OrchestrationError("data/ingestion.json must be an object")
    marker.update(existing)
    return marker


def investigation_status(
    status: dict[str, Any],
    missing: list[str],
    follow_up: object = None,
) -> dict[str, Any]:
    value = {
        **status,
        "status": "active",
        "next_phase": "investigation",
        "missing": missing,
    }
    if isinstance(follow_up, dict):
        value["follow_up"] = {"instructions": follow_up["instructions"]}
    return value


def status_for(
    case: Path, state: dict[str, Any], data_descriptor: int | None = None
) -> dict[str, Any]:
    status = {"schema_version": STATUS_VERSION, "status": "pending", "next_phase": "brief"}
    if not case_path(case, "brief-directions.txt").is_file():
        return status
    if not case_path(case, "data/methodology.json").is_file():
        return {**status, "next_phase": "methodology"}
    methodology_hashes = current_hashes(case, METHODOLOGY_INPUTS)
    # A decision counts only when the nonce ledger backs it, so a decision
    # written into orchestration.json by hand does not advance the case.
    ledger = effective_ledger(case, state, data_descriptor)
    from_v1 = LEGACY_LEDGER_KEY in state

    def backed(gate: str, receipt: object) -> bool:
        return decision_backed(case, gate, receipt, ledger)

    methodology_approval = state["approvals"].get("methodology")
    if not approval_matches(methodology_approval, methodology_hashes) or not backed(
        "methodology", methodology_approval
    ):
        return {**status, "next_phase": "methodology_approval"}
    if "blocked" in state:
        return {**status, "status": "blocked", "next_phase": "blocked", "blocked": state["blocked"]}

    dependencies = investigation_gate_dependencies(case)
    dependency_digest = str(dependencies["dependency_digest"])
    follow_up = state.get("follow_up")
    if not dependencies["investigation_ready"]:
        return investigation_status(status, list(dependencies["investigation_missing"]))
    if (
        isinstance(follow_up, dict)
        and digest_receipt_matches(follow_up, dependency_digest)
        and isinstance(follow_up.get("instructions"), str)
    ):
        return investigation_status(
            status, list(dependencies["investigation_missing"]), follow_up
        )
    if dependencies["investigation_gate_missing"]:
        return {
            **status,
            "next_phase": "investigation_gate_approval",
            "missing": list(dependencies["investigation_gate_missing"]),
        }
    gate_approval = state["approvals"].get("investigation_gate")
    if not investigation_gate_approval_matches(
        gate_approval, dependency_digest
    ) or not backed("investigation_gate", gate_approval):
        return {**status, "next_phase": "investigation_gate_approval"}
    finalization = state.get("investigation_gate_finalization")
    if not digest_receipt_matches(finalization, dependency_digest) or not outputs_match(
        case, finalization
    ):
        if not provenance_matches(case, dependency_digest):
            resume_at = "provenance"
        elif not review_is_fresh(case, gate_approval):
            resume_at = "review"
        else:
            resume_at = "seal"
        return {
            **status,
            "status": "active",
            "next_phase": "investigation_gate_finalization",
            "investigation_gate": {"state": "approved", "resume_at": resume_at},
        }

    report = state["decisions"].get("report")
    if (
        not isinstance(report, dict)
        or report.get("decision") not in {"completed", "declined"}
        or not digest_receipt_matches(report, dependency_digest)
        or not outputs_match(case, report)
        or not backed("report", report)
    ):
        return {**status, "next_phase": "report"}

    marker = load_ingestion_marker(case, data_descriptor)
    marker_status = marker.get("status")
    ingest = state["decisions"].get("ingest")
    if not digest_receipt_matches(ingest, dependency_digest) or not backed("ingest", ingest):
        if (
            digest_receipt_matches(marker, dependency_digest)
            # A v1 case's marker predates the ledger like its state does.
            and (backed("ingest", marker) or (from_v1 and "review" not in marker))
            and marker_status
            in {
                "requested",
                "completed",
            }
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
    if decision in {"completed", "declined"} and outputs_match(case, ingest):
        return {
            "schema_version": STATUS_VERSION,
            "status": "completed",
            "next_phase": "complete",
            "ingest": {"state": decision, "resume_at": "complete"},
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


def _missing_for(status: dict[str, Any]) -> list[str]:
    phase = status["next_phase"]
    if phase == "brief":
        return ["brief-directions.txt"]
    if phase == "methodology":
        return ["data/methodology.json"]
    if phase == "methodology_approval":
        return ["methodology approval"]
    if phase == "investigation":
        return list(status.get("missing", []))
    if phase == "investigation_gate_approval":
        return list(status.get("missing", ["Investigation gate approval"]))
    if phase == "investigation_gate_finalization":
        return [status["investigation_gate"]["resume_at"]]
    if phase == "report":
        return ["report decision"]
    if phase == "ingest" and status.get("ingest", {}).get("resume_at") == "decision":
        return ["ingest decision"]
    return []


def normalized_resolution(case: Path, state: dict[str, Any]) -> dict[str, Any]:
    status = status_for(case, state)
    phase = status["next_phase"]
    resume = (
        status.get("investigation_gate")
        or status.get("ingest")
        or status.get("follow_up")
        or {}
    )
    return {
        **status,
        "phase": phase,
        "owner": _PHASE_OWNERS[phase],
        "missing": _missing_for(status),
        "attempts": dict(state["attempts"]),
        "resume": resume,
    }
