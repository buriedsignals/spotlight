"""Read-only Research-phase rules the resolver and transitions share.

- Which methodology counts as approved: the Methodology approval, the
  methodology the latest Research approval covered, the amendment that
  approval's marks produced (checked by D8), or a stall round's plan built
  on one of those.
- The steps inside Research before and after its gate: lanes, triage,
  suggested marks, approval, amendment.
- The stall: an investigation whose cycle cap is spent goes back to a targeted
  research round instead of stopping, once.
- The checks the resolver runs before it offers the Investigation gate: the
  fact-check validator and, for a researched case, D10.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .contract import (
    ATTEMPT_LIMITS,
    RESEARCH_OUTPUTS,
    RESEARCH_REVIEW,
    RESEARCH_SUGGESTIONS,
    RESEARCH_TRIAGE,
    SCRIPT_DIR,
    OrchestrationError,
)
from .dependencies import approval_matches, current_hashes, sha256
from .storage import case_path

CYCLE = "investigation-cycle"
ROUND_CYCLE = "research-round-cycle"
AMENDMENT_CORRECTION = "amendment-correction"
Backed = Callable[[str, object], bool]


@lru_cache(maxsize=1)
def checks() -> ModuleType:
    """scripts/research_checks.py (D1-D10), loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "spotlight_research_checks", SCRIPT_DIR / "research_checks.py"
    )
    if spec is None or spec.loader is None:
        raise OrchestrationError("cannot load scripts/research_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_case_json(case: Path, relative: str) -> Any:
    """A case JSON file, or None when it does not exist."""
    path = case_path(case, relative)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read {relative}: {exc}") from exc


# ---------------------------------------------------------------- approval


def ledger_entry(ledger: dict[str, Any], receipt: object) -> dict[str, Any] | None:
    binding = receipt.get("review") if isinstance(receipt, dict) else None
    hashed = binding.get("nonce_sha256") if isinstance(binding, dict) else None
    entry = ledger.get("nonces", {}).get(hashed) if isinstance(hashed, str) else None
    return entry if isinstance(entry, dict) else None


def amendment_bound(receipt: object, ledger: dict[str, Any]) -> dict[str, str] | None:
    """The amended methodology a Research approval's marks produced, when the
    receipt and its ledger entry both name it (the entry is written in the
    same publication, so a receipt edited by hand does not count)."""
    if not isinstance(receipt, dict):
        return None
    amended = receipt.get("amended_methodology_sha256")
    entry = ledger_entry(ledger, receipt)
    if not isinstance(amended, dict) or entry is None or entry.get("amended_methodology_sha256") != amended:
        return None
    return amended


def _covered_by_research(receipt: object, hashes: dict[str, str], ledger: dict[str, Any]) -> bool:
    if not isinstance(receipt, dict):
        return False
    entry = ledger_entry(ledger, receipt)
    if entry is None:
        return False
    base = receipt.get("methodology_sha256")
    if base == hashes and entry.get("methodology_sha256") == base:
        return True
    return amendment_bound(receipt, ledger) == hashes


def methodology_accepted(
    state: dict[str, Any], hashes: dict[str, str], ledger: dict[str, Any], backed: Backed
) -> bool:
    """The current methodology is the approved one, an approved amendment of
    it, or a stall round's plan built on one of those. Accepting an amendment
    never clears attempts: only a new Methodology approval does."""

    def settled(candidate: dict[str, str]) -> bool:
        methodology = state["approvals"].get("methodology")
        if approval_matches(methodology, candidate) and backed("methodology", methodology):
            return True
        research = state["approvals"].get("research")
        return backed("research", research) and _covered_by_research(research, candidate, ledger)

    if settled(hashes):
        return True
    stall = state.get("stall")
    return (
        isinstance(stall, dict)
        and stall.get("methodology_sha256") == hashes
        and isinstance(stall.get("base_methodology_sha256"), dict)
        and settled(stall["base_methodology_sha256"])
    )


# ---------------------------------------------------------------- stall


def cycle_cap_spent(state: dict[str, Any]) -> bool:
    return state["attempts"].get(CYCLE, 0) >= ATTEMPT_LIMITS[CYCLE]


def stall_due(state: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """A researched case whose cycle cap is spent and that has not had its
    stall round: it goes to Research for a targeted round. A stored case
    with 4 or 5 cycles (the old cap), blocked or not, reads the same way."""
    if plan is None or "stall" in state or not cycle_cap_spent(state):
        return None
    blocked = state.get("blocked")
    if isinstance(blocked, dict) and blocked.get("exhausted_attempt") != CYCLE:
        return None
    gap = None
    for record in (state.get("open_cycle"), blocked):
        if isinstance(record, dict) and isinstance(record.get("gap"), str):
            gap = record["gap"]
            break
    return {"state": "stall", "resume_at": "round_topics", "gap": gap,
            "cycles": state["attempts"].get(CYCLE, 0)}


# ---------------------------------------------------------------- Research steps


def _plan_topics(plan: dict[str, Any]) -> set[str]:
    return {
        str(topic.get("topic_id"))
        for topic in plan.get("topics") or []
        if isinstance(topic, dict)
    }


def merged_topics(leads: object) -> set[str]:
    lanes = leads.get("lanes") if isinstance(leads, dict) else None
    if not isinstance(lanes, list):
        return set()
    return {str(lane.get("topic_id")) for lane in lanes if isinstance(lane, dict)}


def triage_current(case: Path, leads: object) -> bool:
    """The triage was written by the current question bank, after the merge it
    reads (the merge stamps the newest lane time; the triage its own run time)."""
    triage = read_case_json(case, RESEARCH_TRIAGE)
    if not isinstance(triage, dict) or not isinstance(triage.get("generated_at"), str):
        return False
    if triage.get("bank_version") != checks().rr.RESEARCH_BANK_VERSION:
        return False
    merged_at = leads.get("generated_at") if isinstance(leads, dict) else None
    return not isinstance(merged_at, str) or triage["generated_at"] >= merged_at


def suggestions_problems(case: Path) -> list[str]:
    """Why the suggested marks cannot open the page yet ([] when they can):
    missing, not the schema's shape, written for other leads, clues or
    triage, or missing a clue or an approved direction."""
    suggestions = read_case_json(case, RESEARCH_SUGGESTIONS)
    if suggestions is None:
        return [f"{RESEARCH_SUGGESTIONS} is missing"]
    checker = checks().schema_check.load(SCRIPT_DIR.parent / "schemas" / "research-suggestions.schema.json")
    errors = checker.errors(suggestions)
    if errors:
        return [f"{RESEARCH_SUGGESTIONS}: {error}" for error in errors[:10]]
    expected = {relative: sha256(case_path(case, relative)) for relative in (*RESEARCH_OUTPUTS, RESEARCH_TRIAGE)}
    if suggestions["inputs"] != expected:
        return [f"{RESEARCH_SUGGESTIONS} was written for other leads, clues or triage"]
    problems = []
    clues = read_case_json(case, RESEARCH_OUTPUTS[1])
    suggested = {entry["clue_id"] for entry in suggestions["clues"]}
    for clue in clues.get("clues", []) if isinstance(clues, dict) else []:
        if isinstance(clue, dict) and clue.get("clue_id") not in suggested:
            problems.append(f"no suggested mark for clue {clue.get('clue_id')}")
    methodology = read_case_json(case, "data/methodology.json")
    marked = {entry["direction_id"] for entry in suggestions["directions"] if entry["mark"] != "add"}
    for direction in checks().direction_ids(methodology if isinstance(methodology, dict) else {}):
        if direction not in marked:
            problems.append(f"no suggested keep or drop for direction {direction}")
    return problems


def research_done(case: Path, state: dict[str, Any], ledger: dict[str, Any], backed: Backed) -> bool:
    """Research's approval covers the current leads and clues and its
    amendment is bound. Reads only file hashes, never an output's content."""
    if any(not case_path(case, relative).is_file() for relative in RESEARCH_OUTPUTS):
        return False
    approval = state["approvals"].get("research")
    return (approval_matches(approval, current_hashes(case, RESEARCH_OUTPUTS)) and backed("research", approval)
            and amendment_bound(approval, ledger) is not None)


def research_steps(
    case: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    ledger: dict[str, Any],
    backed: Backed,
) -> dict[str, Any] | None:
    """Where Research stands, or None once its approval and the amendment it
    produced cover the current leads and clues."""
    missing = [relative for relative in RESEARCH_OUTPUTS if not case_path(case, relative).is_file()]
    leads = read_case_json(case, RESEARCH_OUTPUTS[0]) if not missing else None
    unmerged = sorted(_plan_topics(plan) - merged_topics(leads)) if not missing else []
    if missing or unmerged:
        value: dict[str, Any] = {
            "status": "active",
            "next_phase": "research",
            "missing": missing or ["data/leads.json with every approved topic merged"],
            "research": {"state": "running", "resume_at": "lanes"},
        }
        if unmerged:
            value["research"]["unmerged_topics"] = unmerged
        open_research = state.get("open_research")
        if isinstance(open_research, dict):
            value["open_research"] = {"kind": open_research.get("kind"), "gap": open_research.get("gap")}
        return value
    approval = state["approvals"].get("research")
    if approval_matches(approval, current_hashes(case, RESEARCH_OUTPUTS)) and backed("research", approval):
        if amendment_bound(approval, ledger) is not None:
            return None
        value = {
            "status": "active",
            "next_phase": "research",
            "missing": ["data/methodology-amendment.json"],
            "research": {"state": "approved", "resume_at": "amendment"},
        }
        problems = state.get("open_amendment")
        if isinstance(problems, dict):
            value["research"]["refused"] = problems.get("problems", [])
        return value
    if not triage_current(case, leads):
        return {
            "status": "active",
            "next_phase": "research",
            "missing": [RESEARCH_TRIAGE],
            "research": {"state": "merged", "resume_at": "triage"},
        }
    problems = suggestions_problems(case)
    if problems:
        return {
            "status": "active",
            "next_phase": "research",
            "missing": [RESEARCH_SUGGESTIONS],
            "research": {"state": "triaged", "resume_at": "suggestions", "problems": problems},
        }
    return {"next_phase": "research_approval"}


# ---------------------------------------------------------------- readiness checks


def fact_check_problems(case: Path) -> list[str]:
    """validate-fact-check.py on the case: its failure lines, or [] when it
    passes. The Investigation gate is never offered while it fails."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "validate-fact-check.py"), str(case)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [f"validate-fact-check.py could not run: {exc}"]
    if result.returncode == 0:
        return []
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("FAIL")]
    if not lines:
        detail = result.stderr.strip() or result.stdout.strip() or "validate-fact-check.py failed"
        lines = detail.splitlines()[-5:]
    return [f"validate-fact-check: {line}" for line in lines[:20]]


def d10_problems(case: Path) -> list[str]:
    """D10: every direction and pursued clue has a finding or an explicit gap."""
    methodology = read_case_json(case, "data/methodology.json")
    if not isinstance(methodology, dict):
        return []
    findings = read_case_json(case, "data/findings.json")
    review = read_case_json(case, RESEARCH_REVIEW)
    problems = checks().d10_directions_covered(
        methodology, findings if isinstance(findings, dict) else None,
        review if isinstance(review, dict) else None,
    )
    return [f"D10 {problem}" for problem in problems]
