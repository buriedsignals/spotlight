#!/usr/bin/env python3
"""Gate decisions from the review page: nonces, digests, review records.

Drives the command-line seam the local review server calls, against offline
cases built by tests/fixtures/review_case.py.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
BUILDER = ROOT / "tests" / "fixtures" / "review_case.py"
APPROVER = ("--approved-by", "journalist:fixture", "--approved-at", "2026-08-23T12:00:00Z")
DECIDER = ("--decided-by", "journalist:fixture", "--decided-at", "2026-08-23T12:00:00Z")


class CaseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-review-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, name: str, stage: str, *flags: str) -> Path:
        case = self.root / name
        result = subprocess.run(
            [sys.executable, str(BUILDER), str(case), stage, *flags],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return case

    def seam(self, case: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SEAM), *args, str(case)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def phase(self, case: Path) -> str:
        return json.loads(self.seam(case, "status", "--json").stdout)["phase"]

    def view(self, case: Path, gate: str) -> dict:
        return json.loads(self.seam(case, "review-view", gate).stdout)

    def nonce(self, case: Path, gate: str) -> str:
        return json.loads(self.seam(case, "issue-review-nonce", gate).stdout)["nonce"]

    def edit(self, case: Path, gate: str, edit: dict, expect: int = 0) -> dict:
        result = self.seam(case, "review-edit", gate, "--edit", json.dumps(edit), expect=expect)
        return json.loads(result.stdout) if expect == 0 else {}

    def page_approve(self, case: Path, gate: str, nonce: str, digest: str, expect: int = 0):
        return self.seam(
            case, "approve", gate, *APPROVER, "--nonce", nonce, "--digest", digest, expect=expect
        )



class ReviewGate(CaseFixture):
    def test_approval_written_into_state_by_hand_does_not_advance(self) -> None:
        approved = self.build("approved", "methodology")
        self.seam(approved, "approve", "methodology", *APPROVER)
        self.assertEqual(self.phase(approved), "investigation")
        state = (approved / "data/orchestration.json").read_bytes()

        forged = self.build("forged", "methodology")
        (forged / "data/orchestration.json").write_bytes(state)
        self.assertEqual(self.phase(forged), "methodology_approval")
        bare = json.loads(state)
        bare["approvals"]["methodology"].pop("review")
        (forged / "data/orchestration.json").write_text(json.dumps(bare), encoding="utf-8")
        self.assertEqual(self.phase(forged), "methodology_approval")

    def test_replayed_nonce_is_refused(self) -> None:
        case = self.build("replay", "methodology")
        nonce = self.nonce(case, "methodology")
        self.page_approve(case, "methodology", nonce, self.view(case, "methodology")["digest"])
        self.assertEqual(self.phase(case), "investigation")
        receipt = self.view(case, "methodology")["receipt"]
        self.assertEqual(receipt["channel"], "page")

        # Changing the plan reopens the gate; the spent nonce cannot approve it.
        brief = case / "brief-directions.txt"
        brief.write_text(brief.read_text(encoding="utf-8") + "Widen scope.\n", encoding="utf-8")
        self.assertEqual(self.phase(case), "methodology_approval")
        digest = self.view(case, "methodology")["digest"]
        refused = self.page_approve(case, "methodology", nonce, digest, expect=3)
        self.assertIn("already been used", refused.stderr)
        self.assertEqual(self.phase(case), "methodology_approval")
        self.page_approve(case, "methodology", self.nonce(case, "methodology"), digest)
        self.assertEqual(self.phase(case), "investigation")

    def test_decision_whose_digest_does_not_match_the_record_is_refused(self) -> None:
        case = self.build("digest", "methodology")
        nonce = self.nonce(case, "methodology")
        shown = self.view(case, "methodology")["digest"]
        methodology = case / "data/methodology.json"
        plan = json.loads(methodology.read_text(encoding="utf-8"))
        plan["limitations"] = ["Changed after the page loaded."]
        methodology.write_text(json.dumps(plan), encoding="utf-8")

        refused = self.page_approve(case, "methodology", nonce, shown, expect=4)
        self.assertIn("digest changed", refused.stderr)
        self.assertEqual(self.phase(case), "methodology_approval")
        # The page reloads and asks again; the unspent nonce still works.
        self.page_approve(case, "methodology", nonce, self.view(case, "methodology")["digest"])
        self.assertEqual(self.phase(case), "investigation")

    def test_open_disposition_blocks_approval_until_decided(self) -> None:
        case = self.build("disposition", "investigation_gate", "--clean")
        check_path = case / "data/fact-check.json"
        check = json.loads(check_path.read_text(encoding="utf-8"))
        check["claims"][0]["verdict"] = "disputed"
        check_path.write_text(json.dumps(check), encoding="utf-8")
        view = self.view(case, "investigation_gate")
        self.assertEqual(view["blocking"], [])
        self.assertEqual(view["open_required"], 1)

        nonce = self.nonce(case, "investigation_gate")
        refused = self.page_approve(case, "investigation_gate", nonce, view["digest"], expect=3)
        self.assertIn("open dispositions", refused.stderr)
        view = self.edit(case, "investigation_gate", {"kind": "mark", "key": "disposition:F1", "value": "set_aside"})
        self.assertEqual(view["open_required"], 0)
        self.page_approve(case, "investigation_gate", nonce, view["digest"])
        self.assertEqual(self.phase(case), "investigation_gate_finalization")

    def test_blocking_integrity_failure_blocks_approval(self) -> None:
        case = self.build("blocking", "investigation_gate")
        view = self.view(case, "investigation_gate")
        self.assertTrue(view["blocking"])
        self.edit(case, "investigation_gate", {"kind": "mark", "key": "disposition:F1", "value": "keep"})
        view = self.view(case, "investigation_gate")
        self.assertEqual(view["open_required"], 0)
        refused = self.page_approve(
            case, "investigation_gate", self.nonce(case, "investigation_gate"), view["digest"], expect=3
        )
        self.assertIn("blocking failures", refused.stderr)

    def test_review_record_keeps_suggested_and_final_and_binds_the_receipt(self) -> None:
        case = self.build("record", "investigation_gate", "--clean")
        self.edit(case, "investigation_gate", {"kind": "reviewer", "value": "A. Reviewer"})
        self.edit(case, "investigation_gate", {"kind": "mark", "key": "selection:F1", "value": False})
        view = self.edit(case, "investigation_gate", {"kind": "note", "key": "selection:F1", "value": "Hold <b>back</b>"})
        record = json.loads((case / "data/investigation-gate-review.json").read_text(encoding="utf-8"))
        item = record["items"]["selection:F1"]
        self.assertEqual((item["suggested"], item["final"], item["note"]), (True, False, "Hold <b>back</b>"))
        self.assertEqual(record["counts"]["changed"], 1)
        self.assertEqual(record["counts"]["accepted_unchanged"], 0)
        self.edit(case, "investigation_gate", {"kind": "mark", "key": "selection:F1", "value": True})
        record = json.loads((case / "data/investigation-gate-review.json").read_text(encoding="utf-8"))
        self.assertEqual(record["counts"]["accepted_unchanged"], 1)
        self.edit(case, "investigation_gate", {"kind": "mark", "key": "selection:F9", "value": True}, expect=3)
        self.edit(case, "investigation_gate", {"kind": "mark", "key": "selection:F1", "value": "yes"}, expect=3)

        view = self.view(case, "investigation_gate")
        self.page_approve(case, "investigation_gate", self.nonce(case, "investigation_gate"), view["digest"])
        self.assertEqual(self.view(case, "investigation_gate")["receipt"]["review_digest"], view["digest"])
        # Changing the bound review file afterwards reopens the gate.
        path = case / "data/investigation-gate-review.json"
        path.write_text(path.read_text(encoding="utf-8").replace("A. Reviewer", "Someone else"), encoding="utf-8")
        self.assertEqual(self.phase(case), "investigation_gate_approval")

    def test_report_and_ingest_decisions_from_the_page_complete_the_case(self) -> None:
        case = self.build("tail", "report")
        report_digest = self.view(case, "report")["digest"]
        self.seam(case, "decide-report", "declined", "--nonce", self.nonce(case, "report"), "--digest", report_digest, *DECIDER)
        self.assertEqual(self.phase(case), "ingest")
        nonce = self.nonce(case, "ingest")
        self.seam(case, "decide-ingest", "requested", "--nonce", nonce, "--digest", self.view(case, "ingest")["digest"], *DECIDER)
        self.assertEqual(json.loads(self.seam(case, "status", "--json").stdout)["ingest"]["resume_at"], "ingest")
        marker_path = case / "data/ingestion.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["status"] = "completed"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        self.seam(case, "decide-ingest", "completed")
        self.assertEqual(self.phase(case), "complete")

    def test_ingest_marker_cannot_replay_another_decision_binding(self) -> None:
        case = self.build("markerreplay", "ingest")
        self.seam(case, "decide-ingest", "declined", "--nonce=" + self.nonce(case, "ingest"),
                  "--digest", self.view(case, "ingest")["digest"], *DECIDER)
        # A state without the ingest decision, and a marker the model wrote
        # claiming a request under the member's spent decline binding.
        state_path = case / "data/orchestration.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["decisions"].pop("ingest")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        marker_path = case / "data/ingestion.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["status"] = "requested"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        status = json.loads(self.seam(case, "status", "--json").stdout)
        self.assertEqual(status["ingest"], {"state": "pending", "resume_at": "decision"})

    def test_report_page_shows_the_draft_and_its_digest_binds_it(self) -> None:
        case = self.build("draft", "report")
        draft = {
            "schema_version": "1.0", "title": "Fixture report", "deck": "What the fixture shows.",
            "framing_finding_ids": ["F1"], "finding_order": ["F1"],
            "finding_treatments": [{"finding_id": "F1", "headline": "The filing <b>names</b> the payer",
                                    "summary": "One filing.", "why_it_matters": "It is primary."}],
            "caveats": [{"text": "Only one filing.", "finding_ids": ["F1"]}],
            "next_steps": [{"text": "Ask the payer.", "finding_ids": ["F1"]}],
        }
        draft_path = case / "data/report-draft.json"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        view = self.view(case, "report")
        content = view["content"]
        self.assertEqual(content["deck"], "What the fixture shows.")
        self.assertEqual(content["treatments"][0]["headline"], "The filing <b>names</b> the payer")
        self.assertEqual(content["caveats"], ["Only one filing."])
        self.assertEqual(content["next_steps"], ["Ask the payer."])
        # A draft changed after the page loaded is not what the member saw.
        draft["caveats"].append({"text": "Added later.", "finding_ids": ["F1"]})
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        self.assertNotEqual(self.view(case, "report")["digest"], view["digest"])

    def test_page_report_and_ingest_decisions_record_the_members_name(self) -> None:
        case = self.build("named", "report")
        nonce = self.nonce(case, "report")
        digest = self.view(case, "report")["digest"]
        refused = self.seam(case, "decide-report", "declined", "--nonce", nonce, "--digest", digest, expect=3)
        self.assertIn("decided_by", refused.stderr)
        self.seam(case, "decide-report", "declined", "--nonce", nonce, "--digest", digest,
                  "--decided-by", "A. Member", "--decided-at", "2026-08-23T12:00:00Z")
        self.assertEqual(self.view(case, "report")["receipt"]["approved_by"], "A. Member")
        self.seam(case, "decide-ingest", "declined", "--nonce", self.nonce(case, "ingest"),
                  "--digest", self.view(case, "ingest")["digest"],
                  "--decided-by", "B. Member", "--decided-at", "2026-08-23T12:05:00Z")
        receipt = self.view(case, "ingest")["receipt"]
        self.assertEqual((receipt["approved_by"], receipt["approved_at"]), ("B. Member", "2026-08-23T12:05:00Z"))
        self.assertEqual(self.phase(case), "complete")

    def test_page_follow_up_sends_the_case_back_to_investigation(self) -> None:
        case = self.build("followup", "investigation_gate")
        view = self.view(case, "investigation_gate")
        # A follow-up is how the member answers open flags and failures.
        self.assertTrue(view["blocking"])
        nonce = self.nonce(case, "investigation_gate")
        request = ("request-follow-up", "--instructions", "Find a second filing.",
                   "--decided-by", "A. Member", "--decided-at", "2026-08-23T12:00:00Z")
        unknown = self.seam(case, *request, "--nonce", "0" * 64, "--digest", view["digest"], expect=3)
        self.assertIn("not recorded", unknown.stderr)
        self.seam(case, *request, "--nonce", nonce, "--digest", view["digest"])
        status = json.loads(self.seam(case, "status", "--json").stdout)
        self.assertEqual((status["phase"], status["resume"]), ("investigation", {"instructions": "Find a second filing."}))
        receipt = self.view(case, "investigation_gate")["receipt"]
        self.assertEqual((receipt["decision"], receipt["approved_by"], receipt["channel"]),
                         ("follow_up", "A. Member", "page"))
        # The spent nonce cannot record anything further.
        self.seam(case, *request, "--nonce", nonce, "--digest", view["digest"], expect=3)
        self.assertEqual(self.phase(case), "investigation")

    def test_a_nonce_for_one_gate_cannot_decide_another(self) -> None:
        case = self.build("crossgate", "report")
        wrong = self.nonce(case, "ingest")
        refused = self.seam(
            case, "decide-report", "declined", "--nonce", wrong,
            "--digest", self.view(case, "report")["digest"], *DECIDER, expect=3,
        )
        self.assertIn("another gate", refused.stderr)


def as_pre_ledger_v1(case: Path) -> None:
    """Rewrite a case as one saved before the rename and the nonce ledger."""
    state_path = case / "data/orchestration.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for group in ("approvals", "decisions"):
        for receipt in state[group].values():
            receipt.pop("review", None)
    legacy = {
        "schema_version": "spotlight-orchestration/v1",
        "approvals": {
            ("gate1" if key == "investigation_gate" else key): value
            for key, value in state["approvals"].items()
        },
        "attempts": {
            ("execution-cycle" if key == "investigation-cycle" else key): value
            for key, value in state["attempts"].items()
        },
        "decisions": state["decisions"],
    }
    if "investigation_gate_finalization" in state:
        legacy["gate1_finalization"] = state["investigation_gate_finalization"]
    state_path.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    marker_path = case / "data/ingestion.json"
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker.pop("review", None)
        marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        ingest = state["decisions"].get("ingest")
        if isinstance(ingest, dict):
            ingest["output_sha256"]["data/ingestion.json"] = hashlib.sha256(
                marker_path.read_bytes()
            ).hexdigest()
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    (case / "data/review-nonces.json").unlink(missing_ok=True)


class LegacyV1Decisions(CaseFixture):
    """Decisions in a v1 state file predate the ledger and keep counting."""

    def ledger_channels(self, case: Path) -> dict[str, str]:
        ledger = json.loads((case / "data/review-nonces.json").read_text(encoding="utf-8"))
        return {entry["gate"]: entry["channel"] for entry in ledger["nonces"].values()
                if entry["channel"] == "legacy-v1"}

    def test_completed_v1_case_resolves_complete_without_writing(self) -> None:
        case = self.build("done", "ingest")
        self.seam(case, "decide-ingest", "declined")
        as_pre_ledger_v1(case)
        before = (case / "data/orchestration.json").read_bytes()
        self.assertEqual(self.phase(case), "complete")
        self.assertEqual((case / "data/orchestration.json").read_bytes(), before)
        self.assertFalse((case / "data/review-nonces.json").exists())

    def test_v1_decisions_still_count_after_the_first_v2_write(self) -> None:
        case = self.build("ingesting", "ingest")
        as_pre_ledger_v1(case)
        self.assertEqual(self.phase(case), "ingest")
        self.seam(case, "decide-ingest", "declined")
        state = json.loads((case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], "spotlight-orchestration/v2")
        self.assertEqual(
            self.ledger_channels(case),
            {"methodology": "legacy-v1", "investigation_gate": "legacy-v1", "report": "legacy-v1"},
        )
        self.assertEqual(self.phase(case), "complete")

    def test_v1_case_at_report_keeps_its_investigation_gate_approval(self) -> None:
        case = self.build("reporting", "report")
        as_pre_ledger_v1(case)
        self.assertEqual(self.phase(case), "report")
        self.seam(case, "decide-report", "declined")
        self.assertEqual(self.phase(case), "ingest")
        state = json.loads((case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"]["investigation_gate"]["review"]["channel"], "legacy-v1")
        self.assertEqual(self.ledger_channels(case)["investigation_gate"], "legacy-v1")

    def test_v1_marker_counts_only_when_the_v1_state_recorded_the_request(self) -> None:
        case = self.build("v1marker", "ingest")
        as_pre_ledger_v1(case)
        state = json.loads((case / "data/orchestration.json").read_text(encoding="utf-8"))
        digest = state["decisions"]["report"]["dependency_digest"]
        # A marker with no binding and no decision in the v1 state: refused.
        (case / "data/ingestion.json").write_text(
            json.dumps({"schema_version": "1.0", "status": "requested", "dependency_digest": digest}),
            encoding="utf-8",
        )
        status = json.loads(self.seam(case, "status", "--json").stdout)
        self.assertEqual(status["ingest"], {"state": "pending", "resume_at": "decision"})

        recorded = self.build("v1recorded", "ingest")
        self.seam(recorded, "decide-ingest", "requested")
        as_pre_ledger_v1(recorded)
        status = json.loads(self.seam(recorded, "status", "--json").stdout)
        self.assertEqual(status["ingest"], {"state": "requested", "resume_at": "ingest"})

    def test_v2_state_without_its_ledger_entries_does_not_advance(self) -> None:
        upgraded = self.build("upgraded", "report")
        as_pre_ledger_v1(upgraded)
        self.seam(upgraded, "decide-report", "declined")
        copy = self.build("copy", "report")
        (copy / "data/orchestration.json").write_bytes(
            (upgraded / "data/orchestration.json").read_bytes()
        )
        (copy / "data/review-nonces.json").unlink()
        self.assertEqual(self.phase(copy), "methodology_approval")

    def test_v2_state_cannot_claim_the_legacy_channel(self) -> None:
        case = self.build("smuggle", "methodology")
        approved = self.build("source", "methodology")
        self.seam(approved, "approve", "methodology", *APPROVER)
        state = json.loads((approved / "data/orchestration.json").read_text(encoding="utf-8"))
        state["approvals"]["methodology"]["review"] = {
            "channel": "legacy-v1",
            "nonce_sha256": "0" * 64,
            "review_digest": None,
            "review_sha256": None,
        }
        (case / "data/orchestration.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.phase(case), "methodology_approval")
        # Leaving out the binding altogether is no better in a v2 file.
        state["approvals"]["methodology"].pop("review")
        (case / "data/orchestration.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.phase(case), "methodology_approval")


if __name__ == "__main__":
    unittest.main()
