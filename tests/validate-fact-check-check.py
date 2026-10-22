#!/usr/bin/env python3
"""Characterization coverage for the fact-check evidence anchor contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-fact-check.py"
SPEC = importlib.util.spec_from_file_location("validate_fact_check", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case"
        research = case / "research"
        research.mkdir(parents=True)
        text_path = research / "filing.txt"
        text_path.write_text("heading\nAcme paid Doe.\nfooter\n", encoding="utf-8")
        json_path = research / "filing.json"
        json_path.write_text(
            json.dumps({"record": {"title": "Acme paid Doe."}}), encoding="utf-8"
        )

        paths, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "research/filing.txt", "line_start": 2, "line_end": 2},
            "Acme paid Doe.",
        )
        require(paths == {text_path.resolve()} and not failures, "line anchor changed")

        _, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "research/filing.json", "json_pointer": "/record/title"},
            "Acme paid Doe.",
        )
        require(not failures, f"JSON Pointer anchor changed: {failures}")

        _, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "research/filing.txt", "line_start": 2, "line_end": 2},
            "Acme did not pay Doe.",
        )
        require(any("exact quote" in item for item in failures), "quote mismatch accepted")

        outside = Path(tmp) / "outside.txt"
        outside.write_text("Acme paid Doe.\n", encoding="utf-8")
        _, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "../outside.txt", "line_start": 1, "line_end": 1},
            "Acme paid Doe.",
        )
        require(any("case-local" in item for item in failures), "path traversal accepted")

        entry = {
            "local_file": "research/filing.txt",
            "access_method": "full_text",
            "sha256": hashlib.sha256(text_path.read_bytes()).hexdigest(),
        }
        _, failures = VALIDATOR.validate_evidence_entry(
            case, entry, "F1", "Acme paid Doe.", {},
            canonical_bundle=False, verdict="verified",
        )
        require(not failures, f"valid artifact hash rejected: {failures}")
        entry["sha256"] = "0" * 64
        _, failures = VALIDATOR.validate_evidence_entry(
            case, entry, "F1", "Acme paid Doe.", {},
            canonical_bundle=False, verdict="verified",
        )
        require(any("does not match" in item for item in failures), "stale hash accepted")

        # Contract 1.1 end to end: not_checkable needs no anchor; a removed
        # verdict is refused; a legacy 1.0 file keeps its vocabulary.
        data = case / "data"
        data.mkdir()
        (data / "findings.json").write_text(json.dumps({
            "project": "p", "findings": [{
                "id": "F1", "claim": "Acme will pay Doe next year.", "evidence": ["forecast"],
                "sources": [{"url": "https://example.org"}], "confidence": "low"}]}), encoding="utf-8")
        def fact_check(version: str | None, verdict: str) -> None:
            doc = {"project": "p", "claims": [{
                "id": 1, "finding_id": "F1", "claim_text": "Acme will pay Doe next year.",
                "verdict": verdict, "confidence": "low", "evidence_for": [], "evidence_against": []}]}
            if version:
                doc["schema_version"] = version
            (data / "fact-check.json").write_text(json.dumps(doc), encoding="utf-8")
        fact_check("1.1", "not_checkable")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"1.1 not_checkable refused: {results}")
        fact_check("1.1", "mischaracterized")
        results, code = VALIDATOR.validate(case)
        require(code != 0 and "contract 1.1" in json.dumps(results), f"1.1 removed verdict accepted: {results}")
        fact_check(None, "mischaracterized")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"legacy 1.0 verdict no longer readable: {results}")


    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case"
        (case / "data").mkdir(parents=True)
        (case / "data" / "fact-check.json").write_text(
            json.dumps({"project": "nothing-qualified", "claims": []}), encoding="utf-8"
        )
        findings = {"project": "nothing-qualified", "findings": []}
        (case / "data" / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        _, code = VALIDATOR.validate(case)
        require(code == 3, "an empty findings list without an explicit record passed")
        findings["no_qualifying_findings"] = {
            "reason": "No candidate claim met the evidence threshold in the approved directions."
        }
        (case / "data" / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"an explicit no-qualifying-findings outcome failed: {results}")

    print("validate-fact-check characterization: 11 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
