#!/usr/bin/env python3
"""The Research phase in the resolver: Methodology approval -> Research ->
Research approval -> Investigation, for cases whose approved methodology
carries a research_plan. Drives the command-line seam against offline cases
built from the orchestration conformance fixtures.
"""
from __future__ import annotations

import hashlib
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
LEADS_SAMPLE = ROOT / "tests" / "fixtures" / "research" / "research-leads.sample.json"
CLUES_SAMPLE = ROOT / "tests" / "fixtures" / "research" / "research-clues.sample.json"
PLAN_SAMPLE = ROOT / "tests" / "fixtures" / "research" / "methodology-research.sample.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFORMANCE = _load("spotlight_conformance_fixtures", ROOT / "tests" / "orchestration-conformance-check.py")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_research_gate(case: Path, marks: dict[str, str] | None = None) -> None:
    """What opens the Research page after the merge: the triage (written
    after the merge it reads) and the investigator's suggested marks for the
    current leads, clues and triage. ``marks`` overrides the suggestions."""
    leads = json.loads((case / "data/leads.json").read_text(encoding="utf-8"))
    write_json(case / "data/research-triage.json", {"generated_at": leads["generated_at"], "leads": []})
    clues = json.loads((case / "data/clues.json").read_text(encoding="utf-8"))
    methodology = json.loads((case / "data/methodology.json").read_text(encoding="utf-8"))
    write_json(case / "data/research-suggestions.json", {
        "schema_version": "1.0",
        "generated_at": "2026-10-23T09:40:00Z",
        "inputs": {relative: sha(case / relative) for relative in
                   ("data/leads.json", "data/clues.json", "data/research-triage.json")},
        "clues": [{"clue_id": clue["clue_id"], "mark": (marks or {}).get(clue["clue_id"], "pursue"),
                   "reason": "Worth a filing check."} for clue in clues["clues"]],
        "directions": [{"direction_id": d["direction_id"], "mark": "keep", "reason": "Still open."}
                       for d in methodology["investigation_plan"] if d.get("direction_id")],
    })


def amend(case: Path, change) -> None:
    """AMENDMENT mode's output: the current methodology with ``change`` applied."""
    methodology = json.loads((case / "data/methodology.json").read_text(encoding="utf-8"))
    change(methodology)
    write_json(case / "data/methodology-amendment.json", methodology)


def cover_directions(case: Path) -> None:
    """D10: the investigation names a finding or an explicit gap for every direction."""
    path = case / "data/findings.json"
    findings = json.loads(path.read_text(encoding="utf-8"))
    methodology = json.loads((case / "data/methodology.json").read_text(encoding="utf-8"))
    findings["research_gaps"] = [
        {"ref": f"direction:{d['direction_id']}", "reason": "The fixture has no source for it."}
        for d in methodology["investigation_plan"] if d.get("direction_id")
    ]
    write_json(path, findings)


def research_methodology() -> dict:
    plan = json.loads(PLAN_SAMPLE.read_text(encoding="utf-8"))["research_plan"]
    return {**CONFORMANCE.methodology(), "research_plan": plan}


