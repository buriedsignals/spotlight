#!/usr/bin/env python3
"""Local Knowledge Destination for Spotlight reviewed knowledge.

The port is additive: it consumes a reviewed knowledge batch and never edits the
case files, vault claim notes, or their content hashes.

Commands:
    knowledge_destination.py stage --case-root ROOT --case-dir CASE --destination-id ID BATCH.json
    knowledge_destination.py approval --manifest MANIFEST --reviewer-id ID --approved-at TIME
    knowledge_destination.py commit [contained paths and signed approval options] BATCH.json
    knowledge_destination.py policy-commit [contained signed policy options] POLICY.json
    knowledge_destination.py job-claim --workspace-root ROOT --db DB
    knowledge_destination.py job-fail --workspace-root ROOT --db DB --job-id ID --error TEXT
    knowledge_destination.py job-retry --workspace-root ROOT --db DB --job-id ID
    knowledge_destination.py job-complete [final workspace receipt reference options]
    knowledge_destination.py lookup --workspace-root ROOT --db DB [exact lookup options]
    knowledge_destination.py traverse --workspace-root ROOT --db DB --claim-id claim:...
    knowledge_destination.py coverage --workspace-root ROOT --db DB
    knowledge_destination.py verify --workspace-root ROOT --db DB
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_expression_contract import canonical_fingerprint, passage_core


SCHEMA_VERSION = "1.0"
DATABASE_SCHEMA_VERSION = 2
RESPONSE_SCHEMA_VERSION = "1.0"
APPROVAL_NAMESPACE = "spotlight-knowledge-batch-v1"
CASE_POLICY_NAMESPACE = "spotlight-case-policy-v1"
SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_RECORDS_PER_COLLECTION = 10_000
MAX_TEXT_LENGTH = 65_536
MAX_JSON_DEPTH = 20
DEFAULT_QUERY_LIMIT = 200
MAX_QUERY_LIMIT = 1_000
ID_PATTERNS = {
    "batch": re.compile(r"^batch:[a-z0-9][a-z0-9._:-]*$"),
    "claim": re.compile(r"^claim:[a-z0-9][a-z0-9._:-]*$"),
    "event": re.compile(r"^event:[a-z0-9][a-z0-9._:-]*$"),
    "story_arc": re.compile(r"^story-arc:[a-z0-9][a-z0-9._:-]*$"),
    "decision": re.compile(r"^decision:[a-z0-9][a-z0-9._:-]*$"),
    "claim_event": re.compile(r"^relation:claim-event:[a-z0-9][a-z0-9._:-]*$"),
    "event_story": re.compile(r"^relation:event-story:[a-z0-9][a-z0-9._:-]*$"),
}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DESTINATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
JOB_ID_RE = re.compile(r"^projection-job:[a-f0-9]{64}$")
RECEIPT_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,511}$")
PROJECTION_JOB_STATUSES = {"pending", "running", "failed", "completed", "superseded"}
RECORD_STATUSES = {"candidate", "approved", "rejected", "superseded"}
MEMBERSHIP_STATUSES = {"candidate", "approved", "rejected", "superseded"}
DECISION_DISPOSITIONS = {"approved", "rejected", "superseded", "not_applicable"}
PROVENANCE_METHODS = {"journalist", "migration", "agent_candidate", "newsroom_adapter"}


class ContractError(ValueError):
    """Raised when a batch or database transition violates the port contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size > MAX_BATCH_BYTES:
            raise ContractError(
                f"JSON input exceeds {MAX_BATCH_BYTES} byte safety limit"
            )
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FileNotFoundError as exc:
        raise ContractError(f"batch not found: {path}") from exc
    except (OSError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError(f"cannot read batch {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("batch root must be an object")
    return value


def load_json_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    """Read a regular non-symlink JSON file once and hash the captured bytes."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ContractError(f"JSON input is not a regular file: {path}")
            if metadata.st_size > MAX_BATCH_BYTES:
                raise ContractError(
                    f"JSON input exceeds {MAX_BATCH_BYTES} byte safety limit"
                )
            chunks: list[bytes] = []
            remaining = MAX_BATCH_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_BATCH_BYTES:
                raise ContractError(
                    f"JSON input exceeds {MAX_BATCH_BYTES} byte safety limit"
                )
        finally:
            os.close(descriptor)
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError(f"cannot read batch {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("batch root must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source_case(case_dir: Path, batch: dict[str, Any]) -> dict[str, Any]:
    data_dir = case_dir / "data"
    findings_path = data_dir / "findings.json"
    if not findings_path.is_file():
        raise ContractError(f"source case is missing {findings_path}")
    findings_doc, findings_hash_before = load_json_snapshot(findings_path)
    expressions_path = data_dir / "source-expressions.json"
    expressions_before = (
        load_json_snapshot(expressions_path) if expressions_path.is_file() else None
    )
    contract_path = data_dir / "case-contract.json"
    contract_before = (
        load_json_snapshot(contract_path) if contract_path.is_file() else None
    )
    validator = SCRIPT_DIR / "validate-case.py"
    try:
        result = subprocess.run(
            [sys.executable, str(validator), str(case_dir)],
            cwd=SCRIPT_DIR.parent,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"source case validator could not run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ContractError(f"source case validation failed: {detail}")

    findings_doc, findings_hash = load_json_snapshot(findings_path)
    if findings_hash != findings_hash_before:
        raise ContractError("source case changed during validation")
    source_case = batch["source_case"]
    findings_contract_version = findings_doc.get("schema_version", "1.0")
    if source_case["findings_contract_version"] != findings_contract_version:
        raise ContractError(
            "source_case.findings_contract_version does not match findings.json"
        )
    project = source_case["project"]
    if findings_doc.get("project") != project:
        raise ContractError("source case project does not match findings.json")
    findings = {
        row.get("id"): row
        for row in findings_doc.get("findings", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    expressions_doc: dict[str, Any] | None = None
    expressions: dict[str, dict[str, Any]] = {}
    if expressions_path.is_file():
        expressions_doc, expressions_hash = load_json_snapshot(expressions_path)
        if expressions_before is None or expressions_hash != expressions_before[1]:
            raise ContractError("source case changed during validation")
        if expressions_doc.get("project") != project:
            raise ContractError("source case project does not match source-expressions.json")
        expressions = {
            row.get("id"): row
            for row in expressions_doc.get("expressions", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }

    for claim in batch["claims"]:
        origin = claim["origin"]
        if origin["project"] != project:
            raise ContractError(f"claim {claim['id']} origin project does not match source case")
        finding = findings.get(origin["finding_id"])
        if finding is None:
            raise ContractError(
                f"claim {claim['id']} finding {origin['finding_id']} does not resolve"
            )
        fingerprint = canonical_fingerprint({"claim": finding.get("claim")})
        if origin["finding_fingerprint"] != fingerprint:
            raise ContractError(f"claim {claim['id']} finding fingerprint does not match source case")
        if finding.get("finding_fingerprint") not in {None, fingerprint}:
            raise ContractError(f"finding {origin['finding_id']} has an invalid stored fingerprint")
        for ref in claim["source_expression_refs"]:
            if ref["project"] != project:
                raise ContractError(
                    f"claim {claim['id']} expression project does not match source case"
                )
            expression = expressions.get(ref["expression_id"])
            if expression is None:
                raise ContractError(
                    f"claim {claim['id']} expression {ref['expression_id']} does not resolve"
                )
            expression_fingerprint = canonical_fingerprint(passage_core(expression))
            if (
                ref["expression_fingerprint"] != expression_fingerprint
                or expression.get("expression_fingerprint") != expression_fingerprint
            ):
                raise ContractError(
                    f"claim {claim['id']} expression fingerprint does not match source case"
                )
            matching_link = next(
                (
                    link for link in expression.get("finding_links", [])
                    if isinstance(link, dict)
                    and link.get("finding_id") == origin["finding_id"]
                    and link.get("finding_fingerprint") == fingerprint
                    and link.get("relation") == ref["relation"]
                ),
                None,
            )
            if matching_link is None:
                raise ContractError(
                    f"claim {claim['id']} expression link does not match source case"
                )

    artifact_hashes = {"findings_sha256": findings_hash}
    if expressions_doc is not None:
        artifact_hashes["source_expressions_sha256"] = expressions_hash
    if source_case["findings_contract_version"] == "1.1":
        if contract_before is None:
            raise ContractError("activated source case is missing case-contract.json")
        _, actual_contract_hash = load_json_snapshot(contract_path)
        if actual_contract_hash != contract_before[1]:
            raise ContractError("source case changed during validation")
        if source_case.get("case_contract_sha256") != actual_contract_hash:
            raise ContractError("source_case.case_contract_sha256 does not match activated case")
        artifact_hashes["case_contract_sha256"] = actual_contract_hash
    return {
        "project": project,
        "findings_contract_version": source_case["findings_contract_version"],
        "artifact_hashes": artifact_hashes,
    }


def review_manifest(
    batch: dict[str, Any], source_snapshot: dict[str, Any], destination_id: str
) -> dict[str, Any]:
    if DESTINATION_ID_RE.fullmatch(destination_id) is None:
        raise ContractError("destination-id has an invalid format")
    additions = {
        key: [{"id": row["id"], "version": row["version"]} for row in batch[key]]
        for key in (
            "claims", "events", "story_arcs", "claim_event_memberships",
            "event_story_arc_memberships",
        )
    }
    candidates = {
        key: [row["id"] for row in batch[key] if row.get("status") == "candidate"]
        for key in additions
    }
    not_applicable = {
        "claims": [
            row["id"] for row in batch["claims"]
            if row["event_link_disposition"] == "not_applicable"
        ],
        "events": [
            row["id"] for row in batch["events"]
            if row["story_link_disposition"] == "not_applicable"
        ],
    }
    manifest = {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "status": "staged",
        "destination_id": destination_id,
        "batch_id": batch["batch_id"],
        "payload_sha256": payload_sha256(batch),
        "source_snapshot": source_snapshot,
        "additions": additions,
        "candidates": candidates,
        "not_applicable": not_applicable,
        "exclusions": [],
    }
    manifest["review_manifest_sha256"] = payload_sha256(manifest)
    return manifest


def validate_approval_receipt(
    receipt: dict[str, Any], manifest: dict[str, Any]
) -> None:
    required = {
        "schema_version", "namespace", "destination_id", "payload_sha256",
        "review_manifest_sha256", "reviewer_id", "approved_at", "decision",
    }
    if set(receipt) != required:
        raise ContractError("approval receipt has an invalid field set")
    if receipt["schema_version"] != "1.0" or receipt["namespace"] != APPROVAL_NAMESPACE:
        raise ContractError("approval receipt contract is unsupported")
    if receipt["decision"] != "approved" or not nonempty(receipt["reviewer_id"]):
        raise ContractError("approval receipt is not an attributable approval")
    if not valid_datetime(receipt["approved_at"]):
        raise ContractError("approval receipt approved_at is invalid")
    for key in ("destination_id", "payload_sha256", "review_manifest_sha256"):
        if receipt[key] != manifest[key]:
            raise ContractError(f"approval receipt {key} does not match staged operation")


def verify_approval_signature(
    receipt: dict[str, Any], signature_path: Path, allowed_signers_path: Path
) -> dict[str, str]:
    for path, label in (
        (signature_path, "approval signature"),
        (allowed_signers_path, "allowed signers"),
    ):
        _reject_symlink_path(path)
        if not path.is_file():
            raise ContractError(f"{label} file not found: {path}")
    try:
        signature_text = signature_path.read_text(encoding="utf-8")
        allowed_signers_text = allowed_signers_path.read_text(encoding="utf-8")
        if len(signature_text) > 1024 * 1024 or len(allowed_signers_text) > 1024 * 1024:
            raise ContractError("approval evidence exceeds the 1 MiB safety limit")
        result = subprocess.run(
            [
                str(SSH_KEYGEN), "-Y", "verify", "-f", str(allowed_signers_path),
                "-I", receipt["reviewer_id"], "-n", APPROVAL_NAMESPACE,
                "-s", str(signature_path),
            ],
            input=canonical_json_bytes(receipt),
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"approval signature verifier could not run: {exc}") from exc
    if result.returncode != 0:
        raise ContractError("approval signature verification failed")
    return {
        "signature": signature_text,
        "allowed_signers": allowed_signers_text,
        "allowed_signers_sha256": hashlib.sha256(
            allowed_signers_text.encode("utf-8")
        ).hexdigest(),
        "verifier": str(SSH_KEYGEN),
    }


def verify_stored_approval_signature(
    receipt: dict[str, Any], evidence: dict[str, str]
) -> None:
    if evidence.get("verifier") != str(SSH_KEYGEN):
        raise ContractError("stored approval verifier is unsupported")
    allowed = evidence.get("allowed_signers", "")
    if hashlib.sha256(allowed.encode("utf-8")).hexdigest() != evidence.get(
        "allowed_signers_sha256"
    ):
        raise ContractError("stored allowed-signers evidence is corrupt")
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp).resolve()
        signature_path = tmp / "approval.sig"
        allowed_path = tmp / "allowed_signers"
        signature_path.write_text(evidence.get("signature", ""), encoding="utf-8")
        allowed_path.write_text(allowed, encoding="utf-8")
        verify_approval_signature(receipt, signature_path, allowed_path)


def verify_signed_document(
    document: dict[str, Any], identity: str, namespace: str,
    signature_path: Path, allowed_signers_path: Path,
) -> dict[str, str]:
    for path, label in (
        (signature_path, "signature"),
        (allowed_signers_path, "allowed signers"),
    ):
        _reject_symlink_path(path)
        if not path.is_file():
            raise ContractError(f"{label} file not found: {path}")
    signature_text = signature_path.read_text(encoding="utf-8")
    allowed_text = allowed_signers_path.read_text(encoding="utf-8")
    if len(signature_text) > 1024 * 1024 or len(allowed_text) > 1024 * 1024:
        raise ContractError("signature evidence exceeds the 1 MiB safety limit")
    try:
        result = subprocess.run(
            [
                str(SSH_KEYGEN), "-Y", "verify", "-f", str(allowed_signers_path),
                "-I", identity, "-n", namespace, "-s", str(signature_path),
            ],
            input=canonical_json_bytes(document), capture_output=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"signature verifier could not run: {exc}") from exc
    if result.returncode != 0:
        raise ContractError("signature verification failed")
    return {
        "signature": signature_text,
        "allowed_signers": allowed_text,
        "allowed_signers_sha256": hashlib.sha256(
            allowed_text.encode("utf-8")
        ).hexdigest(),
        "verifier": str(SSH_KEYGEN),
        "identity": identity,
        "namespace": namespace,
    }


def verify_stored_signed_document(
    document: dict[str, Any], evidence: dict[str, str]
) -> None:
    if evidence.get("verifier") != str(SSH_KEYGEN):
        raise ContractError("stored signature verifier is unsupported")
    allowed = evidence.get("allowed_signers", "")
    if hashlib.sha256(allowed.encode("utf-8")).hexdigest() != evidence.get(
        "allowed_signers_sha256"
    ):
        raise ContractError("stored allowed-signers evidence is corrupt")
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp).resolve()
        signature_path = tmp / "document.sig"
        allowed_path = tmp / "allowed_signers"
        signature_path.write_text(evidence.get("signature", ""), encoding="utf-8")
        allowed_path.write_text(allowed, encoding="utf-8")
        verify_signed_document(
            document, evidence.get("identity", ""), evidence.get("namespace", ""),
            signature_path, allowed_path,
        )


def case_policy_payload_sha256(receipt: dict[str, Any]) -> str:
    unsigned = json.loads(json_text(receipt))
    issuer = unsigned.get("issuer")
    if isinstance(issuer, dict):
        issuer.pop("payload_sha256", None)
        issuer.pop("signature", None)
    return payload_sha256(unsigned)


def validate_case_policy_receipt(
    receipt: dict[str, Any], destination_id: str
) -> None:
    required = {
        "schema_version", "receipt_id", "case_id", "policy_revision", "status",
        "classification", "allowed_destinations", "provider_policy", "issued_at",
        "expires_at", "revocation_version", "issuer",
    }
    optional = {"revoked_at", "revocation_reason"}
    if not required.issubset(receipt) or set(receipt) - required - optional:
        raise ContractError("case-policy receipt has an invalid field set")
    if receipt["schema_version"] != "spotlight-case-policy-receipt/v1":
        raise ContractError("case-policy receipt contract is unsupported")
    if not re.fullmatch(r"receipt:[a-z0-9][a-z0-9._:-]{0,247}", str(receipt["receipt_id"])):
        raise ContractError("case-policy receipt_id is invalid")
    if not re.fullmatch(r"case:[a-z0-9][a-z0-9._:-]{0,250}", str(receipt["case_id"])):
        raise ContractError("case-policy case_id is invalid")
    if not re.fullmatch(r"destination:[a-z0-9][a-z0-9._:-]{0,243}", destination_id):
        raise ContractError("case-policy destination_id is invalid")
    if receipt["status"] not in {"active", "revoked"}:
        raise ContractError("case-policy status is invalid")
    if receipt["classification"] not in {"shareable", "internal", "personal"}:
        raise ContractError("case-policy classification is invalid")
    if not isinstance(receipt["policy_revision"], int) or receipt["policy_revision"] < 1:
        raise ContractError("case-policy revision is invalid")
    if not isinstance(receipt["revocation_version"], int) or receipt["revocation_version"] < 1:
        raise ContractError("case-policy revocation_version is invalid")
    if destination_id not in receipt["allowed_destinations"]:
        raise ContractError("case-policy does not authorize this destination")
    if not valid_datetime(receipt["issued_at"]) or not valid_datetime(receipt["expires_at"]):
        raise ContractError("case-policy timestamps are invalid")
    issued_at = datetime.fromisoformat(receipt["issued_at"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
    if expires_at <= issued_at:
        raise ContractError("case-policy expires_at must be after issued_at")
    if receipt["status"] == "revoked":
        if not valid_datetime(receipt.get("revoked_at")) or not nonempty(
            receipt.get("revocation_reason")
        ):
            raise ContractError("revoked case-policy requires revocation evidence")
    elif "revoked_at" in receipt or "revocation_reason" in receipt:
        raise ContractError("active case-policy cannot contain revocation evidence")
    issuer = receipt.get("issuer")
    if not isinstance(issuer, dict) or set(issuer) != {
        "issuer_id", "issuer_key_id", "algorithm", "payload_sha256", "signature"
    }:
        raise ContractError("case-policy issuer binding is invalid")
    if not re.fullmatch(r"issuer:[a-z0-9][a-z0-9._:-]{0,246}", str(issuer["issuer_id"])):
        raise ContractError("case-policy issuer_id is invalid")
    if not re.fullmatch(r"key:[a-z0-9][a-z0-9._:-]{0,249}", str(issuer["issuer_key_id"])):
        raise ContractError("case-policy issuer_key_id is invalid")
    if issuer["algorithm"] not in {"ed25519", "ecdsa-p256-sha256"}:
        raise ContractError("case-policy signature algorithm is invalid")
    if not isinstance(issuer["signature"], str) or re.fullmatch(
        r"[A-Za-z0-9+/]+={0,2}", issuer["signature"]
    ) is None:
        raise ContractError("case-policy embedded signature is invalid")
    if issuer["payload_sha256"] != case_policy_payload_sha256(receipt):
        raise ContractError("case-policy payload hash is invalid")
    policy = receipt.get("provider_policy")
    if not isinstance(policy, dict) or set(policy) != {
        "allowed_modes", "provider_ids", "data_localities", "network_egress",
        "max_retention_days",
    }:
        raise ContractError("case-policy provider policy is invalid")
    modes = policy["allowed_modes"]
    if (
        not isinstance(modes, list) or not modes
        or len(modes) != len(set(modes))
        or any(mode not in {"full_text", "semantic"} for mode in modes)
    ):
        raise ContractError("case-policy provider modes are invalid")


def _case_id_for_project(project: str) -> str:
    return project if project.startswith("case:") else f"case:{project}"


def validate_collection_limit(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, list) and len(value) > MAX_RECORDS_PER_COLLECTION:
        errors.append(
            f"{label}: exceeds {MAX_RECORDS_PER_COLLECTION} record safety limit"
        )


def validate_text_limit(value: Any, label: str, errors: list[str]) -> None:
    if isinstance(value, str) and len(value) > MAX_TEXT_LENGTH:
        errors.append(f"{label}: exceeds {MAX_TEXT_LENGTH} character safety limit")


def validate_value_limits(
    value: Any, label: str, errors: list[str], depth: int = 0
) -> None:
    if depth > MAX_JSON_DEPTH:
        errors.append(f"{label}: exceeds JSON depth limit {MAX_JSON_DEPTH}")
        return
    if isinstance(value, str):
        validate_text_limit(value, label, errors)
    elif isinstance(value, list):
        validate_collection_limit(value, label, errors)
        for index, item in enumerate(value):
            validate_value_limits(item, f"{label}[{index}]", errors, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_value_limits(item, f"{label}.{key}", errors, depth + 1)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_datetime(value: Any) -> bool:
    if not nonempty(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def exact_keys(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label}: must be an object")
        return False
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        errors.append(f"{label}: missing fields {sorted(missing)}")
    if unknown:
        errors.append(f"{label}: unknown fields {sorted(unknown)}")
    return not missing and not unknown


def validate_id(value: Any, kind: str, label: str, errors: list[str]) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 256
        or ID_PATTERNS[kind].fullmatch(value) is None
    ):
        errors.append(f"{label}: invalid {kind} identifier")


def validate_sha(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"{label}: must be lowercase SHA-256")


def validate_versioned(value: dict[str, Any], label: str, errors: list[str]) -> None:
    version = value.get("version")
    previous = value.get("supersedes_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append(f"{label}.version: must be a positive integer")
        return
    if version == 1 and previous is not None:
        errors.append(f"{label}: version 1 cannot supersede another version")
    if version > 1 and previous != version - 1:
        errors.append(f"{label}: version {version} must supersede version {version - 1}")


def validate_provenance(value: Any, label: str, errors: list[str]) -> None:
    if not exact_keys(
        value,
        {"actor", "method", "recorded_at"},
        {"model", "notes"},
        label,
        errors,
    ):
        return
    if not nonempty(value.get("actor")):
        errors.append(f"{label}.actor: must be non-empty")
    if value.get("method") not in PROVENANCE_METHODS:
        errors.append(f"{label}.method: invalid provenance method")
    if not valid_datetime(value.get("recorded_at")):
        errors.append(f"{label}.recorded_at: must be a timezone-aware ISO timestamp")
    for key in ("model", "notes"):
        if key in value and not nonempty(value[key]):
            errors.append(f"{label}.{key}: must be non-empty when present")


def validate_endpoint(
    value: Any, kind: str, label: str, errors: list[str]
) -> None:
    if not exact_keys(value, {"id", "version"}, set(), label, errors):
        return
    validate_id(value.get("id"), kind, f"{label}.id", errors)
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append(f"{label}.version: must be a positive integer")


def validate_review_decisions(
    rows: Any, errors: list[str]
) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        errors.append("review_decisions: must be a non-empty array")
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    required = {
        "id", "reviewer_id", "decided_at", "disposition", "rationale", "subject"
    }
    for index, row in enumerate(rows):
        label = f"review_decisions[{index}]"
        if not exact_keys(row, required, set(), label, errors):
            continue
        decision_id = row.get("id")
        validate_id(decision_id, "decision", f"{label}.id", errors)
        if decision_id in decisions:
            errors.append(f"{label}.id: duplicate {decision_id!r}")
        elif isinstance(decision_id, str):
            decisions[decision_id] = row
        if not nonempty(row.get("reviewer_id")):
            errors.append(f"{label}.reviewer_id: must be non-empty")
        validate_text_limit(row.get("reviewer_id"), f"{label}.reviewer_id", errors)
        if not valid_datetime(row.get("decided_at")):
            errors.append(f"{label}.decided_at: must be a timezone-aware ISO timestamp")
        if row.get("disposition") not in DECISION_DISPOSITIONS:
            errors.append(f"{label}.disposition: invalid disposition")
        if not nonempty(row.get("rationale")):
            errors.append(f"{label}.rationale: must be non-empty")
        validate_text_limit(row.get("rationale"), f"{label}.rationale", errors)
        subject = row.get("subject")
        if exact_keys(
            subject,
            {"kind", "id", "version", "payload_sha256"},
            set(),
            f"{label}.subject",
            errors,
        ):
            if subject.get("kind") not in {
                "source_case", "claim", "event", "story_arc",
                "claim_event_membership", "event_story_arc_membership",
            }:
                errors.append(f"{label}.subject.kind: invalid subject kind")
            subject_kind = subject.get("kind")
            subject_id = subject.get("id")
            if not nonempty(subject_id):
                errors.append(f"{label}.subject.id: must be non-empty")
            elif subject_kind != "source_case":
                id_kind = {
                    "claim": "claim",
                    "event": "event",
                    "story_arc": "story_arc",
                    "claim_event_membership": "claim_event",
                    "event_story_arc_membership": "event_story",
                }.get(subject_kind)
                if id_kind is not None:
                    validate_id(subject_id, id_kind, f"{label}.subject.id", errors)
            version = subject.get("version")
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                errors.append(f"{label}.subject.version: must be a positive integer")
            validate_sha(
                subject.get("payload_sha256"),
                f"{label}.subject.payload_sha256",
                errors,
            )
    return decisions


def decision_matches(
    row: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    expected: str,
    label: str,
    errors: list[str],
    field: str = "review_decision_id",
    subject_kind: str | None = None,
    subject_id: str | None = None,
    subject_version: int | None = None,
) -> None:
    decision_id = row.get(field)
    validate_id(decision_id, "decision", f"{label}.{field}", errors)
    decision = decisions.get(decision_id)
    if decision is None:
        errors.append(f"{label}.{field}: decision {decision_id!r} does not resolve in batch")
    elif decision.get("disposition") != expected:
        errors.append(
            f"{label}.{field}: decision disposition must be {expected!r}"
        )
    elif subject_kind is not None:
        expected_subject = {
            "kind": subject_kind,
            "id": subject_id,
            "version": subject_version,
            "payload_sha256": payload_sha256(row),
        }
        if decision.get("subject") != expected_subject:
            errors.append(
                f"{label}.{field}: decision subject does not match exact record"
            )


def validate_record_decision(
    row: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    label: str,
    errors: list[str],
    kind: str,
) -> None:
    status = row.get("status")
    if status == "candidate":
        if "review_decision_id" in row:
            errors.append(f"{label}.review_decision_id: candidate records cannot be approved")
        return
    expected = status
    decision_matches(
        row, decisions, expected, label, errors,
        subject_kind=kind, subject_id=row.get("id"),
        subject_version=row.get("version"),
    )


def validate_membership_decision(
    row: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    label: str,
    errors: list[str],
    kind: str,
) -> None:
    status = row.get("status")
    if status == "candidate":
        if "review_decision_id" in row:
            errors.append(
                f"{label}.review_decision_id: candidate memberships cannot be approved"
            )
        return
    decision_matches(
        row, decisions, status, label, errors,
        subject_kind=kind, subject_id=row.get("id"),
        subject_version=row.get("version"),
    )


def unique_versions(
    rows: list[Any], label: str, errors: list[str]
) -> set[tuple[str, int]]:
    seen: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = (str(row.get("id", "")), row.get("version"))
        if key in seen:
            errors.append(f"{label}[{index}]: duplicate id/version {key!r}")
        seen.add(key)
    return seen


def validate_claims(
    rows: Any, decisions: dict[str, dict[str, Any]], errors: list[str]
) -> set[tuple[str, int]]:
    if not isinstance(rows, list):
        errors.append("claims: must be an array")
        return set()
    required = {
        "id", "version", "origin", "proposition", "status",
        "event_link_disposition", "provenance", "source_expression_refs",
    }
    optional = {"supersedes_version", "review_decision_id", "event_link_decision_id"}
    for index, row in enumerate(rows):
        label = f"claims[{index}]"
        if not exact_keys(row, required, optional, label, errors):
            continue
        validate_id(row.get("id"), "claim", f"{label}.id", errors)
        validate_versioned(row, label, errors)
        if not nonempty(row.get("proposition")):
            errors.append(f"{label}.proposition: must be non-empty")
        if row.get("status") not in RECORD_STATUSES:
            errors.append(f"{label}.status: invalid record status")
        validate_record_decision(row, decisions, label, errors, "claim")
        disposition = row.get("event_link_disposition")
        if disposition not in {"pending", "linked", "not_applicable"}:
            errors.append(f"{label}.event_link_disposition: invalid disposition")
        if disposition == "not_applicable":
            decision_matches(
                row, decisions, "not_applicable", label, errors,
                field="event_link_decision_id",
                subject_kind="claim", subject_id=row.get("id"),
                subject_version=row.get("version"),
            )
        elif "event_link_decision_id" in row:
            errors.append(
                f"{label}.event_link_decision_id: allowed only for not_applicable"
            )
        origin = row.get("origin")
        if exact_keys(
            origin,
            {"project", "finding_id", "finding_fingerprint"},
            {"legacy_claim_id"},
            f"{label}.origin",
            errors,
        ):
            for key in ("project", "finding_id"):
                if not nonempty(origin.get(key)):
                    errors.append(f"{label}.origin.{key}: must be non-empty")
            validate_sha(
                origin.get("finding_fingerprint"),
                f"{label}.origin.finding_fingerprint",
                errors,
            )
            if "legacy_claim_id" in origin and not nonempty(origin["legacy_claim_id"]):
                errors.append(f"{label}.origin.legacy_claim_id: must be non-empty")
        refs = row.get("source_expression_refs")
        if not isinstance(refs, list):
            errors.append(f"{label}.source_expression_refs: must be an array")
        else:
            ref_keys: set[tuple[str, str, str]] = set()
            for ref_index, ref in enumerate(refs):
                ref_label = f"{label}.source_expression_refs[{ref_index}]"
                if not exact_keys(
                    ref,
                    {"project", "expression_id", "expression_fingerprint", "relation"},
                    set(),
                    ref_label,
                    errors,
                ):
                    continue
                for key in ("project", "expression_id"):
                    if not nonempty(ref.get(key)):
                        errors.append(f"{ref_label}.{key}: must be non-empty")
                validate_sha(
                    ref.get("expression_fingerprint"),
                    f"{ref_label}.expression_fingerprint",
                    errors,
                )
                if ref.get("relation") not in {"supports", "contradicts", "context"}:
                    errors.append(f"{ref_label}.relation: invalid relation")
                key = (
                    str(ref.get("project", "")),
                    str(ref.get("expression_id", "")),
                    str(ref.get("expression_fingerprint", "")),
                )
                if key in ref_keys:
                    errors.append(f"{ref_label}: duplicate source expression reference")
                ref_keys.add(key)
        validate_provenance(row.get("provenance"), f"{label}.provenance", errors)
    return unique_versions(rows, "claims", errors)


def validate_events(
    rows: Any, decisions: dict[str, dict[str, Any]], errors: list[str]
) -> set[tuple[str, int]]:
    if not isinstance(rows, list):
        errors.append("events: must be an array")
        return set()
    required = {
        "id", "version", "label", "core", "status", "story_link_disposition",
        "provenance",
    }
    optional = {"supersedes_version", "review_decision_id", "story_link_decision_id"}
    for index, row in enumerate(rows):
        label = f"events[{index}]"
        if not exact_keys(row, required, optional, label, errors):
            continue
        validate_id(row.get("id"), "event", f"{label}.id", errors)
        validate_versioned(row, label, errors)
        if not nonempty(row.get("label")):
            errors.append(f"{label}.label: must be non-empty")
        if row.get("status") not in RECORD_STATUSES:
            errors.append(f"{label}.status: invalid record status")
        validate_record_decision(row, decisions, label, errors, "event")
        disposition = row.get("story_link_disposition")
        if disposition not in {"pending", "linked", "not_applicable"}:
            errors.append(f"{label}.story_link_disposition: invalid disposition")
        if disposition == "not_applicable":
            decision_matches(
                row, decisions, "not_applicable", label, errors,
                field="story_link_decision_id",
                subject_kind="event", subject_id=row.get("id"),
                subject_version=row.get("version"),
            )
        elif "story_link_decision_id" in row:
            errors.append(
                f"{label}.story_link_decision_id: allowed only for not_applicable"
            )
        core = row.get("core")
        if exact_keys(
            core, {"actors", "action", "object", "place", "time"}, set(),
            f"{label}.core", errors,
        ):
            actors = core.get("actors")
            if (
                not isinstance(actors, list)
                or not actors
                or any(not nonempty(actor) for actor in actors)
                or len(actors) != len(set(actors))
            ):
                errors.append(f"{label}.core.actors: must contain unique non-empty strings")
            for key in ("action", "object", "place", "time"):
                if not nonempty(core.get(key)):
                    errors.append(f"{label}.core.{key}: must be non-empty")
        validate_provenance(row.get("provenance"), f"{label}.provenance", errors)
    return unique_versions(rows, "events", errors)


def validate_story_arcs(
    rows: Any, decisions: dict[str, dict[str, Any]], errors: list[str]
) -> set[tuple[str, int]]:
    if not isinstance(rows, list):
        errors.append("story_arcs: must be an array")
        return set()
    required = {"id", "version", "title", "description", "status", "provenance"}
    optional = {"supersedes_version", "review_decision_id"}
    for index, row in enumerate(rows):
        label = f"story_arcs[{index}]"
        if not exact_keys(row, required, optional, label, errors):
            continue
        validate_id(row.get("id"), "story_arc", f"{label}.id", errors)
        validate_versioned(row, label, errors)
        for key in ("title", "description"):
            if not nonempty(row.get(key)):
                errors.append(f"{label}.{key}: must be non-empty")
        if row.get("status") not in RECORD_STATUSES:
            errors.append(f"{label}.status: invalid record status")
        validate_record_decision(row, decisions, label, errors, "story_arc")
        validate_provenance(row.get("provenance"), f"{label}.provenance", errors)
    return unique_versions(rows, "story_arcs", errors)


def validate_memberships(
    rows: Any,
    kind: str,
    decisions: dict[str, dict[str, Any]],
    errors: list[str],
) -> set[tuple[str, int]]:
    field = "claim_event_memberships" if kind == "claim_event" else "event_story_arc_memberships"
    if not isinstance(rows, list):
        errors.append(f"{field}: must be an array")
        return set()
    if kind == "claim_event":
        required = {"id", "version", "claim", "event", "relation", "status", "provenance"}
        valid_edge = {"supports", "contradicts", "contextualizes", "mentions"}
    else:
        required = {"id", "version", "event", "story_arc", "role", "status", "provenance"}
        valid_edge = {"part_of", "background", "parallel"}
    optional = {"supersedes_version", "review_decision_id"}
    for index, row in enumerate(rows):
        label = f"{field}[{index}]"
        if not exact_keys(row, required, optional, label, errors):
            continue
        validate_id(row.get("id"), kind, f"{label}.id", errors)
        validate_versioned(row, label, errors)
        validate_endpoint(row.get("event"), "event", f"{label}.event", errors)
        if kind == "claim_event":
            validate_endpoint(row.get("claim"), "claim", f"{label}.claim", errors)
            if row.get("relation") not in valid_edge:
                errors.append(f"{label}.relation: invalid relation")
        else:
            validate_endpoint(row.get("story_arc"), "story_arc", f"{label}.story_arc", errors)
            if row.get("role") not in valid_edge:
                errors.append(f"{label}.role: invalid role")
        if row.get("status") not in MEMBERSHIP_STATUSES:
            errors.append(f"{label}.status: invalid membership status")
        membership_kind = (
            "claim_event_membership"
            if kind == "claim_event"
            else "event_story_arc_membership"
        )
        validate_membership_decision(row, decisions, label, errors, membership_kind)
        validate_provenance(row.get("provenance"), f"{label}.provenance", errors)
    return unique_versions(rows, field, errors)


def validate_batch(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["batch root must be an object"]
    errors: list[str] = []
    validate_value_limits(data, "batch", errors)
    required = {
        "schema_version", "batch_id", "idempotency_key", "created_at", "source_case",
        "review_decisions", "claims", "events", "story_arcs",
        "claim_event_memberships", "event_story_arc_memberships",
    }
    if not exact_keys(data, required, set(), "batch", errors):
        return errors
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: expected {SCHEMA_VERSION}")
    validate_id(data.get("batch_id"), "batch", "batch_id", errors)
    validate_sha(data.get("idempotency_key"), "idempotency_key", errors)
    if not valid_datetime(data.get("created_at")):
        errors.append("created_at: must be a timezone-aware ISO timestamp")
    decisions = validate_review_decisions(data.get("review_decisions"), errors)
    source_case = data.get("source_case")
    if exact_keys(
        source_case,
        {"project", "findings_contract_version", "review_decision_id"},
        {"case_contract_sha256"},
        "source_case",
        errors,
    ):
        if not nonempty(source_case.get("project")):
            errors.append("source_case.project: must be non-empty")
        if source_case.get("findings_contract_version") not in {"1.0", "1.1"}:
            errors.append("source_case.findings_contract_version: expected 1.0 or 1.1")
        decision_matches(
            source_case, decisions, "approved", "source_case", errors,
            subject_kind="source_case", subject_id=source_case.get("project"),
            subject_version=1,
        )
        if "case_contract_sha256" in source_case:
            validate_sha(
                source_case["case_contract_sha256"],
                "source_case.case_contract_sha256",
                errors,
            )
        if (
            source_case.get("findings_contract_version") == "1.1"
            and "case_contract_sha256" not in source_case
        ):
            errors.append("source_case: activated 1.1 batches require case_contract_sha256")
        if (
            source_case.get("findings_contract_version") == "1.0"
            and "case_contract_sha256" in source_case
        ):
            errors.append("source_case: legacy 1.0 batches cannot carry case_contract_sha256")

    for collection in (
        "review_decisions", "claims", "events", "story_arcs",
        "claim_event_memberships", "event_story_arc_memberships",
    ):
        validate_collection_limit(data.get(collection), collection, errors)

    claims = validate_claims(data.get("claims"), decisions, errors)
    events = validate_events(data.get("events"), decisions, errors)
    story_arcs = validate_story_arcs(data.get("story_arcs"), decisions, errors)
    claim_events = validate_memberships(
        data.get("claim_event_memberships"), "claim_event", decisions, errors
    )
    event_stories = validate_memberships(
        data.get("event_story_arc_memberships"), "event_story", decisions, errors
    )
    del claims, events, story_arcs, claim_events, event_stories
    return sorted(set(errors))


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    destination_id TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    source_project TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    review_manifest_json TEXT NOT NULL,
    approval_receipt_json TEXT NOT NULL,
    approval_evidence_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id TEXT PRIMARY KEY,
    reviewer_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    disposition TEXT NOT NULL,
    rationale TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id)
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_version INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (batch_id, kind, record_id, record_version)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    origin_project TEXT NOT NULL,
    origin_finding_id TEXT NOT NULL,
    origin_finding_fingerprint TEXT NOT NULL,
    proposition TEXT NOT NULL,
    status TEXT NOT NULL,
    event_link_disposition TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    event_link_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    UNIQUE (id, version, origin_project, origin_finding_id, origin_finding_fingerprint)
);

CREATE TABLE IF NOT EXISTS source_expression_refs (
    claim_id TEXT NOT NULL,
    claim_version INTEGER NOT NULL,
    project TEXT NOT NULL,
    expression_id TEXT NOT NULL,
    expression_fingerprint TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (claim_id, claim_version, project, expression_id, expression_fingerprint),
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    label TEXT NOT NULL,
    actors_json TEXT NOT NULL,
    action TEXT NOT NULL,
    object TEXT NOT NULL,
    place TEXT NOT NULL,
    event_time TEXT NOT NULL,
    status TEXT NOT NULL,
    story_link_disposition TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    story_link_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS story_arcs (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS claim_event_memberships (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    claim_id TEXT NOT NULL,
    claim_version INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    relation TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version),
    FOREIGN KEY (event_id, event_version) REFERENCES events(id, version)
);

CREATE TABLE IF NOT EXISTS event_story_arc_memberships (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    event_id TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    story_arc_id TEXT NOT NULL,
    story_arc_version INTEGER NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    FOREIGN KEY (event_id, event_version) REFERENCES events(id, version),
    FOREIGN KEY (story_arc_id, story_arc_version) REFERENCES story_arcs(id, version)
);

CREATE INDEX IF NOT EXISTS claim_event_by_claim
    ON claim_event_memberships(claim_id, claim_version, status);
CREATE INDEX IF NOT EXISTS claim_event_by_event
    ON claim_event_memberships(event_id, event_version, status);
CREATE INDEX IF NOT EXISTS event_story_by_event
    ON event_story_arc_memberships(event_id, event_version, status);
CREATE INDEX IF NOT EXISTS event_story_by_arc
    ON event_story_arc_memberships(story_arc_id, story_arc_version, status);
CREATE INDEX IF NOT EXISTS claims_by_origin
    ON claims(origin_project, origin_finding_id, origin_finding_fingerprint, id);
CREATE INDEX IF NOT EXISTS source_expressions_by_expression
    ON source_expression_refs(project, expression_id, expression_fingerprint,
                              claim_id, claim_version);

CREATE TABLE IF NOT EXISTS case_policy_receipts (
    receipt_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    signature_evidence_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (case_id, destination_id, policy_revision)
);

CREATE TABLE IF NOT EXISTS projection_heads (
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    current_generation INTEGER NOT NULL,
    current_job_id TEXT NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    PRIMARY KEY (case_id, destination_id)
);

CREATE TABLE IF NOT EXISTS projection_jobs (
    job_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('graph_commit', 'case_policy')),
    source_ref TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'failed', 'completed', 'superseded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    final_receipt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (case_id, destination_id, generation),
    UNIQUE (source_kind, source_ref)
);

CREATE TABLE IF NOT EXISTS projection_final_receipts (
    receipt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES projection_jobs(job_id),
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    workspace_receipt_ref TEXT NOT NULL,
    workspace_receipt_sha256 TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_running_projection_per_destination
    ON projection_jobs(destination_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS projection_jobs_ready
    ON projection_jobs(destination_id, status, generation);
CREATE INDEX IF NOT EXISTS claims_by_proposition
    ON claims(proposition, id, version);
"""

MIGRATION_V1_TO_V2 = tuple(
    statement.strip()
    for statement in (
        """
        CREATE TABLE case_policy_receipts (
            receipt_id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
            destination_id TEXT NOT NULL, policy_revision INTEGER NOT NULL,
            status TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL, signature_evidence_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE (case_id, destination_id, policy_revision)
        )
        """,
        """
        CREATE TABLE projection_heads (
            case_id TEXT NOT NULL, destination_id TEXT NOT NULL,
            current_generation INTEGER NOT NULL, current_job_id TEXT NOT NULL,
            desired_projection_set_sha256 TEXT NOT NULL,
            PRIMARY KEY (case_id, destination_id)
        )
        """,
        """
        CREATE TABLE projection_jobs (
            job_id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
            destination_id TEXT NOT NULL, generation INTEGER NOT NULL,
            desired_projection_set_sha256 TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('graph_commit', 'case_policy')),
            source_ref TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'failed', 'completed', 'superseded')),
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            final_receipt_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (case_id, destination_id, generation),
            UNIQUE (source_kind, source_ref)
        )
        """,
        """
        CREATE TABLE projection_final_receipts (
            receipt_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE REFERENCES projection_jobs(job_id),
            case_id TEXT NOT NULL, destination_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            desired_projection_set_sha256 TEXT NOT NULL,
            workspace_receipt_ref TEXT NOT NULL,
            workspace_receipt_sha256 TEXT NOT NULL,
            completed_at TEXT NOT NULL, binding_sha256 TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX one_running_projection_per_destination ON projection_jobs(destination_id) WHERE status = 'running'",
        "CREATE INDEX projection_jobs_ready ON projection_jobs(destination_id, status, generation)",
        "CREATE INDEX claims_by_proposition ON claims(proposition, id, version)",
    )
)


def _reject_symlink_path(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ContractError(f"symlink path is not allowed: {current}")


def resolve_beneath(base: Path, candidate: Path, label: str) -> Path:
    base = resolve_root(base, f"{label} root")
    unresolved = candidate.expanduser()
    if any(part.startswith("-") for part in unresolved.parts if part not in {"/", ""}):
        raise ContractError(f"{label} contains a leading-dash path component")
    if not unresolved.is_absolute():
        unresolved = base / unresolved
    _reject_symlink_path(unresolved)
    resolved = unresolved.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ContractError(f"{label} must resolve beneath {base}") from exc
    return resolved


def resolve_root(path: Path, label: str) -> Path:
    unresolved = path.expanduser()
    _reject_symlink_path(unresolved.absolute())
    resolved = unresolved.resolve()
    if not resolved.is_dir():
        raise ContractError(f"{label} is not a directory: {resolved}")
    return resolved


def _prepare_private_database(path: Path) -> bool:
    _reject_symlink_path(path)
    if path.exists():
        metadata = path.stat()
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o077:
            raise ContractError(
                f"database permissions must be owner-only (0600), got {mode:04o}"
            )
        if not path.is_file():
            raise ContractError(f"database is not a regular file: {path}")
        if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise ContractError("database must be owned by the current user and not hard-linked")
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Path.mkdir(..., exist_ok=True, mode=0o700) does not chmod a pre-existing
    # directory, and mkdir -p uses umask (typically 0755). OpenKnowledge and
    # this port both refuse group/world-readable database dirs.
    parent_meta = path.parent.stat()
    if parent_meta.st_uid != os.getuid():
        raise ContractError(
            f"database directory must be owned by the current user: {path.parent}"
        )
    os.chmod(path.parent, 0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ContractError(
            f"database directory permissions must be owner-only: {path.parent}"
        )
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    return True


def _verify_database_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != DATABASE_SCHEMA_VERSION:
        raise ContractError(
            f"unsupported database schema version {version}; "
            f"expected {DATABASE_SCHEMA_VERSION}"
        )
    row = connection.execute(
        "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if row is None or row[0] != DATABASE_SCHEMA_VERSION:
        raise ContractError("database schema metadata is missing or incompatible")
    expected_connection = sqlite3.connect(":memory:")
    try:
        expected_connection.executescript(SCHEMA_SQL)
        expected = expected_connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        expected_connection.close()
    actual = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    def normalized_schema_row(item: sqlite3.Row | tuple[Any, ...]) -> tuple[Any, ...]:
        kind, name, table, sql = tuple(item)
        normalized_sql = None
        if sql is not None:
            normalized_sql = re.sub(r"\s+", "", sql.lower()).replace(
                "ifnotexists", ""
            )
        return kind, name, table, normalized_sql

    if [normalized_schema_row(item) for item in actual] != [
        normalized_schema_row(item) for item in expected
    ]:
        raise ContractError("database schema objects do not match the reference contract")


def _migrate_database(connection: sqlite3.Connection) -> None:
    """Apply the sole supported additive migration without rewriting graph rows."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == DATABASE_SCHEMA_VERSION:
        return
    if version != 1:
        raise ContractError(
            f"unsupported database schema version {version}; "
            f"expected 1 or {DATABASE_SCHEMA_VERSION}"
        )
    metadata = connection.execute(
        "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata is None or metadata[0] != 1:
        raise ContractError("database schema metadata is missing or incompatible")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in MIGRATION_V1_TO_V2:
            connection.execute(statement)
        destination = connection.execute(
            "SELECT destination_id FROM schema_metadata WHERE singleton = 1"
        ).fetchone()[0]
        if destination is not None:
            for batch in connection.execute(
                "SELECT batch_id, source_project, payload_sha256 FROM batches "
                "ORDER BY committed_at, batch_id"
            ).fetchall():
                enqueue_projection_job(
                    connection, _case_id_for_project(batch["source_project"]),
                    destination, "graph_commit", batch["batch_id"],
                    batch["payload_sha256"],
                )
        connection.execute(
            "UPDATE schema_metadata SET schema_version = ? WHERE singleton = 1",
            (DATABASE_SCHEMA_VERSION,),
        )
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def connect_database(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve(strict=False)
    created = False
    try:
        created = _prepare_private_database(path)
        connection = sqlite3.connect(path, timeout=5.0)
    except (OSError, sqlite3.Error) as exc:
        raise ContractError(f"cannot open database {path}: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA busy_timeout = 5000")
        if created:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_metadata(singleton, schema_version) VALUES (1, ?)",
                (DATABASE_SCHEMA_VERSION,),
            )
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            connection.commit()
        else:
            _migrate_database(connection)
            _verify_database_schema(connection)
        return connection
    except Exception:
        connection.close()
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def initialize_database(database: Path, destination_id: str) -> dict[str, Any]:
    """Provision a reference database identity before any reviewed commit."""
    if DESTINATION_ID_RE.fullmatch(destination_id) is None:
        raise ContractError("destination-id has an invalid format")
    connection = connect_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT destination_id FROM schema_metadata WHERE singleton = 1"
        ).fetchone()
        existing = row["destination_id"] if row else None
        if existing not in {None, destination_id}:
            raise ContractError("database already has another destination identity")
        connection.execute(
            "UPDATE schema_metadata SET destination_id = ? WHERE singleton = 1",
            (destination_id,),
        )
        connection.commit()
        return {
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "status": "initialized",
            "destination_id": destination_id,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _projection_intent_hash(
    previous_hash: str | None, case_id: str, destination_id: str, generation: int,
    source_kind: str, source_ref: str, source_sha256: str,
) -> str:
    return payload_sha256(
        {
            "previous_desired_projection_set_sha256": previous_hash,
            "case_id": case_id,
            "destination_id": destination_id,
            "generation": generation,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
        }
    )


def _projection_job_id(
    case_id: str, destination_id: str, generation: int, desired_hash: str
) -> str:
    return "projection-job:" + payload_sha256(
        {
            "case_id": case_id,
            "destination_id": destination_id,
            "generation": generation,
            "desired_projection_set_sha256": desired_hash,
        }
    )


def enqueue_projection_job(
    connection: sqlite3.Connection, case_id: str, destination_id: str,
    source_kind: str, source_ref: str, source_sha256: str,
) -> sqlite3.Row:
    if source_kind not in {"graph_commit", "case_policy"}:
        raise ContractError("projection job source kind is invalid")
    existing = connection.execute(
        "SELECT * FROM projection_jobs WHERE source_kind = ? AND source_ref = ?",
        (source_kind, source_ref),
    ).fetchone()
    if existing is not None:
        if (
            existing["case_id"] != case_id
            or existing["destination_id"] != destination_id
            or existing["source_sha256"] != source_sha256
        ):
            raise ContractError("projection source already belongs to another intent")
        return existing
    head = connection.execute(
        "SELECT * FROM projection_heads WHERE case_id = ? AND destination_id = ?",
        (case_id, destination_id),
    ).fetchone()
    generation = 1 if head is None else int(head["current_generation"]) + 1
    previous_hash = None if head is None else head["desired_projection_set_sha256"]
    desired_hash = _projection_intent_hash(
        previous_hash, case_id, destination_id, generation,
        source_kind, source_ref, source_sha256,
    )
    job_id = _projection_job_id(case_id, destination_id, generation, desired_hash)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    connection.execute(
        """
        UPDATE projection_jobs SET status = 'superseded', updated_at = ?
         WHERE case_id = ? AND destination_id = ?
           AND status IN ('pending', 'running', 'failed')
        """,
        (now, case_id, destination_id),
    )
    connection.execute(
        """
        INSERT INTO projection_jobs
            (job_id, case_id, destination_id, generation,
             desired_projection_set_sha256, source_kind, source_ref,
             source_sha256, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            job_id, case_id, destination_id, generation, desired_hash,
            source_kind, source_ref, source_sha256, now, now,
        ),
    )
    connection.execute(
        """
        INSERT INTO projection_heads
            (case_id, destination_id, current_generation, current_job_id,
             desired_projection_set_sha256)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(case_id, destination_id) DO UPDATE SET
            current_generation = excluded.current_generation,
            current_job_id = excluded.current_job_id,
            desired_projection_set_sha256 = excluded.desired_projection_set_sha256
        """,
        (case_id, destination_id, generation, job_id, desired_hash),
    )
    return connection.execute(
        "SELECT * FROM projection_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()


def projection_job_response(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "case_id": row["case_id"],
        "destination_id": row["destination_id"],
        "generation": row["generation"],
        "desired_projection_set_sha256": row["desired_projection_set_sha256"],
        "source_kind": row["source_kind"],
        "source_ref": row["source_ref"],
        "status": row["status"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "final_receipt_id": row["final_receipt_id"],
    }


def commit_case_policy(
    database: Path, receipt: dict[str, Any], destination_id: str,
    signature_evidence: dict[str, str],
) -> dict[str, Any]:
    validate_case_policy_receipt(receipt, destination_id)
    digest = payload_sha256(receipt)
    connection = connect_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        metadata = connection.execute(
            "SELECT destination_id FROM schema_metadata WHERE singleton = 1"
        ).fetchone()
        if metadata is None or metadata["destination_id"] != destination_id:
            raise ContractError("case-policy destination does not match database")
        existing = connection.execute(
            "SELECT * FROM case_policy_receipts WHERE receipt_id = ?",
            (receipt["receipt_id"],),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != digest or existing["payload_json"] != json_text(receipt):
                raise ContractError("case-policy receipt_id was reused with another payload")
            job = connection.execute(
                "SELECT * FROM projection_jobs WHERE source_kind = 'case_policy' AND source_ref = ?",
                (receipt["receipt_id"],),
            ).fetchone()
            if job is None:
                raise ContractError("committed case-policy is missing projection intent")
            connection.commit()
            return {
                "response_schema_version": RESPONSE_SCHEMA_VERSION,
                "status": "committed", "replayed": True,
                "policy_receipt_id": receipt["receipt_id"],
                "projection_job": projection_job_response(job),
                "assurance": "local_conformance_only",
            }
        previous = connection.execute(
            """
            SELECT * FROM case_policy_receipts
             WHERE case_id = ? AND destination_id = ?
             ORDER BY policy_revision DESC LIMIT 1
            """,
            (receipt["case_id"], destination_id),
        ).fetchone()
        expected_revision = 1 if previous is None else previous["policy_revision"] + 1
        if receipt["policy_revision"] != expected_revision:
            raise ContractError(
                f"case-policy revision must be {expected_revision} for this destination"
            )
        if previous is not None:
            old = json.loads(previous["payload_json"])
            if receipt["revocation_version"] < old["revocation_version"]:
                raise ContractError("case-policy revocation_version cannot decrease")
            if receipt["status"] == "revoked" and receipt["revocation_version"] <= old["revocation_version"]:
                raise ContractError("case-policy revocation must advance revocation_version")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            """
            INSERT INTO case_policy_receipts
                (receipt_id, case_id, destination_id, policy_revision, status,
                 payload_sha256, payload_json, signature_evidence_json, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["receipt_id"], receipt["case_id"], destination_id,
                receipt["policy_revision"], receipt["status"], digest,
                json_text(receipt), json_text(signature_evidence), now,
            ),
        )
        job = enqueue_projection_job(
            connection, receipt["case_id"], destination_id, "case_policy",
            receipt["receipt_id"], digest,
        )
        connection.commit()
        return {
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "status": "committed", "replayed": False,
            "policy_receipt_id": receipt["receipt_id"],
            "projection_job": projection_job_response(job),
            "assurance": "local_conformance_only",
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def claim_projection_job(connection: sqlite3.Connection) -> dict[str, Any] | None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        running = connection.execute(
            "SELECT job_id FROM projection_jobs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        if running is not None:
            raise ContractError("a projection job is already running")
        row = connection.execute(
            """
            SELECT job.* FROM projection_jobs AS job
            JOIN projection_heads AS head
              ON head.case_id = job.case_id
             AND head.destination_id = job.destination_id
             AND head.current_job_id = job.job_id
             WHERE job.status IN ('pending', 'failed')
             ORDER BY job.destination_id, job.generation, job.job_id LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE projection_jobs SET status = 'running', attempts = attempts + 1, "
            "last_error = NULL, updated_at = ? WHERE job_id = ?",
            (now, row["job_id"]),
        )
        claimed = connection.execute(
            "SELECT * FROM projection_jobs WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        connection.commit()
        return projection_job_response(claimed)
    except Exception:
        connection.rollback()
        raise


def claim_projection_job_exact(
    connection: sqlite3.Connection, job_id: str
) -> dict[str, Any]:
    """Atomically claim one named current job without consuming another queue item."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _require_current_job(connection, job_id, {"pending", "failed"})
        running = connection.execute(
            "SELECT job_id FROM projection_jobs WHERE destination_id=? AND status='running' LIMIT 1",
            (row["destination_id"],),
        ).fetchone()
        if running is not None:
            raise ContractError("a projection job is already running for this destination")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE projection_jobs SET status='running', attempts=attempts+1, "
            "last_error=NULL, updated_at=? WHERE job_id=?", (now, job_id),
        )
        claimed = connection.execute(
            "SELECT * FROM projection_jobs WHERE job_id=?", (job_id,),
        ).fetchone()
        connection.commit()
        return projection_job_response(claimed)
    except Exception:
        connection.rollback()
        raise


def _require_current_job(
    connection: sqlite3.Connection, job_id: str, allowed_statuses: set[str]
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM projection_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise ContractError("projection job not found")
    head = connection.execute(
        "SELECT current_job_id FROM projection_heads WHERE case_id = ? AND destination_id = ?",
        (row["case_id"], row["destination_id"]),
    ).fetchone()
    if head is None or head["current_job_id"] != job_id:
        if row["status"] not in {"completed", "superseded"}:
            connection.execute(
                "UPDATE projection_jobs SET status = 'superseded', updated_at = ? WHERE job_id = ?",
                (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), job_id),
            )
        raise ContractError("projection job lost current-head status")
    if row["status"] not in allowed_statuses:
        raise ContractError(f"projection job cannot transition from {row['status']}")
    return row


def fail_projection_job(
    connection: sqlite3.Connection, job_id: str, error: str
) -> dict[str, Any]:
    if not nonempty(error) or len(error) > 4096 or "\x00" in error:
        raise ContractError("projection failure requires a bounded error")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _require_current_job(connection, job_id, {"running"})
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE projection_jobs SET status = 'failed', last_error = ?, updated_at = ? WHERE job_id = ?",
            (error, now, job_id),
        )
        result = connection.execute(
            "SELECT * FROM projection_jobs WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        connection.commit()
        return projection_job_response(result)
    except Exception:
        connection.rollback()
        raise


def retry_projection_job(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = _require_current_job(connection, job_id, {"failed"})
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            "UPDATE projection_jobs SET status = 'pending', updated_at = ? WHERE job_id = ?",
            (now, job_id),
        )
        result = connection.execute(
            "SELECT * FROM projection_jobs WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        connection.commit()
        return projection_job_response(result)
    except Exception:
        connection.rollback()
        raise


def complete_projection_job(
    connection: sqlite3.Connection, job_id: str, desired_hash: str,
    workspace_receipt_ref: str, workspace_receipt_sha256: str,
    *, in_transaction: bool = False,
) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(desired_hash) or not SHA256_RE.fullmatch(workspace_receipt_sha256):
        raise ContractError("completion hashes must be lowercase SHA-256")
    if RECEIPT_REF_RE.fullmatch(workspace_receipt_ref) is None:
        raise ContractError("workspace receipt reference is invalid")
    if not in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        row = _require_current_job(connection, job_id, {"running"})
        if row["desired_projection_set_sha256"] != desired_hash:
            raise ContractError("workspace receipt does not match the desired projection set")
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt_body = {
            "job_id": job_id, "case_id": row["case_id"],
            "destination_id": row["destination_id"], "generation": row["generation"],
            "desired_projection_set_sha256": desired_hash,
            "workspace_receipt_ref": workspace_receipt_ref,
            "workspace_receipt_sha256": workspace_receipt_sha256,
            "completed_at": completed_at,
        }
        binding = payload_sha256(receipt_body)
        receipt_id = "projection-receipt:" + binding
        connection.execute(
            """
            INSERT INTO projection_final_receipts
                (receipt_id, job_id, case_id, destination_id, generation,
                 desired_projection_set_sha256, workspace_receipt_ref,
                 workspace_receipt_sha256, completed_at, binding_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id, job_id, row["case_id"], row["destination_id"],
                row["generation"], desired_hash, workspace_receipt_ref,
                workspace_receipt_sha256, completed_at, binding,
            ),
        )
        connection.execute(
            "UPDATE projection_jobs SET status = 'completed', final_receipt_id = ?, "
            "updated_at = ? WHERE job_id = ?",
            (receipt_id, completed_at, job_id),
        )
        if not in_transaction:
            connection.commit()
        return {
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "status": "completed", "job_id": job_id,
            "final_receipt_id": receipt_id,
        }
    except Exception:
        if not in_transaction:
            connection.rollback()
        raise


def immutable_insert(
    connection: sqlite3.Connection,
    table: str,
    key_columns: tuple[str, ...],
    columns: tuple[str, ...],
    values: tuple[Any, ...],
    payload: dict[str, Any],
) -> None:
    key_values = values[: len(key_columns)]
    where = " AND ".join(f"{column} = ?" for column in key_columns)
    existing = connection.execute(
        f"SELECT payload_json FROM {table} WHERE {where}", key_values
    ).fetchone()
    payload_value = json_text(payload)
    if existing is not None:
        if existing["payload_json"] != payload_value:
            joined = "/".join(str(item) for item in key_values)
            raise ContractError(f"immutable {table} record conflicts at {joined}")
        return
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def require_previous_version(
    connection: sqlite3.Connection, table: str, row: dict[str, Any]
) -> None:
    if row["version"] == 1:
        return
    previous = connection.execute(
        f"SELECT payload_json FROM {table} WHERE id = ? AND version = ?",
        (row["id"], row["supersedes_version"]),
    ).fetchone()
    if previous is None:
        raise ContractError(
            f"{table} {row['id']} version {row['version']} has no version "
            f"{row['supersedes_version']} to supersede"
        )
    previous_payload = json.loads(previous["payload_json"])
    if table == "claims":
        old_identity = previous_payload.get("origin")
        new_identity = row.get("origin")
    elif table == "events":
        old_identity = previous_payload.get("core")
        new_identity = row.get("core")
    elif table == "claim_event_memberships":
        old_identity = {
            "claim_id": previous_payload.get("claim", {}).get("id"),
            "event_id": previous_payload.get("event", {}).get("id"),
            "relation": previous_payload.get("relation"),
        }
        new_identity = {
            "claim_id": row.get("claim", {}).get("id"),
            "event_id": row.get("event", {}).get("id"),
            "relation": row.get("relation"),
        }
    elif table == "event_story_arc_memberships":
        old_identity = {
            "event_id": previous_payload.get("event", {}).get("id"),
            "story_arc_id": previous_payload.get("story_arc", {}).get("id"),
            "role": previous_payload.get("role"),
        }
        new_identity = {
            "event_id": row.get("event", {}).get("id"),
            "story_arc_id": row.get("story_arc", {}).get("id"),
            "role": row.get("role"),
        }
    else:
        return
    if old_identity != new_identity:
        raise ContractError(
            f"{table} {row['id']} version {row['version']} changes immutable identity"
        )


def insert_batch_records(
    connection: sqlite3.Connection, batch: dict[str, Any]
) -> None:
    batch_id = batch["batch_id"]
    item_collections = {
        "review_decision": batch["review_decisions"],
        "claim": batch["claims"],
        "event": batch["events"],
        "story_arc": batch["story_arcs"],
        "claim_event_membership": batch["claim_event_memberships"],
        "event_story_arc_membership": batch["event_story_arc_memberships"],
    }
    for kind, rows in item_collections.items():
        for row in rows:
            connection.execute(
                """
                INSERT INTO batch_items
                    (batch_id, kind, record_id, record_version, payload_sha256)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_id, kind, row["id"], row.get("version", 1),
                    payload_sha256(row),
                ),
            )
    for row in batch["review_decisions"]:
        immutable_insert(
            connection,
            "review_decisions",
            ("id",),
            (
                "id", "reviewer_id", "decided_at", "disposition", "rationale",
                "payload_json", "batch_id",
            ),
            (
                row["id"], row["reviewer_id"], row["decided_at"],
                row["disposition"], row["rationale"], json_text(row), batch_id,
            ),
            row,
        )

    for row in batch["claims"]:
        require_previous_version(connection, "claims", row)
        origin = row["origin"]
        conflicting_origin = connection.execute(
            """
            SELECT DISTINCT id FROM claims
             WHERE origin_project = ? AND origin_finding_id = ?
               AND origin_finding_fingerprint = ? AND id != ?
            """,
            (
                origin["project"], origin["finding_id"],
                origin["finding_fingerprint"], row["id"],
            ),
        ).fetchone()
        if conflicting_origin is not None:
            raise ContractError(
                "claim origin already maps to canonical claim "
                f"{conflicting_origin['id']}"
            )
        immutable_insert(
            connection,
            "claims",
            ("id", "version"),
            (
                "id", "version", "supersedes_version", "origin_project",
                "origin_finding_id", "origin_finding_fingerprint", "proposition",
                "status", "event_link_disposition", "review_decision_id",
                "event_link_decision_id", "payload_json", "batch_id",
            ),
            (
                row["id"], row["version"], row.get("supersedes_version"),
                origin["project"], origin["finding_id"], origin["finding_fingerprint"],
                row["proposition"], row["status"], row["event_link_disposition"],
                row.get("review_decision_id"), row.get("event_link_decision_id"),
                json_text(row), batch_id,
            ),
            row,
        )
        for ref in row["source_expression_refs"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_expression_refs
                    (claim_id, claim_version, project, expression_id,
                     expression_fingerprint, relation)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["version"], ref["project"], ref["expression_id"],
                    ref["expression_fingerprint"], ref["relation"],
                ),
            )

    for row in batch["events"]:
        require_previous_version(connection, "events", row)
        core = row["core"]
        immutable_insert(
            connection,
            "events",
            ("id", "version"),
            (
                "id", "version", "supersedes_version", "label", "actors_json",
                "action", "object", "place", "event_time", "status",
                "story_link_disposition", "review_decision_id",
                "story_link_decision_id", "payload_json", "batch_id",
            ),
            (
                row["id"], row["version"], row.get("supersedes_version"),
                row["label"], json_text(core["actors"]), core["action"], core["object"],
                core["place"], core["time"], row["status"],
                row["story_link_disposition"], row.get("review_decision_id"),
                row.get("story_link_decision_id"), json_text(row), batch_id,
            ),
            row,
        )

    for row in batch["story_arcs"]:
        require_previous_version(connection, "story_arcs", row)
        immutable_insert(
            connection,
            "story_arcs",
            ("id", "version"),
            (
                "id", "version", "supersedes_version", "title", "description",
                "status", "review_decision_id", "payload_json", "batch_id",
            ),
            (
                row["id"], row["version"], row.get("supersedes_version"),
                row["title"], row["description"], row["status"],
                row.get("review_decision_id"), json_text(row), batch_id,
            ),
            row,
        )

    for row in batch["claim_event_memberships"]:
        require_previous_version(connection, "claim_event_memberships", row)
        immutable_insert(
            connection,
            "claim_event_memberships",
            ("id", "version"),
            (
                "id", "version", "supersedes_version", "claim_id", "claim_version",
                "event_id", "event_version", "relation", "status",
                "review_decision_id", "payload_json", "batch_id",
            ),
            (
                row["id"], row["version"], row.get("supersedes_version"),
                row["claim"]["id"], row["claim"]["version"], row["event"]["id"],
                row["event"]["version"], row["relation"], row["status"],
                row.get("review_decision_id"), json_text(row), batch_id,
            ),
            row,
        )

    for row in batch["event_story_arc_memberships"]:
        require_previous_version(connection, "event_story_arc_memberships", row)
        immutable_insert(
            connection,
            "event_story_arc_memberships",
            ("id", "version"),
            (
                "id", "version", "supersedes_version", "event_id", "event_version",
                "story_arc_id", "story_arc_version", "role", "status",
                "review_decision_id", "payload_json", "batch_id",
            ),
            (
                row["id"], row["version"], row.get("supersedes_version"),
                row["event"]["id"], row["event"]["version"],
                row["story_arc"]["id"], row["story_arc"]["version"], row["role"],
                row["status"], row.get("review_decision_id"), json_text(row), batch_id,
            ),
            row,
        )


def latest_rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(
        f"""
        SELECT item.* FROM {table} AS item
        JOIN (SELECT id, MAX(version) AS version FROM {table} GROUP BY id) AS latest
          ON latest.id = item.id AND latest.version = item.version
        ORDER BY item.id
        """
    ).fetchall()


def enforce_coverage_invariants(connection: sqlite3.Connection) -> None:
    canonical_claims = {
        (row["id"], row["version"]): row
        for row in projected_rows(connection, "claims")
    }
    canonical_events = {
        (row["id"], row["version"]): row
        for row in projected_rows(connection, "events")
    }
    canonical_stories = {
        (row["id"], row["version"]): row
        for row in projected_rows(connection, "story_arcs")
    }
    canonical_claim_events = projected_relation_rows(
        connection, "claim_event_memberships", "1 = 1", (), False
    )
    canonical_event_stories = projected_relation_rows(
        connection, "event_story_arc_memberships", "1 = 1", (), False
    )
    claim_event_counts: dict[tuple[str, int], int] = {}
    for membership in canonical_claim_events:
        claim = canonical_claims.get(
            (membership["claim_id"], membership["claim_version"])
        )
        event = canonical_events.get(
            (membership["event_id"], membership["event_version"])
        )
        if (
            claim is None or event is None
        ):
            raise ContractError(
                f"approved claim-event membership {membership['id']} must reference approved endpoints"
            )
        claim_event_counts[(membership["claim_id"], membership["claim_version"])] = (
            claim_event_counts.get(
                (membership["claim_id"], membership["claim_version"]), 0
            ) + 1
        )
    event_story_counts: dict[tuple[str, int], int] = {}
    for membership in canonical_event_stories:
        event = canonical_events.get(
            (membership["event_id"], membership["event_version"])
        )
        story = canonical_stories.get(
            (membership["story_arc_id"], membership["story_arc_version"])
        )
        if (
            event is None or story is None
        ):
            raise ContractError(
                f"approved event-story membership {membership['id']} must reference approved endpoints"
            )
        event_story_counts[(membership["event_id"], membership["event_version"])] = (
            event_story_counts.get(
                (membership["event_id"], membership["event_version"]), 0
            ) + 1
        )

    for claim in canonical_claims.values():
        approved = claim_event_counts.get((claim["id"], claim["version"]), 0)
        disposition = claim["event_link_disposition"]
        if disposition == "linked" and approved == 0:
            raise ContractError(
                f"approved claim {claim['id']} declares linked but has no approved event membership"
            )
        if disposition == "not_applicable" and approved:
            raise ContractError(
                f"approved claim {claim['id']} is not_applicable but has approved event memberships"
            )
        if disposition == "pending" and approved:
            raise ContractError(
                f"approved claim {claim['id']} is pending but has approved event memberships"
            )

    for event in canonical_events.values():
        approved = event_story_counts.get((event["id"], event["version"]), 0)
        disposition = event["story_link_disposition"]
        if disposition == "linked" and approved == 0:
            raise ContractError(
                f"approved event {event['id']} declares linked but has no approved story membership"
            )
        if disposition == "not_applicable" and approved:
            raise ContractError(
                f"approved event {event['id']} is not_applicable but has approved story memberships"
            )
        if disposition == "pending" and approved:
            raise ContractError(
                f"approved event {event['id']} is pending but has approved story memberships"
            )


def commit_batch(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Reject unverified programmatic commits; the CLI verifies signed review."""
    del args, kwargs
    raise ContractError(
        "direct commit API is disabled; use the signed CLI commit workflow"
    )


def _commit_verified_batch(
    database: Path,
    batch: dict[str, Any],
    expected_sha256: str,
    manifest: dict[str, Any] | None = None,
    approval_receipt: dict[str, Any] | None = None,
    approval_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    errors = validate_batch(batch)
    if errors:
        raise ContractError("invalid batch:\n- " + "\n- ".join(errors))
    digest = payload_sha256(batch)
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ContractError("expected payload hash must be lowercase SHA-256")
    if digest != expected_sha256:
        raise ContractError(
            f"staged payload hash mismatch: expected {expected_sha256}, got {digest}"
        )
    if manifest is None or approval_receipt is None or approval_evidence is None:
        raise ContractError("commit requires a signed external approval receipt")
    if manifest.get("payload_sha256") != digest:
        raise ContractError("review manifest does not match batch payload")
    expected_manifest_hash = manifest.get("review_manifest_sha256")
    manifest_body = dict(manifest)
    manifest_body.pop("review_manifest_sha256", None)
    if expected_manifest_hash != payload_sha256(manifest_body):
        raise ContractError("review manifest hash is invalid")
    validate_approval_receipt(approval_receipt, manifest)
    unauthenticated = [
        decision["id"] for decision in batch["review_decisions"]
        if decision["reviewer_id"] != approval_receipt["reviewer_id"]
    ]
    if unauthenticated:
        raise ContractError(
            "local single-operator approval cannot authenticate decisions from "
            f"other reviewers: {unauthenticated}"
        )
    connection = connect_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        metadata = connection.execute(
            "SELECT destination_id FROM schema_metadata WHERE singleton = 1"
        ).fetchone()
        stored_destination = metadata["destination_id"] if metadata else None
        if stored_destination is None:
            raise ContractError("database destination identity was not provisioned")
        if stored_destination != manifest["destination_id"]:
            raise ContractError("destination identity does not match database")
        existing = connection.execute(
            "SELECT batch_id, payload_sha256, payload_json FROM batches "
            "WHERE idempotency_key = ?",
            (batch["idempotency_key"],),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != digest:
                raise ContractError(
                    "idempotency key already committed with a different payload"
                )
            if existing["payload_json"] != json_text(batch):
                raise ContractError("committed batch receipt does not match stored payload")
            job = connection.execute(
                "SELECT * FROM projection_jobs WHERE source_kind = 'graph_commit' AND source_ref = ?",
                (existing["batch_id"],),
            ).fetchone()
            if job is None:
                raise ContractError("committed batch is missing projection intent")
            connection.commit()
            return {
                "response_schema_version": RESPONSE_SCHEMA_VERSION,
                "status": "committed",
                "replayed": True,
                "batch_id": existing["batch_id"],
                "payload_sha256": digest,
                "projection_job": projection_job_response(job),
                "assurance": "local_conformance_only",
            }
        existing_batch = connection.execute(
            "SELECT payload_sha256 FROM batches WHERE batch_id = ?", (batch["batch_id"],)
        ).fetchone()
        if existing_batch is not None:
            raise ContractError("batch_id already exists with another idempotency key")
        connection.execute(
            """
            INSERT INTO batches
                (batch_id, schema_version, idempotency_key, payload_sha256,
                 source_project, created_at, committed_at, payload_json,
                 review_manifest_json, approval_receipt_json, approval_evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch["batch_id"], batch["schema_version"], batch["idempotency_key"],
                digest, batch["source_case"]["project"], batch["created_at"],
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                json_text(batch), json_text(manifest), json_text(approval_receipt),
                json_text(approval_evidence),
            ),
        )
        insert_batch_records(connection, batch)
        enforce_coverage_invariants(connection)
        job = enqueue_projection_job(
            connection, _case_id_for_project(batch["source_case"]["project"]),
            stored_destination, "graph_commit", batch["batch_id"], digest,
        )
        connection.commit()
        return {
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "status": "committed",
            "replayed": False,
            "batch_id": batch["batch_id"],
            "payload_sha256": digest,
            "projection_job": projection_job_response(job),
            "assurance": "local_conformance_only",
        }
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise ContractError(f"database rejected batch: {exc}") from exc
    except sqlite3.OperationalError as exc:
        connection.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise ContractError(
                "retryable database lock contention after 5000ms"
            ) from exc
        raise ContractError(f"database operation failed: {exc}") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def decode_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return json.loads(row["payload_json"]) if row is not None else None


def latest_one(
    connection: sqlite3.Connection, table: str, record_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT * FROM {table} WHERE id = ? ORDER BY version DESC LIMIT 1",
        (record_id,),
    ).fetchone()


def projected_one(
    connection: sqlite3.Connection,
    table: str,
    record_id: str,
    include_candidates: bool = False,
) -> sqlite3.Row | None:
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE id = ? ORDER BY version DESC", (record_id,)
    ).fetchall()
    if not rows:
        return None
    if include_candidates:
        return rows[0] if rows[0]["status"] in {"approved", "candidate"} else None
    latest = rows[0]
    if latest["status"] == "approved":
        return latest
    if latest["status"] != "candidate":
        return None
    for row in rows[1:]:
        if row["status"] == "candidate":
            continue
        return row if row["status"] == "approved" else None
    return None


def projected_rows(
    connection: sqlite3.Connection,
    table: str,
    include_candidates: bool = False,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        f"SELECT * FROM {table} ORDER BY id, version DESC"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["id"], []).append(row)
    selected: list[sqlite3.Row] = []
    for record_rows in grouped.values():
        latest = record_rows[0]
        if include_candidates:
            if latest["status"] in {"approved", "candidate"}:
                selected.append(latest)
        elif latest["status"] == "approved":
            selected.append(latest)
        elif latest["status"] == "candidate":
            prior = next(
                (row for row in record_rows[1:] if row["status"] != "candidate"),
                None,
            )
            if prior is not None and prior["status"] != "approved":
                prior = None
            if prior is not None:
                selected.append(prior)
    return selected


def projected_relation_rows(
    connection: sqlite3.Connection,
    table: str,
    where: str,
    parameters: tuple[Any, ...],
    include_candidates: bool,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY id, version DESC",
        parameters,
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["id"], []).append(row)
    selected: list[sqlite3.Row] = []
    for relation_rows in grouped.values():
        latest = relation_rows[0]
        if include_candidates:
            if latest["status"] in {"approved", "candidate"}:
                selected.append(latest)
        elif latest["status"] == "approved":
            selected.append(latest)
        elif latest["status"] == "candidate":
            prior = next(
                (row for row in relation_rows[1:] if row["status"] != "candidate"),
                None,
            )
            if prior is not None and prior["status"] != "approved":
                prior = None
            if prior is not None:
                selected.append(prior)
    return selected


def canonical_cte(name: str, table: str) -> str:
    return f"""
    latest_{name} AS (
      SELECT item.* FROM {table} AS item
      JOIN (SELECT id, MAX(version) AS version FROM {table} GROUP BY id) AS latest
        ON latest.id = item.id AND latest.version = item.version
    ),
    effective_{name} AS (
      SELECT id,
             CASE
               WHEN status = 'approved' THEN version
               WHEN status = 'candidate' THEN (
                 SELECT prior.version FROM {table} AS prior
                  WHERE prior.id = latest_{name}.id
                    AND prior.version < latest_{name}.version
                    AND prior.status != 'candidate'
                  ORDER BY prior.version DESC LIMIT 1
               )
             END AS version
        FROM latest_{name}
    ),
    canonical_{name} AS (
      SELECT item.* FROM {table} AS item
      JOIN effective_{name} AS effective
        ON effective.id = item.id AND effective.version = item.version
       AND item.status = 'approved'
    )
    """


def projected_relation_page(
    connection: sqlite3.Connection,
    relation_table: str,
    relation_name: str,
    endpoint_table: str,
    endpoint_name: str,
    endpoint_id_column: str,
    endpoint_version_column: str,
    where: str,
    parameters: tuple[Any, ...],
    limit: int,
    offset: int,
) -> tuple[list[sqlite3.Row], int]:
    ctes = ",".join(
        (
            canonical_cte(relation_name, relation_table),
            canonical_cte(endpoint_name, endpoint_table),
        )
    )
    base = f"""
    WITH {ctes}
    SELECT relation.* FROM canonical_{relation_name} AS relation
    JOIN canonical_{endpoint_name} AS endpoint
      ON endpoint.id = relation.{endpoint_id_column}
     AND endpoint.version = relation.{endpoint_version_column}
    WHERE {where}
    """
    total = connection.execute(
        f"SELECT COUNT(*) FROM ({base})", parameters
    ).fetchone()[0]
    rows = connection.execute(
        base + " ORDER BY relation.id LIMIT ? OFFSET ?",
        parameters + (limit, offset),
    ).fetchall()
    return rows, int(total)


def is_latest_approved(
    connection: sqlite3.Connection, table: str, record_id: str, version: int
) -> bool:
    latest = projected_one(connection, table, record_id)
    return (
        latest is not None
        and latest["version"] == version
        and latest["status"] == "approved"
    )


def current_story_memberships(
    connection: sqlite3.Connection, event_id: str, event_version: int
) -> list[sqlite3.Row]:
    rows = projected_relation_rows(
        connection,
        "event_story_arc_memberships",
        "event_id = ? AND event_version = ?",
        (event_id, event_version),
        False,
    )
    return [
        row for row in rows
        if is_latest_approved(
            connection, "story_arcs", row["story_arc_id"], row["story_arc_version"]
        )
    ]


def source_refs(
    connection: sqlite3.Connection, claim_id: str, claim_version: int
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT project, expression_id, expression_fingerprint, relation
              FROM source_expression_refs
             WHERE claim_id = ? AND claim_version = ?
             ORDER BY project, expression_id, expression_fingerprint
            """,
            (claim_id, claim_version),
        )
    ]


def claim_record(
    connection: sqlite3.Connection, claim_id: str,
    include_candidates: bool = False,
) -> dict[str, Any]:
    row = projected_one(connection, "claims", claim_id, include_candidates)
    if row is None:
        raise ContractError("claim not found in the requested canonical view")
    claim = decode_payload(row)
    claim["source_expression_refs"] = source_refs(
        connection, row["id"], row["version"]
    )
    return {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "query_kind": "claim", "claim": claim,
    }


def _claim_query_page(
    connection: sqlite3.Connection, predicate: str, parameters: tuple[Any, ...],
    query_kind: str, limit: int, offset: int,
) -> dict[str, Any]:
    cte = canonical_cte("claims", "claims")
    base = f"WITH {cte} SELECT * FROM canonical_claims WHERE {predicate}"
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM ({base})", parameters
    ).fetchone()[0])
    rows = connection.execute(
        base + " ORDER BY id LIMIT ? OFFSET ?", parameters + (limit, offset)
    ).fetchall()
    claims = []
    for row in rows:
        claim = decode_payload(row)
        claim["source_expression_refs"] = source_refs(
            connection, row["id"], row["version"]
        )
        claims.append(claim)
    return {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "query_kind": query_kind, "claims": claims,
        "page": {
            "limit": limit, "offset": offset, "total": total,
            "truncated": offset + len(claims) < total,
        },
    }


def project_claims(
    connection: sqlite3.Connection, project: str,
    limit: int = DEFAULT_QUERY_LIMIT, offset: int = 0,
) -> dict[str, Any]:
    return _claim_query_page(
        connection, "origin_project = ?", (project,), "project_claims", limit, offset
    )


def prior_verdicts(
    connection: sqlite3.Connection, finding_fingerprint: str | None = None,
    legacy_claim_id: str | None = None, limit: int = DEFAULT_QUERY_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    if finding_fingerprint is not None:
        if SHA256_RE.fullmatch(finding_fingerprint) is None:
            raise ContractError("finding fingerprint must be lowercase SHA-256")
        return _claim_query_page(
            connection, "origin_finding_fingerprint = ?", (finding_fingerprint,),
            "prior_verdict", limit, offset,
        )
    if not nonempty(legacy_claim_id):
        raise ContractError("prior-verdict lookup requires an exact origin key")
    matching = []
    for row in projected_rows(connection, "claims"):
        claim = decode_payload(row)
        if claim.get("origin", {}).get("legacy_claim_id") == legacy_claim_id:
            claim["source_expression_refs"] = source_refs(
                connection, row["id"], row["version"]
            )
            matching.append(claim)
    matching.sort(key=lambda claim: claim["id"])
    total = len(matching)
    page = matching[offset:offset + limit]
    return {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "query_kind": "prior_verdict", "claims": page,
        "page": {
            "limit": limit, "offset": offset, "total": total,
            "truncated": offset + len(page) < total,
        },
    }


def equivalence_candidates(
    connection: sqlite3.Connection, proposition: str,
    limit: int = DEFAULT_QUERY_LIMIT, offset: int = 0,
) -> dict[str, Any]:
    if not nonempty(proposition) or len(proposition) > MAX_TEXT_LENGTH:
        raise ContractError("equivalence lookup requires an exact bounded proposition")
    return _claim_query_page(
        connection, "proposition = ?", (proposition,),
        "equivalence_candidates", limit, offset,
    )


def traverse_claim(
    connection: sqlite3.Connection,
    claim_id: str,
    include_candidates: bool = False,
    limit: int = DEFAULT_QUERY_LIMIT,
    offset: int = 0,
    nested_offset: int = 0,
) -> dict[str, Any]:
    claim_row = projected_one(connection, "claims", claim_id, include_candidates)
    if claim_row is None:
        raise ContractError(f"claim not found: {claim_id}")
    if include_candidates:
        memberships = projected_relation_rows(
            connection, "claim_event_memberships",
            "claim_id = ? AND claim_version = ?",
            (claim_id, claim_row["version"]), True,
        )
        memberships = [
            row for row in memberships
            if (
                (endpoint := projected_one(connection, "events", row["event_id"], True))
                is not None and endpoint["version"] == row["event_version"]
            )
        ]
        total_events = len(memberships)
        memberships = memberships[offset:offset + limit]
    else:
        memberships, total_events = projected_relation_page(
            connection, "claim_event_memberships", "claim_events",
            "events", "events", "event_id", "event_version",
            "relation.claim_id = ? AND relation.claim_version = ?",
            (claim_id, claim_row["version"]), limit, offset,
        )
    events: list[dict[str, Any]] = []
    for membership in memberships:
        event = connection.execute(
            "SELECT * FROM events WHERE id = ? AND version = ?",
            (membership["event_id"], membership["event_version"]),
        ).fetchone()
        if include_candidates:
            story_memberships = projected_relation_rows(
                connection, "event_story_arc_memberships",
                "event_id = ? AND event_version = ?",
                (membership["event_id"], membership["event_version"]), True,
            )
            story_memberships = [
                row for row in story_memberships
                if (endpoint := projected_one(connection, "story_arcs", row["story_arc_id"], True))
                is not None and endpoint["version"] == row["story_arc_version"]
            ]
            total_stories = len(story_memberships)
            story_memberships = story_memberships[nested_offset:nested_offset + limit]
        else:
            story_memberships, total_stories = projected_relation_page(
                connection, "event_story_arc_memberships", "event_stories",
                "story_arcs", "stories", "story_arc_id", "story_arc_version",
                "relation.event_id = ? AND relation.event_version = ?",
                (membership["event_id"], membership["event_version"]),
                limit, nested_offset,
            )
        stories = []
        for story_membership in story_memberships:
            story = connection.execute(
                "SELECT * FROM story_arcs WHERE id = ? AND version = ?",
                (story_membership["story_arc_id"], story_membership["story_arc_version"]),
            ).fetchone()
            stories.append(
                {
                    "membership": decode_payload(story_membership),
                    "story_arc": decode_payload(story),
                }
            )
        events.append(
            {
                "membership": decode_payload(membership),
                "event": decode_payload(event),
                "story_arcs": stories,
                "story_arc_page": {
                    "limit": limit, "offset": nested_offset, "total": total_stories,
                    "truncated": nested_offset + len(stories) < total_stories,
                },
            }
        )
    return {
        "direction": "claim_to_story_arcs",
        "claim": decode_payload(claim_row),
        "source_expression_refs": source_refs(
            connection, claim_id, claim_row["version"]
        ),
        "events": events,
        "event_page": {
            "limit": limit, "offset": offset, "total": total_events,
            "truncated": offset + len(events) < total_events,
        },
    }


def traverse_story_arc(
    connection: sqlite3.Connection,
    story_arc_id: str,
    include_candidates: bool = False,
    limit: int = DEFAULT_QUERY_LIMIT,
    offset: int = 0,
    nested_offset: int = 0,
) -> dict[str, Any]:
    story_row = projected_one(connection, "story_arcs", story_arc_id, include_candidates)
    if story_row is None:
        raise ContractError(f"story arc not found: {story_arc_id}")
    if include_candidates:
        story_memberships = projected_relation_rows(
            connection, "event_story_arc_memberships",
            "story_arc_id = ? AND story_arc_version = ?",
            (story_arc_id, story_row["version"]), True,
        )
        story_memberships = [
            row for row in story_memberships
            if (endpoint := projected_one(connection, "events", row["event_id"], True))
            is not None and endpoint["version"] == row["event_version"]
        ]
        total_events = len(story_memberships)
        story_memberships = story_memberships[offset:offset + limit]
    else:
        story_memberships, total_events = projected_relation_page(
            connection, "event_story_arc_memberships", "event_stories",
            "events", "events", "event_id", "event_version",
            "relation.story_arc_id = ? AND relation.story_arc_version = ?",
            (story_arc_id, story_row["version"]), limit, offset,
        )
    events: list[dict[str, Any]] = []
    for story_membership in story_memberships:
        event = connection.execute(
            "SELECT * FROM events WHERE id = ? AND version = ?",
            (story_membership["event_id"], story_membership["event_version"]),
        ).fetchone()
        if include_candidates:
            claim_memberships = projected_relation_rows(
                connection, "claim_event_memberships",
                "event_id = ? AND event_version = ?",
                (story_membership["event_id"], story_membership["event_version"]), True,
            )
            claim_memberships = [
                row for row in claim_memberships
                if (endpoint := projected_one(connection, "claims", row["claim_id"], True))
                is not None and endpoint["version"] == row["claim_version"]
            ]
            total_claims = len(claim_memberships)
            claim_memberships = claim_memberships[nested_offset:nested_offset + limit]
        else:
            claim_memberships, total_claims = projected_relation_page(
                connection, "claim_event_memberships", "claim_events",
                "claims", "claims", "claim_id", "claim_version",
                "relation.event_id = ? AND relation.event_version = ?",
                (story_membership["event_id"], story_membership["event_version"]),
                limit, nested_offset,
            )
        claims = []
        for membership in claim_memberships:
            claim = connection.execute(
                "SELECT * FROM claims WHERE id = ? AND version = ?",
                (membership["claim_id"], membership["claim_version"]),
            ).fetchone()
            claims.append(
                {
                    "membership": decode_payload(membership),
                    "claim": decode_payload(claim),
                    "source_expression_refs": source_refs(
                        connection, membership["claim_id"], membership["claim_version"]
                    ),
                }
            )
        events.append(
            {
                "membership": decode_payload(story_membership),
                "event": decode_payload(event),
                "claims": claims,
                "claim_page": {
                    "limit": limit, "offset": nested_offset, "total": total_claims,
                    "truncated": nested_offset + len(claims) < total_claims,
                },
            }
        )
    return {
        "direction": "story_arc_to_claims",
        "story_arc": decode_payload(story_row),
        "events": events,
        "event_page": {
            "limit": limit, "offset": offset, "total": total_events,
            "truncated": offset + len(events) < total_events,
        },
    }


def coverage_report(
    connection: sqlite3.Connection,
    limit: int = DEFAULT_QUERY_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    ctes = ",".join(
        canonical_cte(name, table)
        for name, table in (
            ("claims", "claims"),
            ("events", "events"),
            ("stories", "story_arcs"),
            ("claim_events", "claim_event_memberships"),
            ("event_stories", "event_story_arc_memberships"),
        )
    )
    coverage_cte = f"""
    WITH {ctes},
    coverage_rows AS (
      SELECT claim.id AS claim_id,
             claim.version AS claim_version,
             CASE
               WHEN claim.event_link_disposition = 'not_applicable' THEN 'not_applicable'
               WHEN COUNT(DISTINCT event.id) > 0
                AND COUNT(DISTINCT story.id) > 0 THEN 'complete'
               WHEN COUNT(DISTINCT event.id) > 0 THEN 'event_only'
               ELSE 'pending'
             END AS state,
             COUNT(DISTINCT event.id) AS approved_event_count,
             COUNT(DISTINCT story.id) AS approved_story_arc_count
        FROM canonical_claims AS claim
        LEFT JOIN canonical_claim_events AS claim_event
          ON claim_event.claim_id = claim.id
         AND claim_event.claim_version = claim.version
        LEFT JOIN canonical_events AS event
          ON event.id = claim_event.event_id
         AND event.version = claim_event.event_version
        LEFT JOIN canonical_event_stories AS event_story
          ON event_story.event_id = event.id
         AND event_story.event_version = event.version
        LEFT JOIN canonical_stories AS story
          ON story.id = event_story.story_arc_id
         AND story.version = event_story.story_arc_version
       GROUP BY claim.id, claim.version, claim.event_link_disposition
    )
    """
    summary = connection.execute(
        coverage_cte
        + """
        SELECT COUNT(*) AS approved_claims,
               SUM(CASE WHEN state = 'complete' THEN 1 ELSE 0 END) AS complete,
               SUM(CASE WHEN state = 'event_only' THEN 1 ELSE 0 END) AS event_only,
               SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN state = 'not_applicable' THEN 1 ELSE 0 END) AS not_applicable
          FROM coverage_rows
        """
    ).fetchone()
    page = [
        dict(row)
        for row in connection.execute(
            coverage_cte
            + " SELECT * FROM coverage_rows ORDER BY claim_id LIMIT ? OFFSET ?",
            (limit, offset),
        )
    ]
    counts = {
        key: int(summary[key] or 0)
        for key in ("complete", "event_only", "pending", "not_applicable")
    }
    total = int(summary["approved_claims"] or 0)
    return {
        "summary": {"approved_claims": total, **counts},
        "claims": page,
        "page": {
            "limit": limit, "offset": offset, "total": total,
            "truncated": offset + len(page) < total,
        },
    }


def open_existing_database(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve(strict=False)
    _reject_symlink_path(path)
    if not path.is_file():
        raise ContractError(f"database not found: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ContractError(
            f"database permissions must be owner-only (0600), got {mode:04o}"
        )
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise ContractError("database must be owned by the current user and not hard-linked")
    uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, timeout=5.0, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA busy_timeout = 5000")
    _verify_database_schema(connection)
    return connection


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def expected_projection(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    common = {"id": item["id"]}
    if kind == "review_decision":
        return common | {
            "reviewer_id": item["reviewer_id"],
            "decided_at": item["decided_at"],
            "disposition": item["disposition"],
            "rationale": item["rationale"],
        }
    common |= {
        "version": item["version"],
        "supersedes_version": item.get("supersedes_version"),
        "status": item["status"],
        "review_decision_id": item.get("review_decision_id"),
    }
    if kind == "claim":
        origin = item["origin"]
        return common | {
            "origin_project": origin["project"],
            "origin_finding_id": origin["finding_id"],
            "origin_finding_fingerprint": origin["finding_fingerprint"],
            "proposition": item["proposition"],
            "event_link_disposition": item["event_link_disposition"],
            "event_link_decision_id": item.get("event_link_decision_id"),
        }
    if kind == "event":
        core = item["core"]
        return common | {
            "label": item["label"],
            "actors_json": json_text(core["actors"]),
            "action": core["action"],
            "object": core["object"],
            "place": core["place"],
            "event_time": core["time"],
            "story_link_disposition": item["story_link_disposition"],
            "story_link_decision_id": item.get("story_link_decision_id"),
        }
    if kind == "story_arc":
        return common | {
            "title": item["title"], "description": item["description"]
        }
    if kind == "claim_event_membership":
        return common | {
            "claim_id": item["claim"]["id"],
            "claim_version": item["claim"]["version"],
            "event_id": item["event"]["id"],
            "event_version": item["event"]["version"],
            "relation": item["relation"],
        }
    if kind == "event_story_arc_membership":
        return common | {
            "event_id": item["event"]["id"],
            "event_version": item["event"]["version"],
            "story_arc_id": item["story_arc"]["id"],
            "story_arc_version": item["story_arc"]["version"],
            "role": item["role"],
        }
    raise ContractError(f"unknown projection kind: {kind}")


def verify_projection_state(connection: sqlite3.Connection) -> tuple[int, int]:
    destination_row = connection.execute(
        "SELECT destination_id FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    destination_id = destination_row["destination_id"] if destination_row else None
    if destination_id is None:
        if connection.execute("SELECT COUNT(*) FROM projection_jobs").fetchone()[0]:
            raise ContractError("unprovisioned database contains projection jobs")
        return 0, 0

    graph_sources = {
        row["batch_id"]: (_case_id_for_project(row["source_project"]), row["payload_sha256"])
        for row in connection.execute(
            "SELECT batch_id, source_project, payload_sha256 FROM batches"
        )
    }
    policy_sources: dict[str, tuple[str, str]] = {}
    for row in connection.execute("SELECT * FROM case_policy_receipts"):
        try:
            receipt = json.loads(
                row["payload_json"], object_pairs_hook=_reject_duplicate_keys
            )
            evidence = json.loads(
                row["signature_evidence_json"], object_pairs_hook=_reject_duplicate_keys
            )
        except (json.JSONDecodeError, ContractError) as exc:
            raise ContractError("case-policy receipt storage is corrupt") from exc
        validate_case_policy_receipt(receipt, row["destination_id"])
        if (
            payload_sha256(receipt) != row["payload_sha256"]
            or receipt["receipt_id"] != row["receipt_id"]
            or receipt["case_id"] != row["case_id"]
            or receipt["policy_revision"] != row["policy_revision"]
            or receipt["status"] != row["status"]
            or row["destination_id"] != destination_id
        ):
            raise ContractError("case-policy receipt projection is corrupt")
        if evidence.get("identity") != receipt["issuer"]["issuer_id"] or evidence.get(
            "namespace"
        ) != CASE_POLICY_NAMESPACE:
            raise ContractError("case-policy signature binding is corrupt")
        verify_stored_signed_document(receipt, evidence)
        policy_sources[row["receipt_id"]] = (row["case_id"], row["payload_sha256"])

    seen_graph: set[str] = set()
    seen_policy: set[str] = set()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in connection.execute(
        "SELECT * FROM projection_jobs ORDER BY case_id, destination_id, generation"
    ):
        if row["status"] not in PROJECTION_JOB_STATUSES or not JOB_ID_RE.fullmatch(row["job_id"]):
            raise ContractError("projection job contract is corrupt")
        if row["destination_id"] != destination_id:
            raise ContractError("projection job crosses the database destination")
        if row["source_kind"] == "graph_commit":
            source = graph_sources.get(row["source_ref"])
            seen_graph.add(row["source_ref"])
        else:
            source = policy_sources.get(row["source_ref"])
            seen_policy.add(row["source_ref"])
        if source is None:
            raise ContractError("projection job has a dangling or unreceipted source")
        if row["case_id"] != source[0] or row["source_sha256"] != source[1]:
            raise ContractError("projection job source binding is corrupt or cross-case")
        groups.setdefault((row["case_id"], row["destination_id"]), []).append(row)
    if seen_graph != set(graph_sources) or seen_policy != set(policy_sources):
        raise ContractError("database contains a source without exact projection intent")

    heads = {
        (row["case_id"], row["destination_id"]): row
        for row in connection.execute("SELECT * FROM projection_heads")
    }
    if set(heads) != set(groups):
        raise ContractError("projection heads do not exactly match job groups")
    for key, jobs in groups.items():
        previous_hash: str | None = None
        for expected_generation, job in enumerate(jobs, 1):
            if job["generation"] != expected_generation:
                raise ContractError("projection generations are not contiguous")
            expected_hash = _projection_intent_hash(
                previous_hash, job["case_id"], job["destination_id"],
                job["generation"], job["source_kind"], job["source_ref"],
                job["source_sha256"],
            )
            expected_id = _projection_job_id(
                job["case_id"], job["destination_id"], job["generation"], expected_hash
            )
            if job["desired_projection_set_sha256"] != expected_hash or job["job_id"] != expected_id:
                raise ContractError("projection job intent hash is corrupt")
            if job is not jobs[-1] and job["status"] not in {"completed", "superseded"}:
                raise ContractError("obsolete projection generation is not terminal")
            previous_hash = expected_hash
        head = heads[key]
        latest = jobs[-1]
        if (
            head["current_generation"] != latest["generation"]
            or head["current_job_id"] != latest["job_id"]
            or head["desired_projection_set_sha256"] != latest["desired_projection_set_sha256"]
        ):
            raise ContractError("projection current-head binding is corrupt")

    receipts = {
        row["job_id"]: row
        for row in connection.execute("SELECT * FROM projection_final_receipts")
    }
    completed = 0
    for row in connection.execute("SELECT * FROM projection_jobs"):
        receipt = receipts.get(row["job_id"])
        if row["status"] == "completed":
            completed += 1
            if receipt is None or row["final_receipt_id"] != receipt["receipt_id"]:
                raise ContractError("completed projection job lacks its final receipt")
        elif receipt is not None or row["final_receipt_id"] is not None:
            raise ContractError("non-completed projection job has a final receipt")
        if receipt is None:
            continue
        body = {
            "job_id": receipt["job_id"], "case_id": receipt["case_id"],
            "destination_id": receipt["destination_id"],
            "generation": receipt["generation"],
            "desired_projection_set_sha256": receipt["desired_projection_set_sha256"],
            "workspace_receipt_ref": receipt["workspace_receipt_ref"],
            "workspace_receipt_sha256": receipt["workspace_receipt_sha256"],
            "completed_at": receipt["completed_at"],
        }
        binding = payload_sha256(body)
        if (
            receipt["receipt_id"] != "projection-receipt:" + binding
            or receipt["binding_sha256"] != binding
            or receipt["case_id"] != row["case_id"]
            or receipt["destination_id"] != row["destination_id"]
            or receipt["generation"] != row["generation"]
            or receipt["desired_projection_set_sha256"] != row["desired_projection_set_sha256"]
            or SHA256_RE.fullmatch(receipt["workspace_receipt_sha256"]) is None
            or RECEIPT_REF_RE.fullmatch(receipt["workspace_receipt_ref"]) is None
        ):
            raise ContractError("projection final receipt binding is corrupt")
    if len(receipts) != completed:
        raise ContractError("database contains an extra projection final receipt")
    return len(groups), completed


def verify_database(connection: sqlite3.Connection) -> dict[str, Any]:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise ContractError(f"database integrity check failed: {quick_check}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise ContractError("database foreign-key integrity check failed")
    checked = 0
    receipted_items: dict[tuple[str, str, int], set[str]] = {}
    receipted_source_refs: set[tuple[str, int, str, str, str, str]] = set()
    for row in connection.execute(
        "SELECT batch_id, payload_sha256, payload_json, review_manifest_json, "
        "approval_receipt_json, approval_evidence_json FROM batches ORDER BY batch_id"
    ):
        try:
            batch = json.loads(row["payload_json"], object_pairs_hook=_reject_duplicate_keys)
            manifest = json.loads(
                row["review_manifest_json"], object_pairs_hook=_reject_duplicate_keys
            )
            receipt = json.loads(
                row["approval_receipt_json"], object_pairs_hook=_reject_duplicate_keys
            )
            evidence = json.loads(
                row["approval_evidence_json"], object_pairs_hook=_reject_duplicate_keys
            )
        except (json.JSONDecodeError, ContractError) as exc:
            raise ContractError(f"batch {row['batch_id']} receipt is corrupt") from exc
        if payload_sha256(batch) != row["payload_sha256"]:
            raise ContractError(f"batch {row['batch_id']} payload hash is corrupt")
        errors = validate_batch(batch)
        if errors:
            raise ContractError(f"batch {row['batch_id']} payload contract is corrupt")
        manifest_body = dict(manifest)
        manifest_hash = manifest_body.pop("review_manifest_sha256", None)
        if manifest_hash != payload_sha256(manifest_body):
            raise ContractError(f"batch {row['batch_id']} manifest hash is corrupt")
        if manifest.get("payload_sha256") != row["payload_sha256"]:
            raise ContractError(f"batch {row['batch_id']} manifest payload binding is corrupt")
        validate_approval_receipt(receipt, manifest)
        unauthenticated = [
            decision["id"] for decision in batch["review_decisions"]
            if decision["reviewer_id"] != receipt["reviewer_id"]
        ]
        if unauthenticated:
            raise ContractError(f"batch {row['batch_id']} reviewer binding is corrupt")
        verify_stored_approval_signature(receipt, evidence)
        expected_items: dict[tuple[str, str, int], str] = {}
        expected_payloads: dict[tuple[str, str, int], str] = {}
        for kind, collection in (
            ("review_decision", "review_decisions"),
            ("claim", "claims"),
            ("event", "events"),
            ("story_arc", "story_arcs"),
            ("claim_event_membership", "claim_event_memberships"),
            ("event_story_arc_membership", "event_story_arc_memberships"),
        ):
            for item in batch[collection]:
                item_key = (kind, item["id"], item.get("version", 1))
                expected_items[item_key] = payload_sha256(item)
                expected_payloads[item_key] = json_text(item)
                receipted_items.setdefault(item_key, set()).add(row["batch_id"])
        stored_items = {
            (item["kind"], item["record_id"], item["record_version"]):
                item["payload_sha256"]
            for item in connection.execute(
                "SELECT kind, record_id, record_version, payload_sha256 "
                "FROM batch_items WHERE batch_id = ?",
                (row["batch_id"],),
            )
        }
        if stored_items != expected_items:
            raise ContractError(f"batch {row['batch_id']} item manifest is corrupt")
        table_by_kind = {
            "review_decision": "review_decisions",
            "claim": "claims",
            "event": "events",
            "story_arc": "story_arcs",
            "claim_event_membership": "claim_event_memberships",
            "event_story_arc_membership": "event_story_arc_memberships",
        }
        for kind, record_id, version in expected_items:
            table = table_by_kind[kind]
            if kind == "review_decision":
                target = connection.execute(
                    f"SELECT * FROM {table} WHERE id = ?", (record_id,)
                ).fetchone()
            else:
                target = connection.execute(
                    f"SELECT * FROM {table} WHERE id = ? AND version = ?",
                    (record_id, version),
                ).fetchone()
            if target is None or target["payload_json"] != expected_payloads[
                (kind, record_id, version)
            ]:
                raise ContractError(
                    f"batch {row['batch_id']} item {kind}/{record_id}/{version} is corrupt"
                )
            payload_item = json.loads(target["payload_json"])
            projection = expected_projection(kind, payload_item)
            if any(target[column] != value for column, value in projection.items()):
                raise ContractError(
                    f"batch {row['batch_id']} item {kind}/{record_id}/{version} "
                    "projection is corrupt"
                )
        for claim in batch["claims"]:
            for ref in claim["source_expression_refs"]:
                receipted_source_refs.add(
                    (
                        claim["id"], claim["version"], ref["project"],
                        ref["expression_id"], ref["expression_fingerprint"],
                        ref["relation"],
                    )
                )
            actual_refs = source_refs(connection, claim["id"], claim["version"])
            expected_refs = sorted(
                claim["source_expression_refs"],
                key=lambda item: (
                    item["project"], item["expression_id"],
                    item["expression_fingerprint"],
                ),
            )
            if actual_refs != expected_refs:
                raise ContractError(
                    f"batch {row['batch_id']} source-expression projection is corrupt"
                )
        checked += 1
    materialized_items: dict[tuple[str, str, int], str] = {}
    for kind, table in (
        ("review_decision", "review_decisions"),
        ("claim", "claims"),
        ("event", "events"),
        ("story_arc", "story_arcs"),
        ("claim_event_membership", "claim_event_memberships"),
        ("event_story_arc_membership", "event_story_arc_memberships"),
    ):
        version_sql = "1 AS version" if kind == "review_decision" else "version"
        for item in connection.execute(
            f"SELECT id, {version_sql}, batch_id FROM {table}"
        ):
            key = (kind, item["id"], item["version"])
            materialized_items[key] = item["batch_id"]
    if set(materialized_items) != set(receipted_items):
        raise ContractError("database contains unreceipted or missing materialized records")
    for key, batch_id in materialized_items.items():
        if batch_id not in receipted_items[key]:
            raise ContractError("materialized record has an invalid originating batch")
    materialized_source_refs = {
        tuple(item)
        for item in connection.execute(
            "SELECT claim_id, claim_version, project, expression_id, "
            "expression_fingerprint, relation FROM source_expression_refs"
        )
    }
    if materialized_source_refs != receipted_source_refs:
        raise ContractError("database contains unreceipted source-expression references")
    enforce_coverage_invariants(connection)
    projection_heads, completed_projections = verify_projection_state(connection)
    return {
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "status": "healthy",
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "verified_batches": checked,
        "projection_heads": projection_heads,
        "completed_projections": completed_projections,
        "assurance": "local_conformance_only",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    init_parser = subcommands.add_parser(
        "init-local", aliases=["init-reference"], help="initialize the local Spotlight graph"
    )
    init_parser.add_argument("--workspace-root", required=True, type=Path)
    init_parser.add_argument("--db", required=True, type=Path)
    init_parser.add_argument("--destination-id", required=True)
    init_parser.add_argument("--unsafe-local-reference-commit", action="store_true", help=argparse.SUPPRESS)
    stage_parser = subcommands.add_parser(
        "stage", aliases=["validate"], help="validate and hash a reviewed batch"
    )
    stage_parser.add_argument("--case-root", required=True, type=Path)
    stage_parser.add_argument("--case-dir", required=True, type=Path)
    stage_parser.add_argument("--destination-id", required=True)
    stage_parser.add_argument("batch", type=Path)
    approval_parser = subcommands.add_parser(
        "approval", help="create the canonical receipt bytes for external signing"
    )
    approval_parser.add_argument("--manifest", required=True, type=Path)
    approval_parser.add_argument("--reviewer-id", required=True)
    approval_parser.add_argument("--approved-at", required=True)
    commit_parser = subcommands.add_parser("commit", help="atomically commit a reviewed batch")
    commit_parser.add_argument("--workspace-root", required=True, type=Path)
    commit_parser.add_argument("--case-root", required=True, type=Path)
    commit_parser.add_argument("--case-dir", required=True, type=Path)
    commit_parser.add_argument("--db", required=True, type=Path)
    commit_parser.add_argument("--expected-sha256", required=True)
    commit_parser.add_argument("--manifest", required=True, type=Path)
    commit_parser.add_argument("--approval-receipt", required=True, type=Path)
    commit_parser.add_argument("--approval-signature", required=True, type=Path)
    commit_parser.add_argument("--allowed-signers", required=True, type=Path)
    commit_parser.add_argument("--unsafe-local-reference-commit", action="store_true", help=argparse.SUPPRESS)
    commit_parser.add_argument("--project-after-commit", action="store_true")
    commit_parser.add_argument("--activation", type=Path)
    commit_parser.add_argument("batch", type=Path)
    policy_parser = subcommands.add_parser(
        "policy-commit", help="commit a signed case-policy receipt and projection intent"
    )
    policy_parser.add_argument("--workspace-root", required=True, type=Path)
    policy_parser.add_argument("--db", required=True, type=Path)
    policy_parser.add_argument("--destination-id", required=True)
    policy_parser.add_argument("--signature", required=True, type=Path)
    policy_parser.add_argument("--allowed-signers", required=True, type=Path)
    policy_parser.add_argument("--unsafe-local-reference-commit", action="store_true", help=argparse.SUPPRESS)
    policy_parser.add_argument("receipt", type=Path)
    for name, help_text in (
        ("job-claim", "claim the next current projection job"),
        ("job-fail", "record a retryable projection failure"),
        ("job-retry", "return a failed current job to pending"),
        ("job-complete", "bind a final workspace receipt reference"),
        ("job-reconcile", "reconcile an abandoned running job"),
    ):
        job_parser = subcommands.add_parser(name, help=help_text)
        job_parser.add_argument("--workspace-root", required=True, type=Path)
        job_parser.add_argument("--db", required=True, type=Path)
        job_parser.add_argument("--unsafe-local-reference-commit", action="store_true", help=argparse.SUPPRESS)
        if name != "job-claim":
            job_parser.add_argument("--job-id", required=True)
        if name == "job-fail":
            job_parser.add_argument("--error", required=True)
        if name in {"job-complete", "job-reconcile"}:
            job_parser.add_argument("--desired-sha256")
            job_parser.add_argument("--workspace-receipt-ref")
            job_parser.add_argument("--workspace-receipt-sha256")
    lookup_parser = subcommands.add_parser("lookup", help="perform an exact graph lookup")
    lookup_parser.add_argument("--workspace-root", required=True, type=Path)
    lookup_parser.add_argument("--db", required=True, type=Path)
    lookup_target = lookup_parser.add_mutually_exclusive_group(required=True)
    lookup_target.add_argument("--claim-id")
    lookup_target.add_argument("--project")
    lookup_target.add_argument("--prior-fingerprint")
    lookup_target.add_argument("--legacy-claim-id")
    lookup_target.add_argument("--equivalent-proposition")
    lookup_parser.add_argument("--include-candidates", action="store_true")
    lookup_parser.add_argument("--limit", type=int, default=DEFAULT_QUERY_LIMIT)
    lookup_parser.add_argument("--offset", type=int, default=0)
    traverse_parser = subcommands.add_parser("traverse", help="traverse the approved graph")
    traverse_parser.add_argument("--workspace-root", required=True, type=Path)
    traverse_parser.add_argument("--db", required=True, type=Path)
    target = traverse_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--claim-id")
    target.add_argument("--story-arc-id")
    traverse_parser.add_argument("--include-candidates", action="store_true")
    traverse_parser.add_argument("--limit", type=int, default=DEFAULT_QUERY_LIMIT)
    traverse_parser.add_argument("--offset", type=int, default=0)
    traverse_parser.add_argument("--nested-offset", type=int, default=0)
    coverage_parser = subcommands.add_parser("coverage", help="report claim graph coverage")
    coverage_parser.add_argument("--workspace-root", required=True, type=Path)
    coverage_parser.add_argument("--db", required=True, type=Path)
    coverage_parser.add_argument("--limit", type=int, default=DEFAULT_QUERY_LIMIT)
    coverage_parser.add_argument("--offset", type=int, default=0)
    verify_parser = subcommands.add_parser(
        "verify", help="check database schema, integrity, and stored batch receipts"
    )
    verify_parser.add_argument("--workspace-root", required=True, type=Path)
    verify_parser.add_argument("--db", required=True, type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"init-local", "init-reference"}:
            workspace_root = resolve_root(args.workspace_root, "workspace root")
            database = resolve_beneath(workspace_root, args.db, "database")
            print_json(initialize_database(database, args.destination_id))
            return 0
        if args.command in {"stage", "validate"}:
            case_root = resolve_root(args.case_root, "case root")
            case_dir = resolve_beneath(case_root, args.case_dir, "case directory")
            batch_path = resolve_beneath(case_dir, args.batch, "knowledge batch")
            batch = load_json(batch_path)
            errors = validate_batch(batch)
            if errors:
                print("invalid batch:\n- " + "\n- ".join(errors), file=sys.stderr)
                return 1
            source_snapshot = verify_source_case(case_dir, batch)
            print_json(review_manifest(batch, source_snapshot, args.destination_id))
            return 0
        if args.command == "approval":
            manifest = load_json(args.manifest.expanduser().resolve())
            if not nonempty(args.reviewer_id) or not valid_datetime(args.approved_at):
                raise ContractError("reviewer-id and timezone-aware approved-at are required")
            receipt = {
                "schema_version": "1.0",
                "namespace": APPROVAL_NAMESPACE,
                "destination_id": manifest.get("destination_id"),
                "payload_sha256": manifest.get("payload_sha256"),
                "review_manifest_sha256": manifest.get("review_manifest_sha256"),
                "reviewer_id": args.reviewer_id,
                "approved_at": args.approved_at,
                "decision": "approved",
            }
            validate_approval_receipt(receipt, manifest)
            sys.stdout.buffer.write(canonical_json_bytes(receipt))
            return 0
        if args.command == "commit":
            workspace_root = resolve_root(args.workspace_root, "workspace root")
            case_root = resolve_root(args.case_root, "case root")
            case_dir = resolve_beneath(case_root, args.case_dir, "case directory")
            database = resolve_beneath(workspace_root, args.db, "database")
            batch_path = resolve_beneath(case_dir, args.batch, "knowledge batch")
            manifest_path = resolve_beneath(case_dir, args.manifest, "review manifest")
            receipt_path = resolve_beneath(
                case_dir, args.approval_receipt, "approval receipt"
            )
            signature_path = resolve_beneath(
                case_dir, args.approval_signature, "approval signature"
            )
            allowed_signers_path = resolve_beneath(
                workspace_root, args.allowed_signers, "allowed signers"
            )
            batch = load_json(batch_path)
            manifest = load_json(manifest_path)
            receipt = load_json(receipt_path)
            source_snapshot = verify_source_case(case_dir, batch)
            expected_manifest = review_manifest(
                batch, source_snapshot, manifest.get("destination_id", "")
            )
            if manifest != expected_manifest:
                raise ContractError("review manifest is stale or does not match source case")
            validate_approval_receipt(receipt, manifest)
            approval_evidence = verify_approval_signature(
                receipt, signature_path, allowed_signers_path
            )
            committed = _commit_verified_batch(
                database, batch, args.expected_sha256, manifest, receipt,
                approval_evidence,
            )
            if args.project_after_commit:
                if args.activation is None:
                    raise ContractError("--project-after-commit requires --activation")
                activation_path = resolve_beneath(workspace_root, args.activation, "activation receipt")
                activation_receipt = load_json(activation_path)
                import knowledge_projection as projection
                job = committed["projection_job"]
                projected = projection.run_worker(
                    database, job["job_id"], case_dir, workspace_root,
                    {}, None, activation_receipt,
                )
                print_json({"graph_commit": committed, "projection": projected})
            else:
                print_json(committed)
            return 0
        if args.command == "policy-commit":
            workspace_root = resolve_root(args.workspace_root, "workspace root")
            database = resolve_beneath(workspace_root, args.db, "database")
            receipt_path = resolve_beneath(workspace_root, args.receipt, "case-policy receipt")
            signature_path = resolve_beneath(workspace_root, args.signature, "case-policy signature")
            allowed_path = resolve_beneath(workspace_root, args.allowed_signers, "allowed signers")
            receipt = load_json(receipt_path)
            validate_case_policy_receipt(receipt, args.destination_id)
            evidence = verify_signed_document(
                receipt, receipt["issuer"]["issuer_id"], CASE_POLICY_NAMESPACE,
                signature_path, allowed_path,
            )
            print_json(commit_case_policy(
                database, receipt, args.destination_id, evidence
            ))
            return 0
        if args.command.startswith("job-"):
            workspace_root = resolve_root(args.workspace_root, "workspace root")
            database = resolve_beneath(workspace_root, args.db, "database")
            if not database.is_file():
                raise ContractError(f"database not found: {database}")
            connection = connect_database(database)
            try:
                if args.command == "job-claim":
                    claimed = claim_projection_job(connection)
                    print_json({
                        "response_schema_version": RESPONSE_SCHEMA_VERSION,
                        "status": "claimed" if claimed else "idle",
                        "projection_job": claimed,
                        "assurance": "local_conformance_only",
                    })
                elif args.command == "job-fail":
                    print_json(fail_projection_job(connection, args.job_id, args.error))
                elif args.command == "job-retry":
                    print_json(retry_projection_job(connection, args.job_id))
                elif args.command == "job-complete":
                    if not all((
                        args.desired_sha256, args.workspace_receipt_ref,
                        args.workspace_receipt_sha256,
                    )):
                        raise ContractError("job completion requires all workspace receipt fields")
                    print_json(complete_projection_job(
                        connection, args.job_id, args.desired_sha256,
                        args.workspace_receipt_ref, args.workspace_receipt_sha256,
                    ))
                else:
                    supplied = (
                        args.desired_sha256, args.workspace_receipt_ref,
                        args.workspace_receipt_sha256,
                    )
                    if any(supplied) and not all(supplied):
                        raise ContractError("reconciliation requires all or no workspace receipt fields")
                    if all(supplied):
                        print_json(complete_projection_job(
                            connection, args.job_id, args.desired_sha256,
                            args.workspace_receipt_ref, args.workspace_receipt_sha256,
                        ))
                    else:
                        print_json(fail_projection_job(
                            connection, args.job_id,
                            "abandoned running job requires workspace-journal reconciliation",
                        ))
            finally:
                connection.close()
            return 0
        workspace_root = resolve_root(args.workspace_root, "workspace root")
        database = resolve_beneath(workspace_root, args.db, "database")
        connection = open_existing_database(database)
        try:
            if args.command in {"traverse", "coverage", "lookup"} and (
                not 1 <= args.limit <= MAX_QUERY_LIMIT or args.offset < 0
            ):
                raise ContractError(
                    f"limit must be 1..{MAX_QUERY_LIMIT} and offset must be non-negative"
                )
            if args.command == "traverse" and args.nested_offset < 0:
                raise ContractError("nested-offset must be non-negative")
            if args.command == "traverse":
                if args.claim_id:
                    validate_errors: list[str] = []
                    validate_id(args.claim_id, "claim", "claim_id", validate_errors)
                    if validate_errors:
                        raise ContractError(validate_errors[0])
                    result = traverse_claim(
                        connection, args.claim_id, args.include_candidates,
                        args.limit, args.offset, args.nested_offset,
                    )
                else:
                    validate_errors = []
                    validate_id(
                        args.story_arc_id, "story_arc", "story_arc_id", validate_errors
                    )
                    if validate_errors:
                        raise ContractError(validate_errors[0])
                    result = traverse_story_arc(
                        connection, args.story_arc_id, args.include_candidates,
                        args.limit, args.offset, args.nested_offset,
                    )
                print_json(result)
                return 0
            if args.command == "coverage":
                print_json(coverage_report(connection, args.limit, args.offset))
                return 0
            if args.command == "lookup":
                if args.claim_id:
                    errors: list[str] = []
                    validate_id(args.claim_id, "claim", "claim_id", errors)
                    if errors:
                        raise ContractError(errors[0])
                    result = claim_record(connection, args.claim_id, args.include_candidates)
                elif args.project:
                    result = project_claims(connection, args.project, args.limit, args.offset)
                elif args.prior_fingerprint:
                    result = prior_verdicts(
                        connection, finding_fingerprint=args.prior_fingerprint,
                        limit=args.limit, offset=args.offset,
                    )
                elif args.legacy_claim_id:
                    result = prior_verdicts(
                        connection, legacy_claim_id=args.legacy_claim_id,
                        limit=args.limit, offset=args.offset,
                    )
                else:
                    result = equivalence_candidates(
                        connection, args.equivalent_proposition, args.limit, args.offset
                    )
                print_json(result)
                return 0
            if args.command == "verify":
                print_json(verify_database(connection))
                return 0
        finally:
            connection.close()
    except (ContractError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
