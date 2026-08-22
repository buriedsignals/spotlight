#!/usr/bin/env python3
"""RED contract for local Navigator-to-Spotlight key migration.

The fixture secret exists only in an injected callback. The test never reads the
host keyring, invokes Navigator, prints the value, or places it in argv/files.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "integrations" / "arbiter" / "client.py"
CREDENTIALS = ROOT / "integrations" / "arbiter" / "credentials.py"

def load_module(path: Path, name: str):
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(ROOT)} must provide the native seam")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_client_provider_wiring(credentials) -> None:
    """Native construction must resolve the Spotlight-owned callback in-process."""
    client_module = load_module(CLIENT, "spotlight_arbiter_client_for_credentials")
    events: list[str] = []
    fixture_secret = "provider-fixture-secret-never-print"

    def read_owned():
        events.append("read")
        return fixture_secret

    fixture_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    with patch.object(client_module.socket, "getaddrinfo", return_value=fixture_dns):
        client = client_module.ArbiterClient.from_env(
            {"ARBITER_API_BASE": "https://staging.example/api/v1"},
            credential_provider=lambda: credentials.resolve_spotlight_arbiter_key(read_owned),
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("network must not run during provider wiring")
            ),
        )
    assert events == ["read"], events
    assert client is not None


def load_credentials():
    if not CREDENTIALS.is_file():
        raise AssertionError("integrations/arbiter/credentials.py must provide the credential seam")
    spec = importlib.util.spec_from_file_location("spotlight_arbiter_credentials", CREDENTIALS)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load integrations/arbiter/credentials.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    credentials = load_credentials()
    migrate = getattr(credentials, "migrate_navigator_arbiter_key", None)
    if migrate is None:
        raise AssertionError("credential seam must expose migrate_navigator_arbiter_key")

    fixture_secret = "navigator-fixture-secret-never-print"
    events: list[tuple[str, object]] = []

    def read_legacy():
        events.append(("read", None))
        return fixture_secret

    def write_owned(value):
        events.append(("write", value))

    with patch.object(subprocess, "run", side_effect=AssertionError("Navigator CLI invoked")):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("Navigator CLI invoked")):
            result = migrate(read_legacy=read_legacy, write_spotlight=write_owned)
    assert [kind for kind, _value in events] == ["read", "write"]
    assert events[1][1] == fixture_secret
    assert result is not fixture_secret, "migration must not return the secret"
    check_client_provider_wiring(credentials)
    assert result in (None, True, {"migrated": True}, {"status": "migrated"})
    assert fixture_secret not in repr(result)
    assert fixture_secret not in repr(events[:1])

    print("arbiter key migration: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
