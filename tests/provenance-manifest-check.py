#!/usr/bin/env python3
"""Regression checks for Spotlight provenance manifest generation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_fixture_case(tmp: Path) -> Path:
    case_dir = tmp / "sample-investigation"
    data_dir = case_dir / "data"
    research_dir = case_dir / "research"
    data_dir.mkdir(parents=True)
    research_dir.mkdir()

    fixtures = ROOT / "tests" / "fixtures"
    shutil.copy(fixtures / "findings.sample.json", data_dir / "findings.json")
    shutil.copy(fixtures / "fact-check.sample.json", data_dir / "fact-check.json")
    shutil.copy(fixtures / "evidence-bundle.sample.json", data_dir / "evidence-bundle.json")
    (data_dir / "investigation-log.json").write_text(
        json.dumps({"schema_version": "1.0", "entries": []}) + "\n",
        encoding="utf-8",
    )
    (case_dir / "summary.md").write_text("# Sample Investigation\n", encoding="utf-8")
    return case_dir


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_tmp:
        case_dir = copy_fixture_case(Path(raw_tmp))
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build-provenance-manifest.py"),
                str(case_dir),
            ],
            check=True,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )
        manifest_path = case_dir / "data" / "provenance-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "1.0"
    assert manifest["project"] == "sample-investigation"
    assert manifest["status"] == "unsigned"
    assert manifest["signing"]["requires_api_key"] is False
    assert manifest["signing"]["requires_signing_credential"] is True
    assert {artifact["kind"] for artifact in manifest["case_artifacts"]} >= {
        "summary",
        "findings",
        "fact_check",
        "evidence_bundle",
        "investigation_log",
    }
    assert manifest["claims"]
    assert manifest["sources"]
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["case_artifacts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
