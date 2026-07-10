#!/usr/bin/env python3
"""Integration checks for deterministic report finalization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-report.py"
DECLINE_REPORT = ROOT / "scripts" / "decline-report.py"
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
                    "url": "https://example.org/official_record(v1)",
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
    write_json(data / "evidence-bundle.json", {
        "schema_version": "1.0",
        "project": "Northwind review",
        "run_id": "fixture-run",
        "created_at": "2026-07-10T12:00:00Z",
        "items": [{
            "id": "E1",
            "query_or_task": "Acquire the official record",
            "acquisition_method": "api",
            "source_url": "https://example.org/official_record(v1)",
            "accessed": "2026-07-10T12:00:00Z",
            "raw_path": "research/official-record.md",
            "sha256": sha(research / "official-record.md"),
            "claim_links": [{
                "finding_id": "F1",
                "claim_text": "Ada Lovelace is President of Northwind Research Cooperative.",
                "support_type": "direct",
            }],
            "extraction_confidence": "high",
            "human_verification_required": False,
        }],
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
                    "source": "https://example.org/official_record(v1)",
                    "access_method": "full_text",
                    "local_file": "research/official-record.md",
                }],
            },
            {
                "id": 2,
                "finding_id": "F2",
                "claim_text": "<script>alert('unsafe')</script> An unsupported second claim.",
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
    write_json(data / "report-draft.json", {
        "schema_version": "1.0",
        "language": "fr",
        "title": "Northwind Research Cooperative: one verified finding, one open question",
        "deck": "<script>alert('unsafe')</script> An unsupported second claim. This claim remains unverified.",
        "framing_finding_ids": ["F2", "F1"],
        "finding_order": ["F2", "F1"],
        "finding_treatments": [
            {
                "finding_id": "F2",
                "headline": "Unsupported second claim remains unverified",
                "summary": "<script>alert('unsafe')</script> An unsupported second claim. This claim remains unverified.",
                "why_it_matters": "This unverified claim should not be presented.",
            },
            {
                "finding_id": "F1",
                "headline": "Ada Lovelace: President",
                "summary": "Ada Lovelace is President of Northwind Research Cooperative.",
                "why_it_matters": "The verified leadership claim matters.",
            },
        ],
        "caveats": [{
            "text": "![proof](https://evil.example/track.png) The second claim remains unverified.",
            "finding_ids": ["F2"],
        }],
        "next_steps": [{
            "text": "Acquire an authoritative source for F2.",
            "finding_ids": ["F2"],
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
        assert "evil.example/track.png" in html
        assert "![proof]" not in markdown
        assert "\\!\\[proof\\]" in markdown
        assert "<https://example.org/official_record(v1)>" in markdown
        assert "AI assistance notice" in html
        assert '<html lang="fr">' in html
        assert "The second claim remains unverified" in html
        assert "Acquire an authoritative source for F2" in html
        assert "Northwind Research Cooperative: one verified finding" in html
        assert "Ada Lovelace: President" in html
        assert markdown.index("| F2 |") < markdown.index("| F1 |")
        assert "| F1 |" in markdown and "| Verified | High |" in markdown
        assert "| F2 |" in markdown and "| Unverified | Low |" in markdown
        assert ledger["claims"][0]["report_confidence"] == "low"
        assert ledger["claims"][1]["fact_check_status"] == "verified"
        assert ledger["claims"][1]["editorial"]["headline"] == "Ada Lovelace: President"
        assert ledger["output_sha256"]["findings-report.md"] == sha(outputs[0])
        assert ledger["output_sha256"]["report.html"] == sha(outputs[1])

        # Multiline model prose remains literal text, never active Markdown structure.
        draft_path = case / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        draft["caveats"][0]["text"] = "Line one\n# Injected section\n---\n| fake | table |"
        write_json(draft_path, draft)
        literal = subprocess.run(
            [sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True
        )
        assert literal.returncode == 0, literal.stdout + literal.stderr
        literal_markdown = (case / "findings-report.md").read_text()
        assert "\n# Injected section" not in literal_markdown
        assert "Line one \\# Injected section \\-\\-\\- \\| fake \\| table \\|" in literal_markdown
        literal_hashes = [sha(path) for path in outputs]

        # Same structured inputs must produce byte-identical outputs.
        again = subprocess.run([sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True)
        assert again.returncode == 0, again.stdout + again.stderr
        assert [sha(path) for path in outputs] == literal_hashes

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

        # Legacy aliases cannot override the canonical fields in claims[].
        case = build_case(Path(tmp) / "mixed-claim-aliases")
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["claims"][0]["claim"] = fact_check["claims"][0]["claim_text"]
        fact_check["claims"][0]["claim_text"] = "A different claim."
        write_json(fact_check_path, fact_check)
        mixed_aliases = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert mixed_aliases.returncode == 3, mixed_aliases.stdout

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

        # An honestly marked inaccessible artifact cannot anchor a positive verdict.
        case = build_case(Path(tmp) / "inaccessible-anchor")
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["claims"][0]["evidence_for"][0]["access_method"] = "inaccessible"
        write_json(fact_check_path, fact_check)
        inaccessible = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert inaccessible.returncode == 3, inaccessible.stdout

        case = build_case(Path(tmp) / "missing-access-method")
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["claims"][0]["evidence_for"][0].pop("access_method")
        write_json(fact_check_path, fact_check)
        missing_access = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert missing_access.returncode == 3, missing_access.stdout

        # Canonical bundles require structured, claim-exact links.
        case = build_case(Path(tmp) / "loose-bundle-link")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text())
        bundle["items"][0]["claim_links"] = ["F1"]
        write_json(bundle_path, bundle)
        loose_bundle = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert loose_bundle.returncode == 3, loose_bundle.stdout

        case = build_case(Path(tmp) / "incomplete-canonical-bundle")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text())
        bundle["items"][0].pop("source_url")
        write_json(bundle_path, bundle)
        incomplete_bundle = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert incomplete_bundle.returncode == 3, incomplete_bundle.stdout

        # Legacy evidence[] metadata may be preserved, but cannot itself verify a claim.
        case = build_case(Path(tmp) / "legacy-bundle-anchor")
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["claims"][0]["evidence_for"][0].pop("local_file")
        fact_check["claims"][0]["evidence_for"][0]["evidence_bundle_id"] = "E1"
        write_json(fact_check_path, fact_check)
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text())
        item = bundle["items"][0]
        write_json(bundle_path, {
            "schema_version": "1.0",
            "project": bundle["project"],
            "evidence": [{
                "id": item["id"],
                "finding_id": "F1",
                "path": item["raw_path"],
            }],
        })
        legacy_bundle = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert legacy_bundle.returncode == 3, legacy_bundle.stdout

        # A prefix, blank line, or BOM cannot disguise an RLM-derived lead as source evidence.
        case = build_case(Path(tmp) / "derived-lead-anchor")
        (case / "research" / "official-record.md").write_text(
            "\ufeffAcquisition note\n\n# RLM-distilled leads from source.raw\nAda Lovelace is President.\n"
        )
        derived = subprocess.run(
            [sys.executable, str(FACT_CHECK_VALIDATOR), str(case)], capture_output=True, text=True
        )
        assert derived.returncode == 3, derived.stdout

        # Every prose block must stay attached to a known fact-checked finding.
        case = build_case(Path(tmp) / "unknown-reference")
        draft_path = case / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        draft["caveats"][0]["finding_ids"] = ["F999"]
        write_json(draft_path, draft)
        unknown = subprocess.run(
            [sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True
        )
        assert unknown.returncode == 3, unknown.stdout
        assert "unknown finding IDs" in unknown.stdout

        # Finding IDs are exact structural keys, not whitespace-normalized prose.
        case = build_case(Path(tmp) / "whitespace-reference")
        draft_path = case / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        draft["finding_order"][0] = " F2 "
        draft["finding_treatments"][0]["finding_id"] = " F2 "
        write_json(draft_path, draft)
        whitespace = subprocess.run(
            [sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True
        )
        assert whitespace.returncode == 3, whitespace.stdout
        assert "surrounding whitespace" in whitespace.stdout
        assert not (case / "report.html").exists()

        # Non-Latin finding IDs receive distinct, stable HTML anchors.
        case = build_case(Path(tmp) / "non-latin-ids")
        findings_path = case / "data" / "findings.json"
        findings = json.loads(findings_path.read_text())
        replacements = {"F1": "发现一", "F2": "发现二"}
        for finding in findings["findings"]:
            finding["id"] = replacements[finding["id"]]
        write_json(findings_path, findings)
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        for claim in fact_check["claims"]:
            claim["finding_id"] = replacements[claim["finding_id"]]
        write_json(fact_check_path, fact_check)
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text())
        for link in bundle["items"][0]["claim_links"]:
            link["finding_id"] = replacements[link["finding_id"]]
        write_json(bundle_path, bundle)
        draft_path = case / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        draft["framing_finding_ids"] = [replacements[item] for item in draft["framing_finding_ids"]]
        draft["finding_order"] = [replacements[item] for item in draft["finding_order"]]
        for treatment in draft["finding_treatments"]:
            treatment["finding_id"] = replacements[treatment["finding_id"]]
        for field in ("caveats", "next_steps"):
            for item in draft[field]:
                item["finding_ids"] = [replacements[ref] for ref in item["finding_ids"]]
        write_json(draft_path, draft)
        localized = subprocess.run(
            [sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True
        )
        assert localized.returncode == 0, localized.stdout + localized.stderr
        anchors = re.findall(r'<section class="finding" id="([^"]+)"', (case / "report.html").read_text())
        assert len(anchors) == 2 and len(set(anchors)) == 2, anchors

        # No report is rendered until the model has authored its plan.
        case = build_case(Path(tmp) / "missing-draft")
        (case / "data" / "report-draft.json").unlink()
        missing = subprocess.run(
            [sys.executable, str(FINALIZER), str(case)], capture_output=True, text=True
        )
        assert missing.returncode == 3, missing.stdout
        assert not (case / "report.html").exists()

        # --if-ready may wait at Gate 1, but must reject a silent Phase 5 skip.
        waiting = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert waiting.returncode == 0, waiting.stdout
        write_json(case / "data" / "monitoring.json", {"status": "active"})
        skipped = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert skipped.returncode == 3, skipped.stdout
        assert "report phase was skipped" in skipped.stdout
        # A malformed marker fails closed rather than disabling the guard.
        write_json(case / "data" / "report-declined.json", {})
        malformed = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert malformed.returncode == 3, malformed.stdout

        recorded = subprocess.run(
            [sys.executable, str(DECLINE_REPORT), str(case)], capture_output=True, text=True
        )
        assert recorded.returncode == 0, recorded.stdout
        declined = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert declined.returncode == 0, declined.stdout

        # A decline is tied to the current inputs and cannot survive later edits.
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text())
        fact_check["project"] = "Changed after decision"
        write_json(fact_check_path, fact_check)
        stale = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert stale.returncode == 3, stale.stdout
        assert "stale" in stale.stdout

        # Even a correctly hashed decline cannot bypass a failing evidence validator.
        case = build_case(Path(tmp) / "invalid-evidence-decline")
        (case / "data" / "report-draft.json").unlink()
        (case / "research" / "official-record.md").unlink()
        write_json(case / "data" / "monitoring.json", {"status": "active"})
        write_json(case / "data" / "report-declined.json", {
            "schema_version": "1.0",
            "decision": "declined",
            "input_sha256": {
                "data/findings.json": sha(case / "data" / "findings.json"),
                "data/fact-check.json": sha(case / "data" / "fact-check.json"),
            },
        })
        invalid_decline = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert invalid_decline.returncode == 3, invalid_decline.stdout
        assert "fact-check evidence validation failed" in invalid_decline.stdout
        helper_decline = subprocess.run(
            [sys.executable, str(DECLINE_REPORT), str(case)], capture_output=True, text=True
        )
        assert helper_decline.returncode == 3, helper_decline.stdout

        # Entering Phase 6 is visible even when no monitoring or closure follows.
        case = build_case(Path(tmp) / "ingestion-only-skip")
        (case / "data" / "report-draft.json").unlink()
        write_json(case / "data" / "ingestion.json", {
            "schema_version": "1.0", "status": "pending"
        })
        ingestion_skip = subprocess.run(
            [sys.executable, str(FINALIZER), str(case), "--if-ready"],
            capture_output=True, text=True,
        )
        assert ingestion_skip.returncode == 3, ingestion_skip.stdout
        assert "report phase was skipped" in ingestion_skip.stdout

    print("deterministic report finalizer checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
