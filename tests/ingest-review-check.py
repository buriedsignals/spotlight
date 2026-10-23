#!/usr/bin/env python3
"""Solo Ingest of an activated case on the review page.

The page shows the batch the builder made, blocks a request until every flag
has a disposition and a final batch exists, rebuilds the batch after each
edit, and a requested ingestion binds the final batch through the digest.
Drives the same command-line seam the review server calls.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("review_gate_check", ROOT / "tests" / "review-gate-check.py")
GATES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(GATES)

PROPOSAL = {
    "interface_version": "1",
    "model": "analyst-model-1",
    "claims": [{"key": "c1", "finding_id": "F1", "origin_expression_id": "SX1", "proposition": "Acme paid Doe.",
                "language": "en", "attributed": False,
                "statement": {"subject": {"text": "Acme"}, "predicate": "paid", "object": {"text": "Doe"}}}],
    "events": [{"key": "e1", "language": "en", "actors": [{"name": "Acme", "role": "agent"}], "action": "pay",
                "claim_keys": ["c1"]}],
    "story_arcs": [{"key": "a1", "language": "en", "title": "Acme payments", "description": "Payments Acme made.",
                    "event_keys": ["e1"]}],
}


class SoloCase(GATES.CaseFixture):
    """An activated case at the ingest gate with a built batch (no tests)."""

    def prepared(self, name: str, *flags: str) -> Path:
        case = self.build(name, "ingest", "--clean", "--activated", *flags)
        (case / "data" / "knowledge-proposal.json").write_text(json.dumps(PROPOSAL), encoding="utf-8")
        built = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-knowledge-batch.py"), str(case), "--review",
                                "--model", "investigator=inv-1", "--model", "fact-checker=fc-1"],
                               capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        return case


class SoloIngest(SoloCase):
    def test_a_request_needs_a_built_batch(self) -> None:
        case = self.build("unbuilt", "ingest", "--clean", "--activated")
        view = self.view(case, "ingest")
        self.assertTrue(any("not proposed" in item for item in view["blocking"]), view["blocking"])
        refused = self.seam(case, "decide-ingest", "requested", expect=3)
        self.assertIn("build and review the knowledge batch", refused.stderr + refused.stdout)
        self.seam(case, "decide-ingest", "declined")  # a decline needs no batch

    def test_flags_block_until_decided_and_the_request_binds_the_final_batch(self) -> None:
        case = self.prepared("flagged", "--flagged")
        self.assertFalse((case / "data" / "knowledge-batch.json").exists())
        view = self.view(case, "ingest")
        flag = next(item for item in view["items"] if item["kind"] == "flag")
        self.assertTrue(any("every flag needs a disposition" in item for item in view["blocking"]), view["blocking"])
        self.page_decide(case, view, expect=3)
        self.edit(case, "ingest", {"kind": "mark", "key": flag["key"], "value": "keep"}, expect=3)  # not a flag action
        self.edit(case, "ingest", {"kind": "note", "key": flag["key"], "value": "The documented part is the claim."})
        self.edit(case, "ingest", {"kind": "mark", "key": flag["key"], "value": "accept_with_reason"})
        batch = json.loads((case / "data" / "knowledge-batch.json").read_text(encoding="utf-8"))
        self.assertEqual(batch["assessments"][0]["flags"][0]["disposition"]["action"], "accept_with_reason")
        view = self.view(case, "ingest")
        self.assertEqual(view["blocking"], [])
        self.assertEqual(view["content"]["batch"]["state"], "final")
        # A batch changed after the page loaded is not the one approved.
        nonce = self.nonce(case, "ingest")
        original = (case / "data" / "knowledge-batch.json").read_bytes()
        (case / "data" / "knowledge-batch.json").write_bytes(original.replace(b"Acme paid Doe.", b"Acme paid Doe twice."))
        self.seam(case, "decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], *GATES.DECIDER, expect=4)
        (case / "data" / "knowledge-batch.json").write_bytes(original)
        self.seam(case, "decide-ingest", "requested", "--nonce", self.nonce(case, "ingest"), "--digest", self.view(case, "ingest")["digest"], *GATES.DECIDER)
        status = json.loads(self.seam(case, "status", "--json").stdout)
        self.assertEqual(status["ingest"]["resume_at"], "ingest")

    def test_deselecting_a_record_rebuilds_without_it(self) -> None:
        case = self.prepared("selection")
        view = self.view(case, "ingest")
        self.assertEqual(view["content"]["batch"]["state"], "final")
        self.assertEqual({i["key"] for i in view["items"] if i["kind"] == "selection"}, {"record:c1", "record:e1", "record:a1"})
        before = view["digest"]
        self.edit(case, "ingest", {"kind": "mark", "key": "record:a1", "value": False})
        batch = json.loads((case / "data" / "knowledge-batch.json").read_text(encoding="utf-8"))
        self.assertNotIn("story_arcs", batch)
        self.assertNotEqual(self.view(case, "ingest")["digest"], before)

    def test_an_edit_withdraws_the_batch_until_its_rebuild(self) -> None:
        # The race: a page loaded between an edit and its rebuild would see
        # the new review beside the old batch. The edit itself removes the
        # batch, so nothing can be requested until the rebuild writes it.
        case = self.prepared("race").resolve()
        sys.path.insert(0, str(ROOT / "scripts"))
        from spotlight_orchestration import review
        from spotlight_orchestration.storage import transaction
        with transaction(case) as descriptor:
            review.apply_edit(case, descriptor, "ingest", {"kind": "mark", "key": "record:a1", "value": False})
        self.assertFalse((case / "data" / "knowledge-batch.json").exists())
        self.seam(case, "decide-ingest", "requested", expect=3)

    def test_a_flag_decision_survives_deselecting_and_reselecting_its_claim(self) -> None:
        case = self.prepared("reselect", "--flagged")
        flag = next(i for i in self.view(case, "ingest")["items"] if i["kind"] == "flag")
        self.edit(case, "ingest", {"kind": "note", "key": flag["key"], "value": "Documented part only."})
        self.edit(case, "ingest", {"kind": "mark", "key": flag["key"], "value": "accept_with_reason"})
        self.edit(case, "ingest", {"kind": "mark", "key": "record:c1", "value": False})
        self.edit(case, "ingest", {"kind": "mark", "key": "record:c1", "value": True})
        back = next(i for i in self.view(case, "ingest")["items"] if i["key"] == flag["key"])
        self.assertEqual((back["final"], back["note"]), ("accept_with_reason", "Documented part only."))
        self.assertTrue((case / "data" / "knowledge-batch.json").is_file())

    def test_a_rebuild_refusal_blocks_with_its_reason(self) -> None:
        case = self.prepared("refusal")
        proposal = json.loads((case / "data" / "knowledge-proposal.json").read_text(encoding="utf-8"))
        proposal["claims"][0]["attributed"] = True  # no source position exists
        (case / "data" / "knowledge-proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        self.edit(case, "ingest", {"kind": "mark", "key": "record:a1", "value": False})
        self.assertFalse((case / "data" / "knowledge-batch.json").exists())
        view = self.view(case, "ingest")
        self.assertTrue(any("cannot be built" in b and "no source position" in b for b in view["blocking"]), view["blocking"])
        self.seam(case, "decide-ingest", "declined", "--nonce", self.nonce(case, "ingest"), "--digest", view["digest"], *GATES.DECIDER)

    def test_the_page_reads_the_install_config_named_by_spotlight_config(self) -> None:
        case = self.build("newsroom", "ingest", "--clean", "--activated")
        config = self.root / "installed" / "spotlight-config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"knowledge_destination": {"mode": "newsroom"}}), encoding="utf-8")

        def view(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(GATES.SEAM), "review-view", "ingest", str(case)],
                                  capture_output=True, text=True, env={**os.environ, "SPOTLIGHT_CONFIG": value})

        shown = view(str(config))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["content"]["batch"]["state"], "newsroom")
        # A relative path is refused, as the harness refuses it, rather than read from wherever the page runs.
        refused = view("spotlight-config.json")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("SPOTLIGHT_CONFIG must be an absolute path", refused.stderr + refused.stdout)

    def page_decide(self, case: Path, view: dict, expect: int = 0):
        nonce = self.nonce(case, "ingest")
        return self.seam(case, "decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], *GATES.DECIDER, expect=expect)


if __name__ == "__main__":
    unittest.main()
