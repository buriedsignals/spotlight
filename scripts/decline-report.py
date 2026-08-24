#!/usr/bin/env python3
"""Record a report decline bound to the current structured evidence inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from spotlight_orchestration.case_writer import atomic_write_json
from spotlight_orchestration.contract import OrchestrationError
from spotlight_orchestration.storage import resolve_case


INPUTS = ("findings.json", "fact-check.json")
SCRIPT_DIR = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    args = parser.parse_args()
    try:
        case = resolve_case(args.case_dir)
    except OrchestrationError as exc:
        print(f"FAIL  {exc}")
        return 3
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
    try:
        atomic_write_json(case, "data/report-declined.json", marker)
    except OrchestrationError as exc:
        print(f"FAIL  cannot record report decline: {exc}")
        return 3
    print("report decision: DECLINED — bound to current findings and fact-check inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
