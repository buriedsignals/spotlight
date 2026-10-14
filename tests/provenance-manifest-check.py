#!/usr/bin/env python3
"""Regression checks for Spotlight provenance manifest generation."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
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


def run_builder(
    case_dir: Path, *args: str, ok: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(BUILDER), str(case_dir), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
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
        assert signed_pointer["signing_status"] == "unsigned"
        assert signed_pointer["receipt_status"] == "received_unverified"
        assert_schema(signed_pointer)
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
        assert len(successes) == 1  # retries after a received receipt are idempotent
        retry_pointer = json.loads(pointer_path.read_text())
        assert retry_pointer["signing_status"] == "unsigned"
        assert retry_pointer["receipt_status"] == "received_unverified"
        assert retry_revision.read_bytes() == retry_revision_bytes
        assert signed_revision_path.read_bytes() == unsigned_bytes
        assert first_receipt.read_bytes() == first_receipt_bytes
        assert len(list((case_dir / "data" / "provenance-signing-receipts").glob("*.json"))) == 2

    check_signer_responses()
    check_managed_key_origin()
    check_noosphere_fixtures()
    return 0


def signer_record(request_body: dict) -> dict:
    """The record Noosphere signs: the request projected onto its vocabulary
    (HASHING.md section 5), as in the vendor fixtures."""
    manifest = request_body["provenance_manifest"]
    return {
        "profile": request_body["profile"],
        "input_set_hash": manifest["input_set_hash"],
        "inventory": [
            {key: artifact[key] for key in ("kind", "path", "sha256", "bytes")}
            for artifact in manifest["case_artifacts"]
        ],
        "claims": [
            {"id": claim["finding_id"], "text": claim["claim_text"], "verdict": claim["fact_check_verdict"]}
            for claim in manifest["claims"]
        ],
        "sources": [
            {"id": source["evidence_id"], "url": source["source_url"], "hash": source.get("sha256")}
            for source in manifest["sources"]
        ],
    }


def signer_response(request_body: dict, record: dict | None = None, **overrides: object) -> dict:
    """A response shaped like Noosphere's 1.3.0 contract for the request it answers."""
    manifest = request_body["provenance_manifest"]
    record = record if record is not None else signer_record(request_body)
    record_bytes = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response = {
        "status": "signed",
        "contract_version": "1.3.0",
        "profile": request_body["profile"],
        "manifest_id": "provenance_test.json",
        "input_set_hash": manifest["input_set_hash"],
        "content_hash": "sha256:" + hashlib.sha256(record_bytes).hexdigest(),
        "record": record,
        "record_b64": base64.b64encode(record_bytes).decode("ascii"),
        "c2pa_manifest_b64": base64.b64encode(b"synthetic c2pa sidecar").decode("ascii"),
        "signer": {"org_id": "org_test", "signing_source": "gcp-cloud-hsm"},
        "certificate_chain": {"grade": "production"},
        "signed_at": "2026-10-01T00:00:00Z",
    }
    response.update(overrides)
    return response


def drop_first_artifact(body: dict) -> dict:
    record = signer_record(body)
    record["inventory"] = record["inventory"][1:]
    return signer_response(body, record=record)


def alter_first_source_hash(body: dict) -> dict:
    record = signer_record(body)
    record["sources"][0]["hash"] = "f" * 64
    return signer_response(body, record=record)


HTTP_STATUS_CASES = {
    "/http-401": (401, {}, "rejected the API key (401)"),
    "/http-403": (403, {}, "lacks the 'sign' scope (403)"),
    "/http-429": (429, {"Retry-After": "30"}, "rate limiting (429); retry after 30 s"),
    "/http-503": (503, {}, "no production certificate chain (503)"),
}

SIGNER_CASES = {
    # path: (response builder, expected error fragment or None for an accepted receipt)
    "/ok": (lambda body: signer_response(body), None),
    "/development-chain": (
        lambda body: signer_response(body, certificate_chain={"grade": "development"}),
        "certificate chain grade is 'development'",
    ),
    "/bare-success": (lambda body: {"status": "signed"}, "record_b64 is missing"),
    "/tampered-hash": (
        lambda body: signer_response(body, content_hash="sha256:" + "0" * 64),
        "content_hash does not match",
    ),
    "/other-input-set": (
        lambda body: signer_response(body, input_set_hash="1" * 64),
        "input_set_hash does not match",
    ),
    "/generic-profile": (
        lambda body: signer_response(body, profile="provenance"),
        "profile is 'provenance'",
    ),
    "/next-major-contract": (
        lambda body: signer_response(body, contract_version="2.0.0"),
        "contract_version is '2.0.0'",
    ),
    "/dropped-artifact": (drop_first_artifact, "1 artifact(s) missing or changed"),
    "/changed-source-hash": (alter_first_source_hash, "1 source(s) missing or changed"),
    "/redirect": (None, "redirects are refused"),
    "/oversized": (None, "exceeds"),
    **{path: (None, message) for path, (_, _, message) in HTTP_STATUS_CASES.items()},
}

