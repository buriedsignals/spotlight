#!/usr/bin/env python3
"""Negative-coverage check for scripts/validate-case.py.

Exercises each validator function with valid baselines (zero errors) and
targeted invalid mutations (at least one error), plus end-to-end exit codes.
Stdlib only, no network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-case.py"

spec = importlib.util.spec_from_file_location("validate_case", SCRIPT)
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)

PASS = 0
FAIL = 0


def check(name: str, errors: list[str], expect_errors: bool) -> None:
    global PASS, FAIL
    ok = bool(errors) == expect_errors
    if ok:
        PASS += 1
        print(f"ok   {name}")
    else:
        FAIL += 1
        state = "expected errors, got none" if expect_errors else f"unexpected errors: {errors[:3]}"
        print(f"FAIL {name} — {state}")


def valid_grounding() -> dict:
    return {
        "support_type": "direct",
        "source_role": "primary",
        "claim_elements_supported": ["amount"],
        "missing_assumptions": [],
        "confidence_cap": "high",
        "misgrounding_risk": "low; the claim restates source fields",
        "grounding_rationale": "claim matches the registry filing verbatim; no contradiction found",
    }


def valid_findings() -> dict:
    return {
        "project": "test-case",
        "findings": [{
            "id": "F1",
            "claim": "Acme paid Doe.",
            "evidence": "Filing 123.",
            "sources": [{"url": "https://example.org/x"}],
            "confidence": "high",
            "grounding": valid_grounding(),
        }],
    }


def findings_with_indicator() -> dict:
    findings = valid_findings()
    findings["technical_indicators"] = [{
        "id": "TI-1",
        "finding_id": "F1",
        "type": "domain",
        "value": "observed.example",
        "context": "Observed in a preserved passive record.",
        "sources": ["research/passive-record.json"],
    }]
    return findings


def valid_fact_check() -> dict:
    return {
        "project": "test-case",
        "claims": [{
            "claim_text": "Acme paid Doe.",
            "verdict": "verified",
            "confidence": "high",
            "finding_id": "F1",
            "sources": ["https://example.org/x"],
            "evidence_for": [{
                "description": "filing",
                "source": "https://example.org/x",
                "source_type": "primary",
                "access_method": "full_text",
                "local_file": "research/filing.json",
                "source_ref": {"path": "research/filing.json", "json_pointer": "/record/title"},
                "quote": "Acme paid Doe.",
                "sha256": "a" * 64,
            }],
            "grounding_assessment": {
                "support_type": "direct",
                "claim_elements_checked": ["amount"],
                "missing_assumptions": [],
                "confidence_cap": "high",
                "assessment": "independently confirmed",
            },
        }],
        "summary": {"total_claims": 1, "verified": 1},
        "cycle": 1,
        "gaps_for_next_cycle": [],
    }


def fact_check_with_indicator() -> dict:
    fact_check = valid_fact_check()
    fact_check["claims"][0]["technical_indicator_ids"] = ["TI-1"]
    fact_check["claims"][0]["claim_text"] = "The domain observed.example was independently observed."
    return fact_check


def valid_evidence_bundle() -> dict:
    return {
        "schema_version": "1.0",
        "project": "test-case",
        "run_id": "r1",
        "created_at": "2026-06-01T00:00:00Z",
        "items": [{
            "id": "E1",
            "query_or_task": "fetch filing",
            "source_url": "https://example.org/x",
            "accessed": "2026-06-01T00:00:00Z",
            "acquisition_method": "firecrawl",
            "extraction_confidence": "high",
            "human_verification_required": False,
            "sha256": "a" * 64,
            "claim_links": [{"finding_id": "F1", "claim_text": "Acme paid Doe.", "support_type": "direct"}],
            "missing_source_gate": {
                "requested_source": "filing",
                "returned_artifact": "filing pdf",
                "missing": "nothing",
                "fallback_required": False,
                "confidence_effect": "none",
            },
        }],
    }


def valid_summary() -> dict:
    """The data/summary.json shape phase-investigation-gate tells the runtime to write."""
    return {
        "schema_version": "1.0",
        "project": "test-case",
        "title": "Test case",
        "generated_at": "2026-06-01T00:00:00Z",
        "status": "pending_review",
        "cycles": 1,
        "verified_findings": 1,
        "summary": "One finding, verified.",
        "key_conclusions": ["Acme paid Doe."],
        "limitations": [],
        "methodology_summary": "Registry review.",
        "findings": [{"id": "F1", "claim": "Acme paid Doe.", "confidence": "high", "fact_check_verdict": "verified", "source_count": 1}],
    }


def valid_log() -> dict:
    return {
        "schema_version": "1.0",
        "project": "test-case",
        "cycles": [{
            "cycle": 1,
            "timestamp": "2026-06-01T00:00:00Z",
            "focus": "initial scan",
            "methodology": {"techniques_used": ["x"], "tools_used": ["y"],
                            "search_queries": [], "failed_approaches": []},
            "findings_added": 1,
            "gaps_remaining": [],
            "sources_consulted": [{"url": "https://example.org/x", "type": "registry",
                                   "accessed": "2026-06-01T00:00:00Z", "useful": True}],
        }],
    }


def valid_rlm() -> dict:
    return {
        "schema_version": "1.0",
        "project": "test-case",
        "run_id": "r1",
        "mode": "lite",
        "provider": "deterministic",
        "created_at": "2026-06-01T00:00:00Z",
        "artifacts": [{
            "id": "lead-0001",
            "kind": "lead",
            "text": "possible vendor link",
            "verification_status": "needs_verification",
            "source_refs": [{"path": "research/a.md", "line_start": 3, "line_end": 4}],
        }],
    }


def mutate(base: dict, fn) -> dict:
    doc = json.loads(json.dumps(base))
    fn(doc)
    return doc


def schema_errors(schema_name: str, instance: dict) -> list[str]:
    """Validate a fixture with jsonschema from CI or its installed CLI."""
    schema_path = ROOT / "schemas" / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        from jsonschema import Draft7Validator
    except ModuleNotFoundError:
        command = shutil.which("jsonschema")
        if command is None:
            return ["jsonschema module or CLI is required for schema contract checks"]
        with tempfile.TemporaryDirectory() as tmp:
            instance_path = Path(tmp) / "instance.json"
            instance_path.write_text(json.dumps(instance), encoding="utf-8")
            result = subprocess.run(
                [command, "--instance", str(instance_path), str(schema_path)],
                capture_output=True,
                text=True,
            )
        return [] if result.returncode == 0 else [result.stderr.strip() or result.stdout.strip()]
    Draft7Validator.check_schema(schema)
    return [error.message for error in Draft7Validator(schema).iter_errors(instance)]


def canonical_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def valid_source_expressions() -> dict:
    return json.loads(
        (ROOT / "tests" / "fixtures" / "source-expressions.sample.json").read_text(
            encoding="utf-8"
        )
    )


def valid_case_contract() -> dict:
    return {
        "schema_version": "1.0",
        "project": "test-case",
        "current_contract_version": "1.1",
        "activation_events": [{
            "event_id": "ACT1",
            "previous_contract_version": "1.0",
            "activated_contract_version": "1.1",
            "activated_at": "2026-07-16T12:10:00Z",
            "tool_version": "spotlight-test/1",
            "prior_input_hashes": {
                "findings_sha256": "1" * 64,
                "fact_check_sha256": "2" * 64,
                "evidence_bundle_sha256": "3" * 64,
            },
            "activated_artifact_hashes": {
                "findings_sha256": "4" * 64,
                "fact_check_sha256": "5" * 64,
                "evidence_bundle_sha256": "6" * 64,
                "source_expressions_sha256": "7" * 64,
            },
        }],
    }


def activated_documents(case: Path) -> tuple[dict, dict, dict, dict]:
    research = case / "research"
    research.mkdir(parents=True, exist_ok=True)
    original = research / "filing.bin"
    # SPOTLIGHT_FIXTURE_ORIGINAL_BYTES pads the original (a package near its size cap).
    original.write_bytes(b"original filing bytes\n".ljust(int(os.environ.get("SPOTLIGHT_FIXTURE_ORIGINAL_BYTES", "0")), b"."))
    anchor = research / "filing.txt"
    anchor.write_text("heading\nAcme paid Doe.\n", encoding="utf-8")
    finding_fp = canonical_fingerprint({"claim": "Acme paid Doe."})
    findings = valid_findings()
    findings["schema_version"] = "1.1"
    findings["findings"][0]["finding_fingerprint"] = finding_fp
    bundle = valid_evidence_bundle()
    bundle["items"][0]["raw_path"] = "research/filing.bin"
    bundle["items"][0]["sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    bundle["items"][0]["text_derivatives"] = [{
        "id": "TD1", "derivative_type": "text_extraction",
        "path": "research/filing.txt",
        "sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
        "human_verification_required": False,
    }]
    expression = {
        "id": "SX1", "text": "Acme paid Doe.",
        "anchor_ref": {"path": "research/filing.txt", "line_start": 2, "line_end": 2},
        "anchor_sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
        "original_evidence_bundle_id": "E1",
        "original_artifact_sha256": bundle["items"][0]["sha256"],
        "finding_links": [],
        "lifecycle_events": [{
            "event": "activated", "timestamp": "2026-07-16T12:00:00Z",
            "actor": "investigator", "reason": "captured",
        }],
        "created_by": "investigator", "cycle": 1, "language": "en",
        "attribution": "Registry filing", "direct_quote": True,
    }
    core_fields = {
        "text", "anchor_ref", "anchor_sha256", "original_evidence_bundle_id",
        "original_artifact_sha256", "language", "attribution", "direct_quote",
        "derived_from_expression_id", "derivative_type",
    }
    expression["expression_fingerprint"] = canonical_fingerprint({
        key: value for key, value in expression.items() if key in core_fields
    })
    link = {
        "finding_id": "F1", "finding_fingerprint": finding_fp, "relation": "supports",
    }
    link["link_fingerprint"] = canonical_fingerprint({
        "expression_fingerprint": expression["expression_fingerprint"], **link,
    })
    expression["finding_links"] = [link]
    expressions = {
        "schema_version": "1.0", "project": "test-case",
        "created_at": "2026-07-16T12:00:00Z", "expressions": [expression],
    }
    fact_check = valid_fact_check()
    fact_check["schema_version"] = "1.1"  # activated cases use fact-check contract 1.1
    fact_check["claims"][0]["evidence_for"][0]["source_expression_refs"] = [{
        "expression_id": "SX1",
        "expression_fingerprint": expression["expression_fingerprint"],
        "finding_fingerprint": finding_fp,
        "link_fingerprint": link["link_fingerprint"],
    }]
    return findings, fact_check, bundle, expressions


def write_activated_case(case: Path, fact_check_version: str | None = "1.1", verdict: str | None = None) -> None:
    data = case / "data"
    data.mkdir(parents=True, exist_ok=True)
    findings, fact_check, bundle, expressions = activated_documents(case)
    if fact_check_version is None:
        fact_check.pop("schema_version", None)
    if verdict is not None:
        fact_check["claims"][0]["verdict"] = verdict
    documents = {
        "findings.json": findings,
        "fact-check.json": fact_check,
        "evidence-bundle.json": bundle,
        "source-expressions.json": expressions,
    }
    for name, document in documents.items():
        (data / name).write_text(json.dumps(document), encoding="utf-8")
    contract = valid_case_contract()
    contract["activation_events"][0]["activated_artifact_hashes"] = {
        "findings_sha256": hashlib.sha256((data / "findings.json").read_bytes()).hexdigest(),
        "fact_check_sha256": hashlib.sha256((data / "fact-check.json").read_bytes()).hexdigest(),
        "evidence_bundle_sha256": hashlib.sha256((data / "evidence-bundle.json").read_bytes()).hexdigest(),
        "source_expressions_sha256": hashlib.sha256((data / "source-expressions.json").read_bytes()).hexdigest(),
    }
    (data / "case-contract.json").write_text(json.dumps(contract), encoding="utf-8")


def main() -> int:
    # --- findings ---
    check("findings: valid baseline", vc.validate_findings(valid_findings()), False)
    check("findings: missing project", vc.validate_findings(mutate(valid_findings(), lambda d: d.pop("project"))), True)
    check("findings: missing findings key", vc.validate_findings(mutate(valid_findings(), lambda d: d.pop("findings"))), True)
    check("findings: findings not a list", vc.validate_findings(mutate(valid_findings(), lambda d: d.__setitem__("findings", {}))), True)
    nothing = {"reason": "No candidate claim met the evidence threshold in the approved directions."}
    check("findings: empty list without a no-qualifying-findings record", vc.validate_findings(mutate(valid_findings(), lambda d: d.__setitem__("findings", []))), True)
    check("findings: empty list with an explicit no-qualifying-findings record", vc.validate_findings(mutate(valid_findings(), lambda d: d.update(findings=[], no_qualifying_findings=nothing))), False)
    check("findings: no-qualifying-findings record without a reason", vc.validate_findings(mutate(valid_findings(), lambda d: d.update(findings=[], no_qualifying_findings={"reason": " "}))), True)
    check("findings: no-qualifying-findings record beside findings", vc.validate_findings(mutate(valid_findings(), lambda d: d.__setitem__("no_qualifying_findings", nothing))), True)
    check("findings schema: empty list with the record", schema_errors("findings.schema.json", mutate(valid_findings(), lambda d: d.update(findings=[], no_qualifying_findings=nothing))), False)
    check("findings schema: empty list without the record", schema_errors("findings.schema.json", mutate(valid_findings(), lambda d: d.__setitem__("findings", []))), True)
    check("findings: empty claim", vc.validate_findings(mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("claim", " "))), True)
    check("findings: missing evidence", vc.validate_findings(mutate(valid_findings(), lambda d: d["findings"][0].pop("evidence"))), True)
    check("findings: empty sources", vc.validate_findings(mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("sources", []))), True)
    check("findings: bad confidence", vc.validate_findings(mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("confidence", "certain"))), True)
    check("findings: valid technical indicator", vc.validate_findings(findings_with_indicator()), False)
    check("findings: PII indicator type rejected", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("type", "email"))), True)
    check("findings: dangling indicator finding", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("finding_id", "F9"))), True)
    check("findings: non-string indicator finding", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("finding_id", ["F1"]))), True)
    check("findings: non-string indicator type", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("type", ["domain"]))), True)
    check("findings: indicator sources required", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("sources", []))), True)
    check("findings: duplicate indicator id", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"].append(dict(d["technical_indicators"][0])))), True)
    check("findings: invalid indicator timestamp", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].__setitem__("first_observed", "yesterday"))), True)
    check("findings: reversed indicator window", vc.validate_findings(mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].update({"first_observed": "2026-07-10T10:00:00Z", "last_observed": "2026-07-10T09:00:00Z"}))), True)

    # --- grounding ---
    check("grounding: valid baseline", vc.validate_grounding(valid_grounding(), "g"), False)
    check("grounding: bad support_type", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("support_type", "vibes")), "g"), True)
    check("grounding: bad source_role", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("source_role", "tertiary")), "g"), True)
    check("grounding: bad confidence_cap", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("confidence_cap", "max")), "g"), True)
    check("grounding: empty misgrounding_risk", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("misgrounding_risk", "")), "g"), True)
    check("grounding: empty rationale", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("grounding_rationale", "")), "g"), True)
    check("grounding: non-list elements", vc.validate_grounding(mutate(valid_grounding(), lambda d: d.__setitem__("claim_elements_supported", "amount")), "g"), True)

    # --- fact-check ---
    check("fact-check: valid baseline", vc.validate_fact_check(valid_fact_check()), False)
    check("fact-check: conflicting verdict containers", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d.__setitem__("fact_checks", []))), True)
    check("fact-check: bad verdict", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("verdict", "true"))), True)
    check("fact-check: empty claim_text", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("claim_text", ""))), True)
    check("fact-check: bad confidence", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("confidence", "disputed"))), True)
    # Contract 1.1: Batch 1.1's five verdicts; legacy 1.0 stays readable.
    v11 = lambda d: d.__setitem__("schema_version", "1.1")  # noqa: E731
    for removed in ("partially_verified", "mischaracterized"):
        check(f"fact-check 1.1: refuses removed verdict {removed}", vc.validate_fact_check(mutate(mutate(valid_fact_check(), v11), lambda d: d["claims"][0].__setitem__("verdict", removed))), True)
        check(f"fact-check 1.0: legacy {removed} stays readable", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("verdict", removed))), False)
    check("fact-check 1.1: not_checkable accepted", vc.validate_fact_check(mutate(mutate(valid_fact_check(), v11), lambda d: d["claims"][0].update({"verdict": "not_checkable", "evidence_for": []}))), False)
    check("fact-check 1.0: not_checkable needs 1.1", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].update({"verdict": "not_checkable", "evidence_for": []}))), True)
    check("fact-check 1.1: known flag accepted", vc.validate_fact_check(mutate(mutate(valid_fact_check(), v11), lambda d: d["claims"][0].__setitem__("flags", [{"code": "partial_support", "reason": "the date is unsupported"}]))), False)
    check("fact-check 1.1: unknown flag code refused", vc.validate_fact_check(mutate(mutate(valid_fact_check(), v11), lambda d: d["claims"][0].__setitem__("flags", [{"code": "mostly_true", "reason": "x"}]))), True)
    check("fact-check 1.1: legacy container refused", vc.validate_fact_check({"schema_version": "1.1", "project": "p", "fact_checks": []}), True)
    check("findings 1.1: confidence disputed refused", vc.validate_findings(mutate(valid_findings(), lambda d: (d.__setitem__("schema_version", "1.1"), d["findings"][0].__setitem__("confidence", "disputed")))), True)
    check("findings 1.0: legacy confidence disputed readable", vc.validate_findings(mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("confidence", "disputed"))), False)
    check("fact-check: sources not strings", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("sources", [1]))), True)
    check("fact-check: bad assessment cap", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["grounding_assessment"].__setitem__("confidence_cap", "none"))), True)
    check("fact-check: evidence item missing source", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].pop("source"))), True)
    check("fact-check: bad access_method", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].__setitem__("access_method", "stolen"))), True)
    check("fact-check: positive anchor missing access_method", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].pop("access_method"))), True)
    check("fact-check: bad source_ref path", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0]["source_ref"].__setitem__("path", ""))), True)
    check("fact-check: mixed source_ref locators", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0]["source_ref"].update({"line_start": 1, "line_end": 1}))), True)
    check("fact-check: bad source_ref pointer", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0]["source_ref"].__setitem__("json_pointer", "record/title"))), True)
    check("fact-check: bad anchor hash", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].__setitem__("sha256", "zz"))), True)
    check("fact-check: summary count non-int", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["summary"].__setitem__("verified", "1"))), True)
    check("fact-check: cycle zero", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d.__setitem__("cycle", 0))), True)
    check("fact-check: valid technical indicator IDs", vc.validate_fact_check(fact_check_with_indicator()), False)
    check("fact-check: technical indicator IDs not a list", vc.validate_fact_check(mutate(fact_check_with_indicator(), lambda d: d["claims"][0].__setitem__("technical_indicator_ids", "TI-1"))), True)
    check("fact-check: duplicate technical indicator IDs", vc.validate_fact_check(mutate(fact_check_with_indicator(), lambda d: d["claims"][0].__setitem__("technical_indicator_ids", ["TI-1", "TI-1"]))), True)

    # --- cross reference ---
    check("cross-ref: resolves", vc.cross_reference(valid_findings(), valid_fact_check()), False)
    check("cross-ref: dangling finding_id", vc.cross_reference(valid_findings(), mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("finding_id", "F9"))), True)
    check("cross-ref: technical indicator resolves", vc.cross_reference(findings_with_indicator(), fact_check_with_indicator()), False)
    check("cross-ref: dangling technical indicator", vc.cross_reference(findings_with_indicator(), mutate(fact_check_with_indicator(), lambda d: d["claims"][0].__setitem__("technical_indicator_ids", ["TI-404"]))), True)
    check("cross-ref: technical indicator exact value required", vc.cross_reference(findings_with_indicator(), mutate(fact_check_with_indicator(), lambda d: d["claims"][0].__setitem__("claim_text", "A different domain was checked."))), True)
    case_sensitive_url = mutate(findings_with_indicator(), lambda d: d["technical_indicators"][0].update({"type": "url", "value": "https://observed.example/Reset"}))
    case_folded_claim = mutate(fact_check_with_indicator(), lambda d: d["claims"][0].__setitem__("claim_text", "https://observed.example/reset was checked."))
    check("cross-ref: URL path remains case-sensitive", vc.cross_reference(case_sensitive_url, case_folded_claim), True)
    legacy_indicator = {"project": "p", "fact_checks": [{"finding_id": "F1", "claim": "https://observed.example/Reset was checked.", "status": "verified", "technical_indicator_ids": ["TI-1"]}]}
    check("cross-ref: legacy technical indicator claim", vc.cross_reference(case_sensitive_url, legacy_indicator), False)

    # --- evidence bundle ---
    check("evidence: valid baseline", vc.validate_evidence_bundle(valid_evidence_bundle()), False)
    check("evidence: bad schema_version", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d.__setitem__("schema_version", "2.0"))), True)
    check("evidence: bad acquisition_method", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("acquisition_method", "telepathy"))), True)
    schema_methods = json.loads((ROOT / "schemas" / "evidence-bundle.schema.json").read_text(encoding="utf-8"))["definitions"]["evidence_item"]["properties"]["acquisition_method"]["enum"]
    for method in schema_methods:
        check(f"evidence: schema acquisition_method {method} accepted", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d, m=method: d["items"][0].__setitem__("acquisition_method", m))), False)
    check("evidence: unknown created_by", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("created_by", "human"))), True)
    check("evidence: fact-checker created_by accepted", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("created_by", "fact-checker"))), False)
    check("evidence: bad extraction_confidence", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("extraction_confidence", "total"))), True)
    check("evidence: human_verification non-bool", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("human_verification_required", "no"))), True)
    check("evidence: bad sha256", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("sha256", "zz"))), True)
    check("evidence: claim_link missing text", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].pop("claim_text"))), True)
    check("evidence: claim_link bad support_type", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].__setitem__("support_type", "loose"))), True)
    check("evidence: gate bad confidence_effect", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["missing_source_gate"].__setitem__("confidence_effect", "cap_zero"))), True)
    returned = vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("missing_source_gate", {"requested_source": "https://example.org/a", "returned_artifact": "research/a.md.raw", "missing": False})))
    check("evidence: a partial gate for a returned source says to omit it and gives the shape",
          [] if any("is only for a requested source that was not returned; omit it when the source came back" in e
                    and "'missing' is not text" in e and "'fallback_required' is not true or false" in e for e in returned)
          else ["partial gate not explained"] + returned, False)
    check("evidence: gate fallback non-bool", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["missing_source_gate"].__setitem__("fallback_required", "yes"))), True)
    check("evidence: gate fallback_outcome outside its enum", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["missing_source_gate"].update(fallback_required=True, fallback_outcome="done"))), True)

    # Investigator-owned links between findings and the bundle.
    linked = mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("evidence_bundle_refs", ["E1"]))
    check("evidence links: verbatim claim and resolving refs", vc.evidence_bundle_links(linked, valid_evidence_bundle()), False)
    check("evidence links: unknown finding_id", vc.evidence_bundle_links(linked, mutate(valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].__setitem__("finding_id", "F9"))), True)
    check("evidence links: dangling evidence_bundle_refs", vc.evidence_bundle_links(mutate(linked, lambda d: d["findings"][0].__setitem__("evidence_bundle_refs", ["E9"])), valid_evidence_bundle()), True)
    with tempfile.TemporaryDirectory() as tmp:
        # At the investigator's checkpoint (no fact-check yet) a paraphrased
        # claim link already fails, so it never reaches the fact-checker.
        checkpoint = Path(tmp) / "checkpoint"
        (checkpoint / "data").mkdir(parents=True)
        (checkpoint / "data" / "findings.json").write_text(json.dumps(linked))
        (checkpoint / "data" / "evidence-bundle.json").write_text(json.dumps(mutate(
            valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].__setitem__("claim_text", "Acme paid Doe in 2020."))))
        out = subprocess.run([sys.executable, str(SCRIPT), str(checkpoint)], capture_output=True, text=True)
        check("e2e: investigator checkpoint refuses a paraphrased claim link",
              [] if out.returncode == 1 and "claim verbatim" in out.stderr else [f"rc={out.returncode}: {out.stderr[-300:]}"], False)

    # --- source-expression and activation schemas (U1; runtime enforcement is U2) ---
    expressions = valid_source_expressions()
    check("source expressions: valid many-to-many fixture", schema_errors("source-expressions.schema.json", expressions), False)
    check("source expressions: missing passage fingerprint", schema_errors("source-expressions.schema.json", mutate(expressions, lambda d: d["expressions"][0].pop("expression_fingerprint"))), True)
    check("source expressions: invalid relation", schema_errors("source-expressions.schema.json", mutate(expressions, lambda d: d["expressions"][0]["finding_links"][0].__setitem__("relation", "mentions"))), True)
    check("source expressions: mixed locator union", schema_errors("source-expressions.schema.json", mutate(expressions, lambda d: d["expressions"][0]["anchor_ref"].__setitem__("json_pointer", "/record/title"))), True)
    check("source expressions: derivative lineage is paired", schema_errors("source-expressions.schema.json", mutate(expressions, lambda d: d["expressions"][1].pop("derivative_type"))), True)
    check("source expressions: supersession names successor", schema_errors("source-expressions.schema.json", mutate(expressions, lambda d: d["expressions"][0]["lifecycle_events"].append({"event": "superseded", "timestamp": "2026-07-16T13:00:00Z", "actor": "human", "reason": "corrected"}))), True)

    passage_fields = {
        "text", "anchor_ref", "anchor_sha256", "original_evidence_bundle_id",
        "original_artifact_sha256", "language", "attribution", "direct_quote",
        "derived_from_expression_id", "derivative_type",
    }
    fingerprint_errors = []
    for expression in expressions["expressions"]:
        core = {key: value for key, value in expression.items() if key in passage_fields}
        if canonical_fingerprint(core) != expression["expression_fingerprint"]:
            fingerprint_errors.append(f"{expression['id']}: expression_fingerprint mismatch")
        for link in expression["finding_links"]:
            relation = {
                "expression_fingerprint": expression["expression_fingerprint"],
                "finding_fingerprint": link["finding_fingerprint"],
                "finding_id": link["finding_id"],
                "relation": link["relation"],
            }
            if canonical_fingerprint(relation) != link["link_fingerprint"]:
                fingerprint_errors.append(f"{expression['id']}: link_fingerprint mismatch")
    check("source expressions: canonical passage and relation fingerprints", fingerprint_errors, False)

    activated_findings = valid_findings()
    activated_findings["schema_version"] = "1.1"
    activated_findings["findings"][0]["finding_fingerprint"] = canonical_fingerprint({"claim": "Acme paid Doe."})
    check("findings schema: activated 1.1 dispatch", schema_errors("findings.schema.json", activated_findings), False)
    check("findings schema: legacy omitted version remains valid", schema_errors("findings.schema.json", valid_findings()), False)
    check("findings schema: 1.1 requires finding fingerprint", schema_errors("findings.schema.json", mutate(activated_findings, lambda d: d["findings"][0].pop("finding_fingerprint"))), True)
    check("findings schema: unknown version refused", schema_errors("findings.schema.json", mutate(valid_findings(), lambda d: d.__setitem__("schema_version", "1.2"))), True)

    fact_with_expression = valid_fact_check()
    fact_with_expression["claims"][0]["evidence_for"][0]["source_expression_refs"] = [{
        "expression_id": "SX1",
        "expression_fingerprint": expressions["expressions"][0]["expression_fingerprint"],
        "finding_fingerprint": activated_findings["findings"][0]["finding_fingerprint"],
        "link_fingerprint": expressions["expressions"][0]["finding_links"][0]["link_fingerprint"],
    }]
    check("fact-check schema: immutable expression relation ref", schema_errors("fact-check.schema.json", fact_with_expression), False)
    check("fact-check schema: partial expression relation refused", schema_errors("fact-check.schema.json", mutate(fact_with_expression, lambda d: d["claims"][0]["evidence_for"][0]["source_expression_refs"][0].pop("link_fingerprint"))), True)

    bundle_with_derivative = valid_evidence_bundle()
    bundle_with_derivative["items"][0]["text_derivatives"] = [{
        "id": "TD1",
        "derivative_type": "ocr",
        "path": "research/filing-ocr.txt",
        "sha256": "b" * 64,
        "human_verification_required": True,
        "language": "en",
    }]
    check("evidence schema: original and derivative identities", schema_errors("evidence-bundle.schema.json", bundle_with_derivative), False)

    contract = valid_case_contract()
    check("case contract: valid sole activation artifact", schema_errors("case-contract.schema.json", contract), False)
    check("case contract: omitted version refused", schema_errors("case-contract.schema.json", mutate(contract, lambda d: d.pop("current_contract_version"))), True)
    check("case contract: partial activated artifact set refused", schema_errors("case-contract.schema.json", mutate(contract, lambda d: d["activation_events"][0]["activated_artifact_hashes"].pop("source_expressions_sha256"))), True)
    check("case contract: malformed prior input hash refused", schema_errors("case-contract.schema.json", mutate(contract, lambda d: d["activation_events"][0]["prior_input_hashes"].__setitem__("findings_sha256", "not-a-hash"))), True)

    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "activated"
        findings, fact_check, bundle, expressions = activated_documents(case)
        check(
            "source expressions runtime: valid activated chain",
            vc.validate_source_expressions(case, expressions, findings, fact_check, bundle),
            False,
        )
        # Gold runs on 8c7bf163: the fact-checker kept only expression_id and
        # relation, and "stale" never told it the fingerprints were missing.
        bare = mutate(fact_check, lambda d: d["claims"][0]["evidence_for"][0].__setitem__("source_expression_refs", [{"expression_id": "SX1", "relation": "supports"}]))
        full_ref = json.dumps(fact_check["claims"][0]["evidence_for"][0]["source_expression_refs"][0])
        bare_errors = [e for e in vc.validate_source_expressions(case, expressions, findings, bare, bundle) if "'SX1'" in e]
        check(
            "source expression refs: a ref without fingerprints names the whole ref to copy",
            [] if bare_errors and all("is missing expression_fingerprint, finding_fingerprint, link_fingerprint" in e and full_ref in e for e in bare_errors) else ["missing-fingerprint ref not named"] + bare_errors,
            False,
        )
        check(
            "source expressions runtime: exact selected text",
            vc.validate_source_expressions(
                case,
                mutate(expressions, lambda d: d["expressions"][0].__setitem__("text", "Acme paid Roe.")),
                findings, fact_check, bundle,
            ),
            True,
        )
        check(
            "source expressions runtime: canonical expression fingerprint",
            vc.validate_source_expressions(
                case,
                mutate(expressions, lambda d: d["expressions"][0].__setitem__("expression_fingerprint", "0" * 64)),
                findings, fact_check, bundle,
            ),
            True,
        )
        contradiction = mutate(expressions, lambda d: d["expressions"][0]["finding_links"][0].__setitem__("relation", "contradicts"))
        contradiction["expressions"][0]["finding_links"][0]["link_fingerprint"] = canonical_fingerprint({
            "expression_fingerprint": contradiction["expressions"][0]["expression_fingerprint"],
            "finding_fingerprint": contradiction["expressions"][0]["finding_links"][0]["finding_fingerprint"],
            "finding_id": "F1", "relation": "contradicts",
        })
        fact_contradiction = mutate(fact_check, lambda d: d["claims"][0]["evidence_for"][0]["source_expression_refs"][0].__setitem__("link_fingerprint", contradiction["expressions"][0]["finding_links"][0]["link_fingerprint"]))
        check(
            "source expressions runtime: contradiction-only positive verdict",
            vc.validate_source_expressions(case, contradiction, findings, fact_contradiction, bundle),
            True,
        )
        pending_bundle = mutate(bundle, lambda d: d["items"][0]["text_derivatives"][0].__setitem__("human_verification_required", True))
        check(
            "source expressions runtime: pending-human derivative cannot support positive verdict",
            vc.validate_source_expressions(case, expressions, findings, fact_check, pending_bundle),
            True,
        )
        cycle = mutate(expressions, lambda d: d["expressions"][0].update({"derived_from_expression_id": "SX1", "derivative_type": "translation"}))
        check(
            "source expressions runtime: derivative cycle",
            vc.validate_source_expressions(case, cycle, findings, fact_check, bundle),
            True,
        )

    # --- investigation log ---
    check("log: valid baseline", vc.validate_investigation_log(valid_log()), False)
    check("log: bad schema_version", vc.validate_investigation_log(mutate(valid_log(), lambda d: d.__setitem__("schema_version", "0.9"))), True)
    check("log: cycle below 1", vc.validate_investigation_log(mutate(valid_log(), lambda d: d["cycles"][0].__setitem__("cycle", 0))), True)
    check("log: methodology not object", vc.validate_investigation_log(mutate(valid_log(), lambda d: d["cycles"][0].__setitem__("methodology", "scan"))), True)
    check("log: techniques not strings", vc.validate_investigation_log(mutate(valid_log(), lambda d: d["cycles"][0]["methodology"].__setitem__("techniques_used", [1]))), True)
    check("log: source missing url", vc.validate_investigation_log(mutate(valid_log(), lambda d: d["cycles"][0]["sources_consulted"][0].pop("url"))), True)
    check("log: source useful non-bool", vc.validate_investigation_log(mutate(valid_log(), lambda d: d["cycles"][0]["sources_consulted"][0].__setitem__("useful", "yes"))), True)

    # --- RLM analysis ---
    check("rlm: valid baseline", vc.validate_rlm_analysis(valid_rlm()), False)
    check("rlm: bad mode", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d.__setitem__("mode", "full_gpt"))), True)
    check("rlm: bad provider", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d.__setitem__("provider", "openai"))), True)
    check("rlm: verified-style status forbidden", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d["artifacts"][0].__setitem__("verification_status", "verified"))), True)
    check("rlm: bad kind", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d["artifacts"][0].__setitem__("kind", "fact"))), True)
    check("rlm: non-discarded without refs", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d["artifacts"][0].__setitem__("source_refs", []))), True)
    check("rlm: non-positive line_start", vc.validate_rlm_analysis(mutate(valid_rlm(), lambda d: d["artifacts"][0]["source_refs"][0].__setitem__("line_start", 0))), True)

    # --- end-to-end exit codes ---
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case"
        (case / "data").mkdir(parents=True)
        (case / "data" / "findings.json").write_text(json.dumps(valid_findings()))
        (case / "data" / "fact-check.json").write_text(json.dumps(valid_fact_check()))
        rc = subprocess.run([sys.executable, str(SCRIPT), str(case)], capture_output=True).returncode
        check("e2e: valid case exits 0", [] if rc == 0 else [f"rc={rc}"], False)
        (case / "data" / "findings.json").write_text(json.dumps(mutate(valid_findings(), lambda d: d["findings"][0].__setitem__("sources", []))))
        rc = subprocess.run([sys.executable, str(SCRIPT), str(case)], capture_output=True).returncode
        check("e2e: invalid case exits 1", [] if rc == 1 else [f"rc={rc}"], False)
        rc = subprocess.run([sys.executable, str(SCRIPT), str(case / "missing")], capture_output=True).returncode
        check("e2e: missing dir exits 2", [] if rc == 2 else [f"rc={rc}"], False)

        summary_case = Path(tmp) / "summary-case"
        (summary_case / "data").mkdir(parents=True)
        (summary_case / "data" / "findings.json").write_text(json.dumps(valid_findings()))
        (summary_case / "data" / "fact-check.json").write_text(json.dumps(valid_fact_check()))
        summary_cases = [
            ("the gate skill's summary.json shape is valid", valid_summary(), 0),
            ("summary.json without a title is refused", mutate(valid_summary(), lambda d: d.pop("title")), 1),
            ("summary.json in the old date/overview/conclusions shape is refused",
             {"schema_version": "1.0", "project": "test-case", "title": "Test case", "date": "2026-06-01",
              "overview": "x", "conclusions": ["y"], "findings_summary": []}, 1),
            ("summary.json naming an unknown finding is refused",
             mutate(valid_summary(), lambda d: d["findings"][0].__setitem__("id", "F9")), 1),
            ("summary.json verdict outside the case's fact-check vocabulary is refused",
             mutate(valid_summary(), lambda d: d["findings"][0].__setitem__("fact_check_verdict", "not_checkable")), 1),
            ("summary.json legacy partially_verified verdict stays valid in a 1.0 case",
             mutate(valid_summary(), lambda d: d["findings"][0].__setitem__("fact_check_verdict", "partially_verified")), 0),
        ]
        for name, summary, expected in summary_cases:
            (summary_case / "data" / "summary.json").write_text(json.dumps(summary))
            out = subprocess.run([sys.executable, str(SCRIPT), str(summary_case)], capture_output=True, text=True)
            check(f"e2e: {name}", [] if out.returncode == expected else [f"rc={out.returncode} {out.stderr.strip()[-200:]}"], False)
        check("summary schema: the gate skill's shape", schema_errors("summary.schema.json", valid_summary()), False)
        check("summary schema: the old date/overview/conclusions shape",
              schema_errors("summary.schema.json", {"schema_version": "1.0", "project": "p", "title": "t", "date": "2026-06-01", "overview": "x", "conclusions": []}), True)

        activated = Path(tmp) / "activated"
        write_activated_case(activated)
        rc = subprocess.run([sys.executable, str(SCRIPT), str(activated)], capture_output=True).returncode
        check("e2e: activated case exits 0", [] if rc == 0 else [f"rc={rc}"], False)
        # A migrated case keeps its activation-time hashes enforced, with or
        # without a gate approval (only born cases wait for their binding).
        migrated = Path(tmp) / "migrated-edited"
        write_activated_case(migrated)
        fc_path = migrated / "data" / "fact-check.json"
        fc = json.loads(fc_path.read_text(encoding="utf-8"))
        fc["claims"][0]["notes"] = "edited after migration"
        fc_path.write_text(json.dumps(fc), encoding="utf-8")
        out = subprocess.run([sys.executable, str(SCRIPT), str(migrated)], capture_output=True, text=True)
        check("e2e: a migrated case edited after activation fails its hash check",
              [] if "fact_check_sha256 does not match" in out.stdout + out.stderr else ["no hash error"], False)
        # A case activated before contract 1.1 keeps its 1.0 fact-check and
        # stays valid; a removed verdict in an activated case is refused.
        earlier = Path(tmp) / "activated-before-1.1"
        write_activated_case(earlier, fact_check_version=None)
        rc = subprocess.run([sys.executable, str(SCRIPT), str(earlier)], capture_output=True).returncode
        check("e2e: case activated with a 1.0 fact-check stays valid", [] if rc == 0 else [f"rc={rc}"], False)
        removed = Path(tmp) / "activated-removed-verdict"
        write_activated_case(removed, fact_check_version=None, verdict="partially_verified")
        out = subprocess.run([sys.executable, str(SCRIPT), str(removed)], capture_output=True, text=True)
        check("e2e: activated case refuses a removed verdict",
              [] if "a person must re-assess this one" in out.stdout + out.stderr else ["no removed-verdict error"], False)
        # Shakedown on 26683b3c: a list-shaped evidence bundle crashed the check with a traceback.
        listed = Path(tmp) / "activated-list-bundle"
        write_activated_case(listed)
        (listed / "data" / "evidence-bundle.json").write_text(json.dumps([{"id": "E1"}]), encoding="utf-8")
        out = subprocess.run([sys.executable, str(SCRIPT), str(listed)], capture_output=True, text=True)
        check("e2e: a list-shaped evidence bundle is refused without a traceback",
              [] if out.returncode == 1 and "Traceback" not in out.stderr and "top-level must be an object" in out.stdout + out.stderr
              else [f"rc={out.returncode} {out.stderr.strip()[-300:]}"], False)
        # Gold run 1 on 99c6567e: bare-id refs were dropped silently, leaving only "no active supporting source expression".
        bare = Path(tmp) / "activated-bare-id-ref"
        write_activated_case(bare)
        fc_path = bare / "data" / "fact-check.json"
        fc = json.loads(fc_path.read_text(encoding="utf-8"))
        fc["claims"][0]["evidence_for"][0]["source_expression_refs"] = ["SX1"]
        fc_path.write_text(json.dumps(fc), encoding="utf-8")
        out = subprocess.run([sys.executable, str(SCRIPT), str(bare)], capture_output=True, text=True)
        check("e2e: a bare-id expression ref is named, with the object shape",
              [] if out.returncode == 1 and "source_expression_refs items must be objects" in out.stdout + out.stderr
              else [f"rc={out.returncode} {(out.stdout + out.stderr).strip()[-300:]}"], False)
        (activated / "data" / "source-expressions.json").write_text("{}", encoding="utf-8")
        rc = subprocess.run([sys.executable, str(SCRIPT), str(activated)], capture_output=True).returncode
        check("e2e: activated artifact mutation exits 1", [] if rc == 1 else [f"rc={rc}"], False)

        # A case activated before its first cycle has a contract and no
        # findings yet; the validator reports only the missing findings file.
        born = Path(tmp) / "born-before-findings"
        (born / "data").mkdir(parents=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "case-contract.py"), "activate-new", str(born)],
                       check=True, capture_output=True)
        out = subprocess.run([sys.executable, str(SCRIPT), str(born)], capture_output=True, text=True)
        check("e2e: an activated case before findings reports only the missing findings",
              [] if out.returncode == 1 and "Traceback" not in out.stderr
              and "1 validation error" in out.stderr and "findings.json: not found" in out.stderr
              else [f"rc={out.returncode} {out.stderr.strip()[-300:]}"], False)

        legacy = Path(tmp) / "legacy-with-expression-file"
        (legacy / "data").mkdir(parents=True)
        (legacy / "data" / "findings.json").write_text(json.dumps(valid_findings()))
        (legacy / "data" / "fact-check.json").write_text(json.dumps(valid_fact_check()))
        (legacy / "data" / "source-expressions.json").write_text("{}")
        rc = subprocess.run([sys.executable, str(SCRIPT), str(legacy)], capture_output=True).returncode
        check("e2e: invalid pilot expression file is rejected without activation", [] if rc == 1 else [f"rc={rc}"], False)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
