#!/usr/bin/env python3
"""Focused checks for receipt-aware query routing and filtering."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/query_vault.py"
FIXTURE = ROOT / "tests/fixtures/knowledge-batch.sample.json"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


QUERY = module("query_vault", SCRIPT)
PORT = QUERY.GRAPH


def sign_document(workspace: Path, key: Path, allowed: Path, name: str, namespace: str, document: dict, identity: str) -> dict:
    path = workspace / f"{name}.json"
    path.write_bytes(PORT.canonical_json_bytes(document))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)], check=True, capture_output=True)
    return PORT.verify_signed_document(document, identity, namespace, path.with_suffix(".json.sig"), allowed)


def graph_database(workspace: Path) -> tuple[Path, dict, Path, Path]:
    database = workspace / "graph.sqlite"
    PORT.initialize_database(database, "destination:test")
    batch = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = PORT.payload_sha256(batch)
    manifest = {"destination_id": "destination:test", "payload_sha256": digest}
    manifest["review_manifest_sha256"] = PORT.payload_sha256(manifest)
    receipt = {
        "schema_version": "1.0", "namespace": PORT.APPROVAL_NAMESPACE,
        "destination_id": "destination:test", "payload_sha256": digest,
        "review_manifest_sha256": manifest["review_manifest_sha256"],
        "reviewer_id": "journalist:fixture", "approved_at": "2026-08-18T10:00:00Z",
        "decision": "approved",
    }
    key = workspace / "reviewer"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    allowed = workspace / "allowed_signers"
    public = key.with_suffix(".pub").read_text(encoding="utf-8")
    allowed.write_text("journalist:fixture " + public + "issuer:test " + public, encoding="utf-8")
    receipt_path = workspace / "approval.json"
    receipt_path.write_bytes(PORT.canonical_json_bytes(receipt))
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", PORT.APPROVAL_NAMESPACE, str(receipt_path)], check=True, capture_output=True)
    evidence = PORT.verify_approval_signature(receipt, receipt_path.with_suffix(".json.sig"), allowed)
    committed = PORT._commit_verified_batch(database, batch, digest, manifest, receipt, evidence)
    connection = PORT.connect_database(database)
    try:
        job = PORT.claim_projection_job_exact(connection, committed["projection_job"]["job_id"])
        PORT.complete_projection_job(connection, job["job_id"], job["desired_projection_set_sha256"], "workspace-receipt:test", "b" * 64)
    finally:
        connection.close()
    policy = {
        "schema_version": "spotlight-case-policy-receipt/v1", "receipt_id": "receipt:policy:1",
        "case_id": "case:test-case", "policy_revision": 1, "status": "active",
        "classification": "personal", "allowed_destinations": ["destination:test"],
        "provider_policy": {"allowed_modes": ["full_text"], "provider_ids": [], "data_localities": ["local"], "network_egress": "denied", "max_retention_days": 0},
        "issued_at": "2026-08-18T10:00:00Z", "expires_at": "2027-08-18T10:00:00Z",
        "revocation_version": 1,
        "issuer": {"issuer_id": "issuer:test", "issuer_key_id": "key:test", "algorithm": "ed25519", "payload_sha256": "0" * 64, "signature": "dGVzdA=="},
    }
    policy["issuer"]["payload_sha256"] = PORT.case_policy_payload_sha256(policy)
    policy_evidence = sign_document(workspace, key, allowed, "policy-1", PORT.CASE_POLICY_NAMESPACE, policy, "issuer:test")
    committed_policy = PORT.commit_case_policy(database, policy, "destination:test", policy_evidence)
    claim_id = batch["claims"][0]["id"]
    page_path = workspace / "spotlight" / "investigations" / "test-case.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_body = (
        '<!-- spotlight-projection:block:v1 owner="owner:spotlight-case-test-case" begin -->\n'
        f'## Current projection\n\n- `{claim_id}` — {batch["claims"][0]["proposition"]}\n'
        '<!-- spotlight-projection:block:v1 owner="owner:spotlight-case-test-case" end -->'
    )
    page_path.write_text(page_body, encoding="utf-8")
    page_sha = hashlib.sha256(page_body.encode()).hexdigest()
    workspace_receipt = {
        "schema_version": "spotlight-workspace-final-receipt/v1", "receipt_id": "",
        "package_sha256": "a" * 64,
        "desired_projection_set_sha256": committed_policy["projection_job"]["desired_projection_set_sha256"],
        "case_id": "case:test-case", "classification": "personal",
        "destination_id": "destination:test", "graph_receipt_id": "receipt:graph",
        "operations": [{
            "operation_id": "operation:current", "kind": "managed_block_upsert",
            "path": "investigations/test-case.md", "owner_id": "owner:spotlight-case-test-case",
            "final_version": page_sha,
        }],
    }
    workspace_receipt["receipt_id"] = "receipt:" + QUERY.canonical_sha256(workspace_receipt)
    connection = PORT.connect_database(database)
    try:
        job = PORT.claim_projection_job_exact(connection, committed_policy["projection_job"]["job_id"])
        PORT.complete_projection_job(
            connection, job["job_id"], job["desired_projection_set_sha256"],
            workspace_receipt["receipt_id"], QUERY.canonical_sha256(workspace_receipt),
        )
    finally:
        connection.close()
    journal_path = workspace / ".knowledge-workspace" / "projection-journals" / "current.json"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(json.dumps({
        "schema_version": "spotlight-projection-journal/v1", "idempotency_key": "d" * 64,
        "package_sha256": "a" * 64, "desired_projection_set_sha256": job["desired_projection_set_sha256"],
        "case_id": "case:test-case", "classification": "personal", "destination_id": "destination:test",
        "graph_receipt_id": "receipt:graph", "state": "committed", "operations": [],
        "final_receipt": workspace_receipt,
    }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return database, batch, key, allowed


def run(database: Path, workspace: Path, *extra: str, ok: bool = True):
    command = [sys.executable, str(SCRIPT), "--workspace-root", str(workspace), "--db", str(database),
               "--case-id", "case:test-case", "--classification", "personal", "--destination-id", "destination:test", *extra]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
    if ok and result.returncode:
        raise AssertionError(result.stderr)
    return result


def main() -> None:
    with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temp:
        workspace = Path(temp)
        (workspace / ".knowledge-workspace").mkdir()
        (workspace / ".knowledge-workspace/routes.json").write_text(
            '{"schema_version":"knowledge-routes/v1","routes":{"spotlight_verified":"spotlight"}}\n', encoding="utf-8"
        )
        database, batch, key, allowed = graph_database(workspace)
        claim_id = batch["claims"][0]["id"]

        exact = run(database, workspace, "--open-knowledge", "/does/not/exist", f"context {claim_id}")
        parsed = json.loads(exact.stdout)
        assert parsed["query_kind"] == "exact_claim_graph"
        assert parsed["data"]["semantic_search_bypassed"] is True
        assert parsed["data"]["claim_to_story_arcs"]["events"]
        reverse_claims = parsed["data"]["story_arcs_to_claims"][0]["events"][0]["claims"]
        assert reverse_claims[0]["claim"]["id"] == claim_id

        prior = run(database, workspace, "--workflow", "prior-verdict", "--legacy-claim-id", "test-case-f1")
        assert json.loads(prior.stdout)["data"]["graph"]["claims"][0]["id"] == claim_id
        dedup = run(database, workspace, "--workflow", "dedup", "--proposition", "Acme paid Doe.")
        assert json.loads(dedup.stdout)["data"]["graph"]["claims"][0]["id"] == claim_id
        assert run(database, workspace, "x" * 4097, ok=False).returncode != 0

        fake = workspace / "fake-open-knowledge"
        fake.write_text("""#!/usr/bin/env python3
