#!/usr/bin/env python3
"""Characterization coverage for the fact-check evidence anchor contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-fact-check.py"
SPEC = importlib.util.spec_from_file_location("validate_fact_check", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)
REPORT_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "spotlight_report_fixture", ROOT / "tests" / "render-report-check.py"
)
REPORT_FIXTURE = importlib.util.module_from_spec(REPORT_FIXTURE_SPEC)
assert REPORT_FIXTURE_SPEC.loader is not None
REPORT_FIXTURE_SPEC.loader.exec_module(REPORT_FIXTURE)


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

        # Lines are numbered as grep -n numbers them: a form feed (pdftotext
        # puts one at every page break) does not start a new line.
        (research / "paged.txt").write_text("page one\fpage two\nAcme paid Doe.\n", encoding="utf-8")
        _, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "research/paged.txt", "line_start": 2, "line_end": 2},
            "Acme paid Doe.",
        )
        require(not failures, f"a form feed shifted the line numbering away from grep -n: {failures}")
        # An anchor recorded under the earlier numbering (a form feed ended a
        # line) still validates while its text matches exactly.
        _, failures = VALIDATOR.validate_source_ref(
            case,
            {"path": "research/paged.txt", "line_start": 3, "line_end": 3},
            "Acme paid Doe.",
        )
        require(not failures, f"an anchor numbered before the grep -n rule no longer validates: {failures}")

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
        _, failures, _ = VALIDATOR.validate_evidence_entry(
            case, entry, "F1", "Acme paid Doe.", {},
            canonical_bundle=False, verdict="verified",
        )
        require(not failures, f"valid artifact hash rejected: {failures}")
        entry["sha256"] = "0" * 64
        _, failures, _ = VALIDATOR.validate_evidence_entry(
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

    # Each bundle item belongs to the agent that acquired it. Its record is
    # checked whatever the verdicts are; whether it can anchor a positive
    # verdict is the fact-checker's call, and an honest record of a failed
    # acquisition never forces a downgrade or a rewrite.
    with tempfile.TemporaryDirectory() as tmp:
        case = REPORT_FIXTURE.build_case(Path(tmp))
        bundle_path = case / "data" / "evidence-bundle.json"
        findings_path = case / "data" / "findings.json"
        fact_check_path = case / "data" / "fact-check.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        gate = {
            "requested_source": "official record",
            "returned_artifact": "primary fetch failed; archive copy saved",
            "missing": "nothing material",
            "fallback_required": True,
            "fallback_outcome": "succeeded",
        }
        bundle["items"][0]["missing_source_gate"] = gate
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"a fallback that succeeded still fails: {results}")

        # A verdict relying on a source whose fallback is outstanding is the
        # fact-checker's to fix, and nothing asks for the record to change.
        gate.pop("fallback_outcome")
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        failures = [result for result in results if not result["ok"]]
        require(code == 3 and {result.get("owner") for result in failures} == {"fact-checker"}
                and "lower the verdict" in json.dumps(failures)
                and "fallback_outcome" not in json.dumps(failures),
                f"an outstanding fallback behind a verdict is not the fact-checker's: {results}")
        gate["fallback_outcome"] = "succeeded"

        # A failed alternate the investigator honestly keeps on the finding
        # does not block a verdict anchored elsewhere.
        alternate = case / "research" / "mirror.md"
        alternate.write_text("Mirror page: 403 Forbidden\n", encoding="utf-8")
        bundle["items"].append({
            "id": "E2",
            "query_or_task": "Acquire a mirror of the official record",
            "acquisition_method": "api",
            "source_url": "https://mirror.example.org/record",
            "accessed": "2026-07-10T12:05:00Z",
            "raw_path": "research/mirror.md",
            "sha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
            "claim_links": [{
                "finding_id": "F1",
                "claim_text": "Ada Lovelace is President of Northwind Research Cooperative.",
                "support_type": "insufficient",
            }],
            "extraction_confidence": "low",
            "human_verification_required": True,
            "missing_source_gate": {
                "requested_source": "mirror of the official record",
                "returned_artifact": "403 interstitial",
                "missing": "the record itself",
                "fallback_required": True,
                "fallback_outcome": "failed",
            },
        })
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        findings["findings"][0]["evidence_bundle_refs"] = ["E1", "E2"]
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"an honest failed alternate on record forced a failure: {results}")

        # Lowering every verdict does not hide a broken record.
        fact_check = json.loads(fact_check_path.read_text(encoding="utf-8"))
        for claim in fact_check["claims"]:
            claim["verdict"] = "unverified"
        fact_check_path.write_text(json.dumps(fact_check), encoding="utf-8")
        bundle["items"][1]["sha256"] = "0" * 64
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 3 and any(result.get("owner") == "investigator" and "E2 sha256 does not match"
                                  in result["reason"] for result in results),
                f"downgrading every verdict hid a broken bundle record: {results}")
        bundle["items"][1]["sha256"] = hashlib.sha256(alternate.read_bytes()).hexdigest()
        bundle["items"][1]["claim_links"] = []
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 3 and "E2 has no explicit finding link" in json.dumps(results),
                f"downgrading every verdict hid an unlinked bundle ref: {results}")
        bundle["items"][0]["claim_links"][0]["claim_text"] = "Ada Lovelace leads Northwind."
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 3 and "claim verbatim" in json.dumps(results),
                f"downgrading every verdict hid a broken claim link: {results}")

    # An item the fact-checker acquired itself is its own record: its
    # failures go to the fact-checker, not to the investigator.
    with tempfile.TemporaryDirectory() as tmp:
        case = REPORT_FIXTURE.build_case(Path(tmp))
        independent = case / "research" / "registry.md"
        independent.write_text(
            "Registry: Ada Lovelace, President, Northwind Research Cooperative.\n", encoding="utf-8")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["items"].append({
            "id": "E9",
            "created_by": "fact-checker",
            "query_or_task": "Independent registry check",
            "acquisition_method": "api",
            "source_url": "https://registry.example.org/northwind",
            "accessed": "2026-07-10T13:00:00Z",
            "raw_path": "research/registry.md",
            "sha256": hashlib.sha256(independent.read_bytes()).hexdigest(),
            "claim_links": [{
                "finding_id": "F1",
                "claim_text": "Ada Lovelace is President of Northwind Research Cooperative.",
                "support_type": "direct",
            }],
            "extraction_confidence": "high",
            "human_verification_required": False,
        })
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        fact_check_path = case / "data" / "fact-check.json"
        fact_check = json.loads(fact_check_path.read_text(encoding="utf-8"))
        fact_check["claims"][0]["evidence_for"] = [{
            "description": "An independent registry entry names the President.",
            "source": "https://registry.example.org/northwind",
            "access_method": "full_text",
            "evidence_bundle_id": "E9",
        }]
        fact_check_path.write_text(json.dumps(fact_check), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"a fact-checker's own bundle item cannot anchor its verdict: {results}")
        bundle["items"][1]["sha256"] = "0" * 64
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        results, code = VALIDATOR.validate(case)
        owners = {result.get("owner") for result in results if not result["ok"]}
        require(code == 3 and owners == {"fact-checker"},
                f"a fact-checker's own bundle record is routed to someone else: {results}")

    # A model may reuse its human-facing id in several batches. Repair targets
    # must identify the actual finding and raw claim position, not that label.
    with tempfile.TemporaryDirectory() as tmp:
        case = REPORT_FIXTURE.build_case(Path(tmp))
        path = case / "data" / "fact-check.json"
        document = json.loads(path.read_text())
        document["claims"][1]["id"] = document["claims"][0]["id"]
        document["claims"][1]["verdict"] = "verified"
        document["claims"][1]["evidence_for"] = [{
            "access_method": "full_text", "local_file": "research/official-record.md",
            "description": "A deliberately mismatched quote.", "source": "https://example.org/official_record(v1)",
            "source_ref": {"path": "research/official-record.md", "line_start": 1, "line_end": 1},
            "quote": "This sentence is absent from the source.",
        }]
        path.write_text(json.dumps(document))
        results, code = VALIDATOR.validate(case)
        failed = [row for row in results if not row["ok"]]
        require(code == 3 and len(failed) == 1, f"duplicate label changed validation: {results}")
        require(failed[0].get("finding_id") == "F2" and failed[0].get("locator") == "fact-check.json.claims[1]",
                f"duplicate claim label hides the actual repair target: {failed}")

    # r12 (r12-ingest-audit A3): in an activated case a verified verdict needs
    # evidence the fact-checker acquired. The F1/F2 shape, the investigator's
    # E1 alone, fails for the fact-checker; its own FC-E1 passes; unverified is
    # the honest exit.
    with tempfile.TemporaryDirectory() as tmp:
        case = REPORT_FIXTURE.build_case(Path(tmp) / "activated")
        REPORT_FIXTURE.activate_case(case)
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"a verified verdict on the fact-checker's own capture failed: {results}")
        fact_check_path = case / "data" / "fact-check.json"
        contract_path = case / "data" / "case-contract.json"

        def rebind() -> None:
            # The activation binds the fact-check's hash; this edit is the test's.
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            for event in contract.get("activation_events", []):
                hashes = event.get("activated_artifact_hashes")
                if isinstance(hashes, dict) and "fact_check_sha256" in hashes:
                    hashes["fact_check_sha256"] = hashlib.sha256(fact_check_path.read_bytes()).hexdigest()
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
        own = json.loads(fact_check_path.read_text(encoding="utf-8"))
        borrowed = json.loads(json.dumps(own))
        borrowed["claims"][0]["evidence_for"][0].update({"evidence_bundle_id": "E1", "local_file": "research/official-record.md"})
        fact_check_path.write_text(json.dumps(borrowed), encoding="utf-8")
        rebind()
        results, code = VALIDATOR.validate(case)
        require(code == 3 and any(row.get("owner") == "fact-checker" and "cites no evidence the fact-checker acquired" in row.get("reason", "")
                                  and "or set the verdict to unverified" in row.get("reason", "") for row in results),
                f"a verified verdict on the investigator's capture alone passed: {results}")
        borrowed["claims"][0]["verdict"] = "unverified"
        fact_check_path.write_text(json.dumps(borrowed), encoding="utf-8")
        rebind()
        results, code = VALIDATOR.validate(case)
        require(code == 0, f"unverified on the investigator's capture is not an honest exit: {results}")

        def acquired_failure() -> bool:
            results, code = VALIDATOR.validate(case)
            return code == 3 and any("cites no evidence the fact-checker acquired" in row.get("reason", "") for row in results)

        # Codex review of fix11: the fact-checker's item id, with the quote
        # pointed at the investigator's file, certifies nothing.
        mismatched = json.loads(json.dumps(own))
        mismatched["claims"][0]["evidence_for"][0]["local_file"] = "research/official-record.md"
        fact_check_path.write_text(json.dumps(mismatched), encoding="utf-8")
        rebind()
        require(acquired_failure(), "an FC item id certified the investigator's file")

        # Codex review of fix11: in the Pi harness the fetch receipt decides; a
        # created_by fact-checker that anyone can write does not.
        fact_check_path.write_text(json.dumps(own), encoding="utf-8")
        rebind()
        harness_data = Path(tmp) / "harness-data"
        os.environ["SPOTLIGHT_HARNESS_DATA"] = str(harness_data)
        try:
            require(acquired_failure(), "a created_by fact-checker item with no fetch receipt was accepted in the harness")
            key = hashlib.sha256(os.path.realpath(case).encode("utf-8")).hexdigest()[:16]
            registry = harness_data / "work" / "fetched" / f"{key}.json"
            registry.parent.mkdir(parents=True)
            capture = "research/fc-official-record.md.raw"
            registry.write_text(json.dumps({capture: {
                "url": "https://example.org/official_record(v1)", "accessed": "2026-07-16T12:30:00Z", "provider": "crawl4ai",
                "sha256": hashlib.sha256((case / capture).read_bytes()).hexdigest(), "acquired_by": "fact-checker"}}), encoding="utf-8")
            results, code = VALIDATOR.validate(case)
            require(code == 0, f"the fact-checker's registered capture was refused: {results}")
        finally:
            del os.environ["SPOTLIGHT_HARNESS_DATA"]

    print("validate-fact-check characterization: 24 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
