"""Durable Spotlight state transitions; callers serialize them with a transaction."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .contract import (
    ATTEMPT_LIMITS,
    INVESTIGATION_GATE_FINALIZATION_OUTPUTS,
    METHODOLOGY_AMENDMENT,
    METHODOLOGY_INPUTS,
    OrchestrationError,
    RECORDABLE_ATTEMPTS,
    RESEARCH_OUTPUTS,
    RESEARCH_REVIEW,
    RESEARCH_ROUND,
    SCRIPT_DIR,
)
from .dependencies import (
    approval_matches,
    current_hashes,
    investigation_gate_approval_matches,
    require_investigation_gate_digest,
    rework_snapshot,
    sha256,
    validate_approval,
)
from .resolver import RESEARCH_PAUSED_STEP, load_ingestion_marker, research_plan, status_for
from . import research as research_rules
from .research import AMENDMENT_CORRECTION, CYCLE, ROUND_CYCLE
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
    expected_phase = _APPROVAL_PHASES.get(gate)
    if expected_phase is None:
        raise OrchestrationError("gate must be methodology, research or investigation_gate")
    validate_approval(approved_by, approved_at)
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    _refuse_while_research_off(status)
    if status["next_phase"] != expected_phase:
        raise OrchestrationError(f"case is not awaiting {gate} approval")
    if gate == "investigation_gate" and status.get("missing"):
        raise OrchestrationError(
            f"the Investigation gate still needs: {', '.join(status['missing'])}"
        )
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
            state["approvals"].pop("research", None)
            state["approvals"].pop("investigation_gate", None)
            state["decisions"].clear()
            state["attempts"].clear()
            state.pop("blocked", None)
            state.pop("follow_up", None)
            state.pop("open_cycle", None)
            state.pop("open_research", None)
            state.pop("open_amendment", None)
            state.pop("stall", None)
            state.pop("cycle_gaps", None)
            state.pop("investigation_gate_finalization", None)
    elif gate == "research":
        # The receipt binds the merged leads and clues; its review binding
        # carries the saved review's hash, so all three are approved together.
        # It also names the methodology the marks apply to (with a stall
        # round's topics, approved here); the amendment those marks produce
        # is bound to it once D8 passes (record_amendment).
        hashes = current_hashes(case, RESEARCH_OUTPUTS)
        methodology = current_hashes(case, METHODOLOGY_INPUTS)
        state["approvals"][gate] = {
            "approved_by": approved_by,
            "approved_at": approved_at,
            "input_sha256": hashes,
            "methodology_sha256": methodology,
            "review": binding,
        }
        record_decision(ledger, binding, "approved")
        ledger["nonces"][binding["nonce_sha256"]]["methodology_sha256"] = methodology
        state.pop("open_research", None)
        state.pop("open_amendment", None)
        state["attempts"].pop(AMENDMENT_CORRECTION, None)
        stall = state.get("stall")
        if isinstance(stall, dict) and not stall.get("approved"):
            # Approving the stall round grants one more investigation cycle.
            stall["approved"] = True
        if not approval_matches(previous, hashes):
            # Downstream receipts covered other research. Attempts stay: a
            # new research round does not reset the case's cycle count.
            state["approvals"].pop("investigation_gate", None)
            state["decisions"].clear()
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
            state.pop("open_cycle", None)
            _publish(case, data_descriptor, state, ledger)
        return
    _publish(case, data_descriptor, state, ledger)


_APPROVAL_PHASES = {
    "methodology": "methodology_approval",
    "research": "research_approval",
    "investigation_gate": "investigation_gate_approval",
}
# Where each attempt kind may be recorded: a stopped research round during
# Research; the investigation kinds until a human approves the gate (cycle
# outputs exist before readiness is judged, so the gate may already be offered).
_INVESTIGATION_ATTEMPT_PHASES = frozenset({"investigation", "investigation_gate_approval"})
_RESEARCH_ATTEMPT_PHASES = frozenset({"research"})


def _decider(
    nonce: str | None, decided_by: str | None, decided_at: str | None
) -> dict[str, str]:
    """Who made a report, ingest or follow-up decision. A page decision must
    name the member; the command-line channel may, and keeps working without."""
    if decided_by is None and decided_at is None:
        if nonce is not None:
            raise OrchestrationError("a page decision needs decided_by and decided_at")
        return {}
    validate_approval(decided_by or "", decided_at or "")
    return {"decided_by": decided_by, "decided_at": decided_at}


def record_attempt(case: Path, data_descriptor: int, kind: str, gap: str) -> None:
    if kind not in RECORDABLE_ATTEMPTS:
        raise OrchestrationError(f"unsupported attempt kind: {kind}")
    if not gap.strip():
        raise OrchestrationError("gap must describe the unresolved gap")
    state = load_state(case, data_descriptor)
    research = kind == "research-lane"
    allowed = _RESEARCH_ATTEMPT_PHASES if research else _INVESTIGATION_ATTEMPT_PHASES
    if status_for(case, state, data_descriptor)["next_phase"] not in allowed:
        where = "research" if research else "investigation"
        raise OrchestrationError(f"{kind} attempts can only be recorded during {where}")
    attempts = state["attempts"]
    stall = state.get("stall")
    if kind == CYCLE and isinstance(stall, dict) and stall.get("approved"):
        # The cycle the approved stall round granted has its own count.
        kind = ROUND_CYCLE
    attempts[kind] = attempts.get(kind, 0) + 1
    if research:
        state["open_research"] = {"kind": kind, "gap": gap}
    else:
        state["open_cycle"] = {"kind": kind, "gap": gap, **rework_snapshot(case)}
    if kind in (CYCLE, ROUND_CYCLE):
        state["cycle_gaps"] = [*state.get("cycle_gaps", []), gap]
    exhausted = attempts[kind] >= ATTEMPT_LIMITS[kind]
    # A researched case whose cycle cap is spent goes to a targeted research
    # round (the resolver routes it there), not to a stop; once.
    stall_next = kind == CYCLE and research_plan(case) is not None and "stall" not in state
    if exhausted and not stall_next:
        state["blocked"] = {
            "phase": "research" if research else "investigation",
            "exhausted_attempt": kind,
            "gap": gap,
            "attempts": dict(attempts),
        }
        if kind in (CYCLE, ROUND_CYCLE):
            # The gaps report: every unresolved gap the cycles recorded.
            state["blocked"]["gaps_report"] = list(state["cycle_gaps"])
    _publish(case, data_descriptor, state)


def start_research_round(case: Path, data_descriptor: int) -> None:
    """Append a stalled investigation's targeted round to research_plan.

    The investigator proposes the round in data/research-round.json; code
    appends its topic briefs and their egress, checks the whole plan with
    D1, and records the stall. The round runs under the plan's consent and
    budget, and the member approves it, with its leads, on the Research page.
    """
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    _refuse_while_research_off(status)
    if status.get("research", {}).get("resume_at") != "round_topics":
        raise OrchestrationError("no stalled investigation is waiting for a research round")
    proposal = research_rules.read_case_json(case, RESEARCH_ROUND)
    if not isinstance(proposal, dict):
        raise OrchestrationError(f"{RESEARCH_ROUND} is missing or not an object")
    topics = proposal.get("topics")
    egress = proposal.get("egress", [])
    gap = proposal.get("gap")
    if not isinstance(topics, list) or not topics or not isinstance(egress, list):
        raise OrchestrationError(f"{RESEARCH_ROUND} needs a non-empty topics list (and an egress list)")
    if not isinstance(gap, str) or not gap.strip():
        raise OrchestrationError(f"{RESEARCH_ROUND} gap must name the gaps the round targets")
    methodology_path = case_path(case, "data/methodology.json")
    try:
        methodology = json.loads(methodology_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/methodology.json: {exc}") from exc
    plan = methodology["research_plan"]
    known = {topic.get("topic_id") for topic in plan["topics"] if isinstance(topic, dict)}
    reused = sorted(str(t.get("topic_id")) for t in topics if isinstance(t, dict) and t.get("topic_id") in known)
    if reused:
        raise OrchestrationError(f"a round adds new topics; already in the plan: {', '.join(reused)}")
    plan["topics"] = [*plan["topics"], *topics]
    plan["egress"] = [*plan.get("egress", []), *egress]
    problems = _schema_problems(methodology) + _d1_problems(methodology)
    if problems:
        raise OrchestrationError("the research round fails D1: " + "; ".join(problems[:10]))
    base = current_hashes(case, METHODOLOGY_INPUTS)
    updated = (json.dumps(methodology, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    hashes = {**base, "data/methodology.json": hashlib.sha256(updated).hexdigest()}
    state["stall"] = {
        "round": 1,
        "gap": gap,
        "topics": [str(t.get("topic_id")) for t in topics if isinstance(t, dict)],
        "base_methodology_sha256": base,
        "methodology_sha256": hashes,
        "approved": False,
    }
    # The granted cycle reopens the investigation: the gate waits for new
    # investigator output and a fresh fact-check after the round.
    state["open_cycle"] = {"kind": ROUND_CYCLE, "gap": gap, **rework_snapshot(case)}
    blocked = state.get("blocked")
    if isinstance(blocked, dict) and blocked.get("exhausted_attempt") == CYCLE:
        state.pop("blocked")  # a case stopped under the old cap of 5
    _publish(case, data_descriptor, state, extra={"data/methodology.json": updated})


def record_amendment(case: Path, data_descriptor: int) -> None:
    """Check AMENDMENT mode's proposal against the member's marks (D8).

    On a pass the proposal becomes data/methodology.json and its hash is
    bound to the Research approval and its ledger entry, so the resolver
    accepts it without a new Methodology approval and keeps the attempts.
    On a refusal the problems go back to AMENDMENT once; a second refusal
    stops the case at Research.
    """
    state = load_state(case, data_descriptor)
    status = status_for(case, state, data_descriptor)
    _refuse_while_research_off(status)
    if status.get("research", {}).get("resume_at") != "amendment":
        raise OrchestrationError("the case is not waiting for a methodology amendment")
    approved = research_rules.read_case_json(case, "data/methodology.json")
    amendment_path = case_path(case, METHODOLOGY_AMENDMENT)
    if not amendment_path.is_file():
        raise OrchestrationError(f"{METHODOLOGY_AMENDMENT} is missing")
    amendment_bytes = amendment_path.read_bytes()
    try:
        amended = json.loads(amendment_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read {METHODOLOGY_AMENDMENT}: {exc}") from exc
    if not isinstance(amended, dict) or not isinstance(approved, dict):
        raise OrchestrationError("the methodology and its amendment must be JSON objects")
    review_doc = research_rules.read_case_json(case, RESEARCH_REVIEW)
    problems = research_rules.checks().d8_amendment(
        approved, amended, review_doc if isinstance(review_doc, dict) else None
    )
    problems += _schema_problems(amended)
    if problems:
        attempts = state["attempts"]
        attempts[AMENDMENT_CORRECTION] = attempts.get(AMENDMENT_CORRECTION, 0) + 1
        state["open_amendment"] = {"problems": problems[:50]}
        if attempts[AMENDMENT_CORRECTION] >= ATTEMPT_LIMITS[AMENDMENT_CORRECTION]:
            state["blocked"] = {
                "phase": "research",
                "exhausted_attempt": AMENDMENT_CORRECTION,
                "gap": "D8: " + "; ".join(problems[:5]),
                "attempts": dict(attempts),
            }
        _publish(case, data_descriptor, state)
        return
    receipt = state["approvals"]["research"]
    amended_hashes = {
        **current_hashes(case, METHODOLOGY_INPUTS),
        "data/methodology.json": hashlib.sha256(amendment_bytes).hexdigest(),
    }
    ledger = load_ledger(case, data_descriptor)
    entry = research_rules.ledger_entry(ledger, receipt)
    if entry is None:
        raise OrchestrationError("the Research approval has no ledger entry")
    entry["amended_methodology_sha256"] = amended_hashes
    receipt["amended_methodology_sha256"] = amended_hashes
    state.pop("open_amendment", None)
    state["attempts"].pop(AMENDMENT_CORRECTION, None)
    _publish(case, data_descriptor, state, ledger, {"data/methodology.json": amendment_bytes})


def continue_without_research(case: Path, data_descriptor: int) -> None:
    """The member's choice while Research is off: drop the research plan.
    The methodology changes, so the Methodology gate reopens and the member
    approves the plan without research there; nothing else advances."""
    state = load_state(case, data_descriptor)
    if status_for(case, state, data_descriptor).get("research", {}).get("resume_at") != RESEARCH_PAUSED_STEP:
        raise OrchestrationError("continue without research is only for a case paused because Research is off")
    methodology = research_rules.read_case_json(case, "data/methodology.json")
    methodology.pop("research_plan", None)
    updated = (json.dumps(methodology, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _publish(case, data_descriptor, state, extra={"data/methodology.json": updated})


def _schema_problems(methodology: dict) -> list[str]:
    checker = research_rules.checks().schema_check.load(SCRIPT_DIR.parent / "schemas" / "methodology.schema.json")
    return [f"schema: {error}" for error in checker.errors(methodology)][:10]


def _d1_problems(methodology: dict) -> list[str]:
    return research_rules.checks().d1_from_config(methodology, review.install_config())


def _refuse_while_research_off(status: dict) -> None:
    """A Research transition with the switch off names the resolver's reason."""
    if status.get("research", {}).get("resume_at") == RESEARCH_PAUSED_STEP:
        raise OrchestrationError(status["missing"][0])


