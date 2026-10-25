#!/usr/bin/env python3
"""scripts/validate-indicator-framework.py: references resolve and no scores."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
from indicator_framework import document  # noqa: E402

SCRIPT = ROOT / "scripts" / "validate-indicator-framework.py"


def check(case: Path, framework: dict) -> subprocess.CompletedProcess[str]:
    (case / "data" / "indicator-framework.json").write_text(json.dumps(framework), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(case)], capture_output=True, text=True, check=False)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        case = Path(temp)
        (case / "data").mkdir()
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
    print("indicator framework check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
