#!/usr/bin/env python3
"""RED contract for the distributed Arbiter client and preflight override."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE_INTEGRATION = ROOT / "integrations" / "arbiter"
PLUGIN_INTEGRATION = ROOT / "plugins" / "spotlight" / "integrations" / "arbiter"
PLUGIN_PREFLIGHT = ROOT / "plugins" / "spotlight" / "integrations" / "preflight.py"


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def load_preflight():
    integration_dir = PLUGIN_PREFLIGHT.parent
    sys.path.insert(0, str(integration_dir))
    spec = importlib.util.spec_from_file_location("spotlight_plugin_preflight", PLUGIN_PREFLIGHT)
    if spec is None or spec.loader is None:
        raise AssertionError("plugin preflight must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    source_modules = {
        path.name
        for path in SOURCE_INTEGRATION.glob("*.py")
        if path.name != "__init__.py"
    }
    plugin_modules = {
        path.name
        for path in PLUGIN_INTEGRATION.glob("*.py")
        if path.name != "__init__.py"
    }
    assert plugin_modules == source_modules, (
        f"plugin runtime module set drift: source={source_modules!r} plugin={plugin_modules!r}"
    )
    for filename in sorted(source_modules):
        source = SOURCE_INTEGRATION / filename
        copied = PLUGIN_INTEGRATION / filename
        assert copied.read_bytes() == source.read_bytes(), f"plugin {filename} is stale"

    for filename in ("client.py", "credentials.py", "workflow.py"):
        source = SOURCE_INTEGRATION / filename
        copied = PLUGIN_INTEGRATION / filename
        assert source.is_file(), f"source Arbiter seam missing: {source}"
        assert copied.is_file(), f"distributed plugin missing {filename}"
        assert copied.read_bytes() == source.read_bytes(), f"plugin {filename} is stale"

    manifest = json.loads((PLUGIN_INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    preflight = load_preflight()
    override = "https://staging.arbiter.example/api/v1"
    arbiter_client = importlib.import_module("arbiter.client")
    fixture_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    with patch.object(arbiter_client.socket, "getaddrinfo", return_value=fixture_dns):
        with patch.dict(os.environ, {"ARBITER_API_BASE": override}, clear=False):
            with patch.object(preflight.urllib.request, "urlopen", return_value=FakeResponse()) as probe:
                ok, error = preflight.smoke_test(manifest)
    requested = probe.call_args.args[0].full_url if probe.call_args else None
    assert ok and error is None, (ok, error)
    assert requested == override + "/openapi.json", requested

    calls = []
    with patch.object(preflight.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: calls.append(args)):
        ok, error = preflight.smoke_test(manifest, sensitive=True)
    assert not ok and error and "sensitive" in error.lower()
    assert not calls, "sensitive preflight must block before opening a network"

    print("arbiter plugin parity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