# Stands in for c2patool so the builder's signed/unsigned decision can be
# exercised without a real production-signed sidecar.
TRUSTED_VERIFIER = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "c2patool 0.27.22"; exit 0; fi
echo '{"validation_state": "Trusted", "validation_results": {"activeManifest": {"failure": []}}}'
"""


def check_signer_responses() -> None:
    """The builder keeps a structurally sound Noosphere response as an
    unverified receipt, marks it signed only when the verifier reports it
    Trusted, and refuses false success, mismatched hashes, dropped or altered
    record entries, a development chain, redirects, HTTP errors and oversized
    bodies. A custom endpoint never receives the managed key, and sensitive
    mode never reaches the signer."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[tuple[str, dict, dict]] = []

    class Signer(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def send_json(self, value: object, status: int = 200, headers: dict | None = None) -> None:
            data = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, {}))
            self.send_json({"status": "signed"})

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/stolen")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/oversized":
                size = PROVENANCE.MAX_SIGNING_RESPONSE_BYTES + 1
                self.send_response(200)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                self.wfile.write(b" " * size)
                return
            if self.path in HTTP_STATUS_CASES:
                status, headers, _ = HTTP_STATUS_CASES[self.path]
                self.send_json({"error": "refused"}, status=status, headers=headers)
                return
            self.send_json(SIGNER_CASES[self.path.split("?")[0]][0](body))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Signer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    no_key = {"NOOSPHERE_PROVENANCE_API_KEY": "", "C2PATOOL": "/nonexistent/c2patool"}
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        with tempfile.TemporaryDirectory() as raw_tmp:
            for path, (_, expected_error) in SIGNER_CASES.items():
                case_dir = copy_fixture_case(Path(raw_tmp) / path.strip("/"))
                run_builder(case_dir, "--sign-endpoint", endpoint + path, env=no_key)
                manifest = json.loads((case_dir / "data" / "provenance-manifest.json").read_text())
                assert_schema(manifest)
                assert manifest["status"] != "signed", path
                if expected_error is None:
                    assert manifest["status"] == "unsigned", path
                    assert manifest["signing"]["receipt_status"] == "received_unverified", path
                    assert manifest["signing"]["verification"]["verified"] is False, path
                    assert (case_dir / manifest["signing"]["receipt_path"]).is_file(), path
                else:
                    assert manifest["status"] == "signing_failed", path
                    assert expected_error in manifest["signing"]["error"], (path, manifest["signing"]["error"])
                    assert "receipt_path" not in manifest["signing"], path

            # A receipt the verifier reports Trusted is the only route to signed.
            verifier = Path(raw_tmp) / "c2patool"
            verifier.write_text(TRUSTED_VERIFIER, encoding="utf-8")
            verifier.chmod(0o755)
            case_dir = copy_fixture_case(Path(raw_tmp) / "trusted")
            run_builder(case_dir, "--sign-endpoint", endpoint + "/ok?trusted",
                        env={**no_key, "C2PATOOL": str(verifier)})
            manifest = json.loads((case_dir / "data" / "provenance-manifest.json").read_text())
            assert_schema(manifest)
            assert manifest["status"] == "signed", manifest["signing"]
            assert manifest["signing"]["verification"]["validation_state"] == "Trusted"
            assert "receipt_status" not in manifest["signing"]

            # The managed key is never sent to a custom endpoint.
            case_dir = copy_fixture_case(Path(raw_tmp) / "custom-with-key")
            run_builder(case_dir, "--sign-endpoint", endpoint + "/ok?keyed",
                        env={**no_key, "NOOSPHERE_PROVENANCE_API_KEY": "test-key"})
            manifest = json.loads((case_dir / "data" / "provenance-manifest.json").read_text())
            assert manifest["status"] == "signing_failed"
            assert "only sent to https://platform.noosphere.tech" in manifest["signing"]["error"]
            assert "test-key" not in json.dumps(manifest)

            # SIGN-02: the key is never accepted on the command line.
            flagged = run_builder(case_dir, "--api-key", "test-key", ok=False)
            assert flagged.returncode == 2 and "unrecognized arguments: --api-key" in flagged.stderr

            # Sensitive mode keeps the case local: no request, unsigned manifest.
            case_dir = copy_fixture_case(Path(raw_tmp) / "sensitive")
            run_builder(case_dir, "--sign-endpoint", endpoint + "/ok?sensitive",
                        env={**no_key, "SPOTLIGHT_SENSITIVE": "true"})
            manifest = json.loads((case_dir / "data" / "provenance-manifest.json").read_text())
            assert manifest["status"] == "unsigned" and "receipt_path" not in manifest["signing"]

        request_path, headers, body = next(entry for entry in seen if entry[0] == "/ok")
        assert body["profile"] == "spotlight"
        assert body["provenance_manifest"]["input_set_hash"]
        assert "x-api-key" not in headers
        assert not any(entry[0] in ("/stolen", "/ok?keyed", "/ok?sensitive") for entry in seen)
    finally:
        server.shutdown()
        server.server_close()


