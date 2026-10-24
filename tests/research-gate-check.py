#!/usr/bin/env python3
"""The Research gate's amendment and the investigation cycles around it.

- AMENDMENT mode's proposal becomes the methodology only when D8 accepts it
  against the member's marks; the resolver then accepts it without a new
  Methodology approval and keeps the attempts.
- Three unsuccessful cycles send a researched case to a targeted research
  round; its approval grants one cycle, and a second stall stops the case
  with a gaps report. A stored case with 4 or 5 cycles enters the same flow.
- The Investigation gate is not offered while the fact-check validator fails
  or D10 finds an uncovered direction, and a reopened investigation waits for
  regenerated summaries.

Drives the command-line seam against offline cases.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
APPROVER = ("--approved-by", "journalist:fixture", "--approved-at", "2026-10-23T12:00:00Z")
RESEARCH_PAUSED = ("Research is off. Turn Research on in the app to continue this case, or go back to the "
                   "Methodology gate and approve the plan without research.")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PHASE = _load("spotlight_research_phase_fixtures", ROOT / "tests" / "research-phase-check.py")
CONFORMANCE = PHASE.CONFORMANCE
write_json = PHASE.write_json


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def researched_methodology() -> dict:
    """The research sample: one direction (ownership) with a research plan."""
    methodology = read_json(PHASE.PLAN_SAMPLE)
    methodology["navigator"] = CONFORMANCE.methodology()["navigator"]
    return methodology


def marks_review(marks: dict[str, str]) -> dict:
    """data/research-review.json as the Research page saves the member's marks."""
    return {
        "schema_version": "spotlight-review/v1", "gate": "research", "reviewer": "journalist:fixture",
        "items": {target: {"target": target, "final": mark} for target, mark in marks.items()},
    }


ROUND = {
    "schema_version": "1.0",
    "gap": "The shareholder list is still unconfirmed; court filings may name them.",
    "topics": [{
        "topic_id": "ownership-news-courts",
        "direction_id": "ownership",
        "lane_type": "news-courts",
        "route": "swarm",
        "questions": ["Which court filings name Northwind Holdings' shareholders?"],
        "seed_queries": ["Northwind Holdings shareholders court"],
        "token_budget": 100000,
    }],
    "egress": [
        {"topic_id": "ownership-news-courts", "destination": "openrouter"},
        {"topic_id": "ownership-news-courts", "destination": "public_web"},
    ],
}


class SeamCase(unittest.TestCase):
    case: Path

    # The Pi harness passes Engine's Research switch to the seam; a researched case has it on.
    research = "on"

    def seam(self, *args: str, expect: int = 0, case: Path | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SEAM), *args, str(case or self.case)],
            capture_output=True, text=True, check=False,
            env={"PATH": "", "PYTHONNOUSERSITE": "1", "SPOTLIGHT_RESEARCH_SWARMS": self.research},
        )
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def status(self, case: Path | None = None) -> dict:
        return json.loads(self.seam("status", "--json", case=case).stdout)

    def state(self, case: Path | None = None) -> dict:
        return read_json((case or self.case) / "data/orchestration.json")


