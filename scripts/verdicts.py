"""Verdict and confidence vocabulary, by contract version (stdlib only).

Fact-check contract 1.1 uses Batch 1.1's five assessment verdicts and three
confidence levels. Contract 1.0 is the legacy vocabulary: old cases keep
their recorded verdicts and stay readable, and nothing rewrites them. An
activated case (findings contract 1.1) uses only the 1.1 vocabulary; one
activated before contract 1.1 existed keeps its 1.0 file.
"""

from __future__ import annotations

import json
from pathlib import Path
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
# 22 October 2026. The fact-check schema holds the one list; codes never
# change meaning, and a new meaning needs a new code.
_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "fact-check.schema.json"
FLAG_CODES = frozenset(json.loads(_SCHEMA.read_text(encoding="utf-8"))["definitions"]["flag_code"]["enum"])


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
