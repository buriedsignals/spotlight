"""Portable resolver and durable transitions for one Spotlight case."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .contract import OrchestrationError
from .resolver import normalized_resolution
from .storage import load_state, resolve_case, transaction
from . import transitions

__all__ = [
    "OrchestrationError",
    "approve",
    "decide_ingest",
    "decide_report",
    "record_attempt",
    "request_follow_up",
    "resolve",
    "seal_gate1",
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
    *args: str,
    authorized_cases_root: str | Path | None = None,
) -> None:
    case = resolve_case(case_dir, authorized_cases_root)
    with transaction(case) as data_descriptor:
        operation(case, data_descriptor, *args)


def approve(
    case_dir: str | Path,
    gate: str,
    approved_by: str,
    approved_at: str,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.approve,
        gate,
        approved_by,
        approved_at,
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


def seal_gate1(
    case_dir: str | Path, *, authorized_cases_root: str | Path | None = None
) -> None:
    _transition(
        case_dir,
        transitions.seal_gate1,
        authorized_cases_root=authorized_cases_root,
    )


def decide_report(
    case_dir: str | Path,
    decision: str,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.decide_report,
        decision,
        authorized_cases_root=authorized_cases_root,
    )


def decide_ingest(
    case_dir: str | Path,
    decision: str,
    *,
    authorized_cases_root: str | Path | None = None,
) -> None:
    _transition(
        case_dir,
        transitions.decide_ingest,
        decision,
        authorized_cases_root=authorized_cases_root,
    )
