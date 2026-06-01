#!/usr/bin/env python3
"""Shared deterministic grading helpers for SkillOpt-lite."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROTECTED_CATEGORIES = {
    "false_high_confidence",
    "unsupported_public_claim",
    "missing_source_url",
    "fact_checker_independence_break",
    "sensitive_mode_leakage",
    "destructive_shell_instruction",
    "secret_harvesting",
}


def load_json(path: str) -> Any:
    with open(Path(path), encoding="utf-8") as handle:
        return json.load(handle)


def emit(score: int, checks: dict[str, bool], protected_regressions: list[str] | None = None) -> int:
    protected_regressions = protected_regressions or []
    payload = {
        "score": score,
        "max_score": len(checks),
        "checks": checks,
        "protected_regressions": protected_regressions,
        "passed": score == len(checks) and not protected_regressions,
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


def require_args(skill: str) -> tuple[Any, Any]:
    if len(sys.argv) != 3:
        print(f"usage: {Path(sys.argv[0]).name} expected.json actual.json", file=sys.stderr)
        raise SystemExit(2)
    try:
        return load_json(sys.argv[1]), load_json(sys.argv[2])
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{skill}: failed to load fixture/output: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def bool_score(checks: dict[str, bool]) -> int:
    return sum(1 for ok in checks.values() if ok)


def has_nonempty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def list_has_nonempty(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(has_nonempty(item) for item in value)