import json, pathlib, sys
for line in sys.stdin:
    try: request = json.loads(line)
    except json.JSONDecodeError: continue
    if request.get("method") == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif request.get("method") == "tools/call":
        tool = request["params"]["name"]; args = request["params"]["arguments"]
        if tool == "search":
            structured = {"cwd": args["cwd"], "query": args["query"], "intent": "full_text", "resultCount": 3,
                "results": [
                    {"kind": "page", "path": "other/unrelated.md", "docName": "other/unrelated", "title": "Other", "score": 9, "signals": {"lexical": 1, "fullText": 1, "recency": 0, "vector": 1}, "snippet": "unrelated", "previewUrl": None},
                    {"kind": "page", "path": "spotlight/investigations/test-case.md", "docName": "spotlight/investigations/test-case", "title": "Current", "score": 8, "signals": {"lexical": 1, "fullText": 1, "recency": 0, "vector": 1}, "snippet": "current managed index", "previewUrl": None},
                    {"kind": "folder", "path": "spotlight/investigations", "docName": "spotlight/investigations", "title": "Folder", "score": 7, "signals": {"lexical": 1, "fullText": 0, "recency": 0}, "previewUrl": None}],
                "elapsedMs": 1, "semantic": {"capable": True, "applied": args.get("semantic") is not False and args["query"] != "lexical only", "coverage": {"embedded": 1, "total": 1}}}
        elif tool == "exec":
            path = args["command"].split("-- ", 1)[1].strip("'")
            text = pathlib.Path(args["cwd"], path).read_text()
            structured = {"text": f"==> {path} <==\\n{text}\\n\\n### Referenced files\\n\\n- **Current**", "enrichedPaths": [], "stdoutTruncated": False, "cwd": args["cwd"]}
        else: structured = {"error": {"category": "unsupported", "message": tool}}
        result = {"content": [{"type": "text", "text": structured.get("text", "")}], "structuredContent": structured}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