# A follow-up without a page nonce comes from the agent: the harness names the
# requesting role, and no member name can be recorded for it.
AGENT_CHANNEL = "agent"
DEFAULT_REQUESTING_ROLE = "orchestrator"
_ROLE = re.compile(r"[a-z][a-z0-9-]{0,63}")


def _follow_up_requester(
    nonce: str | None,
    decided_by: str | None,
    decided_at: str | None,
    requested_by_role: str | None,
) -> dict[str, str]:
    """Who asked for a follow-up: the member on the review page (name and
    time), or the agent role that requested it through its own operation."""
    if nonce is not None:
        if requested_by_role is not None:
            raise OrchestrationError("a page follow-up is the member's; it takes no agent role")
        return _decider(nonce, decided_by, decided_at)
    if decided_by is not None or decided_at is not None:
        raise OrchestrationError(
            "a follow-up names a member only through the review page; an agent follow-up records its role"
        )
    role = DEFAULT_REQUESTING_ROLE if requested_by_role is None else requested_by_role
    if not _ROLE.fullmatch(role):
        raise OrchestrationError("requested_by_role must be a Spotlight role name")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {"requested_by_role": role, "decided_at": now}


def request_follow_up(
    case: Path,
    data_descriptor: int,
    instructions: str,
    nonce: str | None = None,
    digest: str | None = None,
    decided_by: str | None = None,
    decided_at: str | None = None,
    requested_by_role: str | None = None,
) -> None:
    if not instructions.strip():
        raise OrchestrationError("instructions must describe the requested follow-up")
    requester = _follow_up_requester(nonce, decided_by, decided_at, requested_by_role)
    state = load_state(case, data_descriptor)
    phase = status_for(case, state, data_descriptor)["next_phase"]
    if phase not in {
        "investigation_gate_approval",
        "investigation_gate_finalization",
        "report",
    }:
        raise OrchestrationError("follow-up can only be requested from the Investigation gate")
    if nonce is not None and phase != "investigation_gate_approval":
        raise OrchestrationError("the Investigation gate is not waiting for the member")
    # The page asks for a follow-up while the gate waits for the member; open
    # flags and blocking failures are what a follow-up answers, so they do not
    # block it. Without a nonce the request is recorded on the agent channel.
    ledger, binding = consume_nonce(
        case, data_descriptor, "investigation_gate", nonce, digest,
        ready_check=False, unpaged_channel=AGENT_CHANNEL,
    )
    dependency_digest = require_investigation_gate_digest(case)
    record_decision(ledger, binding, "follow_up", dependency_digest)
    state["follow_up"] = {
        "instructions": instructions,
        "dependency_digest": dependency_digest,
        **rework_snapshot(case),
        **requester,
        "decision": "follow_up",
        "review": binding,
    }
    state["approvals"].pop("investigation_gate", None)
    state.pop("investigation_gate_finalization", None)
    state["decisions"].clear()
    _publish(case, data_descriptor, state, ledger)


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
    decided_by: str | None = None,
    decided_at: str | None = None,
) -> None:
    if decision not in {"completed", "declined"}:
        raise OrchestrationError("report decision must be completed or declined")
    decider = _decider(nonce, decided_by, decided_at)
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
            **decider,
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
    decided_by: str | None = None,
    decided_at: str | None = None,
) -> None:
    if decision not in {"requested", "completed", "declined"}:
        raise OrchestrationError("ingest decision must be requested, completed, or declined")
    decider = _decider(nonce, decided_by, decided_at)
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
        # The seal keeps the name of the member who requested the ingestion.
        if isinstance(prior, dict):
            decider = {k: prior[k] for k in ("decided_by", "decided_at") if isinstance(prior.get(k), str)}
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
        **decider,
    }
    _publish(case, data_descriptor, state, ledger, {"data/ingestion.json": marker_bytes})
