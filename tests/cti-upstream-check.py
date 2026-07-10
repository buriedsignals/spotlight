#!/usr/bin/env python3
"""Offline regression checks for CTI Expert drift reporting."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-cti-expert-upstream.py"
SHA_A = "a" * 40
SHA_B = "b" * 40


def run(lock: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--lock", str(lock), "--json", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "source.lock.json"
        lock.write_text(
            json.dumps(
                {
                    "name": "cti-expert",
                    "repository": "https://github.com/7onez/cti-expert",
                    "default_branch": "main",
                    "active_sha": SHA_A,
                    "seen_sha": SHA_A,
                }
            ),
            encoding="utf-8",
        )

        current = run(lock, "--head-sha", SHA_A, "--strict")
        assert current.returncode == 0, current.stderr or current.stdout
        assert json.loads(current.stdout)["status"] == "current"

        changed = run(lock, "--head-sha", SHA_B)
        assert changed.returncode == 0, changed.stderr or changed.stdout
        changed_json = json.loads(changed.stdout)
        assert changed_json["status"] == "upstream_changed"
        assert changed_json["runtime_activated"] is False

        strict = run(lock, "--head-sha", SHA_B, "--strict")
        assert strict.returncode == 3

        updated = run(lock, "--head-sha", SHA_B, "--update-seen")
        assert updated.returncode == 0, updated.stderr or updated.stdout
        assert json.loads(updated.stdout)["status"] == "review_pending"
        stored = json.loads(lock.read_text(encoding="utf-8"))
        assert stored["seen_sha"] == SHA_B
        assert stored["active_sha"] == SHA_A

        invalid = run(lock, "--head-sha", "not-a-sha")
        assert invalid.returncode == 2
        assert json.loads(invalid.stdout)["status"] == "error"

    print("CTI upstream drift check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
