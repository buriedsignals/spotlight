#!/usr/bin/env python3
"""End-to-end checks for the reviewed Spotlight knowledge graph port."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "knowledge_destination.py"
FIXTURE = ROOT / "tests" / "fixtures" / "knowledge-batch.sample.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORT = load_module("knowledge_destination", SCRIPT)
CASE_HELPERS = load_module("validate_case_helpers", ROOT / "tests" / "validate-case-check.py")


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict, canonical: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if canonical:
        path.write_bytes(PORT.canonical_json_bytes(value))
    else:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    os.chmod(target, 0o600)


def run(*args: str, ok: bool = True, text: bool = True):
    result = subprocess.run(
        [*args], cwd=ROOT, text=text, capture_output=True, timeout=120
    )
    if ok and result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode()
        raise AssertionError(stderr)
    return result


def run_cli(*args: str, ok: bool = True):
    return run(sys.executable, str(SCRIPT), *args, ok=ok)


def subject_for(kind: str, row: dict) -> dict:
    return {
        "kind": kind,
        "id": row["project"] if kind == "source_case" else row["id"],
        "version": row.get("version", 1),
        "payload_sha256": PORT.payload_sha256(row),
    }


def rebind_decisions(batch: dict) -> None:
    subjects: dict[str, dict] = {
        batch["source_case"]["review_decision_id"]: subject_for(
            "source_case", batch["source_case"]
        )
    }
    for collection, kind in (
        ("claims", "claim"),
        ("events", "event"),
        ("story_arcs", "story_arc"),
        ("claim_event_memberships", "claim_event_membership"),
        ("event_story_arc_memberships", "event_story_arc_membership"),
    ):
        for row in batch[collection]:
            for field in (
                "review_decision_id", "event_link_decision_id", "story_link_decision_id"
            ):
                decision_id = row.get(field)
                if decision_id:
                    subjects[decision_id] = subject_for(kind, row)
    for decision in batch["review_decisions"]:
        if decision["id"] in subjects:
            decision["subject"] = subjects[decision["id"]]


def activated_batch(case_dir: Path) -> dict:
    CASE_HELPERS.write_activated_case(case_dir)
    batch = load_fixture()
    findings = json.loads((case_dir / "data" / "findings.json").read_text(encoding="utf-8"))
    expressions = json.loads(
        (case_dir / "data" / "source-expressions.json").read_text(encoding="utf-8")
    )
    batch["claims"][0]["origin"]["finding_fingerprint"] = (
        findings["findings"][0]["finding_fingerprint"]
    )
    batch["claims"][0]["source_expression_refs"][0]["expression_fingerprint"] = (
        expressions["expressions"][0]["expression_fingerprint"]
    )
    contract = case_dir / "data" / "case-contract.json"
    batch["source_case"]["case_contract_sha256"] = hashlib.sha256(
        contract.read_bytes()
    ).hexdigest()
    rebind_decisions(batch)
    return batch


def create_signer(workspace: Path) -> tuple[Path, Path]:
    key = workspace / "trust" / "reviewer"
    key.parent.mkdir(parents=True, mode=0o700)
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
    allowed = key.parent / "allowed_signers"
    allowed.write_text(
        "journalist:fixture " + (key.with_suffix(".pub")).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return key, allowed


def policy_receipt(revision: int, status: str = "active") -> dict:
    receipt = {
        "schema_version": "spotlight-case-policy-receipt/v1",
        "receipt_id": f"receipt:test-case-policy:{revision}",
        "case_id": "case:test-case",
        "policy_revision": revision,
        "status": status,
        "classification": "internal",
        "allowed_destinations": ["destination:test"],
        "provider_policy": {
            "allowed_modes": ["full_text"],
            "provider_ids": [],
            "data_localities": ["local"],
            "network_egress": "denied",
            "max_retention_days": 0,
        },
        "issued_at": f"2026-08-18T1{revision}:00:00Z",
        "expires_at": "2027-08-18T00:00:00Z",
        "revocation_version": revision,
        "issuer": {
            "issuer_id": "issuer:test",
            "issuer_key_id": "key:test",
            "algorithm": "ed25519",
            "payload_sha256": "0" * 64,
            "signature": "dGVzdA==",
        },
    }
    if status == "revoked":
        receipt["revoked_at"] = "2026-08-18T14:00:00Z"
        receipt["revocation_reason"] = "Case scope was revoked."
    receipt["issuer"]["payload_sha256"] = PORT.case_policy_payload_sha256(receipt)
    return receipt


def stage_sign_commit(
    workspace: Path,
    case_dir: Path,
    batch: dict,
    key: Path,
    allowed: Path,
    database: Path,
) -> tuple[dict, dict]:
    batch_path = case_dir / "data" / "knowledge-batch.json"
    manifest_path = case_dir / "data" / "review-manifest.json"
    receipt_path = case_dir / "data" / "approval.json"
    write_json(batch_path, batch)
    if not database.exists():
        run_cli(
            "init-reference", "--workspace-root", str(workspace),
            "--db", str(database.relative_to(workspace)),
            "--destination-id", "newsroom:test",
            "--unsafe-local-reference-commit",
        )
    staged = run_cli(
        "stage", "--case-root", str(workspace), "--case-dir", "case",
        "--destination-id", "newsroom:test", "data/knowledge-batch.json",
    )
    manifest = json.loads(staged.stdout)
    write_json(manifest_path, manifest)
    approval = run_cli(
        "approval", "--manifest", str(manifest_path),
        "--reviewer-id", "journalist:fixture",
        "--approved-at", "2026-08-18T10:05:00Z",
    )
    receipt_path.write_text(approval.stdout, encoding="utf-8")
    run(
        "ssh-keygen", "-Y", "sign", "-f", str(key),
        "-n", PORT.APPROVAL_NAMESPACE, str(receipt_path),
    )
    committed = run_cli(
        "commit", "--workspace-root", str(workspace),
        "--case-root", str(workspace), "--case-dir", "case",
        "--db", str(database.relative_to(workspace)),
        "--expected-sha256", manifest["payload_sha256"],
        "--manifest", "data/review-manifest.json",
        "--approval-receipt", "data/approval.json",
        "--approval-signature", "data/approval.json.sig",
        "--allowed-signers", str(allowed.relative_to(workspace)),
        "--unsafe-local-reference-commit",
        "data/knowledge-batch.json",
    )
    return manifest, json.loads(committed.stdout)


def assert_schema(batch: dict) -> bool:
    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError as exc:
        if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
            raise AssertionError("jsonschema is required for this contract test") from exc
        return False
    schema = json.loads(
        (ROOT / "schemas" / "knowledge-batch.schema.json").read_text(encoding="utf-8")
    )
    Draft7Validator.check_schema(schema)
    errors = list(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(batch))
    assert not errors, "\n".join(error.message for error in errors)
    return True


def schema_errors(batch: dict) -> list[str]:
    from jsonschema import Draft7Validator
    schema = json.loads(
        (ROOT / "schemas" / "knowledge-batch.schema.json").read_text(encoding="utf-8")
    )
    return [error.message for error in Draft7Validator(schema).iter_errors(batch)]


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_tmp:
        workspace = Path(raw_tmp).resolve()
        preexisting = workspace / "preexisting" / ".knowledge-workspace"
        preexisting.mkdir(parents=True)
        os.chmod(preexisting, 0o755)
        created = PORT._prepare_private_database(preexisting / "spotlight.sqlite")
        assert created is True
        assert stat.S_IMODE(preexisting.stat().st_mode) == 0o700
        assert stat.S_IMODE((preexisting / "spotlight.sqlite").stat().st_mode) == 0o600
        case_dir = workspace / "case"
        database = workspace / "knowledge" / "spotlight.sqlite"
        batch = activated_batch(case_dir)
        schema_checked = assert_schema(batch)
        assert PORT.validate_batch(batch) == []
        if schema_checked:
            wrong_relation_type = copy.deepcopy(batch)
            wrong_relation_type["claim_event_memberships"][0]["id"] = (
                "relation:event-story:wrong-kind"
            )
            assert schema_errors(wrong_relation_type)
            assert PORT.validate_batch(wrong_relation_type)
            legacy_with_contract = copy.deepcopy(batch)
            legacy_with_contract["source_case"]["findings_contract_version"] = "1.0"
            assert schema_errors(legacy_with_contract)
            assert PORT.validate_batch(legacy_with_contract)
            pending_with_decision = copy.deepcopy(batch)
            pending_with_decision["claims"][0]["event_link_disposition"] = "pending"
            pending_with_decision["claims"][0]["event_link_decision_id"] = (
                pending_with_decision["claims"][0]["review_decision_id"]
            )
            assert schema_errors(pending_with_decision)
            assert PORT.validate_batch(pending_with_decision)
            missing_supersedes = copy.deepcopy(batch)
            missing_supersedes["claims"][0]["version"] = 2
            assert schema_errors(missing_supersedes)
            assert PORT.validate_batch(missing_supersedes)
            wrong_supersedes = copy.deepcopy(batch)
            wrong_supersedes["claims"][0]["version"] = 3
            wrong_supersedes["claims"][0]["supersedes_version"] = 1
            # Draft-07 cannot express arithmetic equality across sibling fields;
            # the runtime enforces supersedes_version == version - 1.
            assert not schema_errors(wrong_supersedes)
            assert any(
                "must supersede version 2" in error
                for error in PORT.validate_batch(wrong_supersedes)
            )
        reused_decision = copy.deepcopy(batch)
        reused_decision["events"][0]["review_decision_id"] = (
            reused_decision["claims"][0]["review_decision_id"]
        )
        assert any(
            "decision subject does not match exact record" in error
            for error in PORT.validate_batch(reused_decision)
        )
        key, allowed = create_signer(workspace)
        public_key = key.with_suffix(".pub").read_text(encoding="utf-8")
        allowed.write_text(
            allowed.read_text(encoding="utf-8") + "issuer:test " + public_key,
            encoding="utf-8",
        )

        inverted_policy = policy_receipt(1)
        inverted_policy["expires_at"] = inverted_policy["issued_at"]
        try:
            PORT.validate_case_policy_receipt(inverted_policy, "destination:test")
        except PORT.ContractError as exc:
            assert "after issued_at" in str(exc)
        else:
            raise AssertionError("case policy accepted a non-positive authorization window")

        policy_db = workspace / "knowledge" / "policy.sqlite"
        PORT.initialize_database(policy_db, "destination:test")
        policy_jobs = []
        for revision, status in ((1, "active"), (2, "active"), (3, "revoked")):
            receipt = policy_receipt(revision, status)
            receipt_path = workspace / f"policy-{revision}.json"
            write_json(receipt_path, receipt, canonical=True)
            run(
                "ssh-keygen", "-Y", "sign", "-f", str(key),
                "-n", PORT.CASE_POLICY_NAMESPACE, str(receipt_path),
            )
            committed_policy = json.loads(run_cli(
                "policy-commit", "--workspace-root", str(workspace),
                "--db", "knowledge/policy.sqlite",
                "--destination-id", "destination:test",
                "--signature", f"policy-{revision}.json.sig",
                "--allowed-signers", str(allowed.relative_to(workspace)),
                "--unsafe-local-reference-commit", f"policy-{revision}.json",
            ).stdout)
            policy_jobs.append(committed_policy["projection_job"])
        assert policy_jobs[0]["status"] == "pending"
        policy_connection = PORT.open_existing_database(policy_db)
        try:
            first_policy_job = policy_connection.execute(
                "SELECT status FROM projection_jobs WHERE job_id = ?",
                (policy_jobs[0]["job_id"],),
            ).fetchone()
            assert first_policy_job["status"] == "superseded"
        finally:
            policy_connection.close()
        stale_completion = run_cli(
            "job-complete", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--job-id", policy_jobs[0]["job_id"],
            "--desired-sha256", policy_jobs[0]["desired_projection_set_sha256"],
            "--workspace-receipt-ref", "workspace:test/stale",
            "--workspace-receipt-sha256", "1" * 64,
            "--unsafe-local-reference-commit", ok=False,
        )
        assert "lost current-head status" in stale_completion.stderr or "cannot transition" in stale_completion.stderr
        claimed_policy = json.loads(run_cli(
            "job-claim", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--unsafe-local-reference-commit",
        ).stdout)["projection_job"]
        assert claimed_policy["job_id"] == policy_jobs[2]["job_id"]
        reconciled = run_cli(
            "job-reconcile", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--job-id", claimed_policy["job_id"],
            "--unsafe-local-reference-commit",
        )
        assert json.loads(reconciled.stdout)["status"] == "failed"
        run_cli(
            "job-retry", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--job-id", claimed_policy["job_id"],
            "--unsafe-local-reference-commit",
        )
        claimed_policy = json.loads(run_cli(
            "job-claim", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--unsafe-local-reference-commit",
        ).stdout)["projection_job"]
        run_cli(
            "job-complete", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite", "--job-id", claimed_policy["job_id"],
            "--desired-sha256", claimed_policy["desired_projection_set_sha256"],
            "--workspace-receipt-ref", "workspace:test/final",
            "--workspace-receipt-sha256", "2" * 64,
            "--unsafe-local-reference-commit",
        )
        policy_health = json.loads(run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/policy.sqlite",
        ).stdout)
        assert policy_health["completed_projections"] == 1
        policy_state = sqlite3.connect(policy_db)
        try:
            before_policy_counts = (
                policy_state.execute("SELECT COUNT(*) FROM case_policy_receipts").fetchone()[0],
                policy_state.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0],
                policy_state.execute(
                    "SELECT current_generation FROM projection_heads"
                ).fetchone()[0],
            )
            stored_policy_evidence = json.loads(policy_state.execute(
                "SELECT signature_evidence_json FROM case_policy_receipts "
                "ORDER BY policy_revision DESC LIMIT 1"
            ).fetchone()[0])
        finally:
            policy_state.close()
        original_enqueue = PORT.enqueue_projection_job
        PORT.enqueue_projection_job = lambda *args, **kwargs: (_ for _ in ()).throw(
            PORT.ContractError("injected outbox failure")
        )
        try:
            try:
                PORT.commit_case_policy(
                    policy_db, policy_receipt(4), "destination:test",
                    stored_policy_evidence,
                )
            except PORT.ContractError as exc:
                assert "injected outbox failure" in str(exc)
            else:
                raise AssertionError("policy transaction accepted an outbox failure")
        finally:
            PORT.enqueue_projection_job = original_enqueue
        policy_state = sqlite3.connect(policy_db)
        try:
            after_policy_counts = (
                policy_state.execute("SELECT COUNT(*) FROM case_policy_receipts").fetchone()[0],
                policy_state.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0],
                policy_state.execute(
                    "SELECT current_generation FROM projection_heads"
                ).fetchone()[0],
            )
        finally:
            policy_state.close()
        assert after_policy_counts == before_policy_counts

        manifest, first = stage_sign_commit(
            workspace, case_dir, batch, key, allowed, database
        )
        assert first["status"] == "committed" and first["replayed"] is False
        assert os.stat(database).st_mode & 0o077 == 0

        replay = run_cli(
            "commit", "--workspace-root", str(workspace),
            "--case-root", str(workspace), "--case-dir", "case",
            "--db", "knowledge/spotlight.sqlite",
            "--expected-sha256", manifest["payload_sha256"],
            "--manifest", "data/review-manifest.json",
            "--approval-receipt", "data/approval.json",
            "--approval-signature", "data/approval.json.sig",
            "--allowed-signers", str(allowed.relative_to(workspace)),
            "--unsafe-local-reference-commit",
            "data/knowledge-batch.json",
        )
        assert json.loads(replay.stdout)["replayed"] is True

        unsigned = case_dir / "data" / "approval.json.sig"
        signature = unsigned.read_bytes()
        unsigned.write_bytes(b"not a signature")
        rejected = run_cli(
            "commit", "--workspace-root", str(workspace),
            "--case-root", str(workspace), "--case-dir", "case",
            "--db", "knowledge/spotlight.sqlite",
            "--expected-sha256", manifest["payload_sha256"],
            "--manifest", "data/review-manifest.json",
            "--approval-receipt", "data/approval.json",
            "--approval-signature", "data/approval.json.sig",
            "--allowed-signers", str(allowed.relative_to(workspace)),
            "--unsafe-local-reference-commit",
            "data/knowledge-batch.json", ok=False,
        )
        assert "signature verification failed" in rejected.stderr
        unsigned.write_bytes(signature)

        findings_path = case_dir / "data" / "findings.json"
        original_findings = findings_path.read_bytes()
        findings_path.write_bytes(original_findings + b"\n")
        stale = run_cli(
            "commit", "--workspace-root", str(workspace),
            "--case-root", str(workspace), "--case-dir", "case",
            "--db", "knowledge/spotlight.sqlite",
            "--expected-sha256", manifest["payload_sha256"],
            "--manifest", "data/review-manifest.json",
            "--approval-receipt", "data/approval.json",
            "--approval-signature", "data/approval.json.sig",
            "--allowed-signers", str(allowed.relative_to(workspace)),
            "--unsafe-local-reference-commit",
            "data/knowledge-batch.json", ok=False,
        )
        assert "source case validation failed" in stale.stderr
        findings_path.write_bytes(original_findings)

        forward = json.loads(run_cli(
            "traverse", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
            "--claim-id", "claim:night-discharge:001",
        ).stdout)
        assert forward["claim"]["origin"]["finding_id"] == "F1"
        assert forward["events"][0]["story_arcs"][0]["story_arc"]["id"].startswith(
            "story-arc:"
        )
        reverse = json.loads(run_cli(
            "traverse", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
            "--story-arc-id", "story-arc:after-dark-river-pollution",
        ).stdout)
        assert reverse["events"][0]["claims"][0]["source_expression_refs"]

        exact_claim = json.loads(run_cli(
            "lookup", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
            "--claim-id", "claim:night-discharge:001",
        ).stdout)
        assert exact_claim["claim"]["id"] == "claim:night-discharge:001"
        project_page = json.loads(run_cli(
            "lookup", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", "--project", "test-case",
        ).stdout)
        prior = json.loads(run_cli(
            "lookup", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
            "--prior-fingerprint", batch["claims"][0]["origin"]["finding_fingerprint"],
        ).stdout)
        legacy = json.loads(run_cli(
            "lookup", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", "--legacy-claim-id", "test-case-f1",
        ).stdout)
        equivalent = json.loads(run_cli(
            "lookup", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
            "--equivalent-proposition", batch["claims"][0]["proposition"],
        ).stdout)
        assert all(
            result["claims"][0]["id"] == "claim:night-discharge:001"
            for result in (project_page, prior, legacy, equivalent)
        )

        connection = sqlite3.connect(database)
        try:
            stored = connection.execute(
                "SELECT payload_json, review_manifest_json, approval_receipt_json, "
                "approval_evidence_json FROM batches"
            ).fetchone()
            assert all(stored)
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            job = connection.execute(
                "SELECT source_kind, source_ref, status, generation FROM projection_jobs"
            ).fetchone()
            assert job == ("graph_commit", batch["batch_id"], "pending", 1)
            assert connection.execute(
                "SELECT COUNT(*) FROM projection_final_receipts"
            ).fetchone()[0] == 0
        finally:
            connection.close()

        # Source references are verified against the activated case, not merely shaped.
        forged = copy.deepcopy(batch)
        forged["claims"][0]["source_expression_refs"][0]["expression_id"] = "SX404"
        rebind_decisions(forged)
        write_json(case_dir / "data" / "knowledge-batch.json", forged)
        source_rejected = run_cli(
            "stage", "--case-root", str(workspace), "--case-dir", "case",
            "--destination-id", "newsroom:test", "data/knowledge-batch.json", ok=False,
        )
        assert "does not resolve" in source_rejected.stderr

        downgraded = copy.deepcopy(batch)
        downgraded["source_case"]["findings_contract_version"] = "1.0"
        downgraded["source_case"].pop("case_contract_sha256", None)
        rebind_decisions(downgraded)
        write_json(case_dir / "data" / "knowledge-batch.json", downgraded)
        downgrade_rejected = run_cli(
            "stage", "--case-root", str(workspace), "--case-dir", "case",
            "--destination-id", "newsroom:test", "data/knowledge-batch.json", ok=False,
        )
        assert "does not match findings.json" in downgrade_rejected.stderr

        coverage = json.loads(run_cli(
            "coverage", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
        ).stdout)
        assert coverage["summary"]["complete"] == 1
        health = json.loads(run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
        ).stdout)
        assert health["status"] == "healthy" and health["verified_batches"] == 1
        assert health["projection_heads"] == 1
        claimed_graph = json.loads(run_cli(
            "job-claim", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", "--unsafe-local-reference-commit",
        ).stdout)["projection_job"]
        run_cli(
            "job-complete", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", "--job-id", claimed_graph["job_id"],
            "--desired-sha256", claimed_graph["desired_projection_set_sha256"],
            "--workspace-receipt-ref", "workspace:test/graph-final",
            "--workspace-receipt-sha256", "3" * 64,
            "--unsafe-local-reference-commit",
        )
        assert json.loads(run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite",
        ).stdout)["completed_projections"] == 1
        receipt_tamper = sqlite3.connect(database)
        try:
            original_binding = receipt_tamper.execute(
                "SELECT binding_sha256 FROM projection_final_receipts"
            ).fetchone()[0]
            receipt_tamper.execute(
                "UPDATE projection_final_receipts SET binding_sha256 = ?", ("f" * 64,)
            )
            receipt_tamper.commit()
        finally:
            receipt_tamper.close()

        for name, mutate, expected_error in (
            (
                "dangling-job",
                lambda connection: connection.execute(
                    """
                    INSERT INTO projection_jobs
                        (job_id, case_id, destination_id, generation,
                         desired_projection_set_sha256, source_kind, source_ref,
                         source_sha256, status, created_at, updated_at)
                    VALUES (?, ?, ?, 2, ?, 'graph_commit', 'batch:missing', ?,
                            'pending', '2026-08-18T13:00:00Z', '2026-08-18T13:00:00Z')
                    """,
                    (
                        "projection-job:" + "a" * 64, "case:test-case", "newsroom:test",
                        "b" * 64, "c" * 64,
                    ),
                ),
                "dangling or unreceipted source",
            ),
            (
                "cross-case-job",
                lambda connection: connection.execute(
                    "UPDATE projection_jobs SET case_id = 'case:other'"
                ),
                "source binding is corrupt or cross-case",
            ),
            (
                "tampered-job",
                lambda connection: connection.execute(
                    "UPDATE projection_jobs SET desired_projection_set_sha256 = ?",
                    ("d" * 64,),
                ),
                "intent hash is corrupt",
            ),
            (
                "unreceipted-completion",
                lambda connection: connection.execute(
                    "DELETE FROM projection_final_receipts"
                ),
                "lacks its final receipt",
            ),
        ):
            damaged_db = workspace / "knowledge" / f"{name}.sqlite"
            copy_database(database, damaged_db)
            damaged_connection = sqlite3.connect(damaged_db)
            try:
                mutate(damaged_connection)
                damaged_connection.commit()
            finally:
                damaged_connection.close()
            damaged_state = run_cli(
                "verify", "--workspace-root", str(workspace),
                "--db", str(damaged_db.relative_to(workspace)), ok=False,
            )
            assert expected_error in damaged_state.stderr, damaged_state.stderr
        damaged_receipt = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "final receipt binding is corrupt" in damaged_receipt.stderr
        receipt_tamper = sqlite3.connect(database)
        try:
            receipt_tamper.execute(
                "UPDATE projection_final_receipts SET binding_sha256 = ?",
                (original_binding,),
            )
            receipt_tamper.commit()
        finally:
            receipt_tamper.close()
        tamper = sqlite3.connect(database)
        try:
            original_payload = tamper.execute(
                "SELECT payload_json FROM claims WHERE id = ? AND version = 1",
                ("claim:night-discharge:001",),
            ).fetchone()[0]
            tamper.execute(
                "UPDATE claims SET payload_json = '{}' WHERE id = ? AND version = 1",
                ("claim:night-discharge:001",),
            )
            tamper.commit()
        finally:
            tamper.close()
        damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "is corrupt" in damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE claims SET payload_json = ? WHERE id = ? AND version = 1",
                (original_payload, "claim:night-discharge:001"),
            )
            tamper.commit()
        finally:
            tamper.close()

        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE claims SET status = 'rejected' WHERE id = ? AND version = 1",
                ("claim:night-discharge:001",),
            )
            tamper.commit()
        finally:
            tamper.close()
        projection_damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "projection is corrupt" in projection_damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE claims SET status = 'approved' WHERE id = ? AND version = 1",
                ("claim:night-discharge:001",),
            )
            original_evidence = tamper.execute(
                "SELECT approval_evidence_json FROM batches"
            ).fetchone()[0]
            evidence = json.loads(original_evidence)
            evidence["signature"] = "forged"
            tamper.execute(
                "UPDATE batches SET approval_evidence_json = ?",
                (json.dumps(evidence, sort_keys=True, separators=(",", ":")),),
            )
            tamper.commit()
        finally:
            tamper.close()
        signature_damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "signature verification failed" in signature_damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE batches SET approval_evidence_json = ?", (original_evidence,)
            )
            tamper.commit()
        finally:
            tamper.close()

        tamper = sqlite3.connect(database)
        try:
            original_manifest = tamper.execute(
                "SELECT review_manifest_json FROM batches"
            ).fetchone()[0]
            manifest_tamper = json.loads(original_manifest)
            manifest_tamper["status"] = "forged"
            tamper.execute(
                "UPDATE batches SET review_manifest_json = ?",
                (json.dumps(manifest_tamper, sort_keys=True, separators=(",", ":")),),
            )
            tamper.commit()
        finally:
            tamper.close()
        manifest_damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "manifest hash is corrupt" in manifest_damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE batches SET review_manifest_json = ?", (original_manifest,)
            )
            original_receipt = tamper.execute(
                "SELECT approval_receipt_json FROM batches"
            ).fetchone()[0]
            receipt_tamper = json.loads(original_receipt)
            receipt_tamper["reviewer_id"] = "journalist:forged"
            tamper.execute(
                "UPDATE batches SET approval_receipt_json = ?",
                (json.dumps(receipt_tamper, sort_keys=True, separators=(",", ":")),),
            )
            tamper.commit()
        finally:
            tamper.close()
        receipt_damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "reviewer binding is corrupt" in receipt_damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE batches SET approval_receipt_json = ?", (original_receipt,)
            )
            tamper.execute("PRAGMA foreign_keys = OFF")
            tamper.execute(
                "UPDATE claim_event_memberships SET event_id = 'event:missing:tamper'"
            )
            tamper.commit()
        finally:
            tamper.close()
        foreign_key_damaged = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "foreign-key integrity" in foreign_key_damaged.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "UPDATE claim_event_memberships "
                "SET event_id = 'event:river-monitoring:2026-04-18'"
            )
            tamper.commit()
        finally:
            tamper.close()

        try:
            PORT.commit_batch(database, batch, manifest["payload_sha256"], manifest, {})
        except PORT.ContractError as exc:
            assert "disabled" in str(exc)
        else:
            raise AssertionError("public local commit API must be disabled")

        tamper = sqlite3.connect(database)
        try:
            tamper.execute(
                "INSERT INTO claims SELECT ?, version, supersedes_version, "
                "origin_project, origin_finding_id, origin_finding_fingerprint, "
                "proposition, status, event_link_disposition, review_decision_id, "
                "event_link_decision_id, payload_json, batch_id FROM claims "
                "WHERE id = ? AND version = 1",
                ("claim:unreceipted:001", "claim:night-discharge:001"),
            )
            tamper.commit()
        finally:
            tamper.close()
        unreceipted = run_cli(
            "verify", "--workspace-root", str(workspace),
            "--db", "knowledge/spotlight.sqlite", ok=False,
        )
        assert "unreceipted" in unreceipted.stderr, unreceipted.stderr
        tamper = sqlite3.connect(database)
        try:
            tamper.execute("DELETE FROM claims WHERE id = 'claim:unreceipted:001'")
            tamper.commit()
        finally:
            tamper.close()

    if schema_checked:
        print("ok   schema and runtime contract agree on the sample batch")
    else:
        print("skip JSON Schema parity (jsonschema unavailable; required in CI)")
    print("ok   pre-existing 0755 database dirs are tightened to 0700")
    print("ok   activated source artifacts and fingerprints are resolved")
    print("ok   detached SSH approval is required and verified")
    print("ok   atomic commit, replay, receipts, permissions, and schema version")
    print("ok   forward, reverse, and coverage projections")
    print("ok   exact claim, project, prior-verdict, and equivalence lookups")
    print("ok   signed policy issue, revision, revocation, and generation supersession")
    print("ok   serialized retry, reconciliation, and final receipt transitions")
    print("ok   extra, dangling, cross-case, and tampered projection state is rejected")
    print("ok   stored signatures, projections, and batch integrity verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
