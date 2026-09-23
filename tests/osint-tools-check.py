#!/usr/bin/env python3
"""Validate osint-tools behaviour when the local index has not been built."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "osint-tools.py"


def run(subcommand: list[str], db: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SPOTLIGHT_OSINT_DB": str(db)}
    return subprocess.run([sys.executable, str(SCRIPT), *subcommand], env=env, capture_output=True, text=True)


def main() -> int:
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "osint_tools.db"
        for subcommand in (["find", "whois"], ["categories"]):
            result = run(subcommand, db)
            label = " ".join(subcommand)
            if result.returncode == 0:
                errors.append(f"{label}: exited 0 without an index")
            if "Traceback" in result.stderr:
                errors.append(f"{label}: crashed instead of reporting the missing index")
            if "osint-tools.py build" not in result.stderr:
                errors.append(f"{label}: missing-index message does not give the build command")
            if db.exists():
                errors.append(f"{label}: created an empty index file at {db}")
                db.unlink()
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    if errors:
        return 1
    print("ok osint-tools missing-index handling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
