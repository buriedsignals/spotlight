"""Shared, stdlib-only source-expression identity and lifecycle primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


PASSAGE_CORE_FIELDS = (
    "text",
    "anchor_ref",
    "anchor_sha256",
    "original_evidence_bundle_id",
    "original_artifact_sha256",
    "language",
    "attribution",
    "direct_quote",
    "derived_from_expression_id",
    "derivative_type",
)
POSITIVE_VERDICTS = frozenset({"verified", "partially_verified"})
LIFECYCLE_STATES = frozenset({"activated", "superseded", "withdrawn"})


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def passage_core(expression: dict[str, Any]) -> dict[str, Any]:
    return {
        key: expression[key]
        for key in PASSAGE_CORE_FIELDS
        if key in expression
    }


def lifecycle_state(expression: dict[str, Any]) -> Optional[str]:
    events = expression.get("lifecycle_events")
    if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
        return None
    state = events[-1].get("event")
    return str(state) if state in LIFECYCLE_STATES else None


def fact_check_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the single supported fact-check collection, including legacy aliases."""
    for key in ("claims", "fact_checks", "verdicts"):
        rows = document.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []
