#!/usr/bin/env python3
"""Stable unit checks for integration preflight behavior."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations"))

from integrations import preflight  # noqa: E402
from _preflight_base import build_report  # noqa: E402


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class PreflightCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            "id": "phantom-tide",
            "name": "Phantom Tide",
            "category": "transport-intelligence",
            "type": "api",
            "requires_key": True,
            "env_vars": ["PHANTOM_TIDE_API_KEY"],
        }
        self._old_key = os.environ.pop("PHANTOM_TIDE_API_KEY", None)

    def tearDown(self) -> None:
        if self._old_key is not None:
            os.environ["PHANTOM_TIDE_API_KEY"] = self._old_key
        else:
            os.environ.pop("PHANTOM_TIDE_API_KEY", None)

    def test_phantom_tide_is_red_without_key(self) -> None:
        report = build_report(self.manifest, smoke_fn=preflight.smoke_test)

        self.assertEqual(report["status"], "red")
        self.assertEqual(report["env_vars_missing"], ["PHANTOM_TIDE_API_KEY"])

    def test_phantom_tide_smoke_uses_keyed_airspace_endpoint(self) -> None:
        os.environ["PHANTOM_TIDE_API_KEY"] = "pt-test"
        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["authorization"] = req.get_header("Authorization")
            return FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            report = build_report(self.manifest, smoke_fn=preflight.smoke_test)

        self.assertEqual(report["status"], "green")
        self.assertIn("/api/public/aircraft/restricted-airspace-crossings", captured["url"])
        self.assertIn("limit=1", captured["url"])
        self.assertEqual(captured["authorization"], "Bearer pt-test")
        self.assertEqual(captured["timeout"], 8)


if __name__ == "__main__":
    unittest.main()
