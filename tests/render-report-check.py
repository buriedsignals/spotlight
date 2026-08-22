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


def fingerprint(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def activate_case(case: Path) -> None:
    data = case / "data"
    findings_path = data / "findings.json"
    fact_check_path = data / "fact-check.json"
    bundle_path = data / "evidence-bundle.json"
    source_path = data / "source-expressions.json"
    anchor_path = case / "research" / "official-record.md"

    # Include hostile-looking source text to prove quotations are resolved from
    # the canonical expression and escaped, never accepted from model prose.
    expression_text = (
        "Northwind <em>Research</em> Cooperative appointed Ada Lovelace as its President."
    )
    anchor_path.write_text(expression_text + "\n")

    findings = json.loads(findings_path.read_text())
    findings["schema_version"] = "1.1"
    finding_fingerprints: dict[str, str] = {}
    for finding in findings["findings"]:
        finding_fp = fingerprint({"claim": finding["claim"]})
        finding["finding_fingerprint"] = finding_fp
        finding_fingerprints[finding["id"]] = finding_fp
    write_json(findings_path, findings)

    bundle = json.loads(bundle_path.read_text())
    bundle["items"][0]["sha256"] = sha(anchor_path)
    write_json(bundle_path, bundle)

    core = {
        "text": expression_text,
        "anchor_ref": {
            "path": "research/official-record.md",
            "line_start": 1,
            "line_end": 1,
        },
        "anchor_sha256": sha(anchor_path),
        "original_evidence_bundle_id": "E1",
        "original_artifact_sha256": sha(anchor_path),
        "language": "en",
        "attribution": "Northwind registry <editor>",
        "direct_quote": True,
    }
    expression_fp = fingerprint(core)
    link_payload = {
        "expression_fingerprint": expression_fp,
        "finding_fingerprint": finding_fingerprints["F1"],
        "finding_id": "F1",
        "relation": "supports",
    }
    link_fp = fingerprint(link_payload)
    write_json(source_path, {
        "schema_version": "1.0",
        "project": findings["project"],
        "created_at": "2026-07-16T12:00:00Z",
        "expressions": [{
            "id": "SX1",
            **core,
            "expression_fingerprint": expression_fp,
            "finding_links": [{**link_payload, "link_fingerprint": link_fp}],
            "lifecycle_events": [{
                "event": "activated",
                "timestamp": "2026-07-16T12:00:00Z",
                "actor": "investigator",
                "reason": "Exact registry passage captured.",
            }],
            "created_by": "investigator",
            "cycle": 1,
        }],
    })

    fact_check = json.loads(fact_check_path.read_text())
    fact_check["claims"][0]["evidence_for"][0]["source_expression_refs"] = [{
        "expression_id": "SX1",
        "expression_fingerprint": expression_fp,
        "finding_fingerprint": finding_fingerprints["F1"],
        "link_fingerprint": link_fp,
    }]
    write_json(fact_check_path, fact_check)

    draft_path = data / "report-draft.json"
    draft = json.loads(draft_path.read_text())
    next(item for item in draft["finding_treatments"] if item["finding_id"] == "F1")[
        "quote_selections"
    ] = [{"expression_id": "SX1"}]
    write_json(draft_path, draft)

    artifact_hashes = {
        "findings_sha256": sha(findings_path),
        "fact_check_sha256": sha(fact_check_path),
        "evidence_bundle_sha256": sha(bundle_path),
        "source_expressions_sha256": sha(source_path),
    }
    write_json(data / "case-contract.json", {
        "schema_version": "1.0",
        "project": findings["project"],
        "current_contract_version": "1.1",
        "activation_events": [{
            "event_id": "ACT1",
            "previous_contract_version": "1.0",
            "activated_contract_version": "1.1",
            "activated_at": "2026-07-16T12:10:00Z",
            "tool_version": "test-1",
            "prior_input_hashes": artifact_hashes,
            "activated_artifact_hashes": artifact_hashes,
        }],
    })


def refresh_expression_contract_hash(case: Path) -> None:
    contract_path = case / "data" / "case-contract.json"
    contract = json.loads(contract_path.read_text())
    contract["activation_events"][-1]["activated_artifact_hashes"][
        "source_expressions_sha256"
    ] = sha(case / "data" / "source-expressions.json")
    write_json(contract_path, contract)


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
        assert "mermaid@11.16.1" not in html
        assert '<section class="report-diagrams"' not in html
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

        # Activated reports resolve expression-ID-only selections to canonical,
        # escaped source text and preserve passage-level evidence-map provenance.
        activated = build_case(Path(tmp) / "activated-report")
        activate_case(activated)
        activated_result = subprocess.run(
            [sys.executable, str(FINALIZER), str(activated)], capture_output=True, text=True
        )
        assert activated_result.returncode == 0, activated_result.stdout + activated_result.stderr
        activated_outputs = [
            activated / "findings-report.md",
            activated / "report.html",
            activated / "evidence-map.json",
        ]
        activated_html = activated_outputs[1].read_text()
        activated_markdown = activated_outputs[0].read_text()
        activated_ledger = json.loads(activated_outputs[2].read_text())
        assert "Northwind <em>Research</em>" not in activated_html
        assert "Northwind &lt;em&gt;Research&lt;/em&gt;" in activated_html
        assert "Northwind &lt;em&gt;Research&lt;/em&gt;" in activated_markdown
        assert "Northwind registry &lt;editor&gt;" in activated_html
        assert activated_ledger["input_sha256"]["data/source-expressions.json"] == sha(
            activated / "data" / "source-expressions.json"
        )
        expression_ledger = activated_ledger["source_expressions"][0]
        assert expression_ledger["id"] == "SX1"
        assert expression_ledger["anchor_ref"]["line_start"] == 1
        assert expression_ledger["anchor_sha256"] == sha(
            activated / "research" / "official-record.md"
        )
        assert expression_ledger["lifecycle_state"] == "active"
        expression_ref = next(
            item for item in activated_ledger["claims"] if item["id"] == "F1"
        )["source_expression_refs"][0]
        assert expression_ref == {
            "expression_id": "SX1",
            "relation": "supports",
            "selected_quote": True,
        }
        activated_hashes = [sha(path) for path in activated_outputs]
        activated_again = subprocess.run(
            [sys.executable, str(FINALIZER), str(activated)], capture_output=True, text=True
        )
        assert activated_again.returncode == 0, activated_again.stdout + activated_again.stderr
        assert [sha(path) for path in activated_outputs] == activated_hashes

        # Tampering with an activated anchor blocks finalization and cannot replace
        # the last valid artifacts.
        (activated / "research" / "official-record.md").write_text("tampered\n")
        tampered = subprocess.run(
            [sys.executable, str(FINALIZER), str(activated)], capture_output=True, text=True
        )
        assert tampered.returncode == 3, tampered.stdout + tampered.stderr
        assert [sha(path) for path in activated_outputs] == activated_hashes

        # The model can select an ID only; quote text or attribution cannot enter
        # through report-draft.json.
        injected_quote = build_case(Path(tmp) / "injected-expression-quote")
        activate_case(injected_quote)
        draft_path = injected_quote / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        selection = next(
            item for item in draft["finding_treatments"] if item["finding_id"] == "F1"
        )["quote_selections"][0]
        selection["text"] = "model-authored replacement"
        write_json(draft_path, draft)
        injected = subprocess.run(
            [sys.executable, str(FINALIZER), str(injected_quote)], capture_output=True, text=True
        )
        assert injected.returncode == 3, injected.stdout + injected.stderr
        assert "expression_id only" in injected.stdout
        assert not (injected_quote / "report.html").exists()

        dangling_quote = build_case(Path(tmp) / "dangling-expression-quote")
        activate_case(dangling_quote)
        draft_path = dangling_quote / "data" / "report-draft.json"
        draft = json.loads(draft_path.read_text())
        next(
            item for item in draft["finding_treatments"] if item["finding_id"] == "F1"
        )["quote_selections"] = [{"expression_id": "SX404"}]
        write_json(draft_path, draft)
        dangling = subprocess.run(
            [sys.executable, str(FINALIZER), str(dangling_quote)], capture_output=True, text=True
        )
        assert dangling.returncode == 3, dangling.stdout + dangling.stderr
        assert "unknown source expression" in dangling.stdout
        assert not (dangling_quote / "report.html").exists()

        missing_expression = build_case(Path(tmp) / "missing-source-expressions")
        activate_case(missing_expression)
        (missing_expression / "data" / "source-expressions.json").unlink()
        missing_expression_result = subprocess.run(
            [sys.executable, str(FINALIZER), str(missing_expression)],
            capture_output=True,
            text=True,
        )
        assert missing_expression_result.returncode == 3
        assert "source-expressions.json" in (
            missing_expression_result.stdout + missing_expression_result.stderr
        )
        assert not (missing_expression / "report.html").exists()

        withdrawn_case = build_case(Path(tmp) / "withdrawn-expression")
        activate_case(withdrawn_case)
        expressions_path = withdrawn_case / "data" / "source-expressions.json"
        expressions = json.loads(expressions_path.read_text())
        expressions["expressions"][0]["lifecycle_events"].append({
            "event": "withdrawn",
            "timestamp": "2026-07-16T13:00:00Z",
            "actor": "human",
            "reason": "Source passage was withdrawn from use.",
        })
        write_json(expressions_path, expressions)
        refresh_expression_contract_hash(withdrawn_case)
        withdrawn = subprocess.run(
            [sys.executable, str(FINALIZER), str(withdrawn_case)],
            capture_output=True,
            text=True,
        )
        assert withdrawn.returncode == 3, withdrawn.stdout + withdrawn.stderr
        assert "no active supporting source expression" in (
            withdrawn.stdout + withdrawn.stderr
        )
        assert not (withdrawn_case / "report.html").exists()

        superseded_case = build_case(Path(tmp) / "superseded-expression")
        activate_case(superseded_case)
        expressions_path = superseded_case / "data" / "source-expressions.json"
        expressions = json.loads(expressions_path.read_text())
        old_expression = expressions["expressions"][0]
        old_expression["lifecycle_events"].append({
            "event": "superseded",
            "timestamp": "2026-07-16T13:00:00Z",
            "actor": "human",
            "reason": "A replacement expression was reviewed.",
            "successor_expression_id": "SX2",
        })
        successor = json.loads(json.dumps(old_expression))
        successor["id"] = "SX2"
        successor["supersedes_expression_id"] = "SX1"
        successor["lifecycle_events"] = [{
            "event": "activated",
            "timestamp": "2026-07-16T13:00:00Z",
            "actor": "human",
            "reason": "Replacement expression activated.",
        }]
        expressions["expressions"].append(successor)
        write_json(expressions_path, expressions)
        refresh_expression_contract_hash(superseded_case)
        superseded = subprocess.run(
            [sys.executable, str(FINALIZER), str(superseded_case)],
            capture_output=True,
            text=True,
        )
        assert superseded.returncode == 3, superseded.stdout + superseded.stderr
        assert "no active supporting source expression" in (
            superseded.stdout + superseded.stderr
        )
        assert not (superseded_case / "report.html").exists()

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

        # Data-driven charts (timeline + bar) compile deterministically and
        # keep the pinned strict runtime even alongside no structural diagrams.
        charts = build_case(Path(tmp) / "report-charts")
        charts_findings_path = charts / "data" / "findings.json"
        charts_findings = json.loads(charts_findings_path.read_text())
        charts_findings["technical_indicators"] = [
            {
                "id": "IOC-domain-1",
                "finding_id": "F1",
                "type": "domain",
                "value": "evil.example",
                "context": "Command-and-control domain cited by F1.",
                "sources": ["https://example.org/official_record(v1)"],
                "first_observed": "2026-01-05T09:00:00Z",
                "last_observed": "2026-02-10T21:30:00Z",
            },
            {
                "id": "IOC-ipv4-1",
                "finding_id": "F1",
                "type": "ipv4",
                "value": "203.0.113.10",
                "context": "Staging server cited by F1.",
                "sources": ["https://example.org/official_record(v1)"],
                "first_observed": "2025-11-20T08:00:00Z",
            },
        ]
        write_json(charts_findings_path, charts_findings)
        charts_draft_path = charts / "data" / "report-draft.json"
        charts_draft = json.loads(charts_draft_path.read_text())
        charts_draft["diagrams"] = [
            {
                "id": "indicator-spans",
                "type": "timeline",
                "title": "Indicator observation windows",
                "caption": "Recorded first-to-last observation spans for the cited indicators.",
                "finding_ids": ["F1"],
                "indicator_ids": ["IOC-domain-1", "IOC-ipv4-1"],
            },
            {
                "id": "verdict-counts",
                "type": "bar",
                "title": "Verdict tally",
                "caption": "Fact-check verdicts across the report's findings.",
                "finding_ids": ["F1", "F2"],
                "metric": "verdict_tally",
            },
        ]
        write_json(charts_draft_path, charts_draft)
        charts_result = subprocess.run(
            [sys.executable, str(FINALIZER), str(charts)], capture_output=True, text=True
        )
        assert charts_result.returncode == 0, charts_result.stdout + charts_result.stderr
        charts_outputs = [
            charts / "findings-report.md", charts / "report.html", charts / "evidence-map.json"
        ]
        charts_html = charts_outputs[1].read_text()
        charts_markdown = charts_outputs[0].read_text()
        assert "securityLevel: \"strict\"" in charts_html
        assert "xychart-beta" in charts_html
        assert ">timeline" in charts_html
        assert "Data shown:" in charts_markdown
        charts_hashes = [sha(path) for path in charts_outputs]
        charts_again = subprocess.run(
            [sys.executable, str(FINALIZER), str(charts)], capture_output=True, text=True
        )
        assert charts_again.returncode == 0, charts_again.stdout + charts_again.stderr
        assert [sha(path) for path in charts_outputs] == charts_hashes

    print("deterministic report finalizer checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
