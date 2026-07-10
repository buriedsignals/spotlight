#!/usr/bin/env python3
"""Validate the evidence chain, render canonical reports, then validate outputs."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run(script: str, case: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / script), str(case)],
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--if-ready", action="store_true",
                        help="exit successfully without output until findings + fact-check exist")
    args = parser.parse_args()
    case = Path(args.case_dir)
    ready = (case / "data" / "findings.json").is_file() and (case / "data" / "fact-check.json").is_file()
    if args.if_ready and not ready:
        if args.json:
            print(json.dumps({"passed": True, "skipped": True, "reason": "structured inputs not ready"}))
        return 0
    if not case.is_dir():
        print(f"FAIL  case dir not found: {case}")
        return 3

    stages: list[dict[str, object]] = []
    for label, script in (
        ("fact_check", "validate-fact-check.py"),
        ("render", "render-report.py"),
        ("report", "validate-report.py"),
    ):
        result = run(script, case)
        stages.append({
            "stage": label,
            "passed": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        })
        if result.returncode != 0:
            break

    passed = len(stages) == 3 and all(stage["passed"] for stage in stages)
    if args.json:
        print(json.dumps({"passed": passed, "stages": stages}, indent=2))
    else:
        for stage in stages:
            output = stage["stdout"] or stage["stderr"]
            print(f"[{stage['stage']}] {output}")
        print("report finalizer: " + ("PASSED" if passed else "FAILED"))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
