"""Side-effect-free derivation of the next Spotlight phase owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import METHODOLOGY_INPUTS, RESEARCH_OUTPUTS, STATUS_VERSION, OrchestrationError
from .dependencies import (
    approval_matches,
    current_hashes,
    digest_receipt_matches,
    investigation_gate_approval_matches,
    investigation_gate_dependencies,
    outputs_match,
    provenance_matches,
    review_is_fresh,
    rework_pending,
)
from .review import decision_backed, effective_ledger
from .storage import case_path, read_data_bytes

_PHASE_OWNERS = {
    "brief": "phase-methodology",
    "methodology": "phase-methodology",
    "methodology_approval": "phase-methodology",
    "research": "phase-research",
    "research_approval": "phase-research",
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


def research_plan(case: Path) -> dict[str, Any] | None:
    """The approved methodology's research plan, or None for a case planned
    without one (every case approved before the Research phase existed)."""
    path = case_path(case, "data/methodology.json")
    try:
        methodology = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/methodology.json: {exc}") from exc
    plan = methodology.get("research_plan") if isinstance(methodology, dict) else None
    if plan is None:
        return None
    if not isinstance(plan, dict):
        raise OrchestrationError("data/methodology.json research_plan must be an object")
    return plan


def research_status(
    case: Path, state: dict[str, Any], backed: Any
) -> dict[str, Any] | None:
    """Research and its gate, or None once the Research approval covers the
    current leads, clues and saved review."""
    missing = [relative for relative in RESEARCH_OUTPUTS if not case_path(case, relative).is_file()]
    if missing:
        value: dict[str, Any] = {"status": "active", "next_phase": "research", "missing": missing}
        open_research = state.get("open_research")
        if isinstance(open_research, dict):
            value["open_research"] = {"kind": open_research.get("kind"), "gap": open_research.get("gap")}
        return value
    approval = state["approvals"].get("research")
    if approval_matches(approval, current_hashes(case, RESEARCH_OUTPUTS)) and backed(
        "research", approval
    ):
        return None
    return {"next_phase": "research_approval"}


def investigation_status(
    status: dict[str, Any],
    missing: list[str],
    follow_up: object = None,
    open_cycle: object = None,
) -> dict[str, Any]:
    value = {
        **status,
        "status": "active",
        "next_phase": "investigation",
        "missing": missing,
    }
    if isinstance(follow_up, dict):
        value["follow_up"] = {"instructions": follow_up["instructions"]}
    if isinstance(open_cycle, dict):
        value["open_cycle"] = {"kind": open_cycle.get("kind"), "gap": open_cycle.get("gap")}
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

    def backed(gate: str, receipt: object, *, require_bound: bool = False) -> bool:
        return decision_backed(case, gate, receipt, ledger, require_bound=require_bound)

    methodology_approval = state["approvals"].get("methodology")
    if not approval_matches(methodology_approval, methodology_hashes) or not backed(
        "methodology", methodology_approval
    ):
        return {**status, "next_phase": "methodology_approval"}
    if "blocked" in state:
        return {**status, "status": "blocked", "next_phase": "blocked", "blocked": state["blocked"]}
    if research_plan(case) is not None:
        research = research_status(case, state, backed)
        if research is not None:
            return {**status, **research}

    dependencies = investigation_gate_dependencies(case)
    dependency_digest = str(dependencies["dependency_digest"])
    follow_up = state.get("follow_up")
    if not dependencies["investigation_ready"]:
        return investigation_status(status, list(dependencies["investigation_missing"]))
    if isinstance(follow_up, dict) and isinstance(follow_up.get("instructions"), str):
        pending = rework_pending(case, follow_up, dependency_digest)
        if pending:
            return investigation_status(status, pending, follow_up)
    # A recorded unsuccessful attempt (readiness not met, or a failed
    # correction) keeps the investigation open until the next cycle has
    # refreshed the investigator's outputs and the fact-check.
    open_cycle = state.get("open_cycle")
    pending = rework_pending(case, open_cycle, dependency_digest)
    if pending:
        return investigation_status(status, pending, open_cycle=open_cycle)
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
            # The marker is written outside the resolver too (the ingest step
            # records its receipt there), so it counts only with a ledger entry
            # bound to this decision and digest. A v1 state that recorded the
            # decision is carried by its own legacy-bound receipt above.
            and backed("ingest", marker, require_bound=True)
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
    if phase == "research":
        return list(status.get("missing", []))
    if phase == "research_approval":
        return ["Research gate approval"]
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
        or status.get("open_cycle")
        or status.get("open_research")
        or {}
    )
    owner = _PHASE_OWNERS[phase]
    if phase == "blocked" and status["blocked"].get("phase") == "research":
        owner = "phase-research"
    return {
        **status,
        "phase": phase,
        "owner": owner,
        "missing": _missing_for(status),
        "attempts": dict(state["attempts"]),
        "resume": resume,
    }
