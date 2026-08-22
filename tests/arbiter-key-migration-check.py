#!/usr/bin/env python3
"""RED contract for local Navigator-to-Spotlight key migration.

The fixture secret exists only in an injected callback. The test never reads the
host keyring, invokes Navigator, prints the value, or places it in argv/files.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = ROOT / "integrations" / "arbiter" / "credentials.py"


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
    assert result in (None, True, {"migrated": True}, {"status": "migrated"})
    assert fixture_secret not in repr(result)
    assert fixture_secret not in repr(events[:1])

    print("arbiter key migration: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
