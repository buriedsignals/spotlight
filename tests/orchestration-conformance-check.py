#!/usr/bin/env python3
"""Offline behavioral contract for Spotlight's case-local orchestration seam."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
FIXED_APPROVAL = ("journalist:fixture", "2026-08-23T12:00:00Z")
PROVENANCE_BUILDER = ROOT / "scripts" / "build-provenance-manifest.py"
CASE_VALIDATOR = ROOT / "scripts" / "validate-case.py"
PROVENANCE_FIXTURE_PATH = ROOT / "tests" / "provenance-manifest-check.py"
PROVENANCE_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "spotlight_provenance_fixture", PROVENANCE_FIXTURE_PATH
)
assert PROVENANCE_FIXTURE_SPEC and PROVENANCE_FIXTURE_SPEC.loader
PROVENANCE_FIXTURE = importlib.util.module_from_spec(PROVENANCE_FIXTURE_SPEC)
PROVENANCE_FIXTURE_SPEC.loader.exec_module(PROVENANCE_FIXTURE)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def methodology() -> dict:
    return {
        "schema_version": "1.0",
        "project": "offline-demo",
        "investigation_plan": [{
            "direction": "verify a fixture claim",
            "questions": ["What does the fixture establish?"],
            "steps": [{"order": 1, "action": "Read the local fixture", "tool": "read-file"}],
        }],
        "tools_required": ["read-file"],
        "navigator": {
            "required": False,
            "used": False,
            "fallback_used": True,
            "fallback_reason": "offline deterministic demo",
        },
    }


def investigation_outputs(case: Path) -> None:
    claim = "The offline fixture contains one unresolved claim."
    write_json(case / "data/findings.json", {
        "schema_version": "1.0", "project": "offline-demo",
        "findings": [{
            "id": "F1", "claim": claim, "evidence": "Local fixture only.",
            "sources": [{"url": "https://example.test/offline-fixture"}],
            "confidence": "low",
            "grounding": {
                "support_type": "indirect", "source_role": "secondary",
                "claim_elements_supported": ["fixture"], "missing_assumptions": ["Independent corroboration"],
                "confidence_cap": "low", "misgrounding_risk": "Fixture evidence is intentionally limited.",
                "grounding_rationale": "The local fixture supports only the narrow claim; no contradiction is asserted.",
            },
        }],
    })
    write_json(case / "data/fact-check.json", {
        "schema_version": "1.0", "project": "offline-demo", "cycle": 1,
        "claims": [{
            "id": "FC1", "finding_id": "F1", "claim_text": claim,
            "verdict": "unverified", "confidence": "low", "sources": [],
            "evidence_for": [], "evidence_against": [],
            "grounding_assessment": {
                "support_type": "insufficient", "claim_elements_checked": ["fixture"],
                "missing_assumptions": ["Independent corroboration"], "confidence_cap": "low",
                "assessment": "The offline fixture does not independently corroborate the claim.",
            },
        }],
        "summary": {"total_claims": 1, "verified": 0}, "gaps_for_next_cycle": ["Independent corroboration"],
    })
    write_json(case / "data/evidence-bundle.json", {
        "schema_version": "1.0", "project": "offline-demo", "run_id": "offline-1",
        "created_at": "2026-08-23T11:00:00Z", "items": [],
    })
    write_json(case / "data/investigation-log.json", {
        "schema_version": "1.0", "project": "offline-demo",
        "cycles": [{
            "cycle": 1, "timestamp": "2026-08-23T11:00:00Z", "focus": "offline fixture",
            "methodology": {"techniques_used": ["local review"], "tools_used": ["read-file"],
                            "search_queries": [], "failed_approaches": []},
            "findings_added": 1, "gaps_remaining": ["Independent corroboration"],
            "sources_consulted": [],
        }],
    })
    (case / "summary.md").write_text("# Offline demo\n\n**Status:** Pending review\n", encoding="utf-8")
    write_json(case / "data/summary.json", {
        "schema_version": "1.0", "project": "offline-demo", "title": "Offline demo",
        "generated_at": "2026-08-23T11:30:00Z", "status": "pending_review", "cycles": 1,
        "verified_findings": 0, "summary": "One fixture claim remains unverified.",
        "key_conclusions": [], "limitations": ["Independent corroboration is unavailable."],
        "methodology_summary": "Local fixture review.", "findings": [],
    })


class OrchestrationConformance(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-orchestration-")
        self.case = Path(self.temp.name) / "offline-demo"
        (self.case / "data").mkdir(parents=True)
        (self.case / "brief-directions.txt").write_text(
            "Verify the offline fixture.\n", encoding="utf-8"
        )
        write_json(self.case / "data/methodology.json", methodology())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_command(
        self, *args: str, case: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SEAM), *args, str(case or self.case)],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )

    def command(
        self, *args: str, expect: int = 0, case: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = self.run_command(*args, case=case)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def status(self) -> dict:
        return json.loads(self.command("status", "--json").stdout)

    def approve(self, gate: str) -> None:
        actor, approved_at = FIXED_APPROVAL
        self.command(
            "approve", gate, "--approved-by", actor, "--approved-at", approved_at
        )

    def build_provenance(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROVENANCE_BUILDER), str(self.case)],
            text=True,
            capture_output=True,
            check=False,
            cwd=ROOT,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def finish_gate1(self) -> None:
        self.build_provenance()
        (self.case / "review.html").write_text(
            "<!doctype html><title>Offline review</title>\n", encoding="utf-8"
        )
        self.command("seal-gate1")

    def use_activated_case(self) -> None:
        self.case = PROVENANCE_FIXTURE.copy_fixture_case(Path(self.temp.name))
        (self.case / "brief-directions.txt").write_text(
            "Verify the activated fixture.\n", encoding="utf-8"
        )
        write_json(self.case / "data/methodology.json", methodology())
        findings = json.loads(
            (self.case / "data/findings.json").read_text(encoding="utf-8")
        )
        write_json(
            self.case / "data/investigation-log.json",
            {
                "schema_version": "1.0",
                "project": findings["project"],
                "cycles": [
                    {
                        "cycle": 1,
                        "timestamp": "2026-08-23T11:00:00Z",
                        "focus": "activated fixture",
                        "methodology": {
                            "techniques_used": ["local review"],
                            "tools_used": ["read-file"],
                            "search_queries": [],
                            "failed_approaches": [],
                        },
                        "findings_added": 1,
                        "gaps_remaining": [],
                        "sources_consulted": [],
                    }
                ],
            },
        )
        write_json(
            self.case / "data/summary.json",
            {
                "schema_version": "1.0",
                "project": "test-investigation",
                "title": "Activated fixture",
                "generated_at": "2026-08-23T11:30:00Z",
                "status": "pending_review",
                "cycles": 1,
                "verified_findings": 1,
                "summary": "Activated fixture summary.",
                "key_conclusions": [],
                "limitations": [],
                "methodology_summary": "Local fixture review.",
                "findings": [],
            },
        )
        PROVENANCE_FIXTURE.activate_case(self.case)
        self.assert_case_valid()

    def assert_case_valid(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CASE_VALIDATOR), str(self.case)],
            text=True,
            capture_output=True,
            check=False,
            cwd=ROOT,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_drafts_and_pending_summary_are_not_human_approval(self) -> None:
        self.assertEqual(self.status()["next_phase"], "methodology_approval")
        self.approve("methodology")
        self.assertEqual(self.status()["next_phase"], "execution")
        investigation_outputs(self.case)
        pending = self.status()
        self.assertEqual(
            (pending["next_phase"], pending["status"]), ("gate1_approval", "pending")
        )

    def test_changed_approved_inputs_reopen_the_earliest_human_gate(self) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        self.approve("gate1")
        (self.case / "summary.md").write_text(
            "# Corrected summary\n", encoding="utf-8"
        )
        self.assertEqual(self.status()["next_phase"], "gate1_approval")
        write_json(
            self.case / "data/methodology.json",
            {**methodology(), "tools_required": ["read-file", "grep-files"]},
        )
        self.assertEqual(self.status()["next_phase"], "methodology_approval")

    def test_activated_validation_inputs_stale_gate1_as_one_dependency_set(self) -> None:
        self.use_activated_case()
        self.approve("methodology")
        self.approve("gate1")
        self.finish_gate1()
        self.assertEqual(self.status()["next_phase"], "report")

        expressions_path = self.case / "data/source-expressions.json"
        expressions = json.loads(expressions_path.read_text(encoding="utf-8"))
        expressions["expressions"][0]["created_by"] = "human"
        write_json(expressions_path, expressions)
        contract_path = self.case / "data/case-contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["activation_events"][-1]["activated_artifact_hashes"][
            "source_expressions_sha256"
        ] = sha256(expressions_path)
        write_json(contract_path, contract)
        self.assert_case_valid()

        self.assertEqual(self.status()["next_phase"], "gate1_approval")

    def test_gate1_approval_resumes_finalization_until_both_outputs_are_sealed(
        self,
    ) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        self.approve("gate1")
        interrupted = self.status()
        self.assertEqual(
            (interrupted["next_phase"], interrupted.get("gate1")),
            (
                "gate1_finalization",
                {"state": "approved", "resume_at": "provenance"},
            ),
        )
        self.command("seal-gate1", expect=3)

        self.build_provenance()
        self.assertEqual(
            self.status()["gate1"], {"state": "approved", "resume_at": "review"}
        )
        (self.case / "review.html").write_text(
            "<!doctype html><title>Offline review</title>\n", encoding="utf-8"
        )
        self.assertEqual(
            self.status()["gate1"], {"state": "approved", "resume_at": "seal"}
        )
        self.command("seal-gate1")
        self.assertEqual(self.status()["next_phase"], "report")

    def test_gate1_follow_up_reopens_execution_durably(self) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        self.command(
            "request-follow-up",
            "--instructions",
            "Re-check the unresolved fixture claim.",
        )
        reopened = self.status()
        self.assertEqual((reopened["status"], reopened["next_phase"]), ("active", "execution"))
        self.assertEqual(
            reopened["follow_up"]["instructions"],
            "Re-check the unresolved fixture claim.",
        )
        (self.case / "summary.md").write_text(
            "# Follow-up summary\n", encoding="utf-8"
        )
        self.assertEqual(self.status()["next_phase"], "gate1_approval")

    def test_concurrent_attempts_serialize_at_the_cap(self) -> None:
        methodology_path = self.case / "data/methodology.json"
        methodology_path.write_text(
            json.dumps(methodology()) + (" " * 8_000_000), encoding="utf-8"
        )
        self.approve("methodology")
        workers = 16
        ready_read, ready_write = os.pipe()
        start_read, start_write = os.pipe()
        worker = (
            "import os,sys;"
            "ready=int(sys.argv[1]);start=int(sys.argv[2]);"
            "os.write(ready,b'1');os.read(start,1);"
            "os.execv(sys.executable,[sys.executable,*sys.argv[3:]])"
        )
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(ready_write),
                    str(start_read),
                    str(SEAM),
                    "record-attempt",
                    "execution-cycle",
                    "--gap",
                    "fixture gap remains",
                    str(self.case),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "", "PYTHONNOUSERSITE": "1"},
                pass_fds=(ready_write, start_read),
            )
            for _ in range(workers)
        ]
        os.close(ready_write)
        os.close(start_read)
        ready = b""
        while len(ready) < workers:
            ready += os.read(ready_read, workers - len(ready))
        os.close(ready_read)
        os.write(start_write, b"1" * workers)
        os.close(start_write)
        results = [process.communicate() for process in processes]
        returncodes = [process.returncode for process in processes]

        self.assertEqual(sum(code == 0 for code in returncodes), 5, results)
        self.assertTrue(all(code in {0, 3} for code in returncodes), results)
        blocked = self.status()
        self.assertEqual(
            (blocked["status"], blocked["next_phase"]), ("blocked", "blocked")
        )
        self.assertEqual(blocked["blocked"]["attempts"]["execution-cycle"], 5)

    def test_execution_and_repair_caps_persist_explicit_blocked_state(self) -> None:
        limits = (
            ("execution-cycle", 5),
            ("fact-check-evidence-repair", 1),
            ("structural-correction", 2),
        )
        for kind, limit in limits:
            with self.subTest(kind=kind):
                isolated = Path(self.temp.name) / kind
                isolated.mkdir()
                (isolated / "data").mkdir()
                (isolated / "brief-directions.txt").write_text(
                    "Approved fixture brief.\n", encoding="utf-8"
                )
                write_json(isolated / "data/methodology.json", methodology())
                original = self.case
                self.case = isolated
                self.approve("methodology")
                for _ in range(limit):
                    self.command(
                        "record-attempt", kind, "--gap", "fixture gap remains"
                    )
                blocked = self.status()
                self.assertEqual(
                    (blocked["status"], blocked["next_phase"]), ("blocked", "blocked")
                )
                self.assertEqual(blocked["blocked"]["attempts"][kind], limit)
                self.assertEqual(blocked["blocked"]["gap"], "fixture gap remains")
                self.case = original

    def test_positive_ingest_interruptions_resume_without_reasking(self) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        self.approve("gate1")
        self.finish_gate1()
        self.command("decide-report", "declined")
        self.assertEqual(
            self.status()["ingest"], {"state": "pending", "resume_at": "decision"}
        )

        self.command("decide-ingest", "requested")
        self.assertEqual(
            self.status()["ingest"], {"state": "requested", "resume_at": "ingest"}
        )
        marker_path = self.case / "data/ingestion.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker.update({"status": "completed", "receipt_id": "offline-ingest-1"})
        write_json(marker_path, marker)
        self.assertEqual(
            self.status()["ingest"], {"state": "completed", "resume_at": "seal"}
        )

        self.command("decide-ingest", "completed")
        complete = self.status()
        self.assertEqual((complete["status"], complete["next_phase"]), ("completed", "complete"))
        self.assertEqual(
            complete["ingest"], {"state": "completed", "resume_at": "complete"}
        )

    def test_offline_demo_resumes_and_completes_report_ingest_declines(self) -> None:
        self.approve("methodology")
        self.assertEqual(self.status()["next_phase"], "execution")
        investigation_outputs(self.case)
        self.assertEqual(self.status()["next_phase"], "gate1_approval")
        self.approve("gate1")
        self.finish_gate1()
        self.command("decide-report", "declined")
        self.command("decide-ingest", "declined")
        completed = self.status()
        self.assertEqual(
            (completed["status"], completed["next_phase"]), ("completed", "complete")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
