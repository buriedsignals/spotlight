"""Verdict and confidence vocabulary, by contract version (stdlib only).

Fact-check contract 1.1 uses Batch 1.1's five assessment verdicts and three
confidence levels. Contract 1.0 is the legacy vocabulary: old cases keep
their recorded verdicts and stay readable, and nothing rewrites them. An
activated case (findings contract 1.1) must carry a 1.1 fact-check.
"""

from __future__ import annotations

from typing import Any

VERDICTS_1_1 = frozenset({"verified", "unverified", "disputed", "false", "not_checkable"})
LEGACY_VERDICTS = frozenset(
    {"verified", "partially_verified", "unverified", "disputed", "false", "mischaracterized"}
)
CONFIDENCE_1_1 = frozenset({"high", "medium", "low"})
# Legacy findings could record a finding's confidence as "disputed".
LEGACY_FINDING_CONFIDENCE = frozenset({"high", "medium", "low", "disputed"})

POSITIVE_1_1 = frozenset({"verified"})
LEGACY_POSITIVE = frozenset({"verified", "partially_verified"})

# Batch 1.1 §8.4 flag registry: the initial codes plus the eight added on
# 22 October 2026. Codes never change meaning; a new meaning needs a new code.
FLAG_CODES = frozenset(
    {
        "partial_support",
        "contradiction",
        "allegation_as_fact",
        "amount_mismatch",
        "date_mismatch",
        "added_scope",
        "added_causation",
        "proposition_drift",
        "ambiguous_entity",
        "unresolved_semantic_ambiguity",
        "announced_not_current",
        "incomplete_list",
        "address_as_legal_domicile",
        "direction_mismatch",
        "control_overstated",
        "beneficial_owner_not_stated",
        "stage_overstated",
        "dispute_unrecorded",
    }
)


def fact_check_version(document: Any) -> str:
    """The fact-check contract version; an absent field is the legacy 1.0."""
    if isinstance(document, dict) and document.get("schema_version") == "1.1":
        return "1.1"
    return "1.0"


def allowed_verdicts(version: str) -> frozenset[str]:
    return VERDICTS_1_1 if version == "1.1" else LEGACY_VERDICTS


def positive_verdicts(version: str) -> frozenset[str]:
    return POSITIVE_1_1 if version == "1.1" else LEGACY_POSITIVE


def finding_confidences(findings_version: str) -> frozenset[str]:
    return CONFIDENCE_1_1 if findings_version == "1.1" else LEGACY_FINDING_CONFIDENCE
