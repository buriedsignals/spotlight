#!/usr/bin/env python3
"""Regression checks for the deterministic report gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-report.py"
TEMPLATE = ROOT / "skills" / "report-drafting" / "references" / "report-template.html"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_report", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_case(root: Path) -> Path:
    case = root / "case"
    data = case / "data"
    data.mkdir(parents=True)
    (case / "findings-report.md").write_text(
        "# Findings Report\n\n" + ("Grounded reporting context. " * 25)
        + "\n\n| ID | Claim | Confidence |\n|---|---|---|\n"
        + "| F1 | Ethereum Foundation finding | Low |\n"
    )
    (case / "evidence-map.json").write_text("{}\n")
    (data / "findings.json").write_text(json.dumps({
        "findings": [{"id": "F1", "claim": "The Ethereum Foundation is based in Zug."}]
    }))
    (data / "fact-check.json").write_text(json.dumps({
        "fact_checks": [{
            "id": "FC1",
            "finding_id": "F1",
            "claim": "The Ethereum Foundation is based in Zug.",
            "status": "unverified",
            "confidence": "low",
            "verification_evidence": "Unverified in this fixture.",
        }]
    }))
    return case


def main() -> int:
    validator = load_validator()
    with tempfile.TemporaryDirectory(prefix="validate-report-") as tmp:
        case = make_case(Path(tmp))
        report = case / "report.html"

        shutil.copyfile(TEMPLATE, report)
        failures = validator.check(case)
        assert any("byte-identical" in failure for failure in failures), failures

        # Regression: a one-line cosmetic edit must not turn the empty template
        # into a passing report.
        text = report.read_text().replace(
            "<title>{{INVESTIGATION TITLE}} — {{OUTLET}}</title>",
            "<title>Ethereum Foundation — Buried Signals</title>",
            1,
        )
        report.write_text(text)
        failures = validator.check(case)
        assert any("unresolved template placeholder" in failure for failure in failures), failures

        report.write_text(
            "<!doctype html><html><head><title>Ethereum Foundation — Buried Signals</title>"
            "</head><body><h1>Ethereum Foundation</h1><p>Grounded case report.</p></body></html>"
        )
        failures = validator.check(case)
        assert not failures, failures

    print("validate-report regression checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
