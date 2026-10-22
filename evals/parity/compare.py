#!/usr/bin/env python3
"""Compare a harness's gold-case runs with the recorded Flue baseline.

    python3 evals/parity/compare.py RUNS_ROOT [--baseline evals/parity/baseline.json]

RUNS_ROOT holds one case folder per baseline case, named ``gold-<id>``. The
tolerances are fixed in this file and in README.md before any run; changing
them after seeing a run's results defeats the comparison.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate-case.py"

# Written down before the first Pi run (see README.md).
FINDINGS_BELOW = 1          # a case may have at most one finding fewer than the baseline
FINDINGS_ABOVE = 2          # ... and at most two more
TOTAL_FINDINGS_SHORTFALL = 3
MIN_VERIFIED_SHARE = 0.75   # across all cases' fact-checked claims


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verdict_rows(fact_check: dict) -> list[dict]:
    for key in ("claims", "verdicts", "results", "checks"):
        if isinstance(fact_check.get(key), list):
            return fact_check[key]
    return []


def measure(case: Path) -> dict:
    data = case / "data"
    findings = load_json(data / "findings.json").get("findings", [])
    rows = verdict_rows(load_json(data / "fact-check.json"))
    cycles = load_json(data / "investigation-log.json").get("cycles", [])
    validated = subprocess.run(
        [sys.executable, str(VALIDATOR), str(case)], capture_output=True, text=True, check=False
    )
    return {
        "findings": len(findings),
        "claims": len(rows),
        "verified": sum(1 for row in rows if row.get("verdict") == "verified"),
        "cycles": len(cycles),
        "validators_pass": validated.returncode == 0,
    }


def compare(baseline: dict, runs_root: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    failures: list[str] = []
    total_base = total_run = claims = verified = 0
    for expected in baseline["cases"]:
        case = runs_root / f"gold-{expected['id']}"
        if not (case / "data").is_dir():
            failures.append(f"{expected['id']}: no run at {case}")
            continue
        try:
            got = measure(case)
        except (OSError, ValueError) as exc:
            failures.append(f"{expected['id']}: unreadable run output ({exc})")
            continue
        rows.append({"id": expected["id"], "baseline": expected, "run": got})
        total_base += expected["findings"]
        total_run += got["findings"]
        claims += got["claims"]
        verified += got["verified"]
        if not got["validators_pass"]:
            failures.append(f"{expected['id']}: validate-case.py fails")
        low = max(1, expected["findings"] - FINDINGS_BELOW)
        high = expected["findings"] + FINDINGS_ABOVE
        if not low <= got["findings"] <= high:
            failures.append(
                f"{expected['id']}: {got['findings']} findings, expected {low}-{high}"
            )
        if got["cycles"] > expected["cycles"]:
            failures.append(
                f"{expected['id']}: {got['cycles']} cycles, baseline {expected['cycles']}"
            )
    if total_run < total_base - TOTAL_FINDINGS_SHORTFALL:
        failures.append(f"total findings {total_run}, baseline {total_base}")
    if claims and verified / claims < MIN_VERIFIED_SHARE:
        failures.append(f"verified share {verified}/{claims} below {MIN_VERIFIED_SHARE}")
    if rows and not claims:
        failures.append("no fact-checked claims in any run")
    return rows, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path(__file__).with_name("baseline.json"))
    args = parser.parse_args(argv)
    rows, failures = compare(load_json(args.baseline), args.runs_root)
    for row in rows:
        base, run = row["baseline"], row["run"]
        print(
            f"{row['id']:<26} findings {run['findings']} (base {base['findings']})  "
            f"verified {run['verified']}/{run['claims']}  cycles {run['cycles']} "
            f"(base {base['cycles']})  validators {'pass' if run['validators_pass'] else 'FAIL'}"
        )
    for failure in failures:
        print(f"FAIL  {failure}")
    print("parity: PASS" if not failures else f"parity: FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
