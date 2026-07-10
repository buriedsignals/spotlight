#!/usr/bin/env python3
"""Record a report decline bound to the current structured evidence inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


INPUTS = ("findings.json", "fact-check.json")
SCRIPT_DIR = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    args = parser.parse_args()
    case = Path(args.case_dir).resolve()
    data = case / "data"
    missing = [name for name in INPUTS if not (data / name).is_file()]
    if missing:
        print(f"FAIL  cannot decline report before structured evidence exists: {missing}")
        return 3

    validation = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "validate-fact-check.py"), str(case)],
        capture_output=True,
        text=True,
    )
    if validation.returncode != 0:
        print(validation.stdout.strip() or validation.stderr.strip())
        print("FAIL  cannot decline report over an invalid fact-check evidence chain")
        return 3

    marker = {
        "schema_version": "1.0",
        "decision": "declined",
        "input_sha256": {f"data/{name}": sha256(data / name) for name in INPUTS},
    }
    handle, temp_name = tempfile.mkstemp(prefix=".report-declined.", dir=data, text=True)
    try:
        with os.fdopen(handle, "w") as temp:
            json.dump(marker, temp, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        Path(temp_name).replace(data / "report-declined.json")
    finally:
        Path(temp_name).unlink(missing_ok=True)
    print("report decision: DECLINED — bound to current findings and fact-check inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