def check_managed_key_origin() -> None:
    """The managed key goes to platform.noosphere.tech as X-API-Key (SIGN-01/02)."""
    builder = importlib.util.module_from_spec(SPEC)  # main() patches PROVENANCE's functions
    SPEC.loader.exec_module(builder)
    sent: list = []

    class Opener:
        def open(self, request, timeout):  # noqa: ARG002
            sent.append(request)
            raise builder.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    original = builder.urllib.request.build_opener
    builder.urllib.request.build_opener = lambda *handlers: Opener()
    os.environ["NOOSPHERE_PROVENANCE_API_KEY"] = "managed-key"
    try:
        builder.post_for_signing(
            "https://platform.noosphere.tech/api/provenance/sign", {"input_set_hash": "x"}, None, None
        )
    except builder.SigningHTTPError as exc:
        assert "rejected the API key (401)" in str(exc) and "managed-key" not in str(exc)
    else:
        raise AssertionError("a 401 must raise SigningHTTPError")
    finally:
        builder.urllib.request.build_opener = original
        del os.environ["NOOSPHERE_PROVENANCE_API_KEY"]
    assert sent and sent[0].get_header("X-api-key") == "managed-key"


def check_noosphere_fixtures() -> None:
    """Noosphere's signed fixtures (contract 1.3.0, development CA) pass the
    record checks, and c2patool verifies their sidecars: Trusted with the
    development CA pinned, untrusted against the C2PA trust list, invalid after
    one record byte changes."""
    if not shutil.which("c2patool") and not os.environ.get("C2PATOOL"):
        if os.environ.get("CI"):
            raise AssertionError("c2patool is required in CI")
        print("SKIP check_noosphere_fixtures: c2patool not installed")
        return
    verify_path = ROOT / "scripts" / "noosphere_verify.py"
    spec = importlib.util.spec_from_file_location("noosphere_verify", verify_path)
    assert spec and spec.loader
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)

    for product in ("spotlight", "mycroft"):
        bundle = ROOT / "tests" / "fixtures" / "noosphere" / "dev-v1" / product
        request = json.loads((bundle / "request.json").read_text(encoding="utf-8"))
        response = json.loads((bundle / "response.json").read_text(encoding="utf-8"))
        if product == "spotlight":
            assert PROVENANCE.signing_response_problems(response, request["provenance_manifest"]) == [
                "certificate chain grade is 'development', not 'production'"
            ]

        with tempfile.TemporaryDirectory() as raw_tmp:
            dev_ca = Path(raw_tmp) / "dev-ca.pem"
            chain = (bundle / "certificate-chain.pem").read_text(encoding="utf-8")
            dev_ca.write_text("-----BEGIN CERTIFICATE-----" + chain.split("-----BEGIN CERTIFICATE-----")[2])
            os.environ["NOOSPHERE_C2PA_TRUST_ANCHORS"] = str(dev_ca)
            try:
                trusted = verify.verify_sidecar(response)
                record = bytearray(base64.b64decode(response["record_b64"]))
                record[10] ^= 1
                tampered = verify.verify_sidecar({**response, "record_b64": base64.b64encode(bytes(record)).decode()})
            finally:
                del os.environ["NOOSPHERE_C2PA_TRUST_ANCHORS"]
            public = verify.verify_sidecar(response)

        assert trusted["verified"] and trusted["validation_state"] == "Trusted", (product, trusted)
        assert not public["verified"] and "signingCredential.untrusted" in public["failures"], (product, public)
        assert not tampered["verified"] and "assertion.dataHash.mismatch" in tampered["failures"], (product, tampered)


if __name__ == "__main__":
    raise SystemExit(main())
