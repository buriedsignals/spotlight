#!/usr/bin/env python3
"""The newsroom Submit package (scripts/newsroom-package.py) and the newsroom
review pages.

The package carries the cited evidence only, with hashes that match its
evidence manifest; raw research stays behind; a file changed after it was
cited stops the build; the submission is recorded only after the member
requested ingestion; the pages show the newsroom's check flags and a refused
submission's reasons. Offline cases from tests/fixtures/review_case.py.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import schema_check  # noqa: E402

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

    def case(self, name: str) -> Path:
        case = self.build(name, "ingest", "--clean", "--activated")
        # Research material that nothing cites, and a lane folder a passage
        # points into: neither may travel.
        lane = case / "research" / "swarm" / "ownership-web"
        lane.mkdir(parents=True)
        (lane / "leads.jsonl").write_text('{"lead_id": "L1"}\n', encoding="utf-8")
        (case / "data" / "leads.json").write_text('{"leads": []}\n', encoding="utf-8")
        passages = json.loads((case / "data" / "source-expressions.json").read_text(encoding="utf-8"))
        extra = json.loads(json.dumps(passages["expressions"][0]))
        extra.update({"id": "SX2", "anchor_ref": {"path": "research/swarm/ownership-web/leads.jsonl", "line_start": 1, "line_end": 1}})
        extra["finding_links"][0]["relation"] = "context"
        passages["expressions"].append(extra)
        (case / "data" / "source-expressions.json").write_text(json.dumps(passages), encoding="utf-8")
        return case

    def test_the_package_carries_only_cited_evidence_with_hashes_matching_its_manifest(self) -> None:
        case = self.case("cited")
        built = json.loads(self.run_seam("build", str(case)).stdout)
        package, files = built["package"], built["files"]
        manifest = package["evidence_manifest"]

        sent = sorted(f["path"] for f in files)
        self.assertEqual(sent, ["research/filing.bin", "research/filing.txt"])
        self.assertEqual([entry["path"] for entry in manifest["files"]], sent)
        for file, entry in zip(sorted(files, key=lambda f: f["path"]), manifest["files"]):
            data = base64.b64decode(file["content_base64"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            self.assertEqual(file["sha256"], entry["sha256"])
            self.assertEqual(data, (case / file["path"]).read_bytes())
        filing = next(entry for entry in manifest["files"] if entry["path"] == "research/filing.txt")
        self.assertEqual(sorted(filing["roles"]), ["contextual", "supporting"])
        # Raw research stays behind: uncited files, the lane folder, the leads.
        text = json.dumps(built)
        for stays in ("primary-record.txt", "corroboration.txt", "L1", '"leads": []'):
            self.assertNotIn(stays, text)
        self.assertEqual(manifest["omissions"], [{"path": "research/swarm/ownership-web/leads.jsonl", "expression_id": "SX2",
                                                  "reason": "research-phase material stays on this computer"}])
        self.assertEqual(package["journalist_input_units"], [])
        # The package is what the published contract says the server receives.
        self.assertEqual(schema_check.load(ROOT / "schemas" / "editorial-mcp" / "package.schema.json").errors(package), [])
        # Same case, same key: the idempotency key is stable.
        self.assertEqual(json.loads(self.run_seam("build", str(case)).stdout)["package_sha256"], built["package_sha256"])

    def test_a_cited_file_changed_after_it_was_declared_stops_the_build(self) -> None:
        case = self.case("changed")
        (case / "research" / "filing.txt").write_text("Someone edited the filing.\n", encoding="utf-8")
        refused = self.run_seam("build", str(case), expect=2)
        self.assertIn("research/filing.txt no longer matches its declared sha256", refused.stderr)

    def test_a_solo_install_builds_nothing(self) -> None:
        case = self.case("solo")
        self.config.write_text(json.dumps({"knowledge_destination": {"mode": "solo"}}), encoding="utf-8")
        self.assertIn("not in newsroom mode", self.run_seam("build", str(case), expect=2).stderr)

    def test_a_submission_is_recorded_only_after_ingestion_was_requested(self) -> None:
        case = self.build("requested", "ingest", "--clean", "--activated")
        args = ("complete", str(case), "--package-sha256", "a" * 64, "--submission-id", "sub-1", "--review-id", "rev-1")
        self.assertIn("ingestion has not been requested", self.run_seam(*args, expect=2).stderr)
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        view = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        nonce = json.loads(self.run_seam("issue-review-nonce", "ingest", str(case), script=seam).stdout)["nonce"]
        self.run_seam("decide-ingest", "requested", "--nonce", nonce, "--digest", view["digest"], *GATES.DECIDER, str(case), script=seam)
        self.run_seam(*args)
        marker = json.loads((case / "data" / "ingestion.json").read_text(encoding="utf-8"))
        self.assertEqual((marker["status"], marker["destination"], marker["submission_id"]), ("completed", "newsroom", "sub-1"))
        status = json.loads(self.run_seam("status", "--json", str(case), script=seam).stdout)
        self.assertEqual(status["ingest"], {"state": "completed", "resume_at": "seal"})

    def test_the_pages_show_the_newsroom_flags_and_a_refused_submissions_reasons(self) -> None:
        case = self.case("pages")
        seam = ROOT / "scripts" / "spotlight-orchestration.py"
        (case / "data" / "newsroom-checks.json").write_text(json.dumps({"schema_version": "1.0", "checks": {
            "report": {"kind": "report", "state": "unavailable", "reason": "the decision model is down", "flags": [], "input_sha256": "b" * 64, "checked_at": "x"},
        }}), encoding="utf-8")
        (case / "data" / "newsroom-submission.json").write_text(json.dumps({"schema_version": "1.0", "state": "blocked",
            "integrity_failures": [{"check": "file_hash", "reason": "research/filing.txt does not match the manifest hash"}]}), encoding="utf-8")
        ingest = json.loads(self.run_seam("review-view", "ingest", str(case), script=seam).stdout)
        self.assertEqual(ingest["content"]["submission"]["reasons"], ["research/filing.txt does not match the manifest hash"])
        report = json.loads(self.run_seam("review-view", "report", str(case), script=seam).stdout)
        self.assertEqual(report["content"]["newsroom_checks"], {"state": "unavailable", "reason": "the decision model is down", "flags": []})


if __name__ == "__main__":
    unittest.main()
