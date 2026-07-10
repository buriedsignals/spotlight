#!/usr/bin/env python3
"""Negative-coverage check for scripts/validate-case.py.

Exercises each validator function with valid baselines (zero errors) and
targeted invalid mutations (at least one error), plus end-to-end exit codes.
Stdlib only, no network.
"""

from __future__ import annotations

import importlib.util
import json
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
            "evidence_for": [{"description": "filing", "source": "https://example.org/x",
                              "source_type": "primary", "access_method": "full_text"}],
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


def main() -> int:
    # --- findings ---
    check("findings: valid baseline", vc.validate_findings(valid_findings()), False)
    check("findings: missing project", vc.validate_findings(mutate(valid_findings(), lambda d: d.pop("project"))), True)
    check("findings: missing findings key", vc.validate_findings(mutate(valid_findings(), lambda d: d.pop("findings"))), True)
    check("findings: findings not a list", vc.validate_findings(mutate(valid_findings(), lambda d: d.__setitem__("findings", {}))), True)
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
    check("fact-check: bad verdict", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("verdict", "true"))), True)
    check("fact-check: empty claim_text", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("claim_text", ""))), True)
    check("fact-check: bad confidence", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("confidence", "disputed"))), True)
    check("fact-check: sources not strings", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0].__setitem__("sources", [1]))), True)
    check("fact-check: bad assessment cap", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["grounding_assessment"].__setitem__("confidence_cap", "none"))), True)
    check("fact-check: evidence item missing source", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].pop("source"))), True)
    check("fact-check: bad access_method", vc.validate_fact_check(mutate(valid_fact_check(), lambda d: d["claims"][0]["evidence_for"][0].__setitem__("access_method", "stolen"))), True)
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

    # --- evidence bundle ---
    check("evidence: valid baseline", vc.validate_evidence_bundle(valid_evidence_bundle()), False)
    check("evidence: bad schema_version", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d.__setitem__("schema_version", "2.0"))), True)
    check("evidence: bad acquisition_method", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("acquisition_method", "telepathy"))), True)
    check("evidence: bad extraction_confidence", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("extraction_confidence", "total"))), True)
    check("evidence: human_verification non-bool", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("human_verification_required", "no"))), True)
    check("evidence: bad sha256", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0].__setitem__("sha256", "zz"))), True)
    check("evidence: claim_link missing text", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].pop("claim_text"))), True)
    check("evidence: claim_link bad support_type", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["claim_links"][0].__setitem__("support_type", "loose"))), True)
    check("evidence: gate bad confidence_effect", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["missing_source_gate"].__setitem__("confidence_effect", "cap_zero"))), True)
    check("evidence: gate fallback non-bool", vc.validate_evidence_bundle(mutate(valid_evidence_bundle(), lambda d: d["items"][0]["missing_source_gate"].__setitem__("fallback_required", "yes"))), True)

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

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
