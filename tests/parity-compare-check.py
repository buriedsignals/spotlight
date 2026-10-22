#!/usr/bin/env python3
"""The parity comparer passes a run inside the written tolerances and fails each breach."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_check = load("render_report_check", ROOT / "tests" / "render-report-check.py")
parity = load("parity_compare", ROOT / "evals" / "parity" / "compare.py")


def run_case(root: Path, case_id: str, cycles: int = 1, all_verified: bool = True) -> Path:
    case = render_check.build_case(root / f"build-{case_id}").rename(root / f"gold-{case_id}")
    if all_verified:
        fact_check = json.loads((case / "data" / "fact-check.json").read_text())
        rows = parity.verdict_rows(fact_check)
        support = next(row["evidence_for"] for row in rows if row.get("evidence_for"))
        for row in rows:
            row["verdict"] = "verified"
            row["evidence_for"] = row.get("evidence_for") or support
        (case / "data" / "fact-check.json").write_text(json.dumps(fact_check))
    findings = json.loads((case / "data" / "findings.json").read_text())
    (case / "data" / "investigation-log.json").write_text(json.dumps({
        "schema_version": "1.0", "project": findings["project"],
        "cycles": [{"cycle": n + 1, "timestamp": "2026-10-22T10:00:00Z", "focus": "parity",
                    "methodology": {"techniques_used": [], "tools_used": [], "search_queries": [],
                                    "failed_approaches": []},
                    "findings_added": 0, "gaps_remaining": [], "sources_consulted": []}
                   for n in range(cycles)],
    }))
    return case


def baseline_for(case: Path, **overrides) -> dict:
    measured = parity.measure(case)
    entry = {"id": case.name.removeprefix("gold-"), "findings": measured["findings"], "cycles": 1}
    entry.update(overrides)
    return {"cases": [entry]}


def failures(baseline: dict, root: Path) -> list[str]:
    return parity.compare(baseline, root)[1]


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        case = run_case(root, "a")
        assert parity.measure(case)["validators_pass"], "fixture case must validate"
        assert failures(baseline_for(case), root) == [], failures(baseline_for(case), root)

        found = parity.measure(case)["findings"]
        assert any("findings, expected" in f for f in failures(baseline_for(case, findings=found + parity.FINDINGS_BELOW + 1), root))
        assert any("findings, expected" in f for f in failures(baseline_for(case, findings=found - parity.FINDINGS_ABOVE - 1), root))
        assert any("no run at" in f for f in failures({"cases": [{"id": "missing", "findings": 1, "cycles": 1}]}, root))

        mixed = run_case(root / "mixed", "a", all_verified=False)
        assert parity.measure(mixed)["verified"] < parity.measure(mixed)["claims"]
        assert any("verified share" in f for f in failures(baseline_for(mixed), root / "mixed"))

        two_cycles = run_case(root / "cycles", "a", cycles=2)
        assert any("cycles, baseline 1" in f for f in failures(baseline_for(two_cycles), root / "cycles"))

        log = case / "data" / "investigation-log.json"
        document = json.loads(log.read_text())
        document.pop("project")
        log.write_text(json.dumps(document))
        assert any("validate-case.py fails" in f for f in failures(baseline_for(case), root))
    print("parity compare check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
