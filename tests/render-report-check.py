#!/usr/bin/env python3
"""Integration checks for deterministic report finalization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-report.py"
FACT_CHECK_VALIDATOR = ROOT / "scripts" / "validate-fact-check.py"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def build_case(root: Path) -> Path:
    case = root / "case"
    data = case / "data"
    research = case / "research"
    data.mkdir(parents=True)
    research.mkdir()
    (research / "official-record.md").write_text(
        "Northwind Research Cooperative appointed Ada Lovelace as its President.\n"
    )
    write_json(data / "findings.json", {
        "schema_version": "1.0",
        "project": "Northwind <script>alert(1)</script> review",
        "lead": "Check leadership without trusting <b>markup</b>.",
        "investigated_at": "2026-07-10T12:00:00Z",
        "findings": [
            {
                "id": "F1",
                "claim": "Ada Lovelace is President of Northwind Research Cooperative.",
                "evidence": "The official record names Ada Lovelace as President.",
                "sources": [{
                    "url": "https://example.org/official-record",
                    "local_file": "research/official-record.md",
                    "type": "government",
                }],
                "confidence": "high",
                "evidence_bundle_refs": ["E1"],
            },
            {
                "id": "F2",
                "claim": "<script>alert('unsafe')</script> An unsupported second claim.",
                "evidence": "No supporting evidence was acquired.",
                "sources": [{"url": "javascript:alert(1)"}],
                "confidence": "high",
            },
        ],
        "gaps": ["Acquire an authoritative source for F2."],
    })
    # Current schema: claims/verdict, not the older fact_checks/status shape.
    write_json(data / "fact-check.json", {
        "schema_version": "1.0",
        "project": "Northwind review",
        "claims": [
            {
                "id": 1,
                "finding_id": "F1",
                "claim_text": "Ada Lovelace is President of Northwind Research Cooperative.",
                "verdict": "verified",
                "confidence": "high",
                "evidence_for": [{
                    "description": "The official record confirms the appointment.",
                    "source": "https://example.org/official-record",
                    "local_file": "research/official-record.md",
                }],
            },
            {
                "id": 2,
                "finding_id": "F2",
                "claim_text": "An unsupported second claim.",
                "verdict": "unverified",
                "confidence": "high",
                "notes": "No source supports this claim.",
            },
        ],
    })
    write_json(data / "methodology.json", {
        "schema_version": "1.0",
        "project": "Northwind review",
        "investigation_plan": [{
            "direction": "Leadership verification",
            "questions": ["Who holds the recorded role?"],
            "steps": [{"order": 1, "action": "Inspect the official record.", "tool": "fetch"}],
        }],
    })
    return case


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="render-report-") as tmp:
        case = build_case(Path(tmp))
        result = subprocess.run([sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

        outputs = [case / "findings-report.md", case / "report.html", case / "evidence-map.json"]
        first_hashes = [sha(path) for path in outputs]
        html = outputs[1].read_text()
        markdown = outputs[0].read_text()
        ledger = json.loads(outputs[2].read_text())

        assert "{{" not in html
        assert "<script>alert('unsafe')</script>" not in html
        assert "&lt;script&gt;alert" in html
        assert "<script>alert('unsafe')</script>" not in markdown
        assert "&lt;script&gt;alert" in markdown
        assert "javascript:alert" not in html
        assert "AI assistance notice" in html
        assert "| F1 |" in markdown and "| Verified | High |" in markdown
        assert "| F2 |" in markdown and "| Unverified | Low |" in markdown
        assert ledger["claims"][0]["fact_check_status"] == "verified"
        assert ledger["claims"][1]["report_confidence"] == "low"

        # Same structured inputs must produce byte-identical outputs.
        again = subprocess.run([sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True)
        assert again.returncode == 0, again.stdout + again.stderr
        assert [sha(path) for path in outputs] == first_hashes

        # Current-schema completeness is enforced: silently dropping F2 is a fail.
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["claims"] = fact_check["claims"][:1]
        write_json(fact_check_path, fact_check)
        validation = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert validation.returncode == 3
        assert "finding F2 has no fact-check verdict" in validation.stdout

        # A failing evidence chain must not overwrite already-rendered deliverables.
        before_failure = [sha(path) for path in outputs]
        failed = subprocess.run([sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True)
        assert failed.returncode == 3
        assert [sha(path) for path in outputs] == before_failure

        # A fact-check cannot verify a different/easier claim under F1.
        case = build_case(Path(tmp) / "claim-mismatch")
        findings_path = case / "data" / "findings.json"
        findings = json.loads(findings_path.read_text())
        findings["findings"][0]["claim"] = "Ada Lovelace diverted all Northwind funds."
        write_json(findings_path, findings)
        mismatch = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert mismatch.returncode == 3, mismatch.stdout

        # An absolute source outside the case cannot anchor a verified verdict.
        case = build_case(Path(tmp) / "path-escape")
        outside = Path(tmp) / "outside.md"
        outside.write_text("Ada Lovelace is President of Northwind Research Cooperative.\n")
        (case / "research" / "official-record.md").unlink()
        findings_path = case / "data" / "findings.json"
        findings = json.loads(findings_path.read_text())
        findings["findings"][0]["sources"][0]["local_file"] = str(outside)
        write_json(findings_path, findings)
        escaped = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert escaped.returncode == 3, escaped.stdout

    print("deterministic report finalizer checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
