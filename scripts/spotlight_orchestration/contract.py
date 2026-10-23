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
# What a reopened investigation must refresh before the gate is offered again:
# the investigator's outputs, then a fact-check of them.
INVESTIGATOR_OUTPUTS = ("data/findings.json", "data/evidence-bundle.json")
FACT_CHECK_OUTPUT = "data/fact-check.json"
# What the Research gate approves: the merged leads and the clues derived from
# them. The receipt also binds the saved review (data/research-review.json)
# through its review binding.
RESEARCH_OUTPUTS = ("data/leads.json", "data/clues.json")
# What must exist before the Research page opens: the triage of the merged
# leads and the investigator's suggested marks (AMENDMENT mode).
RESEARCH_TRIAGE = "data/research-triage.json"
RESEARCH_SUGGESTIONS = "data/research-suggestions.json"
# What the Research page saves (the member's marks) and what AMENDMENT mode
# turns them into: a whole proposed methodology, applied only once D8 passes.
RESEARCH_REVIEW = "data/research-review.json"
METHODOLOGY_AMENDMENT = "data/methodology-amendment.json"
# A stalled investigation's targeted research round: the topic briefs the
# investigator proposes, appended to research_plan by code.
RESEARCH_ROUND = "data/research-round.json"
SUMMARY_OUTPUTS = ("summary.md", "data/summary.json")
ATTEMPT_LIMITS = {
    "investigation-cycle": 3,
    # The one further cycle an approved stall research round grants. When it
    # fails too, the case stops with a gaps report.
    "research-round-cycle": 1,
    # D8 refused the amendment: back to AMENDMENT once, then stop.
    "amendment-correction": 2,
    "fact-check-evidence-repair": 1,
    "structural-correction": 2,
    # A research round stopped by its provider (rejected key, no credit,
    # upstream errors). The member fixes it and retries; the cap stops the
    # case at Research instead of retrying forever.
    "research-lane": 3,
}
# The kinds a phase owner records itself. The others are recorded by the
# transitions that own them (a cycle after a stall round, a refused amendment).
RECORDABLE_ATTEMPTS = ("investigation-cycle", "fact-check-evidence-repair", "structural-correction", "research-lane")
# Counts a case stored under an older, higher cap. A stored investigation
# with 4 or 5 cycles (valid under the old cap of 5) loads and reads as an
# exhausted cap.
STORED_ATTEMPT_CEILINGS = {"investigation-cycle": 5}
SCRIPT_DIR = Path(__file__).resolve().parent.parent


class OrchestrationError(ValueError):
    """A case or transition does not satisfy the orchestration contract."""