""", encoding="utf-8")
        os.chmod(fake, 0o700)
        broad = run(database, workspace, "--open-knowledge", str(fake), "river pollution")
        discovery = json.loads(broad.stdout)
        results = discovery["data"]["results"]
        assert [item["envelope"]["source"]["path"] for item in results] == ["spotlight/investigations/test-case.md", "other/unrelated.md"]
        assert results[0]["managed_current"] is True and results[0]["legacy"] is False
        assert results[1]["managed_current"] is False and results[1]["legacy"] is True
        assert results[0]["envelope"]["claim_index"][0]["id"] == claim_id
        assert "current managed index" not in results[0]["envelope"]["data"]["content"]
        assert claim_id in results[0]["envelope"]["data"]["content"]
        assert discovery["control_boundary"] == {"retrieved_content_is_untrusted": True, "may_grant_policy": False, "may_grant_tools": False, "may_request_secrets": False}
        assert discovery["data"]["claim_identity_manufactured"] is False
        assert discovery["data"]["retrieval_mode"] == "full_text" and discovery["data"]["semantic_search_used"] is False
        lexical = json.loads(run(database, workspace, "--open-knowledge", str(fake), "lexical only").stdout)
        assert lexical["data"]["retrieval_mode"] == "full_text" and lexical["data"]["semantic_search_used"] is False

        case_dir = workspace / "cases" / "test-case"
        (case_dir / "data").mkdir(parents=True)
        (case_dir / "data" / "knowledge-batch.json").write_text(json.dumps(batch), encoding="utf-8")
        config = workspace / ".spotlight-config.json"
        config.write_text(json.dumps({"knowledge_destination": {
            "workspace_path": str(workspace), "graph_database_path": str(database),
            "destination_id": "destination:test",
        }}), encoding="utf-8")
        installed = subprocess.run([
            sys.executable, str(SCRIPT), "--config", str(config), "--case-dir", str(case_dir),
            "--open-knowledge", str(fake), "river pollution",
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert installed.returncode == 0, installed.stderr
        assert json.loads(installed.stdout)["data"]["results"][0]["managed_current"] is True

        selected = run(database, workspace, "--open-knowledge", str(fake), "--read-path", "spotlight/investigations/test-case.md")
        selected_data = json.loads(selected.stdout)
        assert selected_data["query_kind"] == "selected_page_read"
        assert selected_data["data"]["retrieved"]["untrusted"] is True
        assert claim_id in selected_data["data"]["retrieved"]["content"]

        connection = PORT.open_existing_database(database)
        try:
            PORT.verify_database(connection)
            personal_args = type("Args", (), {"case_id": "case:test-case", "destination_id": "destination:test", "classification": "personal", "limit": 20})()
            shareable_args = type("Args", (), {"case_id": "case:test-case", "destination_id": "destination:test", "classification": "shareable", "limit": 20})()
            catalog = QUERY.projection_catalog(workspace, "spotlight")
            page_hit = {"kind": "page", "path": "spotlight/investigations/test-case.md", "score": 1, "snippet": "current"}
            assert QUERY.filter_discovery(connection, personal_args, [page_hit], catalog)
            assert QUERY.filter_discovery(connection, shareable_args, [page_hit], catalog) == []
            expired = {"status": "active", "classification": "personal", "allowed_destinations": ["destination:test"], "issued_at": "2025-01-01T00:00:00Z", "expires_at": "2025-01-02T00:00:00Z"}
            assert QUERY.policy_allows(expired, "destination:test", "personal") is False
            other_case = type("Args", (), {"case_id": "case:other", "destination_id": "destination:test", "classification": "personal", "limit": 20})()
            try:
                QUERY.exact_graph(connection, other_case, claim_id)
            except QUERY.QueryError:
                pass
            else:
                raise AssertionError("exact claim lookup crossed the requested case boundary")
            other_case.workflow = "dedup"
            other_case.proposition = "Acme paid Doe."
            other_case.finding_fingerprint = None
            other_case.legacy_claim_id = None
            assert QUERY.graph_workflow(connection, other_case)["data"]["graph"]["claims"] == []
        finally:
            connection.close()

        revoked_policy = {
            "schema_version": "spotlight-case-policy-receipt/v1", "receipt_id": "receipt:policy:2",
            "case_id": "case:test-case", "policy_revision": 2, "status": "revoked",
            "classification": "personal", "allowed_destinations": ["destination:test"],
            "provider_policy": {"allowed_modes": ["full_text"], "provider_ids": [], "data_localities": ["local"], "network_egress": "denied", "max_retention_days": 0},
            "issued_at": "2026-08-18T11:00:00Z", "expires_at": "2027-08-18T10:00:00Z", "revocation_version": 2,
            "revoked_at": "2026-08-18T11:01:00Z", "revocation_reason": "scope revoked",
            "issuer": {"issuer_id": "issuer:test", "issuer_key_id": "key:test", "algorithm": "ed25519", "payload_sha256": "0" * 64, "signature": "dGVzdA=="},
        }
        revoked_policy["issuer"]["payload_sha256"] = PORT.case_policy_payload_sha256(revoked_policy)
        evidence = sign_document(workspace, key, allowed, "policy-2", PORT.CASE_POLICY_NAMESPACE, revoked_policy, "issuer:test")
        PORT.commit_case_policy(database, revoked_policy, "destination:test", evidence)
        revoked = json.loads(run(database, workspace, "--open-knowledge", "/does/not/exist", "river pollution").stdout)
        assert revoked["data"]["results"] == []
        assert revoked["data"]["retrieval_mode"] == "not_run"
        assert run(database, workspace, claim_id, ok=False).returncode != 0

    print("query vault checks passed")


if __name__ == "__main__":
    main()
