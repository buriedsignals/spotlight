#!/usr/bin/env python3
"""Focused integration checks for deterministic legacy case migration."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/migrate-source-expressions.py"
FIXTURES = ROOT / "tests/fixtures/source-expression-migration"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(case: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(case), *arguments],
        capture_output=True,
        text=True,
    )


def build_case(root: Path, *, reverse: bool = False, ambiguous: bool = False) -> Path:
    case = root
    data = case / "data"
    research = case / "research"
    data.mkdir(parents=True)
    research.mkdir()
    anchor = research / "exact-anchor.txt"
    original = research / "original-artifact.txt"
    shutil.copyfile(FIXTURES / anchor.name, anchor)
    shutil.copyfile(FIXTURES / original.name, original)

    findings_rows = [
        {
            "id": "F1",
            "claim": "Acme paid Doe.",
            "evidence": "Registry filing.",
            "sources": [{
                "url": "https://example.test/filing",
                "type": "registry",
                "local_file": "research/exact-anchor.txt",
            }],
            "confidence": "high",
            "evidence_bundle_refs": ["E1"],
        },
        {
            "id": "F2",
            "claim": "The filing records a payment.",
            "evidence": "Registry filing.",
            "sources": [{
                "url": "https://example.test/filing",
                "type": "registry",
                "local_file": "research/exact-anchor.txt",
            }],
            "confidence": "medium",
            "evidence_bundle_refs": ["E1"],
        },
    ]
    claims = []
    for index, finding in enumerate(findings_rows, 1):
        source_ref = {"path": "research/exact-anchor.txt", "line_start": 2, "line_end": 2}
        if ambiguous and finding["id"] == "F1":
            source_ref = {"path": "research/exact-anchor.txt", "line_start": 1, "line_end": 2}
        claims.append({
            "id": f"FC{index}",
            "finding_id": finding["id"],
            "claim_text": finding["claim"],
            "verdict": "verified",
            "confidence": finding["confidence"],
            "evidence_for": [{
                "description": "Exact registry passage.",
                "source": "Registry filing",
                "access_method": "full_text",
                "evidence_bundle_id": "E1",
                "source_ref": source_ref,
                "quote": "Acme paid Doe.",
            }],
        })
    if reverse:
        findings_rows.reverse()
        claims.reverse()
    write_json(data / "findings.json", {
        "schema_version": "1.0",
        "project": "migration-case",
        "cycle": 1,
        "findings": findings_rows,
    })
    write_json(data / "fact-check.json", {
        "schema_version": "1.0",
        "project": "migration-case",
        "checked_at": "2026-07-16T12:00:00Z",
        "cycle": 1,
        "claims": claims,
    })
    write_json(data / "evidence-bundle.json", {
        "schema_version": "1.0",
        "project": "migration-case",
        "run_id": "migration-fixture",
        "created_at": "2026-07-16T11:00:00Z",
        "items": [{
            "id": "E1",
            "query_or_task": "Acquire registry filing",
            "acquisition_method": "manual",
            "source_url": "https://example.test/filing",
            "accessed": "2026-07-16T11:00:00Z",
            "raw_path": "research/original-artifact.txt",
            "sha256": sha(original),
            "text_derivatives": [{
                "id": "TD1",
                "derivative_type": "text_extraction",
                "path": "research/exact-anchor.txt",
                "sha256": sha(anchor),
                "human_verification_required": False,
                "language": "en",
            }],
            "claim_links": [
                {
                    "finding_id": finding["id"],
                    "claim_text": finding["claim"],
                    "support_type": "direct",
                }
                for finding in findings_rows
            ],
            "extraction_confidence": "high",
            "human_verification_required": False,
        }],
    })
    return case


def snapshot(data: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(data.glob("*.json"))}


def validate_schema(audit: dict) -> None:
    try:
        from jsonschema import Draft7Validator
    except ModuleNotFoundError:
        return
    schema = json.loads(
        (ROOT / "schemas/source-expression-migration.schema.json").read_text(encoding="utf-8")
    )
    errors = list(Draft7Validator(schema).iter_errors(audit))
    assert not errors, "; ".join(error.message for error in errors)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)

        exact = build_case(root / "exact")
        dry = run(exact)
        assert dry.returncode == 0 and "DRY-RUN READY" in dry.stdout, dry.stderr
        audit_path = exact / "data/source-expression-migration.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        validate_schema(audit)
        assert audit["eligible"] and len(audit["candidate_mappings"]) == 1
        assert audit["candidate_mappings"][0]["finding_ids"] == ["F1", "F2"]
        assert not (exact / "data/case-contract.json").exists(), "dry run activated the case"
        assert json.loads((exact / "data/findings.json").read_text())["schema_version"] == "1.0"

        dry_bytes = audit_path.read_bytes()
        dry_again = run(exact)
        assert dry_again.returncode == 0 and audit_path.read_bytes() == dry_bytes

        reordered = build_case(root / "reordered", reverse=True)
        assert run(reordered).returncode == 0
        reordered_audit = json.loads(
            (reordered / "data/source-expression-migration.json").read_text(encoding="utf-8")
        )
        assert (
            reordered_audit["candidate_mappings"][0]["expression_id"]
            == audit["candidate_mappings"][0]["expression_id"]
        ), "expression ID depends on input ordering"

        stale = build_case(root / "stale")
        assert run(stale).returncode == 0
        findings_path = stale / "data/findings.json"
        findings_path.write_bytes(findings_path.read_bytes() + b"\n")
        stale_apply = run(stale, "--apply")
        assert stale_apply.returncode == 3 and "stale dry run" in stale_apply.stderr
        assert not (stale / "data/case-contract.json").exists()

        interrupted = build_case(root / "interrupted")
        assert run(interrupted).returncode == 0
        write_json(interrupted / "data/source-expressions.json", {})
        interrupted_apply = run(interrupted, "--apply")
        assert interrupted_apply.returncode == 3 and "partial migration" in interrupted_apply.stderr
        assert not (interrupted / "data/case-contract.json").exists()

        ambiguous = build_case(root / "ambiguous", ambiguous=True)
        ambiguous_dry = run(ambiguous)
        assert ambiguous_dry.returncode == 0 and "DRY-RUN BLOCKED" in ambiguous_dry.stdout
        ambiguous_audit = json.loads(
            (ambiguous / "data/source-expression-migration.json").read_text(encoding="utf-8")
        )
        assert any(
            item["reason"] == "quote_not_exact_selected_text"
            for item in ambiguous_audit["skips"]
        )
        before_ambiguous_apply = snapshot(ambiguous / "data")
        refused = run(ambiguous, "--apply")
        assert refused.returncode == 3 and "migration is blocked" in refused.stderr
        assert snapshot(ambiguous / "data") == before_ambiguous_apply
        assert json.loads((ambiguous / "data/findings.json").read_text())["schema_version"] == "1.0"

        before_apply = snapshot(exact / "data")
        applied = run(exact, "--apply")
        assert applied.returncode == 0 and "case contract written last" in applied.stdout, applied.stderr
        data = exact / "data"
        assert (data / "case-contract.json").exists()
        assert json.loads((data / "findings.json").read_text())["schema_version"] == "1.1"
        expressions = json.loads((data / "source-expressions.json").read_text())
        assert len(expressions["expressions"]) == 1
        contract = json.loads((data / "case-contract.json").read_text())
        latest = contract["activation_events"][-1]["activated_artifact_hashes"]
        assert latest["source_expressions_sha256"] == sha(data / "source-expressions.json")
        assert before_apply["findings.json"] != (data / "findings.json").read_bytes()

        activated_snapshot = snapshot(data)
        repeated = run(exact, "--apply")
        assert repeated.returncode == 0 and "already applied" in repeated.stdout
        assert snapshot(data) == activated_snapshot, "repeated apply changed activated files"
        downgrade = run(exact)
        assert downgrade.returncode == 3 and "downgrade" in downgrade.stderr

        validation = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate-case.py"), str(exact)],
            capture_output=True,
            text=True,
        )
        assert validation.returncode == 0, validation.stderr

    print("ok   source-expression migration dry-run/apply contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
