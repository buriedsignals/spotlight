#!/usr/bin/env python3
"""Exercise the opt-in investigator/fact-checker source-expression contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-case.py"
FIXTURE = ROOT / "tests" / "fixtures" / "source-expression-producer-cases.json"

spec = importlib.util.spec_from_file_location("validate_case", VALIDATOR)
validate_case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_case)


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_text(case: Path, relative_path: str, content: str) -> Path:
    path = case / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def build_documents(case: Path, fixture: dict) -> tuple[dict, dict, dict, dict]:
    finding_rows = []
    finding_fingerprints = {}
    for item in fixture["findings"]:
        finding_fingerprint = fingerprint({"claim": item["claim"]})
        finding_fingerprints[item["id"]] = finding_fingerprint
        finding_rows.append({
            "id": item["id"],
            "claim": item["claim"],
            "evidence": "See exact source-expression anchors.",
            "sources": [{"url": "https://example.org/source"}],
            "confidence": "high",
            "finding_fingerprint": finding_fingerprint,
            "grounding": {
                "support_type": "direct",
                "source_role": "primary",
                "claim_elements_supported": ["actor", "action"],
                "missing_assumptions": [],
                "confidence_cap": "high",
                "misgrounding_risk": "Fixture exercises exact passage anchors.",
                "grounding_rationale": "Exact source expressions are preserved.",
            },
        })
    findings = {
        "schema_version": "1.1",
        "project": fixture["project"],
        "findings": finding_rows,
    }

    source_index = {}
    evidence_items = []
    for source in fixture["sources"]:
        original = write_text(case, source["original_path"], source["original_content"])
        anchor = write_text(case, source["anchor_path"], source["anchor_content"])
        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        anchor_hash = hashlib.sha256(anchor.read_bytes()).hexdigest()
        source_index[source["id"]] = {
            **source,
            "original_sha256": original_hash,
            "anchor_sha256": anchor_hash,
        }
        evidence_item = {
            "id": source["id"],
            "query_or_task": f"Acquire {source['id']}",
            "acquisition_method": "manual",
            "source_url": source["source_url"],
            "accessed": "2026-07-16T12:00:00Z",
            "raw_path": source["original_path"],
            "sha256": original_hash,
            "extraction_confidence": "high",
            "human_verification_required": False,
            "claim_links": [],
        }
        if source["anchor_path"] != source["original_path"]:
            evidence_item["text_derivatives"] = [{
                "id": f"TD-{source['id']}",
                "derivative_type": source["anchor_type"],
                "path": source["anchor_path"],
                "sha256": anchor_hash,
                "human_verification_required": False,
                "language": source["language"],
            }]
        evidence_items.append(evidence_item)
    evidence = {
        "schema_version": "1.0",
        "project": fixture["project"],
        "run_id": "source-expression-pilot",
        "created_at": "2026-07-16T12:00:00Z",
        "items": evidence_items,
    }

    core_fields = {
        "text", "anchor_ref", "anchor_sha256", "original_evidence_bundle_id",
        "original_artifact_sha256", "language", "attribution", "direct_quote",
        "derived_from_expression_id", "derivative_type",
    }
    expression_rows = []
    for item in fixture["expressions"]:
        source = source_index[item["source_id"]]
        expression = {
            "id": item["id"],
            "text": item["text"],
            "anchor_ref": {
                "path": source["anchor_path"],
                "line_start": item["line_start"],
                "line_end": item["line_end"],
            },
            "anchor_sha256": source["anchor_sha256"],
            "original_evidence_bundle_id": source["id"],
            "original_artifact_sha256": source["original_sha256"],
            "finding_links": [],
            "lifecycle_events": [{
                "event": "activated",
                "timestamp": "2026-07-16T12:00:00Z",
                "actor": item["created_by"],
                "reason": "Exact passage inspected during pilot acquisition.",
            }],
            "created_by": item["created_by"],
            "cycle": 1,
            "language": item["language"],
            "attribution": item["attribution"],
            "direct_quote": item["direct_quote"],
        }
        for optional in ("derived_from_expression_id", "derivative_type"):
            if optional in item:
                expression[optional] = item[optional]
        expression["expression_fingerprint"] = fingerprint({
            key: value for key, value in expression.items() if key in core_fields
        })
        for raw_link in item["finding_links"]:
            link = {
                **raw_link,
                "finding_fingerprint": finding_fingerprints[raw_link["finding_id"]],
            }
            link["link_fingerprint"] = fingerprint({
                "expression_fingerprint": expression["expression_fingerprint"],
                **link,
            })
            expression["finding_links"].append(link)
        expression_rows.append(expression)
    expressions = {
        "schema_version": "1.0",
        "project": fixture["project"],
        "created_at": "2026-07-16T12:00:00Z",
        "expressions": expression_rows,
    }

    expression_index = {item["id"]: item for item in expression_rows}
    fact_rows = []
    for index, verdict in enumerate(fixture["verdicts"], start=1):
        finding_id = verdict["finding_id"]

        def evidence_rows(expression_ids: list[str]) -> list[dict]:
            rows = []
            for expression_id in expression_ids:
                expression = expression_index[expression_id]
                link = next(
                    link for link in expression["finding_links"]
                    if link["finding_id"] == finding_id
                )
                rows.append({
                    "description": f"Exact expression {expression_id}",
                    "source": f"fixture://{expression_id}",
                    "source_type": "primary",
                    "access_method": "full_text",
                    "source_expression_refs": [{
                        "expression_id": expression_id,
                        "expression_fingerprint": expression["expression_fingerprint"],
                        "finding_fingerprint": link["finding_fingerprint"],
                        "link_fingerprint": link["link_fingerprint"],
                    }],
                })
            return rows

        fact_rows.append({
            "id": index,
            "finding_id": finding_id,
            "claim_text": next(row["claim"] for row in finding_rows if row["id"] == finding_id),
            "verdict": verdict["verdict"],
            "confidence": "high" if verdict["verdict"] == "verified" else "low",
            "grounding_assessment": {
                "support_type": "direct" if verdict["verdict"] == "verified" else "contradicted",
                "claim_elements_checked": ["actor", "action"],
                "missing_assumptions": [],
                "confidence_cap": "high" if verdict["verdict"] == "verified" else "low",
                "assessment": "Pilot expression chain reviewed.",
            },
            "evidence_for": evidence_rows(verdict["evidence_for"]),
            "evidence_against": evidence_rows(verdict["evidence_against"]),
            "sources": [],
        })
    fact_check = {
        "schema_version": "1.0",
        "project": fixture["project"],
        "source_document": "data/findings.json",
        "checked_at": "2026-07-16T12:00:00Z",
        "cycle": 1,
        "summary": {"total_claims": 2, "verified": 1, "disputed": 1},
        "claims": fact_rows,
        "gaps_for_next_cycle": [],
    }
    return findings, fact_check, evidence, expressions


def assert_prompt_contracts() -> None:
    investigator = (ROOT / "agents" / "investigator.md").read_text(encoding="utf-8")
    fact_checker = (ROOT / "agents" / "fact-checker.md").read_text(encoding="utf-8")
    grounding = (ROOT / "skills" / "epistemic-grounding" / "SKILL.md").read_text(encoding="utf-8")
    combined = "\n".join((investigator, fact_checker, grounding))
    required = (
        "SOURCE_EXPRESSION_MODE",
        "original-language",
        "created_by: investigator",
        "created_by: fact-checker",
        "text_derivatives[]",
        "derived_from_expression_id",
        "Never auto-extract",
        "do not produce an expression",
        "cannot prove that a passage makes a claim true",
    )
    missing = [phrase for phrase in required if phrase not in combined]
    assert not missing, f"producer contract text missing: {missing}"


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert_prompt_contracts()
    with tempfile.TemporaryDirectory() as raw_tmp:
        case = Path(raw_tmp) / "case"
        findings, fact_check, evidence, expressions = build_documents(case, fixture)
        errors = validate_case.validate_source_expressions(
            case, expressions, findings, fact_check, evidence
        )
        assert not errors, f"valid producer fixture failed: {errors}"

        expression_index = {item["id"]: item for item in expressions["expressions"]}
        assert len(expression_index["SX1"]["finding_links"]) == 2
        assert sum(
            any(link["finding_id"] == "F2" for link in item["finding_links"])
            for item in expressions["expressions"]
        ) == 2
        assert expression_index["SX3"]["anchor_sha256"] != expression_index["SX3"]["original_artifact_sha256"]
        assert expression_index["SX4"]["derived_from_expression_id"] == "SX1"
        assert expression_index["SX4"]["direct_quote"] is False
        assert expression_index["SX5"]["created_by"] == "fact-checker"
        assert expression_index["SX5"]["finding_links"][0]["relation"] == "contradicts"

        for negative in fixture["negative_cases"]:
            unavailable = case / negative["remove_path"]
            unavailable.unlink()
            errors = validate_case.validate_source_expressions(
                case, expressions, findings, fact_check, evidence
            )
            assert any(negative["expected_error"] in error for error in errors), (
                f"{negative['name']} did not fail as expected: {errors}"
            )

    print("ok   source-expression producer contract and pilot fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
