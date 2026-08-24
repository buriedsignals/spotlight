#!/usr/bin/env python3
"""Offline behavioral contract for Spotlight's case-local orchestration seam."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
FIXED_APPROVAL = ("journalist:fixture", "2026-08-23T12:00:00Z")
REPORT_OUTPUTS = {
    "completed": ("report.html", "findings-report.md", "evidence-map.json"),
    "declined": ("data/report-declined.json",),
}
PROVENANCE_BUILDER = ROOT / "scripts" / "build-provenance-manifest.py"
CASE_VALIDATOR = ROOT / "scripts" / "validate-case.py"
PROVENANCE_FIXTURE_PATH = ROOT / "tests" / "provenance-manifest-check.py"
REPORT_FIXTURE_PATH = ROOT / "tests" / "render-report-check.py"
DOCS_README = ROOT / "docs" / "README.md"
PUBLIC_GUIDE = ROOT / "docs" / "index.html"
ROOT_README = ROOT / "README.md"
PLUGIN_DOCS_README = ROOT / "plugins" / "spotlight" / "docs" / "README.md"
SCRIPTS = ROOT / "scripts"
PREFLIGHT_SKILL = ROOT / "skills" / "phase-preflight" / "SKILL.md"
PLUGIN_PREFLIGHT_SKILL = (
    ROOT / "plugins" / "spotlight" / "skills" / "phase-preflight" / "SKILL.md"
)
INGEST_SKILL = ROOT / "skills" / "phase-ingest" / "SKILL.md"
PLUGIN_INGEST_SKILL = (
    ROOT / "plugins" / "spotlight" / "skills" / "phase-ingest" / "SKILL.md"
)
METHODOLOGY_SKILL = ROOT / "skills" / "phase-methodology" / "SKILL.md"
PLUGIN_METHODOLOGY_SKILL = (
    ROOT / "plugins" / "spotlight" / "skills" / "phase-methodology" / "SKILL.md"
)
EXECUTION_SKILL = ROOT / "skills" / "phase-execution" / "SKILL.md"
PLUGIN_EXECUTION_SKILL = (
    ROOT / "plugins" / "spotlight" / "skills" / "phase-execution" / "SKILL.md"
)
PROVENANCE_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "spotlight_provenance_fixture", PROVENANCE_FIXTURE_PATH
)
assert PROVENANCE_FIXTURE_SPEC and PROVENANCE_FIXTURE_SPEC.loader
PROVENANCE_FIXTURE = importlib.util.module_from_spec(PROVENANCE_FIXTURE_SPEC)
PROVENANCE_FIXTURE_SPEC.loader.exec_module(PROVENANCE_FIXTURE)
REPORT_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "spotlight_report_fixture", REPORT_FIXTURE_PATH
)
assert REPORT_FIXTURE_SPEC and REPORT_FIXTURE_SPEC.loader
REPORT_FIXTURE = importlib.util.module_from_spec(REPORT_FIXTURE_SPEC)
REPORT_FIXTURE_SPEC.loader.exec_module(REPORT_FIXTURE)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import spotlight_orchestration


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
            "steps": [
                {"order": 1, "action": "Assess the brief and source limits", "tool": "read-file"},
                {"order": 2, "action": "Scan the local source set", "tool": "list-files"},
                {"order": 3, "action": "Build the primary document trail", "tool": "read-file"},
                {"order": 4, "action": "Cross-reference the fixture claim", "tool": "grep-files"},
                {"order": 5, "action": "Map the documented connections", "tool": "read-file"},
                {"order": 6, "action": "Compile findings and explicit gaps", "tool": "write-file"},
            ],
        }],
        "tools_required": ["read-file", "list-files", "grep-files", "write-file"],
        "navigator": {
            "required": False,
            "used": False,
            "fallback_used": True,
            "fallback_reason": "offline deterministic demo",
        },
    }


def investigation_outputs(case: Path) -> None:
    research = case / "research"
    research.mkdir(exist_ok=True)
    primary = research / "primary-record.txt"
    corroboration = research / "corroboration.txt"
    claim = "The offline fixture contains one unresolved claim."
    primary.write_text(f"Primary record: {claim}\nEntity A appointed Entity B.\n", encoding="utf-8")
    corroboration.write_text(f"Independent index repeats: {claim}\n", encoding="utf-8")

    brief = (case / "brief-directions.txt").read_text(encoding="utf-8")
    scanned = sorted(path.name for path in research.iterdir() if path.is_file())
    primary_text = primary.read_text(encoding="utf-8")
    corroboration_text = corroboration.read_text(encoding="utf-8")
    cross_reference_matches = claim in primary_text and claim in corroboration_text
    connections = [{"from": "Entity A", "to": "Entity B", "relationship": "appointed"}]
    steps_completed = [
        {"order": 1, "step": "assess", "result": {"brief_sha256": hashlib.sha256(brief.encode()).hexdigest()}},
        {"order": 2, "step": "scan", "result": {"files": scanned}},
        {"order": 3, "step": "document trail", "result": {"primary_sha256": sha256(primary)}},
        {"order": 4, "step": "cross-reference", "result": {"matched": cross_reference_matches}},
        {"order": 5, "step": "map connections", "result": {"connections": connections}},
        {"order": 6, "step": "compile", "result": {"finding_ids": ["F1"], "gaps": ["Independent corroboration"]}},
    ]

    write_json(case / "data/findings.json", {
        "schema_version": "1.0", "project": "offline-demo",
        "findings": [{
            "id": "F1", "claim": claim, "evidence": "Two local fixture records.",
            "sources": [
                {"url": "https://example.test/primary", "local_file": "research/primary-record.txt"},
                {"url": "https://example.test/corroboration", "local_file": "research/corroboration.txt"},
            ],
            "confidence": "low",
            "grounding": {
                "support_type": "indirect", "source_role": "secondary",
                "claim_elements_supported": ["fixture"], "missing_assumptions": ["External corroboration"],
                "confidence_cap": "low", "misgrounding_risk": "Fixture evidence is intentionally limited.",
                "grounding_rationale": "The offline records support only the narrow fixture claim.",
            },
        }],
        "connections": connections,
    })
    write_json(case / "data/fact-check.json", {
        "schema_version": "1.0", "project": "offline-demo", "cycle": 1,
        "claims": [{
            "id": "FC1", "finding_id": "F1", "claim_text": claim,
            "verdict": "unverified", "confidence": "low", "sources": [],
            "evidence_for": [], "evidence_against": [],
            "grounding_assessment": {
                "support_type": "insufficient", "claim_elements_checked": ["fixture"],
                "missing_assumptions": ["External corroboration"], "confidence_cap": "low",
                "assessment": "The offline records do not provide external corroboration.",
            },
        }],
        "summary": {"total_claims": 1, "verified": 0}, "gaps_for_next_cycle": ["External corroboration"],
    })
    write_json(case / "data/evidence-bundle.json", {
        "schema_version": "1.0", "project": "offline-demo", "run_id": "offline-1",
        "created_at": "2026-08-23T11:00:00Z", "items": [],
    })
    write_json(case / "data/investigation-log.json", {
        "schema_version": "1.0", "project": "offline-demo",
        "cycles": [{
            "cycle": 1, "timestamp": "2026-08-23T11:00:00Z", "focus": "offline fixture",
            "methodology": {
                "techniques_used": [step["step"] for step in steps_completed],
                "tools_used": ["read-file", "list-files", "grep-files", "write-file"],
                "search_queries": [], "failed_approaches": [],
                "steps_completed": steps_completed,
            },
            "findings_added": 1, "gaps_remaining": ["External corroboration"],
            "sources_consulted": scanned,
        }],
    })
    (case / "summary.md").write_text("# Offline demo\n\n**Status:** Pending review\n", encoding="utf-8")
    write_json(case / "data/summary.json", {
        "schema_version": "1.0", "project": "offline-demo", "title": "Offline demo",
        "generated_at": "2026-08-23T11:30:00Z", "status": "pending_review", "cycles": 1,
        "verified_findings": 0, "summary": "One fixture claim remains unverified.",
        "key_conclusions": [], "limitations": ["External corroboration is unavailable."],
        "methodology_summary": "Six-step offline fixture review.", "findings": [],
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

    def prepare_completed_report_case(
        self, fixture_name: str = "completed-report"
    ) -> None:
        self.case = REPORT_FIXTURE.build_case(Path(self.temp.name) / fixture_name)
        (self.case / "brief-directions.txt").write_text(
            "Verify the report fixture.\n", encoding="utf-8"
        )
        write_json(
            self.case / "data/investigation-log.json",
            {"schema_version": "1.0", "project": "Northwind review", "cycles": []},
        )
        (self.case / "summary.md").write_text(
            "# Northwind review\n\n**Status:** Pending review\n", encoding="utf-8"
        )
        write_json(
            self.case / "data/summary.json",
            {"schema_version": "1.0", "project": "Northwind review"},
        )
        self.approve("methodology")
        self.approve("gate1")
        self.finish_gate1()

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

    def test_referenced_text_derivative_bytes_stale_gate1(self) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        transcript_path = self.case / "research/transcript.txt"
        transcript_path.parent.mkdir(exist_ok=True)
        transcript_path.write_text("Original fixture transcript.\n", encoding="utf-8")
        evidence_path = self.case / "data/evidence-bundle.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["items"] = [
            {
                "id": "E1",
                "text_derivatives": [
                    {
                        "id": "TD-E1",
                        "path": "research/transcript.txt",
                        "sha256": sha256(transcript_path),
                    }
                ],
            }
        ]
        write_json(evidence_path, evidence)
        self.approve("gate1")
        self.finish_gate1()
        self.assertEqual(self.status()["next_phase"], "report")

        transcript_path.write_text("Mutated fixture transcript.\n", encoding="utf-8")

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

    def test_old_review_cannot_finalize_a_new_gate1_dependency_digest(self) -> None:
        self.approve("methodology")
        investigation_outputs(self.case)
        self.approve("gate1")
        self.finish_gate1()
        (self.case / "summary.md").write_text(
            "# Corrected offline demo\n", encoding="utf-8"
        )
        self.approve("gate1")
        self.build_provenance()

        self.assertEqual(
            self.status()["gate1"], {"state": "approved", "resume_at": "review"}
        )
        (self.case / "review.html").write_text(
            "<!doctype html><title>Corrected offline review</title>\n",
            encoding="utf-8",
        )
        self.assertEqual(
            self.status()["gate1"], {"state": "approved", "resume_at": "seal"}
        )


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
        state_path = self.case / "data/orchestration.json"
        interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
        interrupted_state["decisions"].pop("ingest")
        write_json(state_path, interrupted_state)
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

    def test_preflight_binds_flue_without_removing_non_flue_case_discovery(
        self,
    ) -> None:
        preflight = PREFLIGHT_SKILL.read_text(encoding="utf-8")
        generated = PLUGIN_PREFLIGHT_SKILL.read_text(encoding="utf-8")
        self.assertEqual(generated, preflight)
        self.assertIn("### Flue-native case binding", preflight)
        self.assertIn("### Non-Flue case selection", preflight)
        flue_branch = preflight.split("### Flue-native case binding", 1)[1].split(
            "### Non-Flue case selection", 1
        )[0]
        non_flue_branch = preflight.split("### Non-Flue case selection", 1)[1].split(
            "## 8. Write config", 1
        )[0]

        self.assertIn("launcher-bound", flue_branch)
        self.assertIn("spotlight_resolve({})", flue_branch)
        self.assertNotIn("list-files(", flue_branch)
        self.assertNotIn("spotlight-orchestration.py", flue_branch)

        documented = re.findall(
            r"`(python3 scripts/spotlight-orchestration\.py status --json[^`]*)`",
            non_flue_branch,
        )
        self.assertEqual(len(documented), 1, non_flue_branch)
        arguments = shlex.split(documented[0])
        arguments[0] = sys.executable
        arguments = [
            str(self.case) if argument == "{CASE_DIR}" else argument
            for argument in arguments
        ]
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_ingest_metadata_advertises_only_the_resolver_owned_route(self) -> None:
        canonical = INGEST_SKILL.read_text(encoding="utf-8")
        generated = PLUGIN_INGEST_SKILL.read_text(encoding="utf-8")
        self.assertEqual(generated, canonical)
        frontmatter = canonical.split("---", 2)[1]
        description = next(
            line.removeprefix("description:").strip()
            for line in frontmatter.splitlines()
            if line.startswith("description:")
        )

        self.assertIn("resolver-owned", description)
        self.assertNotIn("finalize-report", description)


    def test_completed_report_runs_through_public_transition_and_resumes_ingest(
        self,
    ) -> None:
        self.prepare_completed_report_case()

        spotlight_orchestration.decide_report(self.case, "completed")

        resolution = spotlight_orchestration.resolve(self.case)
        state = json.loads(
            (self.case / "data/orchestration.json").read_text(encoding="utf-8")
        )
        report = state["decisions"]["report"]
        self.assertEqual(report["decision"], "completed")
        self.assertEqual(
            set(report["output_sha256"]),
            {"report.html", "findings-report.md", "evidence-map.json"},
        )
        self.assertTrue(all((self.case / path).is_file() for path in report["output_sha256"]))
        self.assertEqual((resolution["phase"], resolution["owner"]), ("ingest", "phase-ingest"))

    def test_report_helper_failure_restores_artifacts_and_state(self) -> None:
        for decision, outputs in REPORT_OUTPUTS.items():
            with self.subTest(decision=decision):
                self.prepare_completed_report_case(f"{decision}-helper-failure")
                originals = {
                    name: (
                        b'{"existing":"decline marker"}\n'
                        if decision == "declined"
                        else None
                    )
                    for name in outputs
                }
                for name, content in originals.items():
                    if content is not None:
                        (self.case / name).write_bytes(content)
                state_path = self.case / "data/orchestration.json"
                state_before = state_path.read_bytes()

                def fail_after_partial_output(
                    *_args: object, **_kwargs: object
                ) -> None:
                    for name in outputs:
                        (self.case / name).write_text(
                            f"partial {name}\n", encoding="utf-8"
                        )
                    raise spotlight_orchestration.OrchestrationError(
                        "report helper failed"
                    )

                with mock.patch.object(
                    spotlight_orchestration.transitions,
                    "_run_helper",
                    fail_after_partial_output,
                ):
                    with self.assertRaises(
                        spotlight_orchestration.OrchestrationError
                    ):
                        spotlight_orchestration.decide_report(self.case, decision)

                self.assertEqual(state_path.read_bytes(), state_before)
                self.assertEqual(
                    {
                        name: (
                            (self.case / name).read_bytes()
                            if (self.case / name).exists()
                            else None
                        )
                        for name in outputs
                    },
                    originals,
                )

    def test_report_state_failure_restores_artifacts_and_state(self) -> None:
        for decision, outputs in REPORT_OUTPUTS.items():
            with self.subTest(decision=decision):
                self.prepare_completed_report_case(f"{decision}-state-failure")
                originals = {
                    name: f"existing {decision} {name}\n".encode("utf-8")
                    for name in outputs
                }
                for name, content in originals.items():
                    (self.case / name).write_bytes(content)
                state_path = self.case / "data/orchestration.json"
                state_before = state_path.read_bytes()

                def fail_state_publication(
                    *_args: object, **_kwargs: object
                ) -> None:
                    raise spotlight_orchestration.OrchestrationError(
                        "state publication failed"
                    )

                with mock.patch.object(
                    spotlight_orchestration.transitions,
                    "atomic_write_json",
                    fail_state_publication,
                ):
                    with self.assertRaises(
                        spotlight_orchestration.OrchestrationError
                    ):
                        spotlight_orchestration.decide_report(self.case, decision)

                self.assertEqual(
                    {name: (self.case / name).read_bytes() for name in outputs},
                    originals,
                )
                self.assertEqual(state_path.read_bytes(), state_before)

    def test_runtime_docs_keep_report_between_gate1_and_ingest(self) -> None:
        expected = (
            "Pipeline: Preflight → Brief → Methodology → Execution → "
            "Gate 1 → Report → Ingestion"
        )
        canonical = DOCS_README.read_text(encoding="utf-8")
        generated = PLUGIN_DOCS_README.read_text(encoding="utf-8")
        self.assertIn(expected, canonical)
        self.assertEqual(generated, canonical)

    def test_public_workflow_surfaces_require_report_before_ingest_and_name_outputs(
        self,
    ) -> None:
        guide = PUBLIC_GUIDE.read_text(encoding="utf-8")
        guide_pipeline = guide.split('<section id="pipeline">', 1)[1].split(
            "</section>", 1
        )[0]
        readme = ROOT_README.read_text(encoding="utf-8")
        readme_workflow = readme.split("## Investigation Workflow", 1)[1].split(
            "## Readiness Criteria", 1
        )[0]
        readme_outputs = readme.split("## Case Outputs", 1)[1].split(
            "## ", 1
        )[0]

        for label, workflow in (
            ("docs/index.html", guide_pipeline),
            ("README.md", readme_workflow),
        ):
            with self.subTest(surface=label):
                self.assertRegex(
                    workflow,
                    r"(?s)Gate 1.*Report.*Ingest",
                )
        for label, outputs in (
            ("docs/index.html", guide_pipeline),
            ("README.md", readme_outputs),
        ):
            with self.subTest(surface=label):
                for artifact in (
                    "report.html",
                    "findings-report.md",
                    "evidence-map.json",
                ):
                    self.assertIn(artifact, outputs)

    def test_public_docs_define_the_trusted_local_active_case_boundary(self) -> None:
        for label, surface in (
            ("docs/index.html", PUBLIC_GUIDE.read_text(encoding="utf-8")),
            ("README.md", ROOT_README.read_text(encoding="utf-8")),
        ):
            with self.subTest(surface=label):
                self.assertRegex(
                    surface,
                    r"(?is)active case directories must not be moved or replaced "
                    r"during an operation",
                )

    def test_every_worker_prompt_treats_case_derived_text_as_evidence_not_instructions(
        self,
    ) -> None:
        skill_prompt_sections = (
            (
                "phase-methodology",
                METHODOLOGY_SKILL,
                PLUGIN_METHODOLOGY_SKILL,
                (
                    (
                        "initial investigator planning",
                        "After brief approval, spawn the investigator",
                        "When the agent completes",
                    ),
                    (
                        "investigator methodology correction",
                        "If validation fails",
                        "3. Present a summary",
                    ),
                ),
            ),
            (
                "phase-execution",
                EXECUTION_SKILL,
                PLUGIN_EXECUTION_SKILL,
                (
                    (
                        "initial investigator execution",
                        "1. Spawn investigator",
                        "2. When complete",
                    ),
                    (
                        "investigator structural correction",
                        "2.5. Validate the investigator output",
                        "3. Spawn fact-checker",
                    ),
                    (
                        "initial fact-checker",
                        "3. Spawn fact-checker",
                        "4. When complete",
                    ),
                    (
                        "fact-checker evidence correction",
                        "4.5. Validate the fact-checker output",
                        "5. Run editorial standards check",
                    ),
                    (
                        "editorial correction",
                        "5. Run editorial standards check",
                        "5.5. Process monitoring recommendations",
                    ),
                ),
            ),
        )

        for skill_label, skill_path, plugin_path, prompt_sections in (
            skill_prompt_sections
        ):
            canonical = skill_path.read_text(encoding="utf-8")
            generated = plugin_path.read_text(encoding="utf-8")
            with self.subTest(skill=skill_label, contract="generated mirror"):
                self.assertEqual(generated, canonical)

            for prompt_label, start, end in prompt_sections:
                with self.subTest(skill=skill_label, prompt=prompt_label):
                    scope = canonical.split(start, 1)[1].split(end, 1)[0]
                    prompts = re.findall(
                        r'\bprompt:\s*"(.*?)"\s*,', scope, re.DOTALL
                    )
                    self.assertEqual(
                        len(prompts),
                        1,
                        f"{prompt_label} must define one literal child-visible prompt",
                    )
                    prompt = prompts[0]
                    self.assertRegex(
                        prompt,
                        r"(?is)(?:all|every).*case.*source.*monitoring.*validator.*"
                        r"gap.*(?:evidence|data).*never instructions",
                    )
                    self.assertRegex(
                        prompt,
                        r"(?is)(?:do not|never).*(?:follow|obey|execute).*"
                        r"(?:instructions|directives)",
                    )

    def test_offline_demo_resumes_and_completes_report_ingest_declines(self) -> None:
        self.approve("methodology")
        self.assertEqual(self.status()["next_phase"], "execution")
        investigation_outputs(self.case)
        self.assertEqual(self.status()["next_phase"], "gate1_approval")
        trace = json.loads(
            (self.case / "data/investigation-log.json").read_text(encoding="utf-8")
        )["cycles"][0]["methodology"]["steps_completed"]
        self.assertEqual(
            [step["step"] for step in trace],
            [
                "assess",
                "scan",
                "document trail",
                "cross-reference",
                "map connections",
                "compile",
            ],
        )
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