class ResearchGate(SeamCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-research-gate-")
        self.addCleanup(self.temp.cleanup)
        self.case = self.new_case("case")

    def new_case(self, name: str, methodology: dict | None = None) -> Path:
        case = Path(self.temp.name) / name
        (case / "data").mkdir(parents=True)
        (case / "brief-directions.txt").write_text("Who owns Northwind Holdings?\n", encoding="utf-8")
        write_json(case / "data/methodology.json", methodology or researched_methodology())
        return case

    # -- steps a run takes -------------------------------------------------

    def to_research_gate(self, marks: dict[str, str] | None = None, case: Path | None = None) -> None:
        case = case or self.case
        self.seam("approve", "methodology", *APPROVER, case=case)
        self.research_outputs(case)
        if marks is not None:
            write_json(case / "data/research-review.json", marks_review(marks))

    def research_outputs(self, case: Path, extra_topics: tuple[str, ...] = ()) -> None:
        leads = read_json(PHASE.LEADS_SAMPLE)
        for topic in extra_topics:
            leads["lanes"].append({"topic_id": topic, "status": "completed", "lane_sha256": None})
            leads["generated_at"] = "2026-10-24T09:00:00Z"
        write_json(case / "data/leads.json", leads)
        write_json(case / "data/clues.json", read_json(PHASE.CLUES_SAMPLE))
        PHASE.prepare_research_gate(case)

    def unchanged_amendment(self, case: Path | None = None) -> None:
        PHASE.amend(case or self.case, lambda methodology: None)
        self.seam("record-amendment", case=case)

    def complete_cycle(self, cycle: int, case: Path | None = None) -> None:
        """One investigation cycle's outputs: the fact-check validator passes
        and every direction has a finding or an explicit gap (D10)."""
        case = case or self.case
        if not (case / "data/findings.json").is_file():
            CONFORMANCE.investigation_outputs(case)
        findings = read_json(case / "data/findings.json")
        findings["findings"][0]["evidence"] = f"Cycle {cycle} evidence."
        write_json(case / "data/findings.json", findings)
        PHASE.cover_directions(case)
        fact_check = read_json(case / "data/fact-check.json")
        fact_check["cycle"] = cycle
        write_json(case / "data/fact-check.json", fact_check)

    def fail_cycle(self, cycle: int, case: Path | None = None) -> None:
        self.complete_cycle(cycle, case)
        self.assertEqual(self.status(case)["phase"], "investigation_gate_approval")
        self.seam("record-attempt", "investigation-cycle", "--gap", f"gap {cycle}", case=case)

    # -- the amendment -----------------------------------------------------

    def test_d8_refuses_an_unmarked_change_then_stops_at_research(self) -> None:
        self.to_research_gate({"clue:clue-1": "dismiss"})
        self.seam("approve", "research", *APPROVER)
        approved = (self.case / "data/methodology.json").read_bytes()

        def unmarked(methodology: dict) -> None:
            methodology["investigation_plan"][0]["steps"].append(
                {"order": 2, "action": "Read the court docket", "tool": "fetch", "cites": ["clue:clue-1"]})

        PHASE.amend(self.case, unmarked)
        self.seam("record-amendment")
        refused = self.status()
        self.assertEqual((refused["phase"], refused["research"]["resume_at"]), ("research", "amendment"))
        self.assertEqual(refused["research"]["refused"],
                         ["direction ownership: new step 2 cites no pursued clue or direction"])
        self.assertEqual((self.case / "data/methodology.json").read_bytes(), approved)

        def dropped(methodology: dict) -> None:
            methodology["investigation_plan"] = []

        PHASE.amend(self.case, dropped)
        self.seam("record-amendment")
        stopped = self.status()
        self.assertEqual((stopped["status"], stopped["owner"]), ("blocked", "phase-research"))
        self.assertEqual(stopped["blocked"]["exhausted_attempt"], "amendment-correction")
        self.assertEqual((self.case / "data/methodology.json").read_bytes(), approved)

    def test_an_approved_amendment_goes_to_investigation_with_attempts_kept(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        self.seam("record-attempt", "research-lane", "--gap", "OpenRouter key rejected; update it")
        self.research_outputs(self.case)
        write_json(self.case / "data/research-review.json", marks_review({"clue:clue-1": "pursue"}))
        self.seam("approve", "research", *APPROVER)
        methodology_receipt = self.state()["approvals"]["methodology"]

        def pursue(methodology: dict) -> None:
            methodology["investigation_plan"][0]["steps"].append(
                {"order": 2, "action": "Read the court docket", "tool": "fetch", "cites": ["clue:clue-1"]})

        PHASE.amend(self.case, pursue)
        self.seam("record-amendment")
        resolved = self.status()
        self.assertEqual(resolved["phase"], "investigation")
        self.assertEqual(resolved["attempts"], {"research-lane": 1})
        self.assertEqual(read_json(self.case / "data/methodology.json")["investigation_plan"][0]["steps"][-1]["cites"],
                         ["clue:clue-1"])
        state = self.state()
        self.assertEqual(state["approvals"]["methodology"], methodology_receipt)
        self.assertEqual(state["approvals"]["research"]["amended_methodology_sha256"]["data/methodology.json"],
                         PHASE.sha(self.case / "data/methodology.json"))

        # Any other change to the methodology still reopens the Methodology gate.
        changed = read_json(self.case / "data/methodology.json")
        changed["limitations"] = ["edited after the amendment"]
        write_json(self.case / "data/methodology.json", changed)
        self.assertEqual(self.status()["phase"], "methodology_approval")

    def test_a_hand_written_amendment_binding_does_not_count(self) -> None:
        self.to_research_gate({})
        self.seam("approve", "research", *APPROVER)
        PHASE.amend(self.case, lambda m: m["investigation_plan"][0]["questions"].append("Forged question?"))
        amended = self.case / "data/methodology-amendment.json"
        (self.case / "data/methodology.json").write_bytes(amended.read_bytes())
        state = self.state()
        state["approvals"]["research"]["amended_methodology_sha256"] = {
            "brief-directions.txt": PHASE.sha(self.case / "brief-directions.txt"),
            "data/methodology.json": PHASE.sha(amended),
        }
        write_json(self.case / "data/orchestration.json", state)
        self.assertEqual(self.status()["phase"], "methodology_approval")

    def test_suggested_marks_must_cover_every_clue_and_direction(self) -> None:
        self.to_research_gate()
        suggestions = read_json(self.case / "data/research-suggestions.json")
        suggestions["clues"] = []
        write_json(self.case / "data/research-suggestions.json", suggestions)
        waiting = self.status()
        self.assertEqual((waiting["phase"], waiting["research"]["resume_at"]), ("research", "suggestions"))
        self.assertEqual(waiting["research"]["problems"], ["no suggested mark for clue clue-1"])
        self.seam("approve", "research", *APPROVER, expect=3)

    # -- the stall ---------------------------------------------------------

    def through_amendment(self, case: Path | None = None) -> None:
        self.to_research_gate({}, case=case)
        self.seam("approve", "research", *APPROVER, case=case)
        self.unchanged_amendment(case)

    def test_a_stall_goes_to_a_research_round_once_then_stops_with_a_gaps_report(self) -> None:
        self.through_amendment()
        for cycle in (1, 2, 3):
            self.fail_cycle(cycle)
        phases = [self.status()["phase"]]
        stalled = self.status()
        self.assertEqual((stalled["phase"], stalled["owner"], stalled["status"]),
                         ("research", "phase-research", "active"))
        self.assertEqual(stalled["research"]["resume_at"], "round_topics")
        self.assertEqual(stalled["missing"], ["data/research-round.json"])
        self.assertEqual(stalled["attempts"], {"investigation-cycle": 3})
        self.seam("record-attempt", "investigation-cycle", "--gap", "no stop", expect=3)

        write_json(self.case / "data/research-round.json", ROUND)
        self.seam("start-research-round")
        self.assertIn("ownership-news-courts",
                      [t["topic_id"] for t in read_json(self.case / "data/methodology.json")["research_plan"]["topics"]])
        lanes = self.status()
        self.assertEqual((lanes["phase"], lanes["research"]["resume_at"], lanes["research"]["unmerged_topics"]),
                         ("research", "lanes", ["ownership-news-courts"]))
        self.research_outputs(self.case, extra_topics=("ownership-news-courts",))
        phases.append(self.status()["phase"])
        self.seam("approve", "research", *APPROVER)
        self.unchanged_amendment()
        # The granted cycle needs new investigator output and a fresh
        # fact-check; the cycle count is kept.
        granted = self.status()
        phases.append(granted["phase"])
        self.assertEqual(phases, ["research", "research_approval", "investigation"])
        self.assertEqual(granted["attempts"], {"investigation-cycle": 3})

        self.fail_cycle(4)
        stopped = self.status()
        self.assertEqual((stopped["status"], stopped["owner"]), ("blocked", "phase-investigation"))
        self.assertEqual(stopped["blocked"]["exhausted_attempt"], "research-round-cycle")
        self.assertEqual(stopped["blocked"]["gaps_report"], ["gap 1", "gap 2", "gap 3", "gap 4"])

    def test_a_round_that_fails_d1_is_refused(self) -> None:
        self.through_amendment()
        for cycle in (1, 2, 3):
            self.fail_cycle(cycle)
        bad = json.loads(json.dumps(ROUND))
        bad["topics"][0]["direction_id"] = "unplanned"
        write_json(self.case / "data/research-round.json", bad)
        refused = self.seam("start-research-round", expect=3)
        self.assertIn("not a direction_id of investigation_plan", refused.stderr)
        self.assertEqual(self.status()["research"]["resume_at"], "round_topics")

    def test_a_stall_round_is_refused_when_research_is_off(self) -> None:
        self.through_amendment()
        for cycle in (1, 2, 3):
            self.fail_cycle(cycle)
        write_json(self.case / "data/research-round.json", ROUND)
        self.research = "off"
        refused = self.seam("start-research-round", expect=3)
        self.assertIn(RESEARCH_PAUSED, refused.stderr)
        self.assertEqual(self.status()["research"]["paused_at"], "round_topics")

    def test_research_off_holds_a_researched_case_at_its_step_until_the_plan_drops_research(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        self.research = "off"
        lanes = self.status()
        self.assertEqual((lanes["phase"], lanes["research"]["resume_at"], lanes["research"]["paused_at"]),
                         ("research", "research_off", "lanes"))
        self.assertEqual(lanes["missing"], [RESEARCH_PAUSED])
        self.research = "on"
        self.research_outputs(self.case)
        write_json(self.case / "data/research-review.json", marks_review({}))
        self.assertEqual(self.status()["phase"], "research_approval")
        self.research = "off"
        held = self.status()
        self.assertEqual((held["phase"], held["research"]["paused_at"]), ("research", "approval"))
        self.assertIn(RESEARCH_PAUSED, self.seam("approve", "research", *APPROVER, expect=3).stderr)
        self.research = "maybe"
        self.assertIn('SPOTLIGHT_RESEARCH_SWARMS must be "on" or "off"', self.seam("status", "--json", expect=3).stderr)
        # The way back: continue without research, then the member approves that plan at the Methodology gate.
        self.research = "off"
        self.seam("continue-without-research")
        self.assertNotIn("research_plan", read_json(self.case / "data/methodology.json"))
        self.assertEqual(self.status()["phase"], "methodology_approval")
        self.seam("approve", "methodology", *APPROVER)
        self.assertEqual(self.status()["phase"], "investigation")

    def test_continue_without_research_is_only_for_a_case_research_off_paused(self) -> None:
        self.to_research_gate({})
        refused = self.seam("continue-without-research", expect=3)
        self.assertIn("only for a case paused because Research is off", refused.stderr)
        self.assertIn("research_plan", read_json(self.case / "data/methodology.json"))

    def test_a_stored_case_with_four_or_five_cycles_enters_the_stall_flow(self) -> None:
        for cycles, blocked in ((5, True), (4, False)):
            with self.subTest(cycles=cycles):
                case = self.new_case(f"stored-{cycles}")
                self.through_amendment(case)
                self.complete_cycle(cycles, case)
                state = self.state(case)
                state["attempts"] = {"investigation-cycle": cycles}
                if blocked:
                    # What the old cap of 5 wrote at its fifth cycle.
                    state["blocked"] = {"phase": "investigation", "exhausted_attempt": "investigation-cycle",
                                        "gap": "gap 5", "attempts": {"investigation-cycle": 5}}
                write_json(case / "data/orchestration.json", state)
                stalled = self.status(case)
                self.assertEqual((stalled["phase"], stalled["research"]["resume_at"]), ("research", "round_topics"))
                write_json(case / "data/research-round.json", ROUND)
                self.seam("start-research-round", case=case)
                self.assertNotIn("blocked", self.state(case))
                self.assertEqual(self.status(case)["research"]["resume_at"], "lanes")

    def test_an_activated_case_keeps_its_contract_through_the_amendment_and_the_stall(self) -> None:
        case = CONFORMANCE.PROVENANCE_FIXTURE.copy_fixture_case(Path(self.temp.name) / "activated")
        (case / "brief-directions.txt").write_text("Verify the activated fixture.\n", encoding="utf-8")
        write_json(case / "data/methodology.json", researched_methodology())
        CONFORMANCE.PROVENANCE_FIXTURE.activate_case(case)
        contract = (case / "data/case-contract.json").read_bytes()
        expressions = (case / "data/source-expressions.json").read_bytes()
        self.through_amendment(case)
        state = self.state(case)
        state["attempts"] = {"investigation-cycle": 3}
        write_json(case / "data/orchestration.json", state)
        write_json(case / "data/research-round.json", ROUND)
        self.seam("start-research-round", case=case)
        self.research_outputs(case, extra_topics=("ownership-news-courts",))
        self.seam("approve", "research", *APPROVER, case=case)
        self.unchanged_amendment(case)
        self.assertEqual((case / "data/case-contract.json").read_bytes(), contract)
        self.assertEqual((case / "data/source-expressions.json").read_bytes(), expressions)
        self.assertEqual(self.status(case)["phase"], "investigation")

    # -- the Investigation gate's readiness ------------------------------

    def test_the_gate_waits_for_d10(self) -> None:
        self.through_amendment()
        CONFORMANCE.investigation_outputs(self.case)
        uncovered = self.status()
        self.assertEqual(uncovered["phase"], "investigation")
        self.assertEqual(uncovered["checks"], ["D10 direction:ownership has no finding and no explicit gap"])
        PHASE.cover_directions(self.case)
        self.assertEqual(self.status()["phase"], "investigation_gate_approval")


class InvestigationGateReadiness(SeamCase):
    """Found in an end-to-end run: the gate opened while validate-fact-check failed, and a
    follow-up left the earlier summaries in place."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-gate-readiness-")
        self.case = Path(self.temp.name) / "case"
        (self.case / "data").mkdir(parents=True)
        (self.case / "brief-directions.txt").write_text("Verify the offline fixture.\n", encoding="utf-8")
        write_json(self.case / "data/methodology.json", CONFORMANCE.methodology())
        self.addCleanup(self.temp.cleanup)

    def test_the_gate_is_not_offered_while_the_fact_check_validator_fails(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        CONFORMANCE.investigation_outputs(self.case)
        fact_check = read_json(self.case / "data/fact-check.json")
        fact_check["claims"][0]["claim_text"] = "A claim the finding does not make."
        fact_check["claims"][0]["verdict"] = "verified"
        write_json(self.case / "data/fact-check.json", fact_check)
        failing = self.status()
        self.assertEqual(failing["phase"], "investigation")
        self.assertTrue(failing["checks"] and all(c.startswith("validate-fact-check:") for c in failing["checks"]))
        self.seam("approve", "investigation_gate", *APPROVER, expect=3)

        CONFORMANCE.investigation_outputs(self.case)
        self.assertEqual(self.status()["phase"], "investigation_gate_approval")

    def test_a_follow_up_waits_for_regenerated_summaries(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        CONFORMANCE.investigation_outputs(self.case)
        self.seam("request-follow-up", "--instructions", "Re-check the unresolved claim.")
        findings = read_json(self.case / "data/findings.json")
        findings["findings"][0]["evidence"] = "Follow-up evidence."
        write_json(self.case / "data/findings.json", findings)
        fact_check = read_json(self.case / "data/fact-check.json")
        fact_check["cycle"] = 2
        write_json(self.case / "data/fact-check.json", fact_check)
        stale = self.status()
        self.assertEqual(stale["phase"], "investigation_gate_approval")
        self.assertEqual(stale["missing"], [
            "summary.md regenerated after the reopened investigation",
            "data/summary.json regenerated after the reopened investigation",
        ])
        self.seam("approve", "investigation_gate", *APPROVER, expect=3)

        (self.case / "summary.md").write_text("# Offline demo, after the follow-up\n", encoding="utf-8")
        summary = read_json(self.case / "data/summary.json")
        summary["cycles"] = 2
        write_json(self.case / "data/summary.json", summary)
        self.assertEqual(self.status()["missing"], ["Investigation gate approval"])
        self.seam("approve", "investigation_gate", *APPROVER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