class ResearchPhase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-research-")
        self.case = self.new_case("research-case", research_methodology())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def new_case(self, name: str, methodology: dict) -> Path:
        case = Path(self.temp.name) / name
        (case / "data").mkdir(parents=True)
        (case / "brief-directions.txt").write_text("Verify the offline fixture.\n", encoding="utf-8")
        write_json(case / "data/methodology.json", methodology)
        return case

    def seam(self, *args: str, expect: int = 0, case: Path | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SEAM), *args, str(case or self.case)],
            capture_output=True, text=True, check=False,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def status(self, case: Path | None = None) -> dict:
        return json.loads(self.seam("status", "--json", case=case).stdout)

    def write_research_outputs(self, case: Path | None = None) -> None:
        case = case or self.case
        write_json(case / "data/leads.json", json.loads(LEADS_SAMPLE.read_text(encoding="utf-8")))
        write_json(case / "data/clues.json", json.loads(CLUES_SAMPLE.read_text(encoding="utf-8")))
        prepare_research_gate(case)

    def through_research(self, case: Path | None = None) -> None:
        self.seam("approve", "methodology", *APPROVER, case=case)
        self.write_research_outputs(case)
        self.seam("approve", "research", *APPROVER, case=case)
        # The approval's marks change nothing here: the amendment is the
        # approved methodology itself, which D8 accepts.
        amend(case or self.case, lambda methodology: None)
        self.seam("record-amendment", case=case)

    def test_research_sits_between_methodology_approval_and_investigation(self) -> None:
        self.assertEqual(self.status()["phase"], "methodology_approval")
        self.seam("approve", "methodology", *APPROVER)
        research = self.status()
        self.assertEqual(
            (research["phase"], research["owner"], research["status"]),
            ("research", "phase-research", "active"),
        )
        self.assertEqual(research["missing"], ["data/leads.json", "data/clues.json"])
        # A Research approval before the merged outputs exist is refused.
        self.seam("approve", "research", *APPROVER, expect=3)

        # After the merge, the triage and then the suggested marks come
        # before the page opens.
        write_json(self.case / "data/leads.json", json.loads(LEADS_SAMPLE.read_text(encoding="utf-8")))
        write_json(self.case / "data/clues.json", json.loads(CLUES_SAMPLE.read_text(encoding="utf-8")))
        steps = []
        for _ in range(2):
            current = self.status()
            steps.append((current["phase"], current["research"]["resume_at"], current["missing"]))
            if current["research"]["resume_at"] == "triage":
                write_json(self.case / "data/research-triage.json", {"generated_at": "2026-10-23T09:30:00Z"})
        self.assertEqual(steps, [
            ("research", "triage", ["data/research-triage.json"]),
            ("research", "suggestions", ["data/research-suggestions.json"]),
        ])
        self.seam("approve", "research", *APPROVER, expect=3)
        prepare_research_gate(self.case)
        gate = self.status()
        self.assertEqual((gate["phase"], gate["owner"]), ("research_approval", "phase-research"))
        self.assertEqual(gate["missing"], ["Research gate approval"])
        self.seam("approve", "research", *APPROVER)
        amending = self.status()
        self.assertEqual((amending["phase"], amending["owner"], amending["research"]["resume_at"]),
                         ("research", "phase-research", "amendment"))
        amend(self.case, lambda methodology: None)
        self.seam("record-amendment")
        self.assertEqual(self.status()["phase"], "investigation")

        CONFORMANCE.investigation_outputs(self.case)
        self.assertEqual(self.status()["phase"], "investigation_gate_approval")

    def test_changed_leads_reopen_the_research_gate(self) -> None:
        self.through_research()
        CONFORMANCE.investigation_outputs(self.case)
        self.assertEqual(self.status()["phase"], "investigation_gate_approval")
        leads = json.loads((self.case / "data/leads.json").read_text(encoding="utf-8"))
        leads["leads"][0]["lead"]["summary"] = "A different summary."
        write_json(self.case / "data/leads.json", leads)
        # The suggested marks were written for the old leads: they are
        # written again before the page reopens.
        stale = self.status()
        self.assertEqual((stale["phase"], stale["research"]["resume_at"]), ("research", "suggestions"))
        prepare_research_gate(self.case)
        self.assertEqual(self.status()["phase"], "research_approval")

    def test_receipt_binds_the_clues_and_the_saved_review(self) -> None:
        self.through_research()
        state = json.loads((self.case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(state["approvals"]["research"]["input_sha256"]), {"data/leads.json", "data/clues.json"}
        )
        # The review result saved after approval is not what was approved.
        write_json(self.case / "data/research-review.json", {
            "schema_version": "spotlight-review/v1", "gate": "research", "reviewer": "", "items": {},
        })
        self.assertEqual(self.status()["phase"], "research_approval")
        (self.case / "data/research-review.json").unlink()
        self.assertEqual(self.status()["phase"], "investigation")
        clues = json.loads((self.case / "data/clues.json").read_text(encoding="utf-8"))
        clues["clues"] = []
        write_json(self.case / "data/clues.json", clues)
        prepare_research_gate(self.case)
        self.assertEqual(self.status()["phase"], "research_approval")

    def test_a_hand_written_research_approval_does_not_advance(self) -> None:
        self.through_research()
        state = (self.case / "data/orchestration.json").read_bytes()
        forged = self.new_case("forged", research_methodology())
        self.seam("approve", "methodology", *APPROVER, case=forged)
        self.write_research_outputs(forged)
        approved = json.loads(state)["approvals"]["research"]
        current = json.loads((forged / "data/orchestration.json").read_text(encoding="utf-8"))
        current["approvals"]["research"] = approved
        write_json(forged / "data/orchestration.json", current)
        self.assertEqual(self.status(forged)["phase"], "research_approval")

    def test_page_nonce_records_the_research_gate(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        self.write_research_outputs()
        # Every clue needs a mark before the page can approve.
        self.seam("review-edit", "research", "--edit", json.dumps({"kind": "mark", "key": "clue:clue-1", "value": "pursue"}))
        view = json.loads(self.seam("review-view", "research").stdout)
        self.assertEqual((view["pending_gate"], view["decisions"]), ("research", ["approved"]))
        nonce = json.loads(self.seam("issue-review-nonce", "research").stdout)["nonce"]
        self.seam("approve", "research", *APPROVER, "--nonce", nonce, "--digest", view["digest"])
        self.assertEqual(self.status()["research"]["resume_at"], "amendment")
        amend(self.case, lambda methodology: None)
        self.seam("record-amendment")
        self.assertEqual(self.status()["phase"], "investigation")
        receipt = json.loads(self.seam("review-view", "research").stdout)["receipt"]
        self.assertEqual((receipt["decision"], receipt["channel"]), ("approved", "page"))

    def test_a_changed_research_plan_reopens_methodology_and_clears_research(self) -> None:
        self.through_research()
        changed = research_methodology()
        changed["research_plan"]["round_budget_usd"] = 2
        write_json(self.case / "data/methodology.json", changed)
        self.assertEqual(self.status()["phase"], "methodology_approval")
        self.seam("approve", "methodology", *APPROVER)
        # The earlier Research receipt covered another plan; it no longer counts.
        self.assertEqual(self.status()["phase"], "research_approval")
        state = json.loads((self.case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertNotIn("research", state["approvals"])

    def test_a_stopped_lane_is_recorded_only_during_research_and_blocks_at_its_cap(self) -> None:
        self.seam("approve", "methodology", *APPROVER)
        self.seam("record-attempt", "investigation-cycle", "--gap", "too early", expect=3)
        self.seam("record-attempt", "research-lane", "--gap", "OpenRouter key rejected; update it")
        waiting = self.status()
        self.assertEqual((waiting["phase"], waiting["attempts"]), ("research", {"research-lane": 1}))
        self.assertEqual(waiting["resume"], {"kind": "research-lane", "gap": "OpenRouter key rejected; update it"})
        for _ in range(2):
            self.seam("record-attempt", "research-lane", "--gap", "still rejected")
        blocked = self.status()
        self.assertEqual((blocked["status"], blocked["owner"]), ("blocked", "phase-research"))
        self.assertEqual(blocked["blocked"]["phase"], "research")

        other = self.new_case("researched", research_methodology())
        self.through_research(other)
        self.seam("record-attempt", "research-lane", "--gap", "late", expect=3, case=other)

    def test_an_activated_case_keeps_its_contract_through_research(self) -> None:
        case = CONFORMANCE.PROVENANCE_FIXTURE.copy_fixture_case(Path(self.temp.name) / "activated")
        (case / "brief-directions.txt").write_text("Verify the activated fixture.\n", encoding="utf-8")
        write_json(case / "data/methodology.json", research_methodology())
        CONFORMANCE.PROVENANCE_FIXTURE.activate_case(case)
        contract = (case / "data/case-contract.json").read_bytes()
        self.through_research(case)
        self.assertEqual((case / "data/case-contract.json").read_bytes(), contract)
        self.assertNotEqual(self.status(case)["phase"], "research_approval")

    def test_a_case_approved_without_a_research_plan_skips_research(self) -> None:
        legacy = self.new_case("legacy", CONFORMANCE.methodology())
        self.seam("approve", "methodology", *APPROVER, case=legacy)
        self.assertEqual(self.status(legacy)["phase"], "investigation")
        self.seam("record-attempt", "research-lane", "--gap", "no research", expect=3, case=legacy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
