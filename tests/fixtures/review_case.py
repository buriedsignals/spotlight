#!/usr/bin/env python3
"""Build an offline case paused at one review gate, for the review-page checks.

Usage: review_case.py CASE_DIR {methodology,investigation_gate,report,ingest}
       [--excerpt TEXT] [--clean]

Earlier gates are passed on the command-line channel, the same path the
current skills use. ``--excerpt`` places hostile case text in the finding's
claim and evidence. Without ``--clean`` the Investigation gate shows the shared
fixture as it is: one unverified finding (an open disposition) and a log the
case validator rejects (a blocking failure). ``--clean`` marks the finding
verified and writes a valid log, so nothing blocks approval.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
STAGES = ("methodology", "investigation_gate", "report", "ingest")


def conformance_fixtures():
    spec = importlib.util.spec_from_file_location(
        "spotlight_conformance_fixtures", ROOT / "tests" / "orchestration-conformance-check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, check=False, cwd=ROOT
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build(case: Path, stage: str, excerpt: str | None, clean: bool) -> None:
    fixtures = conformance_fixtures()
    (case / "data").mkdir(parents=True)
    (case / "brief-directions.txt").write_text("Verify the offline fixture.\n", encoding="utf-8")
    write_json(case / "data/methodology.json", fixtures.methodology())
    if stage == "methodology":
        return
    approval = ("--approved-by", "journalist:fixture", "--approved-at", "2026-08-23T12:00:00Z")
    run(str(SEAM), "approve", "methodology", *approval, str(case))
    fixtures.investigation_outputs(case)
    if excerpt is not None or clean:
        findings = json.loads((case / "data/findings.json").read_text(encoding="utf-8"))
        check = json.loads((case / "data/fact-check.json").read_text(encoding="utf-8"))
        if excerpt is not None:
            findings["findings"][0]["evidence"] = excerpt
            findings["findings"][0]["sources"][0]["description"] = excerpt
        if clean:
            check["claims"][0]["verdict"] = "verified"
            check["claims"][0]["evidence_for"] = [{
                "description": "Primary record states the claim.",
                "source": "research/primary-record.txt",
                "access_method": "full_text",
                "local_file": "research/primary-record.txt",
            }]
            log = json.loads((case / "data/investigation-log.json").read_text(encoding="utf-8"))
            log["cycles"][0]["sources_consulted"] = [
                {"url": source["url"], "type": "file", "accessed": "2026-08-23T11:00:00Z", "useful": True}
                for source in findings["findings"][0]["sources"]
            ]
            write_json(case / "data/investigation-log.json", log)
        write_json(case / "data/findings.json", findings)
        write_json(case / "data/fact-check.json", check)
    if stage == "investigation_gate":
        return
    run(str(SEAM), "approve", "investigation_gate", *approval, str(case))
    run(str(ROOT / "scripts" / "build-provenance-manifest.py"), str(case))
    (case / "review.html").write_text("<!doctype html><title>Offline review</title>\n", encoding="utf-8")
    run(str(SEAM), "seal-investigation-gate", str(case))
    if stage == "report":
        return
    run(str(SEAM), "decide-report", "declined", str(case))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--excerpt")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    build(Path(args.case_dir), args.stage, args.excerpt, args.clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
