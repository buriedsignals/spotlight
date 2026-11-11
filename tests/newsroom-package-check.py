#!/usr/bin/env python3
"""Newsroom Submit (scripts/newsroom-package.py, scripts/newsroom_batch.py)
and the ingest page's view of it.

The approved batch is prepared as the newsroom service's input; a submission
is recorded only after the member requested ingestion, under a recorded
intent; the ingest page shows a refused submission's reasons. Offline cases
from tests/fixtures/review_case.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PACKAGE = ROOT / "scripts" / "newsroom-package.py"
_SPEC = importlib.util.spec_from_file_location("review_gate_check", ROOT / "tests" / "review-gate-check.py")
GATES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(GATES)


class NewsroomPackage(GATES.CaseFixture):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.root / "spotlight-config.json"
        self.config.write_text(json.dumps({"knowledge_destination": {"mode": "newsroom"}}), encoding="utf-8")
        self.env = {**os.environ, "SPOTLIGHT_CONFIG": str(self.config)}

    def run_seam(self, *args: str, expect: int = 0, script: Path = PACKAGE) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def request(self, case: Path, expect: int = 0) -> subprocess.CompletedProcess[str]:
        return self.run_seam("request", str(case), "--case-id", "case-uuid", "--batch-id", "batch-uuid",
                             "--out", str(self.root / "request.json"), expect=expect)

    def test_a_solo_install_prepares_nothing(self) -> None:
        case = self.build("solo", "ingest", "--clean", "--activated", "--proposal")
        self.config.write_text(json.dumps({"knowledge_destination": {"mode": "solo"}}), encoding="utf-8")
        self.assertIn("not in newsroom mode", self.request(case, expect=2).stderr)

    def run_with(self, *args: str, payload: dict, expect: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(PACKAGE), *args], input=json.dumps(payload),
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def request_ingest(self, case: Path) -> None:
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        view = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        nonce = json.loads(self.run_seam("issue-review-nonce", "ingest", str(case), script=seam).stdout)["nonce"]
        self.run_seam("decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], *GATES.DECIDER, str(case), script=seam)

    def test_a_submission_starts_only_after_the_member_requested_ingestion_and_is_recorded_under_its_intent(self) -> None:
        case = self.build("requested", "ingest", "--clean", "--activated", "--proposal")
        key, other, batch = "a" * 64, "b" * 64, "00000000-0000-4000-8000-0000000ba7c4"
        assigned = [{"type": "claim", "client_id": "claim:1", "uuid": "00000000-0000-4000-8000-00000000c1a1"}]
        receipts = {"receipts": {f"submission-{batch}.json": {"tool": "start_case_review"}}, "review_state": "submitted",
                    "assigned_ids": assigned}
        begin = ("begin", str(case), "--package-sha256", key, "--batch-id", batch)
        complete = ("complete", str(case), "--package-sha256", key, "--batch-id", batch)
        # No member decision on the ingest page yet: nothing starts.
        self.assertIn("ingestion has not been requested", self.run_seam(*begin, expect=2).stderr)
        self.request_ingest(case)
        # No recorded intent: nothing is recorded as submitted.
        self.assertIn("no submission of this package is pending", self.run_with(*complete, payload=receipts, expect=2).stderr)
        self.assertFalse(json.loads(self.run_seam("pending", str(case)).stdout)["pending"])
        self.run_seam(*begin)
        # The intent keeps the batch id the service issued, so a restart sends that one again.
        self.assertEqual(json.loads(self.run_seam("pending", str(case)).stdout), {"pending": True, "idempotency_key": key, "batch_id": batch})
        # A resumed start with the same batch goes on; a different one waits.
        self.assertTrue(json.loads(self.run_seam(*begin).stdout)["resumed"])
        self.assertIn("different version of this case is pending",
                      self.run_seam("begin", str(case), "--package-sha256", other, "--batch-id", batch, expect=2).stderr)
        self.run_with(*complete, payload=receipts)
        marker = json.loads((case / "data" / "ingestion.json").read_text(encoding="utf-8"))
        self.assertEqual((marker["status"], marker["destination"], marker["batch_id"]), ("completed", "newsroom", batch))
        submission = json.loads((case / "data" / "newsroom-submission.json").read_text(encoding="utf-8"))
        self.assertEqual((submission["state"], submission["idempotency_key"]), ("submitted", key))
        self.assertEqual(json.loads((case / "receipts" / f"submission-{batch}.json").read_text(encoding="utf-8")), {"tool": "start_case_review"})
        # The ids the service assigned are kept for the case's next batch.
        self.assertEqual(json.loads((case / "data" / "newsroom-ids.json").read_text(encoding="utf-8"))["assigned_ids"], assigned)
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        status = json.loads(self.run_seam("status", "--json", str(case), script=seam).stdout)
        self.assertEqual(status["ingest"], {"state": "completed", "resume_at": "seal"})
        # Recording the same outcome again changes nothing.
        self.assertTrue(json.loads(self.run_with(*complete, payload=receipts).stdout)["already_recorded"])

    def test_only_a_newsroom_install_gets_the_newsroom_ingest_steps_with_its_status(self) -> None:
        case = self.build("note", "ingest", "--clean", "--activated")
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        status = json.loads(self.run_seam("status", "--json", str(case), script=seam).stdout)
        # The steps name the command a runtime without the harness submits with.
        self.assertIn("node harness/pi/src/newsroom-submit.ts {CASE_DIR}", status["newsroom_edition"])
        solo = self.root / "solo-config.json"
        solo.write_text(json.dumps({"knowledge_destination": {"mode": "solo"}}), encoding="utf-8")
        plain = subprocess.run([sys.executable, str(seam), "status", "--json", str(case)], capture_output=True, text=True,
                               env={**os.environ, "SPOTLIGHT_CONFIG": str(solo)})
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertNotIn("newsroom_edition", json.loads(plain.stdout))

    def test_the_approved_batch_is_prepared_under_the_services_case_and_batch_ids(self) -> None:
        case = self.build("request", "ingest", "--clean", "--activated", "--proposal")
        approved = json.loads((case / "data" / "knowledge-batch.json").read_text(encoding="utf-8"))
        out = self.root / "request.json"
        self.run_seam("request", str(case), "--case-id", "case-uuid", "--batch-id", "batch-uuid", "--out", str(out))
        built = json.loads(out.read_text(encoding="utf-8"))
        request = built["request"]
        self.assertEqual((request["case_id"], request["batch_id"]), ("case-uuid", "batch-uuid"))
        self.assertEqual([claim["client_id"] for claim in request["claims"]], [claim["id"] for claim in approved["claims"]])
        # The key the intent is recorded under is the sha256 of exactly what is sent.
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(built["request_sha256"], hashlib.sha256(canonical).hexdigest())
        # Without an approved batch there is nothing to send.
        (case / "data" / "knowledge-batch.json").unlink()
        refused = self.run_seam("request", str(case), "--case-id", "case-uuid", "--batch-id", "batch-uuid", "--out", str(out), expect=2)
        self.assertIn("data/knowledge-batch.json is missing", refused.stderr)

    def test_an_install_config_present_but_malformed_refuses_instead_of_meaning_solo(self) -> None:
        case = self.build("malformed-config", "ingest", "--clean", "--activated", "--proposal")
        for content, reason in (({"knowledge_destination": "newsroom"}, "knowledge_destination must be an object"),
                                ({"knowledge_destination": {"mode": None}}, 'knowledge_destination.mode must be "solo" or "newsroom"'),
                                ("{not json", "the install config cannot be read")):
            self.config.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
            self.assertIn(reason, self.request(case, expect=2).stderr, content)

    def test_the_ingest_page_shows_a_refused_submissions_reasons(self) -> None:
        case = self.build("pages", "ingest", "--clean", "--activated")
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        (case / "data" / "newsroom-submission.json").write_text(json.dumps({"schema_version": "1.1", "state": "blocked",
            "integrity_failures": [{"check": "service", "reason": "evidence[0].content_sha256: does not match"}]}), encoding="utf-8")
        ingest = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        self.assertEqual(ingest["content"]["submission"]["reasons"], ["evidence[0].content_sha256: does not match"])

class StartCaseReviewInput(unittest.TestCase):
    """scripts/newsroom_batch.py: a Batch 1.1 as the newsroom service's start_case_review input."""

    PROVENANCE = {"method": "journalist"}

    def convert(self, batch: dict[str, Any], server_ids: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
        import newsroom_batch
        return newsroom_batch.start_case_review_input(batch, "case-uuid", "batch-uuid", server_ids)

    def test_new_records_carry_client_ids_and_typed_references(self) -> None:
        request = self.convert({
            "schema_version": "1.1", "batch_id": "local-batch", "case_id": "local-case",
            "source_expressions": [{"id": "sx:1", "version": 1, "text": "Acme paid Doe.", "language": "en", "direct_quote": True,
                                    "origin_ref": {"type": "evidence", "id": "ev:1", "version": 1, "derivative_id": "text"},
                                    "provenance": self.PROVENANCE}],
            "evidence": [{"id": "ev:1", "version": 1, "provenance": self.PROVENANCE}],
            "claims": [{"id": "claim:1", "version": 1, "proposition": "Acme paid Doe.",
                        "origin": {"project": "local-case", "finding_id": "F1"},
                        "origin_source_expression_ref": {"id": "sx:1", "version": 1}, "provenance": self.PROVENANCE}],
            "assessments": [{"id": "as:1", "version": 1, "claim_ref": {"id": "claim:1", "version": 1}, "verdict": "verified",
                             "basis_refs": [{"type": "source_expression", "id": "sx:1", "version": 1}],
                             "flags": [{"code": "proposition_drift"}], "provenance": self.PROVENANCE}],
        })
        sx = {"id": {"type": "source_expression", "client_id": "sx:1"}, "version": 1}
        self.assertEqual(request, {
            "case_id": "case-uuid", "batch_id": "batch-uuid",
            "evidence": [{"client_id": "ev:1", "version": 1, "provenance": self.PROVENANCE}],
            "source_expressions": [{"client_id": "sx:1", "version": 1, "text": "Acme paid Doe.", "language": "en", "is_direct_quote": True,
                                    "origin_ref": {"id": {"type": "evidence", "client_id": "ev:1"}, "version": 1, "derivative_id": "text"},
                                    "provenance": self.PROVENANCE}],
            "claims": [{"client_id": "claim:1", "version": 1, "proposition": "Acme paid Doe.", "finding_id": "F1",
                        "origin_source_expression_ref": sx, "provenance": self.PROVENANCE}],
            "assessments": [{"client_id": "as:1", "version": 1, "claim_ref": {"id": {"type": "claim", "client_id": "claim:1"}, "version": 1},
                             "verdict": "verified", "basis_refs": [sx], "provenance": self.PROVENANCE}],
        })

    def test_a_later_version_and_a_reference_to_an_admitted_record_carry_the_services_ids(self) -> None:
        request = self.convert({
            "claims": [{"id": "claim:1", "version": 2, "proposition": "Acme paid Doe twice.",
                        "origin_source_expression_ref": {"id": "sx:1", "version": 1}, "provenance": self.PROVENANCE}],
        }, {("claim", "claim:1"): "uuid-claim", ("source_expression", "sx:1"): "uuid-sx"})
        self.assertEqual(request["claims"], [{
            "existing_server_id": "uuid-claim", "version": 2, "proposition": "Acme paid Doe twice.",
            "origin_source_expression_ref": {"id": {"type": "source_expression", "existing_server_id": "uuid-sx"}, "version": 1},
            "provenance": self.PROVENANCE}])

    def test_a_record_the_service_never_assigned_an_id_is_refused_by_name(self) -> None:
        import newsroom_batch
        with self.assertRaisesRegex(newsroom_batch.Refused, r"claims\[0\]\.origin_source_expression_ref.*source_expression sx:9"):
            self.convert({"claims": [{"id": "claim:1", "version": 1,
                                      "origin_source_expression_ref": {"id": "sx:9", "version": 1}, "provenance": self.PROVENANCE}]})


if __name__ == "__main__":
    unittest.main()
