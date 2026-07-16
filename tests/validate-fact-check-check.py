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

    print("validate-fact-check characterization: 6 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
