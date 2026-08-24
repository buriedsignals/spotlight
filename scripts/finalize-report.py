#!/usr/bin/env python3
"""Validate the evidence chain, render canonical reports, then validate outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_decline(data_dir: Path) -> tuple[bool, str | None]:
    marker_path = data_dir / "report-declined.json"
    if not marker_path.is_file():
        return False, None
    try:
        marker = json.loads(marker_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return False, f"data/report-declined.json is malformed: {exc}"
    expected_keys = {"schema_version", "decision", "input_sha256"}
    if not isinstance(marker, dict) or set(marker) != expected_keys:
        return False, "data/report-declined.json does not match the required decline contract"
    if marker.get("schema_version") != "1.0" or marker.get("decision") != "declined":
        return False, "data/report-declined.json is not a valid report decline"
    hashes = marker.get("input_sha256")
    required = {"data/findings.json", "data/fact-check.json"}
    if not isinstance(hashes, dict) or set(hashes) != required:
        return False, "data/report-declined.json is missing current input hashes"
    for relative in sorted(required):
        path = data_dir.parent / relative
        if not path.is_file() or hashes.get(relative) != sha256(path):
            return False, f"data/report-declined.json is stale for {relative}"
    return True, None


def run(script: str, case: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / script), str(case)],
        capture_output=True,
        text=True,
    )


def report_stages(case: Path) -> list[dict[str, object]]:
    stages: list[dict[str, object]] = []
    for label, script in (
        ("fact_check", "validate-fact-check.py"),
        ("report_draft", "validate-report-draft.py"),
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
    return stages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--if-ready", action="store_true",
                        help="exit successfully until findings, fact-check, and report draft exist")
    args = parser.parse_args()
    case = Path(args.case_dir)
    data_dir = case / "data"
    ready = all(
        (data_dir / name).is_file()
        for name in ("findings.json", "fact-check.json", "report-draft.json")
    )

    if args.if_ready and not ready:
        declined, decline_error = valid_decline(data_dir)
        advanced = any(path.is_file() for path in (
            data_dir / "ingestion.json",
            data_dir / "monitoring.json",
            case / "closure.md",
        ))
        evidence_ready = (data_dir / "findings.json").is_file() and (data_dir / "fact-check.json").is_file()
        if evidence_ready:
            fact_check = run("validate-fact-check.py", case)
            if fact_check.returncode != 0:
                message = fact_check.stdout.strip() or fact_check.stderr.strip()
                if args.json:
                    print(json.dumps({
                        "passed": False,
                        "skipped": False,
                        "reason": "fact-check evidence validation failed",
                        "detail": message,
                    }))
                else:
                    print(message)
                    print("FAIL  fact-check evidence validation failed before report decision")
                return 3
        if decline_error:
            message = decline_error
            if args.json:
                print(json.dumps({"passed": False, "skipped": False, "reason": message}))
            else:
                print(f"FAIL  {message}")
            return 3
        if evidence_ready and advanced and not declined:
            message = (
                "report phase was skipped: later-phase artifacts exist but data/report-draft.json "
                "and data/report-declined.json are both missing"
            )
            if args.json:
                print(json.dumps({"passed": False, "skipped": False, "reason": message}))
            else:
                print(f"FAIL  {message}")
            return 3
        if args.json:
            reason = "report explicitly declined" if declined else "structured inputs not ready"
            print(json.dumps({"passed": True, "skipped": True, "reason": reason}))
        return 0
    if not case.is_dir():
        print(f"FAIL  case dir not found: {case}")
        return 3

    stages = report_stages(case)

    passed = len(stages) == 4 and all(stage["passed"] for stage in stages)
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
