#!/usr/bin/env python3
"""validate-install-config.py accepts the knowledge destination Engine writes
(type, workspace_path, namespace, mode), tolerates the retired 1.0 graph keys
an older config still holds, and refuses a block Spotlight cannot use."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-install-config.py"


def check(destination: dict | None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as raw:
        config = Path(raw) / ".spotlight-config.json"
        config.write_text(json.dumps({} if destination is None else {"knowledge_destination": destination}))
        done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config)], capture_output=True, text=True)
    return done.returncode, json.loads(done.stdout)


class InstallConfigChecks(unittest.TestCase):
    def setUp(self):
        self.wiki = Path(tempfile.mkdtemp())
        self.block = {"type": "openknowledge", "workspace_path": str(self.wiki), "namespace": "spotlight", "mode": "solo"}

    def test_reduced_block_is_valid(self):
        code, result = check(self.block)
        self.assertEqual((code, result["status"], result["mode"], result["blockers"]), (0, "valid", "solo", []))

    def test_retired_graph_keys_are_tolerated(self):
        legacy = {"project_id": "local:x", "destination_id": "destination:spotlight-local", "projection_namespace": "investigations",
                  "story_namespace": "stories", "graph_database_path": ".knowledge-workspace/spotlight.sqlite",
                  "provider_policy": {"model": "something-else"}, "legacy_vault_path": "/nowhere", "legacy_mode": "x"}
        code, result = check({**self.block, **legacy, "mode": "newsroom"})
        self.assertEqual((code, result["status"], result["mode"]), (0, "valid", "newsroom"))

    def test_unusable_blocks_are_invalid(self):
        for destination, reason in (
            (None, "knowledge_destination is missing"),
            ({**self.block, "workspace_path": ""}, "workspace_path is missing"),
            ({**self.block, "workspace_path": str(self.wiki / "absent")}, "not a directory"),
            ({**self.block, "mode": "team"}, "solo or newsroom"),
        ):
            code, result = check(destination)
            self.assertEqual((code, result["status"]), (2, "invalid"), destination)
            self.assertIn(reason, " ".join(result["blockers"]))

    def test_namespace_must_match_the_sealed_route(self):
        routes = self.wiki / ".knowledge-workspace" / "routes.json"
        routes.parent.mkdir()
        routes.write_text(json.dumps({"schema_version": "knowledge-routes/v1", "routes": {"spotlight_verified": "spotlight"}}))
        self.assertEqual(check(self.block)[0], 0)
        code, result = check({**self.block, "namespace": "elsewhere"})
        self.assertEqual(code, 2)
        self.assertIn("sealed Spotlight route", " ".join(result["blockers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
