#!/usr/bin/env python3
"""Regression checks for Spotlight provenance manifest generation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build-provenance-manifest.py"
SPEC = importlib.util.spec_from_file_location("build_provenance_manifest", BUILDER)
assert SPEC and SPEC.loader
PROVENANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROVENANCE)


def canonical_hash(value: object) -> str:
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def copy_fixture_case(tmp: Path) -> Path:
    case_dir = tmp / "sample-investigation"
    data_dir = case_dir / "data"
    research_dir = case_dir / "research"
    data_dir.mkdir(parents=True)
    research_dir.mkdir()

    fixtures = ROOT / "tests" / "fixtures"
    shutil.copy(fixtures / "findings.sample.json", data_dir / "findings.json")
    shutil.copy(fixtures / "fact-check.sample.json", data_dir / "fact-check.json")
    shutil.copy(fixtures / "evidence-bundle.sample.json", data_dir / "evidence-bundle.json")
    write_json(data_dir / "investigation-log.json", {"schema_version": "1.0", "entries": []})
    (case_dir / "summary.md").write_text("# Sample Investigation\n", encoding="utf-8")
    return case_dir


def activate_case(case_dir: Path) -> None:
    data = case_dir / "data"
    findings = json.loads((data / "findings.json").read_text(encoding="utf-8"))
    findings["schema_version"] = "1.1"
    finding = findings["findings"][0]
    finding_fp = canonical_hash({"claim": finding["claim"]})
    finding["finding_fingerprint"] = finding_fp
    for other in findings["findings"][1:]:
        other["finding_fingerprint"] = canonical_hash({"claim": other["claim"]})
    write_json(data / "findings.json", findings)

    anchor = case_dir / "research" / "filing.txt"
    anchor.write_text(finding["claim"] + "\n", encoding="utf-8")
    anchor_sha = hashlib.sha256(anchor.read_bytes()).hexdigest()
    evidence_bundle = json.loads((data / "evidence-bundle.json").read_text(encoding="utf-8"))
    evidence_bundle["items"][0]["raw_path"] = "research/filing.txt"
    evidence_bundle["items"][0]["sha256"] = anchor_sha
    write_json(data / "evidence-bundle.json", evidence_bundle)
    expression_core = {
        "text": finding["claim"],
        "anchor_ref": {"path": "research/filing.txt", "line_start": 1, "line_end": 1},
        "anchor_sha256": anchor_sha,
        "original_evidence_bundle_id": "E1",
        "original_artifact_sha256": anchor_sha,
        "language": "en",
        "attribution": "BVI FSC filing",
        "direct_quote": True,
    }
    expression_fp = canonical_hash(expression_core)
    link = {
        "finding_id": "F1",
        "finding_fingerprint": finding_fp,
        "relation": "supports",
    }
    link["link_fingerprint"] = canonical_hash({
        "expression_fingerprint": expression_fp,
        **link,
    })
    expressions = {
        "schema_version": "1.0",
        "project": findings["project"],
        "created_at": "2026-07-16T12:00:00Z",
        "expressions": [{
            "id": "SX1",
            **expression_core,
            "expression_fingerprint": expression_fp,
            "finding_links": [link],
            "lifecycle_events": [{
                "event": "activated",
                "timestamp": "2026-07-16T12:00:00Z",
                "actor": "investigator",
                "reason": "Captured during acquisition.",
            }],
            "created_by": "investigator",
            "cycle": 1,
        }],
    }
    write_json(data / "source-expressions.json", expressions)

    fact_check = json.loads((data / "fact-check.json").read_text(encoding="utf-8"))
    fact_check["claims"][0]["evidence_for"][0]["source_expression_refs"] = [{
        "expression_id": "SX1",
        "expression_fingerprint": expression_fp,
        "finding_fingerprint": finding_fp,
        "link_fingerprint": link["link_fingerprint"],
    }]
    write_json(data / "fact-check.json", fact_check)
    hashes = {
        "findings_sha256": hashlib.sha256((data / "findings.json").read_bytes()).hexdigest(),
        "fact_check_sha256": hashlib.sha256((data / "fact-check.json").read_bytes()).hexdigest(),
        "evidence_bundle_sha256": hashlib.sha256((data / "evidence-bundle.json").read_bytes()).hexdigest(),
        "source_expressions_sha256": hashlib.sha256((data / "source-expressions.json").read_bytes()).hexdigest(),
    }
    write_json(data / "case-contract.json", {
        "schema_version": "1.0",
        "project": findings["project"],
        "current_contract_version": "1.1",
        "activation_events": [{
            "event_id": "activate-provenance-fixture",
            "previous_contract_version": "1.0",
            "activated_contract_version": "1.1",
            "activated_at": "2026-07-16T12:00:00Z",
            "tool_version": "test/1",
            "prior_input_hashes": {key: value for key, value in hashes.items() if key != "source_expressions_sha256"},
            "activated_artifact_hashes": hashes,
        }],
    })


def run_builder(case_dir: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(BUILDER), str(case_dir), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def assert_schema(document: dict) -> None:
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        return
    schema = json.loads(
        (ROOT / "schemas" / "provenance-manifest.schema.json").read_text(encoding="utf-8")
    )
    errors = sorted(Draft7Validator(schema).iter_errors(document), key=lambda error: list(error.path))
    assert not errors, "\n".join(error.message for error in errors)


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)

        # Legacy cases retain the original single-file contract.
        legacy = copy_fixture_case(tmp / "legacy")
        run_builder(legacy)
        legacy_manifest = json.loads(
            (legacy / "data" / "provenance-manifest.json").read_text(encoding="utf-8")
        )
        assert legacy_manifest["schema_version"] == "1.0"
        assert legacy_manifest["status"] == "unsigned"
        assert legacy_manifest["claims"] and legacy_manifest["sources"]
        assert_schema(legacy_manifest)
        secret_endpoint = run_builder(
            legacy,
            "--sign-endpoint",
            "https://user:secret@signer.example/sign",
            ok=False,
        )
        assert secret_endpoint.returncode == 2
        assert "must not contain credentials" in secret_endpoint.stderr

        case_dir = copy_fixture_case(tmp / "activated")
        activate_case(case_dir)
        pointer_path = case_dir / "data" / "provenance-manifest.json"

        # Repeated unsigned builds reuse one immutable revision.
        run_builder(case_dir)
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        revision_path = case_dir / pointer["revision_path"]
        first_revision_bytes = revision_path.read_bytes()
        revision = json.loads(first_revision_bytes)
        assert pointer["derived_status"] == "current"
        assert revision["parent_revision_id"] is None
        assert {item["kind"] for item in revision["case_artifacts"]} >= {
            "findings", "fact_check", "source_expressions", "evidence_bundle"
        }
        expression = revision["claims"][0]["source_expressions"][0]
        assert expression["expression_id"] == "SX1"
        assert expression["relation"] == "supports"
        assert expression["anchor_sha256"]
        assert expression["original_artifact_sha256"] == hashlib.sha256(
            (case_dir / "research" / "filing.txt").read_bytes()
        ).hexdigest()
        assert revision["claims"][0]["finding_fingerprint"]
        assert revision["claims"][0]["fact_check_fingerprint"]
        assert_schema(pointer)
        assert_schema(revision)

        run_builder(case_dir)
        assert len(list((case_dir / "data" / "provenance-manifests").glob("*.json"))) == 1
        assert revision_path.read_bytes() == first_revision_bytes

        # Fact-check mutation makes the pointer stale and a rebuild chains a revision.
        fact_path = case_dir / "data" / "fact-check.json"
        fact_check = json.loads(fact_path.read_text(encoding="utf-8"))
        fact_check["claims"][0]["notes"] = "Editorial review mutation."
        write_json(fact_path, fact_check)
        stale = run_builder(case_dir, "--check-current", ok=False)
        assert stale.returncode == 1 and stale.stdout.strip() == "stale"
        assert json.loads(pointer_path.read_text())["derived_status"] == "stale"
        run_builder(case_dir)
        child_pointer = json.loads(pointer_path.read_text())
        child_revision = json.loads((case_dir / child_pointer["revision_path"]).read_text())
        assert child_revision["parent_revision_id"] == pointer["revision_id"]
        assert child_revision["parent_input_set_hash"] == pointer["input_set_hash"]
        assert revision_path.read_bytes() == first_revision_bytes

        # Superseding an expression requires the stale fact-check reference to be removed.
        expression_path = case_dir / "data" / "source-expressions.json"
        expressions = json.loads(expression_path.read_text())
        expressions["expressions"][0]["lifecycle_events"].append({
            "event": "withdrawn",
            "timestamp": "2026-07-16T14:00:00Z",
            "actor": "human",
            "reason": "Source correction.",
        })
        write_json(expression_path, expressions)
        rejected = run_builder(case_dir, ok=False)
        assert rejected.returncode == 2 and "inactive source expression SX1" in rejected.stderr
        fact_check = json.loads(fact_path.read_text())
        fact_check["claims"][0]["verdict"] = "unverified"
        fact_check["claims"][0]["evidence_for"][0].pop("source_expression_refs")
        write_json(fact_path, fact_check)
        run_builder(case_dir)

        # Signing keeps revision bytes immutable and receipts append-only.
        signed_pointer_before = json.loads(pointer_path.read_text())
        signed_revision_path = case_dir / signed_pointer_before["revision_path"]
        unsigned_bytes = signed_revision_path.read_bytes()
        revision_for_signing = json.loads(signed_revision_path.read_text())
        calls = []
        PROVENANCE.post_for_signing = lambda *args: calls.append(args) or {"receipt": "signed-1"}
        PROVENANCE.sign_revision(
            case_dir, signed_pointer_before, revision_for_signing,
            "http://localhost/sign", None, "test-key", None,
        )
        PROVENANCE.atomic_replace(pointer_path, PROVENANCE.rendered_bytes(signed_pointer_before))
        assert len(calls) == 1
        signed_pointer = json.loads(pointer_path.read_text())
        assert signed_pointer["signing_status"] == "signed"
        first_receipt = case_dir / signed_pointer["receipt_path"]
        first_receipt_bytes = first_receipt.read_bytes()
        assert signed_revision_path.read_bytes() == unsigned_bytes

        # A new input revision records one deduplicated failed attempt, then retries safely.
        fact_check = json.loads(fact_path.read_text())
        fact_check["claims"][0]["notes"] = "Changed after signing."
        write_json(fact_path, fact_check)
        run_builder(case_dir)
        failed_pointer = json.loads(pointer_path.read_text())
        failed_revision = json.loads((case_dir / failed_pointer["revision_path"]).read_text())
        failures = []

        def fail_signing(*args: object) -> dict:
            failures.append(args)
            raise json.JSONDecodeError("bad receipt", "not-json", 0)

        PROVENANCE.post_for_signing = fail_signing
        PROVENANCE.sign_revision(
            case_dir, failed_pointer, failed_revision,
            "http://localhost/sign", None, None, None,
        )
        PROVENANCE.sign_revision(
            case_dir, failed_pointer, failed_revision,
            "http://localhost/sign", None, None, None,
        )
        PROVENANCE.atomic_replace(pointer_path, PROVENANCE.rendered_bytes(failed_pointer))
        assert len(failures) == 2
        assert failed_pointer["signing_status"] == "signing_failed"
        attempts = list((case_dir / "data" / "provenance-signing-attempts").glob("*.json"))
        assert len(attempts) == 1
        retry_revision = case_dir / failed_pointer["revision_path"]
        retry_revision_bytes = retry_revision.read_bytes()

        successes = []
        PROVENANCE.post_for_signing = lambda *args: successes.append(args) or {"receipt": "signed-retry"}
        PROVENANCE.sign_revision(
            case_dir, failed_pointer, failed_revision,
            "http://localhost/sign", None, None, None,
        )
        PROVENANCE.sign_revision(
            case_dir, failed_pointer, failed_revision,
            "http://localhost/sign", None, None, None,
        )
        PROVENANCE.atomic_replace(pointer_path, PROVENANCE.rendered_bytes(failed_pointer))
        assert len(successes) == 1  # already-signed retries are idempotent
        retry_pointer = json.loads(pointer_path.read_text())
        assert retry_pointer["signing_status"] == "signed"
        assert retry_revision.read_bytes() == retry_revision_bytes
        assert signed_revision_path.read_bytes() == unsigned_bytes
        assert first_receipt.read_bytes() == first_receipt_bytes
        assert len(list((case_dir / "data" / "provenance-signing-receipts").glob("*.json"))) == 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
