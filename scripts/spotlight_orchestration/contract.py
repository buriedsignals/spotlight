"""Shared constants and errors for Spotlight orchestration."""

from __future__ import annotations

from pathlib import Path

STATE_VERSION = "spotlight-orchestration/v1"
STATUS_VERSION = "spotlight-orchestration-status/v1"
DEPENDENCY_STATUS_VERSION = "spotlight-gate1-dependencies/v1"
METHODOLOGY_INPUTS = ("brief-directions.txt", "data/methodology.json")
GATE1_FINALIZATION_OUTPUTS = ("data/provenance-manifest.json", "review.html")
ATTEMPT_LIMITS = {
    "execution-cycle": 5,
    "fact-check-evidence-repair": 1,
    "structural-correction": 2,
}
SCRIPT_DIR = Path(__file__).resolve().parent.parent


class OrchestrationError(ValueError):
    """A case or transition does not satisfy the orchestration contract."""
