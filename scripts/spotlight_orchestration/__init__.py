"""Portable resolver and durable transitions for one Spotlight case."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .contract import OrchestrationError
from .resolver import normalized_resolution
from .storage import load_state, resolve_case, transaction
from . import review, transitions

__all__ = [
    "OrchestrationError",
    "approve",
    "decide_ingest",
    "decide_report",
    "issue_review_nonce",
    "record_attempt",
    "request_follow_up",
    "resolve",
    "review_edit",
    "review_view",
    "seal_investigation_gate",
]


def resolve(
    case_dir: str | Path, *, authorized_cases_root: str | Path | None = None
) -> dict[str, Any]:
    """Resolve current durable state without writing case files or locks."""
    case = resolve_case(case_dir, authorized_cases_root)
    return normalized_resolution(case, load_state(case))


def _transition(
    case_dir: str | Path,
    operation: Callable[..., None],
    *args: Any,
    authorized_cases_root: str | Path | None = None,
) -> Any:
    case = resolve_case(case_dir, authorized_cases_root)
    with transaction(case) as data_descriptor:
        return operation(case, data_descriptor, *args)


def approve(
    case_dir: str | Path,
    gate: str,
    approved_by: str,
    approved_at: str,
    *,
    nonce: str | None = None,
    digest: str | None = None,
    authorized_cases_root: str | Path | None = None,
) -> None:
    """Record a human approval. ``nonce``/``digest`` come from the review page;
    without them the approval is recorded on the command-line channel."""
    _transition(
        case_dir,
        transitions.approve,
        gate,
        approved_by,
        approved_at,
        nonce,
        digest,
        authorized_cases_root=authorized_cases_root,
    )


def record_attempt(
    case_dir: str | Path,
    kind: str,
    gap: str,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.record_attempt,
        kind,
        gap,
        authorized_cases_root=authorized_cases_root,
    )


def request_follow_up(
    case_dir: str | Path,
    instructions: str,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.request_follow_up,
        instructions,
        authorized_cases_root=authorized_cases_root,
    )


def seal_investigation_gate(
    case_dir: str | Path, *, authorized_cases_root: str | Path | None = None
) -> None:
    _transition(
        case_dir,
        transitions.seal_investigation_gate,
        authorized_cases_root=authorized_cases_root,
    )


def decide_report(
    case_dir: str | Path,
    decision: str,
    *,
    nonce: str | None = None,
    digest: str | None = None,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.decide_report,
        decision,
        nonce,
        digest,
        authorized_cases_root=authorized_cases_root,
    )


def decide_ingest(
    case_dir: str | Path,
    decision: str,
    *,
    nonce: str | None = None,
    digest: str | None = None,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.decide_ingest,
        decision,
        nonce,
        digest,
        authorized_cases_root=authorized_cases_root,
    )


def issue_review_nonce(
    case_dir: str | Path, gate: str, *, authorized_cases_root: str | Path | None = None
) -> str:
    """Record a one-time page nonce for ``gate``; only its hash is stored."""
    return _transition(
        case_dir, review.issue_nonce, gate, authorized_cases_root=authorized_cases_root
    )


def review_edit(
    case_dir: str | Path,
    gate: str,
    edit: object,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    """Apply one page edit and save ``data/<gate>-review.json`` under the case lock."""
    _transition(
        case_dir, review.apply_edit, gate, edit, authorized_cases_root=authorized_cases_root
    )


def review_view(
    case_dir: str | Path, gate: str, *, authorized_cases_root: str | Path | None = None
) -> dict[str, Any]:
    """Read-only page model for ``gate`` with the resolver's pending gate and receipt."""
    review.require_gate(gate)
    case = resolve_case(case_dir, authorized_cases_root)
    state = load_state(case)
    status = normalized_resolution(case, state)
    pending = review.pending_gate(status)
    view = review.build_view(case, gate)
    stored = (
        state["approvals"].get(gate)
        if gate in {"methodology", "investigation_gate"}
        else state["decisions"].get(gate)
    )
    receipt = None
    if pending != gate and review.decision_backed(
        case, gate, stored, review.effective_ledger(case, state), bind_review=False
    ):
        receipt = {
            "decision": stored.get("decision", "approved"),
            "approved_by": stored.get("approved_by"),
            "approved_at": stored.get("approved_at"),
            **{k: stored["review"][k] for k in ("channel", "review_digest", "nonce_sha256")},
        }
    return {**view, "phase": status["phase"], "pending_gate": pending, "receipt": receipt}
