"""Shared constants and errors for Spotlight orchestration."""

from __future__ import annotations

from pathlib import Path

STATE_VERSION = "spotlight-orchestration/v2"
# v1 used numbered-phase names (gate1, execution); load_state maps it in memory
# and the next transition writes v2.
LEGACY_STATE_VERSION = "spotlight-orchestration/v1"
STATUS_VERSION = "spotlight-orchestration-status/v2"
DEPENDENCY_STATUS_VERSION = "spotlight-investigation-gate-dependencies/v2"
METHODOLOGY_INPUTS = ("brief-directions.txt", "data/methodology.json")
INVESTIGATION_GATE_FINALIZATION_OUTPUTS = ("data/provenance-manifest.json", "review.html")
ATTEMPT_LIMITS = {
    "investigation-cycle": 5,
    "fact-check-evidence-repair": 1,
    "structural-correction": 2,
}
SCRIPT_DIR = Path(__file__).resolve().parent.parent


class OrchestrationError(ValueError):
    """A case or transition does not satisfy the orchestration contract."""
