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


class SoloIngest(GATES.CaseFixture):
    def prepared(self, name: str, *flags: str) -> Path:
        case = self.build(name, "ingest", "--clean", "--activated", *flags)
        (case / "data" / "knowledge-proposal.json").write_text(json.dumps(PROPOSAL), encoding="utf-8")
        built = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-knowledge-batch.py"), str(case), "--review",
                                "--model", "investigator=inv-1", "--model", "fact-checker=fc-1"],
                               capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        return case

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
        self.seam(case, "decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], expect=4)
        (case / "data" / "knowledge-batch.json").write_bytes(original)
        self.seam(case, "decide-ingest", "requested", "--nonce", self.nonce(case, "ingest"), "--digest", self.view(case, "ingest")["digest"])
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

    def page_decide(self, case: Path, view: dict, expect: int = 0):
        nonce = self.nonce(case, "ingest")
        return self.seam(case, "decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], expect=expect)


if __name__ == "__main__":
    unittest.main()
