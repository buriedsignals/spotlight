#!/usr/bin/env python3
"""scripts/validate-indicator-framework.py: references resolve and no scores."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
from indicator_framework import document  # noqa: E402

SCRIPT = ROOT / "scripts" / "validate-indicator-framework.py"


def check(case: Path, framework: dict, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    (case / "data" / "indicator-framework.json").write_text(json.dumps(framework), encoding="utf-8")
    return subprocess.run([sys.executable, str(script), str(case)], capture_output=True, text=True, check=False)


# The framework's values live only in the Lab-gated bundle: the validator
# checks them when framework.json sits beside it there, and the public
# checkout, which carries none of them, checks structure only.
def check_framework_values(case: Path) -> None:
    bundle = case.parent / "bundle"
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "assets").mkdir()
    shutil.copy(SCRIPT, bundle / "scripts" / SCRIPT.name)
    (bundle / "assets" / "framework.json").write_text(json.dumps({
        "matrix": [{"quadrant": "Q-anchor"}, {"quadrant": "Q-context"}],
        "pairing_statuses": ["confirmed_theirs", "not_applicable"],
        "item_kinds": [{"kind": "kind-a"}, {"kind": "kind-b"}],
    }), encoding="utf-8")
    in_bundle = bundle / "scripts" / SCRIPT.name
    good = check(case, document(), in_bundle)
    assert good.returncode == 0, good.stdout

    bad = document()
    bad["indicator_gaps"][0]["kind"] = "kind-z"
    bad["indicators"][0]["current_rating"]["quadrant"] = "Q-other"
    result = check(case, bad, in_bundle)
    assert result.returncode == 3, result.stdout
    assert "item t-1: kind 'kind-z' is not one of kind-a, kind-b" in result.stdout, result.stdout
    assert "indicator ind-1 current_rating: quadrant 'Q-other' is not one of Q-anchor, Q-context" in result.stdout, result.stdout
    assert check(case, bad).returncode == 0, "the public checkout has no values to check"


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        case = Path(temp) / "case"
        (case / "data").mkdir(parents=True)
        (case / "data" / "findings.json").write_text(json.dumps({"findings": [{"id": "F1", "claim": "x"}]}), encoding="utf-8")
        good = check(case, document())
        assert good.returncode == 0, good.stdout

        bad = document()
        bad["indicators"][0]["finding_ids"] = ["F9"]
        bad["indicators"][1]["current_rating"]["relevance_score"] = 7
        result = check(case, bad)
        assert result.returncode == 3, result.stdout
        assert "indicator ind-1 finding_ids: F9 is not a finding in data/findings.json" in result.stdout, result.stdout
        assert "/indicators/1/current_rating/relevance_score: the Indicator Framework carries no scores" in result.stdout, result.stdout
        check_framework_values(case)
    print("indicator framework check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
