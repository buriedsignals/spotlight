#!/usr/bin/env python3
"""Validate U1 projection schemas, shared vocabulary, and strict boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

try:
    from jsonschema import Draft7Validator, FormatChecker
except ImportError:
    if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
        raise
    print("skip projection schema validation (jsonschema unavailable; required in CI)")
    raise SystemExit(0)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
HASHES = {letter: letter * 64 for letter in "abcdef"}
SCHEMA_FILES = {
    "manifest": "projection-manifest.schema.json",
    "job": "projection-job.schema.json",
    "policy": "case-policy-receipt.schema.json",
    "package": "knowledge-workspace-package.schema.json",
}
EXPECTED_VERSIONS = {
    "manifest": "spotlight-projection-manifest/v1",
    "job": "spotlight-projection-job/v1",
    "policy": "spotlight-case-policy-receipt/v1",
    "package": "spotlight-workspace-projection-package/v1",
}
KNOWLEDGE_BATCH_V1_SHA256 = (
    "660a1bcba8e2738bfe287badfce941e4195de0d46681fedb73b7d8c65f2f02ec"
)


def load_schemas() -> dict[str, dict]:
    return {
        name: json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
        for name, filename in SCHEMA_FILES.items()
    }


def errors(schema: dict, document: dict) -> list[str]:
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    return [error.message for error in validator.iter_errors(document)]


def assert_valid(schema: dict, document: dict) -> None:
    found = errors(schema, document)
    assert not found, "\n".join(found)


def valid_policy() -> dict:
    return {
        "schema_version": EXPECTED_VERSIONS["policy"],
        "receipt_id": "receipt:policy-one",
        "case_id": "case:alpha",
        "policy_revision": 1,
        "status": "active",
        "classification": "personal",
        "allowed_destinations": ["destination:newsroom"],
        "provider_policy": {
            "allowed_modes": ["full_text", "semantic"],
            "provider_ids": ["openknowledge:configured"],
            "data_localities": ["local"],
            "network_egress": "denied",
            "max_retention_days": 0,
        },
        "issued_at": "2026-08-18T10:00:00Z",
        "expires_at": "2026-08-19T10:00:00Z",
        "revocation_version": 1,
        "issuer": {
            "issuer_id": "issuer:newsroom",
            "issuer_key_id": "key:one",
            "algorithm": "ed25519",
            "payload_sha256": HASHES["a"],
            "signature": "c2ln",
        },
    }


def valid_manifest() -> dict:
    return {
        "schema_version": EXPECTED_VERSIONS["manifest"],
        "manifest_id": "manifest:alpha-one",
        "case_id": "case:alpha",
        "destination_id": "destination:newsroom",
        "classification": "personal",
        "generation": 1,
        "created_at": "2026-08-18T10:00:00Z",
        "graph": {
            "receipt_id": "receipt:graph-one",
            "commit_sha256": HASHES["a"],
            "snapshot_at": "2026-08-18T09:59:00Z",
            "records": [
                {
                    "kind": "claim",
                    "id": "claim:alpha",
                    "version": 1,
                    "payload_sha256": HASHES["b"],
                }
            ],
        },
        "signed_case": {
            "provenance_revision": 1,
            "provenance_receipt_id": "receipt:case-one",
            "provenance_sha256": HASHES["c"],
            "artifacts": [
                {
                    "path": "data/findings.json",
                    "role": "findings",
                    "sha256": HASHES["d"],
                }
            ],
        },
        "case_policy": {
            "receipt_id": "receipt:policy-one",
            "policy_revision": 1,
            "receipt_sha256": HASHES["e"],
        },
        "desired_projection_set_sha256": HASHES["f"],
        "pages": [
            {
                "path": "investigations/alpha.md",
                "kind": "investigation_block",
                "owner_id": "owner:spotlight-alpha",
                "content_sha256": HASHES["a"],
                "expected_version": HASHES["f"],
            }
        ],
    }


def valid_job() -> dict:
    return {
        "schema_version": EXPECTED_VERSIONS["job"],
        "job_id": "job:alpha-one",
        "idempotency_key": HASHES["a"],
        "case_id": "case:alpha",
        "destination_id": "destination:newsroom",
        "generation": 1,
        "desired_projection_set_sha256": HASHES["b"],
        "status": "pending",
        "attempts": 0,
        "created_at": "2026-08-18T10:00:00Z",
        "updated_at": "2026-08-18T10:00:00Z",
    }


def valid_package() -> dict:
    return {
        "schema_version": EXPECTED_VERSIONS["package"],
        "package_id": "package:alpha-one",
        "idempotency_key": HASHES["a"],
        "case_id": "case:alpha",
        "classification": "personal",
        "destination_id": "destination:newsroom",
        "graph_receipt_id": "receipt:graph-one",
        "desired_projection_set_sha256": HASHES["d"],
        "operations": [
            {
                "operation_id": "operation:upsert-one",
                "kind": "managed_block_upsert",
                "path": "investigations/alpha.md",
                "owner_id": "owner:spotlight-alpha",
                "expected_version": HASHES["f"],
                "expected_outside_sha256": HASHES["a"],
                "expected_managed_sha256": HASHES["b"],
                "desired_sha256": HASHES["e"],
                "content": "<!-- managed -->\n",
            }
        ],
    }


def valid_deindex_receipt() -> dict:
    hashes = {
        "document": HASHES["a"], "full_text": HASHES["b"],
        "vector": HASHES["c"], "cache": HASHES["d"],
        "provider_derived": HASHES["e"],
    }
    return {
        "schema_version": "knowledge-deindex-receipt/v1",
        "receipt_id": "receipt:delete-one",
        "destination_id": "destination:newsroom",
        "document_path": "stories/arc-one.md",
        "deleted_version": HASHES["a"],
        "deleted_hashes": hashes,
        "storage_classes": {key: True for key in hashes},
        "confirmed_at": "2026-08-18T10:00:00Z",
        "valid_until": "2026-08-18T10:05:00Z",
        "retention_exclusions": [],
        "issuer": {
            "issuer_id": "issuer:workspace",
            "issuer_key_id": "key:workspace",
            "algorithm": "ed25519",
            "payload_sha256": HASHES["f"],
            "signature": "c2ln",
        },
    }


def valid_final_receipt() -> dict:
    return {
        "schema_version": "spotlight-workspace-final-receipt/v1",
        "receipt_id": "receipt:projection-one",
        "package_sha256": HASHES["a"],
        "desired_projection_set_sha256": HASHES["b"],
        "case_id": "case:alpha", "classification": "personal",
        "destination_id": "destination:newsroom", "graph_receipt_id": "receipt:graph-one",
        "operations": [{
            "operation_id": "operation:upsert-one",
            "kind": "managed_block_upsert",
            "path": "investigations/alpha.md",
            "owner_id": "owner:spotlight-alpha",
            "final_version": HASHES["c"],
        }],
    }
def main() -> int:
    schemas = load_schemas()
    for name, schema in schemas.items():
        Draft7Validator.check_schema(schema)
        assert schema["properties"]["schema_version"]["const"] == EXPECTED_VERSIONS[name]
        assert schema["additionalProperties"] is False

    # Shared primitives must not drift between the four serialized contracts.
    for definition in ("sha256", "date_time", "case_id", "destination_id"):
        values = [schema["definitions"][definition] for schema in schemas.values()]
        assert all(value == values[0] for value in values[1:]), definition
    for definition in ("classification",):
        values = [schemas[name]["definitions"][definition] for name in ("manifest", "policy", "package")]
        assert all(value == values[0] for value in values[1:]), definition

    documents = {
        "manifest": valid_manifest(), "job": valid_job(),
        "policy": valid_policy(), "package": valid_package(),
    }
    for name, document in documents.items():
        assert_valid(schemas[name], document)
        unknown = copy.deepcopy(document)
        unknown["schema_version"] = "unknown/v99"
        assert errors(schemas[name], unknown), f"{name} accepted unknown version"
        extra = copy.deepcopy(document)
        extra["unexpected"] = True
        assert errors(schemas[name], extra), f"{name} accepted extra property"

    missing_outside_hash = valid_package()
    del missing_outside_hash["operations"][0]["expected_outside_sha256"]
    assert errors(schemas["package"], missing_outside_hash)
    non_hash_version = valid_package()
    non_hash_version["operations"][0]["expected_version"] = "version:one"
    assert errors(schemas["package"], non_hash_version)
    wrong_owner = valid_package()
    wrong_owner["operations"][0]["owner_id"] = "spotlight-alpha"
    assert errors(schemas["package"], wrong_owner)
    absent_investigation = valid_package()
    absent_investigation["operations"][0]["expected_version"] = "absent"
    assert errors(schemas["package"], absent_investigation)
    absent_story = valid_package()
    absent_story["operations"][0]["path"] = "stories/new.md"
    absent_story["operations"][0]["expected_version"] = "absent"
    absent_story["operations"][0]["expected_outside_sha256"] = hashlib.sha256(b"").hexdigest()
    del absent_story["operations"][0]["expected_managed_sha256"]
    assert_valid(schemas["package"], absent_story)
    valid_removal = valid_package()
    valid_removal["operations"] = [{
        "operation_id": "operation:remove-one",
        "kind": "managed_page_removal",
        "path": "stories/arc-one.md",
        "owner_id": "owner:spotlight-alpha",
        "expected_version": HASHES["a"],
        "deleted_sha256": HASHES["a"],
    }]
    assert_valid(schemas["package"], valid_removal)
    caller_derived_hashes = copy.deepcopy(valid_removal)
    caller_derived_hashes["operations"][0]["deleted_hashes"] = {
        "document": HASHES["a"], "full_text": HASHES["a"],
        "vector": HASHES["a"], "cache": HASHES["a"],
        "provider_derived": HASHES["a"],
    }
    assert errors(schemas["package"], caller_derived_hashes)
    invalid_managed_hash = valid_package()
    invalid_managed_hash["operations"][0]["expected_managed_sha256"] = "version:one"
    assert errors(schemas["package"], invalid_managed_hash)
    ambiguous_removal = valid_package()
    ambiguous_removal["operations"] = [{
        "operation_id": "operation:remove-one",
        "kind": "managed_page_removal",
        "path": "stories/arc-one.md",
        "owner_id": "owner:spotlight-alpha",
        "expected_version": HASHES["a"],
        "deleted_sha256": HASHES["a"],
        "content": "must not be accepted",
    }]
    assert errors(schemas["package"], ambiguous_removal)

    final_receipt_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/definitions/workspace_final_receipt",
        "definitions": schemas["package"]["definitions"],
    }
    final_receipt = valid_final_receipt()
    assert_valid(final_receipt_schema, final_receipt)
    # Receipt identity uses UTF-8, recursive lexicographic keys, compact
    # separators, and ensure_ascii=False; receipt_id is blank while hashing.
    golden_material = copy.deepcopy(final_receipt)
    golden_material["receipt_id"] = ""
    golden_bytes = json.dumps(
        golden_material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert hashlib.sha256(golden_bytes).hexdigest() == (
        "f490978dce2e72b00a04dc0c65d46af9eeceba7673ae0409bb94bcfe6dd330fe"
    )
    leaked_content = copy.deepcopy(final_receipt)
    leaked_content["operations"][0]["content"] = "must never be serialized"
    assert errors(final_receipt_schema, leaked_content)
    missing_final_version = copy.deepcopy(final_receipt)
    del missing_final_version["operations"][0]["final_version"]
    assert errors(final_receipt_schema, missing_final_version)
    removal_receipt = copy.deepcopy(final_receipt)
    removal_receipt["operations"] = [{
        "operation_id": "operation:remove-one",
        "kind": "managed_page_removal",
        "path": "stories/arc-one.md",
        "owner_id": "owner:spotlight-alpha",
        "removed": True,
    }]
    assert_valid(final_receipt_schema, removal_receipt)
    removal_receipt["operations"][0]["removed"] = False
    assert errors(final_receipt_schema, removal_receipt)
    pending_receipt = copy.deepcopy(removal_receipt)
    pending_receipt["operations"][0]["deindex_pending"] = True
    assert errors(final_receipt_schema, pending_receipt)

    # The reviewed-batch 1.0 schema is a frozen pre-existing public contract.
    batch_bytes = (SCHEMA_DIR / "knowledge-batch.schema.json").read_bytes()
    assert hashlib.sha256(batch_bytes).hexdigest() == KNOWLEDGE_BATCH_V1_SHA256

    print("projection schema validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
