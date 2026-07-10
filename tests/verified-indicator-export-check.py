#!/usr/bin/env python3
"""Regression checks for verified technical-indicator export."""

from __future__ import annotations

import copy
import csv
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export-verified-indicators.py"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_export(case_dir: Path, fmt: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--findings",
            str(case_dir / "data" / "findings.json"),
            "--fact-check",
            str(case_dir / "data" / "fact-check.json"),
            "--format",
            fmt,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def fixtures() -> tuple[dict, dict]:
    findings = {
        "schema_version": "1.0",
        "project": "indicator-regression",
        "investigated_at": "2026-07-10T09:00:00Z",
        "findings": [
            {
                "id": "F1",
                "claim": "A passive source observed the indicator during the stated window.",
                "evidence": "research/passive-record.json",
                "sources": [{"url": "https://source.example/report", "type": "url"}],
                "confidence": "high",
            },
            {
                "id": "F2",
                "claim": "A second indicator remains unverified.",
                "evidence": "research/unverified.json",
                "sources": [{"url": "https://do-not-extract.example/article", "type": "url"}],
                "confidence": "low",
            },
            {
                "id": "F3",
                "claim": "A mixed-verdict finding must fail closed.",
                "evidence": "research/mixed.json",
                "sources": [{"url": "https://mixed-source.example/article", "type": "url"}],
                "confidence": "medium",
            },
        ],
        "technical_indicators": [
            {
                "id": "TI-1",
                "finding_id": "F1",
                "type": "domain",
                "value": "Observed.Example",
                "context": "Observed in a preserved passive record; inclusion is time-bounded.",
                "sources": ["research/passive-record.json", "https://source.example/report"],
            },
            {
                "id": "TI-2",
                "finding_id": "F2",
                "type": "ipv4",
                "value": "203.0.113.10",
                "context": "This explicit indicator is not verified.",
                "sources": ["research/unverified.json"],
            },
            {
                "id": "TI-3",
                "finding_id": "F3",
                "type": "sha256",
                "value": "a" * 64,
                "context": "One of two linked claims is not verified.",
                "sources": ["research/mixed.json"],
            },
            {
                "id": "TI-UNLINKED",
                "finding_id": "F1",
                "type": "domain",
                "value": "unreviewed.example",
                "context": "This item shares a verified finding but has no claim-level assessment.",
                "sources": ["research/passive-record.json"],
            },
        ],
    }
    fact_check = {
        "schema_version": "1.0",
        "project": "indicator-regression",
        "checked_at": "2026-07-10T10:00:00Z",
        "claims": [
            {
                "id": "FC1",
                "finding_id": "F1",
                "technical_indicator_ids": ["TI-1"],
                "claim_text": "Observed.Example was independently observed.",
                "verdict": "verified",
            },
            {
                "id": "FC2",
                "finding_id": "F2",
                "technical_indicator_ids": ["TI-2"],
                "claim_text": "203.0.113.10 remains open.",
                "verdict": "unverified",
            },
            {
                "id": "FC3a",
                "finding_id": "F3",
                "technical_indicator_ids": ["TI-3"],
                "claim_text": f"{'a' * 64} has partial supporting context.",
                "verdict": "verified",
            },
            {
                "id": "FC3b",
                "finding_id": "F3",
                "claim_text": "part two",
                "verdict": "partially_verified",
            },
        ],
    }
    return findings, fact_check


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="spotlight-indicator-test-") as temp:
        case_dir = Path(temp) / "case"
        findings, fact_check = fixtures()
        findings_path = case_dir / "data" / "findings.json"
        fact_check_path = case_dir / "data" / "fact-check.json"
        write_json(findings_path, findings)
        write_json(fact_check_path, fact_check)

        json_path = case_dir / "exports" / "verified.json"
        first = run_export(case_dir, "json", json_path)
        require(first.returncode == 0, first.stderr)
        first_bytes = json_path.read_bytes()
        payload = json.loads(first_bytes)
        require(payload["indicator_count"] == 1, "only fully verified findings may export")
        require(payload["indicators"][0]["id"] == "TI-1", "verified explicit indicator missing")
        require(
            payload["indicators"][0]["normalized_value"] == "observed.example",
            "domain normalization drifted",
        )
        require("do-not-extract.example" not in first_bytes.decode(), "source URL was regex-extracted")
        require("203.0.113.10" not in first_bytes.decode(), "unverified indicator leaked")
        require("unreviewed.example" not in first_bytes.decode(), "unreviewed attached indicator leaked")

        second = run_export(case_dir, "json", json_path)
        require(second.returncode == 0, second.stderr)
        require(json_path.read_bytes() == first_bytes, "JSON rerun is not deterministic")

        formula_findings = copy.deepcopy(findings)
        formula_findings["technical_indicators"][0]["context"] = '=HYPERLINK("https://attacker.example")'
        write_json(findings_path, formula_findings)
        csv_path = case_dir / "exports" / "verified.csv"
        csv_result = run_export(case_dir, "csv", csv_path)
        require(csv_result.returncode == 0, csv_result.stderr)
        require(csv_path.read_text(encoding="utf-8").count("\n") == 2, "CSV row count drifted")
        with csv_path.open(encoding="utf-8", newline="") as handle:
            csv_row = next(csv.DictReader(handle))
        require(csv_row["context"].startswith("'="), "CSV formula injection was not neutralized")
        write_json(findings_path, findings)

        stix_path = case_dir / "exports" / "verified.stix.json"
        stix_result = run_export(case_dir, "stix", stix_path)
        require(stix_result.returncode == 0, stix_result.stderr)
        stix = json.loads(stix_path.read_text(encoding="utf-8"))
        require(stix["type"] == "bundle" and len(stix["objects"]) == 1, "STIX bundle drifted")
        require(stix["objects"][0]["pattern"] == "[domain-name:value = 'observed.example']", "STIX pattern drifted")
        stix_uuid = uuid.UUID(stix["objects"][0]["id"].split("--", 1)[1])
        require(stix_uuid.version == 5, "deterministic STIX identifier is not UUIDv5")
        require(
            "00abedb4-aa42-466c-9c01-fed23315a9b7" not in SCRIPT.read_text(encoding="utf-8"),
            "reserved STIX SCO UUIDv5 namespace reused for an SDO",
        )

        duplicate_value = copy.deepcopy(findings)
        duplicate_value["technical_indicators"].append(
            {
                "id": "TI-4",
                "finding_id": "F1",
                "type": "domain",
                "value": "Observed.Example",
                "context": "A second explicit occurrence linked to the same verified finding.",
                "sources": ["research/passive-record-2.json"],
            }
        )
        write_json(findings_path, duplicate_value)
        duplicate_fact_check = copy.deepcopy(fact_check)
        duplicate_fact_check["claims"][0]["technical_indicator_ids"].append("TI-4")
        write_json(fact_check_path, duplicate_fact_check)
        duplicate_stix_path = case_dir / "exports" / "duplicate.stix.json"
        duplicate_stix = run_export(case_dir, "stix", duplicate_stix_path)
        require(duplicate_stix.returncode == 0, duplicate_stix.stderr)
        duplicate_objects = json.loads(duplicate_stix_path.read_text(encoding="utf-8"))["objects"]
        require(len({item["id"] for item in duplicate_objects}) == 2, "duplicate STIX object IDs emitted")
        write_json(findings_path, findings)
        write_json(fact_check_path, fact_check)

        missing_value_fact_check = copy.deepcopy(fact_check)
        missing_value_fact_check["claims"][0]["claim_text"] = "A different indicator was assessed."
        write_json(fact_check_path, missing_value_fact_check)
        missing_value_path = case_dir / "exports" / "missing-value.json"
        missing_value = run_export(case_dir, "json", missing_value_path)
        require(
            missing_value.returncode == 2 and not missing_value_path.exists(),
            "claim link without the exact indicator value failed open",
        )

        unknown_link_fact_check = copy.deepcopy(fact_check)
        unknown_link_fact_check["claims"][0]["technical_indicator_ids"].append("TI-404")
        write_json(fact_check_path, unknown_link_fact_check)
        unknown_link_path = case_dir / "exports" / "unknown-link.json"
        unknown_link = run_export(case_dir, "json", unknown_link_path)
        require(
            unknown_link.returncode == 2 and not unknown_link_path.exists(),
            "unknown claim-level indicator link failed open",
        )
        write_json(fact_check_path, fact_check)

        outside = Path(temp) / "outside.json"
        boundary = run_export(case_dir, "json", outside)
        require(boundary.returncode == 2 and not outside.exists(), "case output boundary failed open")

        invalid = copy.deepcopy(findings)
        invalid["technical_indicators"][0]["type"] = "email"
        invalid["technical_indicators"][0]["value"] = "person@example.org"
        write_json(findings_path, invalid)
        pii_path = case_dir / "exports" / "pii.json"
        pii = run_export(case_dir, "json", pii_path)
        require(pii.returncode == 2 and not pii_path.exists(), "PII selector type was not rejected")

        query_url = copy.deepcopy(findings)
        query_url["technical_indicators"][0].update(
            {
                "type": "url",
                "value": "https://observed.example/reset?token=secret&email=victim@example.org",
            }
        )
        write_json(findings_path, query_url)
        query_path = case_dir / "exports" / "query-url.json"
        query = run_export(case_dir, "json", query_path)
        require(query.returncode == 2 and not query_path.exists(), "URL query PII was not rejected")

        malformed_url = copy.deepcopy(findings)
        malformed_url["technical_indicators"][0].update(
            {"type": "url", "value": "https://[malformed.example"}
        )
        write_json(findings_path, malformed_url)
        malformed_path = case_dir / "exports" / "malformed-url.json"
        malformed = run_export(case_dir, "json", malformed_path)
        require(malformed.returncode == 2 and not malformed_path.exists(), "malformed URL failed open")
        require("Traceback" not in malformed.stderr, "malformed URL escaped deterministic error handling")

    print("verified indicator export: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        raise SystemExit(1)
