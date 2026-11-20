#!/usr/bin/env python3
"""The install archive (`git archive`, the payload Indicator Labs installs)
leaves out development and website files and still runs.

    git archive HEAD | tar -x -C <dir>
    python3 tests/archive-check.py <dir>

Checks the unpacked tree: tests, evals and the website are absent; the files
the runtime reads by path are present; the archive's own case validator and
integration preflight run on it, fed the checkout's fixtures. The CI job
"Install archive" also boots the Pi harness from the same tree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

# Development and website files an install never needs.
ABSENT = ["tests", "evals", ".github", "install-spotlight.sh", "CONTRIBUTING.md",
          "index.html", "docs/index.html", "going-sovereign", "components", "CNAME"]
# Read at runtime by path (research_checks.py, validate-methodology-navigator.py,
# skills/arbiter, the Pi harness), so a broader denylist would break an install.
PRESENT = ["AGENTS.md", "LICENSE", "NOTICE.md", "third_party", "lanes", "upstreams",
           "docs/runtimes.md", "skills.manifest", "scripts/spotlight-python",
           "harness/pi/package.json", "harness/pi/package-lock.json", "harness/review/mcp.mjs"]

INVESTIGATION_LOG = {
    "schema_version": "1.0",
    "project": "sample-investigation",
    "cycles": [{
        "cycle": 1,
        "timestamp": "2026-04-17T15:00:00Z",
        "focus": "sample validation",
        "methodology": {"techniques_used": ["registry lookup"], "tools_used": ["firecrawl"],
                        "search_queries": ["Example Corp Ltd BVI"], "failed_approaches": []},
        "sources_consulted": [{"url": "https://bvifsc.example/filings/2019-ABC-123", "type": "registry",
                               "accessed": "2026-04-17T12:03:00Z", "useful": True}],
        "findings_added": 1,
        "findings_upgraded": 0,
        "gaps_resolved": [],
        "gaps_remaining": ["Primary UBO disclosure"],
        "notes": "Sample log.",
    }],
}


def run(archive: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=archive, text=True, capture_output=True, timeout=300, check=False)


def sample_case(directory: Path, *, empty_claim: bool = False) -> Path:
    data = directory / "data"
    data.mkdir(parents=True)
    findings = json.loads((FIXTURES / "findings.sample.json").read_text(encoding="utf-8"))
    if empty_claim:
        findings["findings"][0]["claim"] = ""
    (data / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
    shutil.copy(FIXTURES / "fact-check.sample.json", data / "fact-check.json")
    shutil.copy(FIXTURES / "evidence-bundle.sample.json", data / "evidence-bundle.json")
    (data / "investigation-log.json").write_text(json.dumps(INVESTIGATION_LOG), encoding="utf-8")
    return directory


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print("usage: archive-check.py <unpacked archive>", file=sys.stderr)
        return 2
    archive = Path(sys.argv[1]).resolve()
    failures: list[str] = []

    failures += [f"{path} is in the archive" for path in ABSENT if (archive / path).exists()]
    failures += [f"{path} is missing from the archive" for path in PRESENT if not (archive / path).exists()]

    with tempfile.TemporaryDirectory() as raw:
        good = run(archive, "scripts/validate-case.py", str(sample_case(Path(raw) / "good")))
        if good.returncode != 0:
            failures.append(f"validate-case.py rejected the sample case: {good.stdout[-800:]}{good.stderr[-800:]}")
        bad = run(archive, "scripts/validate-case.py", str(sample_case(Path(raw) / "bad", empty_claim=True)))
        if bad.returncode != 1:  # 1 is a validation failure; anything else means it could not run
            failures.append(f"validate-case.py did not reject an empty claim (rc={bad.returncode}): {bad.stderr[-800:]}")

    preflight = run(archive, "integrations/preflight.py", "--json")
    try:
        reported = {item["id"] for item in json.loads(preflight.stdout)["integrations"]}
    except (ValueError, KeyError, TypeError):
        reported = None
    shipped = {path.parent.name for path in (archive / "integrations").glob("*/manifest.json")}
    if preflight.returncode not in (0, 1) or reported != shipped:
        failures.append(f"integrations/preflight.py crashed or omitted an integration (rc={preflight.returncode}): {preflight.stderr[-800:]}")

    if failures:
        for failure in failures:
            print(f"FAIL  {failure}", file=sys.stderr)
        return 1
    print("archive checks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
