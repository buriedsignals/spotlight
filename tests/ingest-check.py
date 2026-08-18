#!/usr/bin/env python3
"""Focused local-first activation and claim-note retirement checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("activation", ROOT / "scripts" / "validate-install-config.py")
activation = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(activation)


def write_json(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return raw


class ActivationChecks(unittest.TestCase):
    def fixture(self) -> tuple[Path, Path, Path]:
        root = Path(tempfile.mkdtemp())
        workspace, case = root / "OpenKnowledge", root / "cases" / "alpha"
        migration = {"schema_version": "spotlight-workflow-migration/v1", "workflows": {name: "migrated" for name in sorted(activation.MIGRATED_WORKFLOWS)}}
        migration_raw = write_json(workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json", migration)
        destination = {
            "type": "openknowledge", "workspace_path": str(workspace), "namespace": "spotlight",
            "project_id": "local:" + str(workspace), "destination_id": "destination:spotlight-local",
            "projection_namespace": "investigations", "story_namespace": "stories",
            "graph_database_path": ".knowledge-workspace/spotlight.sqlite",
            "provider_policy": {"provider_mode": "local_device", "model": "bge-m3:567m", "network_egress": "denied", "retention_days": 0},
        }
        config = {"knowledge_destination": destination, "knowledge_activation": {"schema_version": "spotlight-activation-reference/v1", "receipt_path": ".knowledge-workspace/spotlight-activation.json"}}
        config_path = root / ".spotlight-config.json"
        write_json(config_path, config)
        write_json(workspace / ".knowledge-workspace" / "routes.json", {"schema_version": "knowledge-routes/v1", "routes": {"spotlight_verified": "spotlight"}})
        issued = activation.issue_local_activation(config_path)
        self.assertEqual(issued["status"], "active")
        write_json(case / "data" / "knowledge-batch.json", {
            "schema_version": "1.0", "batch_id": "batch:alpha",
            "source_case": {"project": "alpha"},
            "review_decisions": [{"disposition": "approved", "subject": {"kind": "source_case"}}],
        })
        return config_path, case, workspace / ".knowledge-workspace" / "spotlight-activation.json"

    def test_local_conformance_suppresses_only_graph_enabled_new_claims(self):
        config, case, _ = self.fixture()
        with mock.patch.object(activation, "_case_projection_ready", return_value=(True, "")):
            result = activation.claim_note_gate(config, case)
        self.assertTrue(result["suppress_new_claim_notes"])
        self.assertFalse(result["production_security"])
        (case / "data" / "knowledge-batch.json").unlink()
        self.assertFalse(activation.claim_note_gate(config, case)["suppress_new_claim_notes"])

    def test_reviewed_batch_without_completed_projection_preserves_claim_notes(self):
        config, case, _ = self.fixture()
        result = activation.claim_note_gate(config, case)
        self.assertFalse(result["suppress_new_claim_notes"])
        self.assertIn("graph/projection verification failed", " ".join(result["blockers"]))

    def test_explicit_cli_issues_local_activation_from_hashed_receipts(self):
        config, _, receipt_path = self.fixture()
        workspace = Path(json.loads(config.read_text())["knowledge_destination"]["workspace_path"])
        receipt_path.unlink()
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts" / "validate-install-config.py"),
            "--config", str(config), "--issue-local",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        issued = json.loads(result.stdout)
        self.assertEqual((issued["status"], issued["assurance"]), ("active", "local_conformance"))
        self.assertTrue(receipt_path.is_file())

    def test_fresh_install_initializes_workflow_receipt_and_activation(self):
        config, _, receipt_path = self.fixture()
        workspace = Path(json.loads(config.read_text())["knowledge_destination"]["workspace_path"])
        receipt_path.unlink()
        (workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json").unlink()
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts" / "validate-install-config.py"),
            "--config", str(config), "--initialize-local",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "active")
        migration = json.loads((workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json").read_text())
        self.assertEqual(set(migration["workflows"]), activation.MIGRATED_WORKFLOWS)

    def test_missing_invalid_or_incomplete_activation_preserves_ingest(self):
        config, case, receipt_path = self.fixture()
        receipt_path.unlink()
        self.assertFalse(activation.claim_note_gate(config, case)["suppress_new_claim_notes"])
        config, case, receipt_path = self.fixture()
        receipt = json.loads(receipt_path.read_text())
        receipt["provider_policy_sha256"] = "0" * 64
        write_json(receipt_path, receipt)
        self.assertFalse(activation.claim_note_gate(config, case)["suppress_new_claim_notes"])

    def test_non_local_assurance_is_rejected(self):
        config, case, receipt_path = self.fixture()
        receipt = json.loads(receipt_path.read_text())
        receipt["assurance"] = "production_ready"
        write_json(receipt_path, receipt)
        result = activation.claim_note_gate(config, case)
        self.assertFalse(result["suppress_new_claim_notes"])
        self.assertIn("assurance tier is invalid", " ".join(result["blockers"]))

    def test_all_four_migrations_are_required(self):
        config, case, _ = self.fixture()
        workspace = Path(json.loads(config.read_text())["knowledge_destination"]["workspace_path"])
        migration_path = workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json"
        migration = json.loads(migration_path.read_text())
        del migration["workflows"]["prior_verdict"]
        raw = write_json(migration_path, migration)
        receipt_path = workspace / ".knowledge-workspace" / "spotlight-activation.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["workflow_migration_receipt"]["sha256"] = hashlib.sha256(raw).hexdigest()
        write_json(receipt_path, receipt)
        self.assertFalse(activation.claim_note_gate(config, case)["suppress_new_claim_notes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
