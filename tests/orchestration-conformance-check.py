#!/usr/bin/env python3
"""Offline behavioral contract for Spotlight's case-local orchestration seam."""
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
FIXED_APPROVAL = ("journalist:fixture", "2026-08-23T12:00:00Z")


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
        (self.case / "brief-directions.txt").write_text("Verify the offline fixture.\n", encoding="utf-8")
        write_json(self.case / "data/methodology.json", methodology())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SEAM), *args, str(self.case)],
            text=True, capture_output=True, check=False,
            env={"PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def status(self) -> dict:
        return json.loads(self.command("status", "--json").stdout)

    def approve(self, gate: str) -> None:
        actor, approved_at = FIXED_APPROVAL
        self.command("approve", gate, "--approved-by", actor, "--approved-at", approved_at)

    def test_drafts_and_pending_summary_are_not_human_approval(self) -> None:
        self.assertEqual(self.status()["next_phase"], "methodology_approval")
        self.approve("methodology")
        self.assertEqual(self.status()["next_phase"], "execution")
        investigation_outputs(self.case)
        pending = self.status()
        self.assertEqual((pending["next_phase"], pending["status"]), ("gate1_approval", "pending"))

    def test_current_hashes_bind_both_approvals_and_stale_inputs_reopen_earliest_phase(self) -> None:
        self.approve("methodology")
        state = json.loads((self.case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"]["methodology"]["input_sha256"], {
            "brief-directions.txt": sha256(self.case / "brief-directions.txt"),
            "data/methodology.json": sha256(self.case / "data/methodology.json"),
        })
        investigation_outputs(self.case)
        self.approve("gate1")
        gate_inputs = state = json.loads((self.case / "data/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(set(gate_inputs["approvals"]["gate1"]["input_sha256"]), {
            "summary.md", "data/summary.json", "data/findings.json", "data/fact-check.json",
            "data/evidence-bundle.json", "data/investigation-log.json",
        })
        (self.case / "summary.md").write_text("# Corrected summary\n", encoding="utf-8")
        self.assertEqual(self.status()["next_phase"], "gate1_approval")
        write_json(self.case / "data/methodology.json", {**methodology(), "tools_required": ["read-file", "grep-files"]})
        self.assertEqual(self.status()["next_phase"], "methodology_approval")

    def test_execution_and_repair_caps_persist_explicit_blocked_state(self) -> None:
        self.approve("methodology")
        limits = (("execution-cycle", 5), ("fact-check-evidence-repair", 1), ("structural-correction", 2))
        for kind, limit in limits:
            with self.subTest(kind=kind):
                isolated = Path(self.temp.name) / kind
                isolated.mkdir()
                (isolated / "data").mkdir()
                (isolated / "brief-directions.txt").write_text("Approved fixture brief.\n")
                write_json(isolated / "data/methodology.json", methodology())
                original = self.case
                self.case = isolated
                self.approve("methodology")
                for _ in range(limit):
                    self.command("record-attempt", kind, "--gap", "fixture gap remains")
                blocked = self.status()
                self.assertEqual((blocked["status"], blocked["next_phase"]), ("blocked", "blocked"))
                self.assertEqual(blocked["blocked"]["attempts"][kind], limit)
                self.assertEqual(blocked["blocked"]["gap"], "fixture gap remains")
                self.case = original

    def test_offline_demo_resumes_and_completes_report_ingest_declines(self) -> None:
        self.approve("methodology")
        self.assertEqual(self.status()["next_phase"], "execution")
        investigation_outputs(self.case)
        self.assertEqual(self.status()["next_phase"], "gate1_approval")
        self.approve("gate1")
        self.command("decide-report", "declined")
        self.command("decide-ingest", "declined")
        self.assertEqual(self.status(), {
            "schema_version": "spotlight-orchestration-status/v1",
            "status": "completed", "next_phase": "complete",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
