#!/usr/bin/env python3
"""Build a Spotlight provenance manifest for optional Noosphere C2PA signing.

Legacy cases retain the mutable 1.0 manifest. Source-expression-aware cases use
immutable 1.1 revision files plus a derived ``provenance-manifest.json`` pointer.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_expression_contract import canonical_fingerprint, fact_check_rows, lifecycle_state
import noosphere_verify


ARTIFACTS = [
    {
        "kind": "summary",
        "path": "summary.md",
        "gate1_dependency": True,
        "gate1_required": True,
        "gate1_authored": True,
    },
    {
        "kind": "summary_json",
        "path": "data/summary.json",
        "gate1_dependency": True,
        "gate1_required": True,
        "gate1_authored": True,
    },
    {"kind": "findings", "path": "data/findings.json", "gate1_dependency": True, "gate1_required": True},
    {"kind": "fact_check", "path": "data/fact-check.json", "gate1_dependency": True, "gate1_required": True},
    {"kind": "source_expressions", "path": "data/source-expressions.json", "gate1_dependency": True},
    {"kind": "other", "path": "data/case-contract.json", "gate1_dependency": True},
    {"kind": "evidence_bundle", "path": "data/evidence-bundle.json", "gate1_dependency": True, "gate1_required": True},
    {"kind": "investigation_log", "path": "data/investigation-log.json", "gate1_dependency": True, "gate1_required": True},
    {"kind": "review_html", "path": "review.html"},
    {"kind": "report_markdown", "path": "findings-report.md"},
    {"kind": "report_html", "path": "report.html"},
    {"kind": "evidence_map", "path": "evidence-map.json"},
]
ARTIFACT_PATH_KEYS = ("raw_path", "screenshot_path", "downloaded_document_path")
DEPENDENCY_STATUS_VERSION = "spotlight-gate1-dependencies/v1"
SIGNING_PROFILE = "spotlight"
# Noosphere caps the signed record at 5 MB and returns it twice (object and
# base64) next to the sidecar; anything far beyond that is not a receipt.
MAX_SIGNING_RESPONSE_BYTES = 32 * 1024 * 1024
# A response that passes every check is still not "signed" until the C2PA
# sidecar verifies locally against the pinned trust anchors (noosphere_verify).
RECEIPT_RECEIVED_UNVERIFIED = "received_unverified"
# The managed signer. NOOSPHERE_PROVENANCE_API_KEY is sent to this origin only.
MANAGED_ORIGIN = ("https", "platform.noosphere.tech")
API_KEY_ENV = "NOOSPHERE_PROVENANCE_API_KEY"
CONTRACT_MAJOR = "1"
HTTP_ERRORS = {
    401: "the signer rejected the API key (401); check NOOSPHERE_PROVENANCE_API_KEY",
    403: "the API key lacks the 'sign' scope (403)",
    413: "the manifest is larger than the signer accepts (413)",
    422: "the signer refused a manifest with no artifacts, claims or sources (422)",
    429: "the signer is rate limiting (429)",
    503: "the signer has no production certificate chain (503)",
}


class SigningResponseError(ValueError):
    """The signer answered, but the answer is not a usable signature."""


class SigningHTTPError(SigningResponseError):
    """The signer answered with an HTTP error status."""


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    # Noosphere never redirects; following one would resend X-API-Key elsewhere.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    with open(path, encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


canonical_hash = canonical_fingerprint


def rendered_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_immutable(path: Path, data: bytes) -> bool:
    """Create immutable history bytes, or idempotently accept identical bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"refusing to overwrite immutable history: {path}")
        return False


def case_relative(case_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(case_dir.resolve()))
    except ValueError as exc:
        raise ValueError(f"provenance history path escapes case directory: {path}") from exc


def artifact_entries(case_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for artifact in ARTIFACTS:
        path = case_dir / artifact["path"]
        if not path.exists():
            continue
        digest, size = sha256_file(path)
        entries.append({
            "kind": artifact["kind"],
            "path": artifact["path"],
            "sha256": digest,
            "bytes": size,
        })
    return entries


def dependency_entry(case_dir: Path, kind: str, relative: str) -> dict[str, Any]:
    case_root = case_dir.resolve()
    path = (case_root / relative).resolve()
    present = path.is_relative_to(case_root) and path.is_file()
    entry: dict[str, Any] = {"kind": kind, "path": relative, "present": present}
    if present:
        entry["sha256"], entry["bytes"] = sha256_file(path)
    return entry


def direct_evidence_paths(item: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for key in ARTIFACT_PATH_KEYS:
        relative = item.get(key)
        if isinstance(relative, str) and relative:
            yield key, relative


def evidence_dependency_paths(item: dict[str, Any]) -> Iterator[tuple[str, str]]:
    yield from direct_evidence_paths(item)
    derivatives = item.get("text_derivatives", []) or []
    if not isinstance(derivatives, list):
        raise ValueError("data/evidence-bundle.json text_derivatives must be an array")
    for derivative in derivatives:
        if not isinstance(derivative, dict):
            raise ValueError("data/evidence-bundle.json text_derivatives must contain objects")
        relative = derivative.get("path")
        if isinstance(relative, str) and relative:
            yield "text_derivative", relative

def gate1_dependency_snapshot(case_dir: Path) -> dict[str, Any]:
    dependencies = [artifact for artifact in ARTIFACTS if artifact.get("gate1_dependency")]
    entries = [
        dependency_entry(case_dir, artifact["kind"], artifact["path"])
        for artifact in dependencies
    ]
    required_missing = [
        artifact
        for artifact, entry in zip(dependencies, entries)
        if artifact.get("gate1_required") and not entry["present"]
    ]

    evidence_path = case_dir / "data/evidence-bundle.json"
    if evidence_path.is_file():
        evidence_bundle = load_json(evidence_path)
        referenced = []
        for item in evidence_bundle.get("items", []):
            if not isinstance(item, dict):
                raise ValueError("data/evidence-bundle.json items must be objects")
            for key, relative in evidence_dependency_paths(item):
                referenced.append(dependency_entry(case_dir, f"evidence_{key}", relative))
        entries.extend(sorted(referenced, key=lambda entry: (entry["path"], entry["kind"])))

    gate1_missing = [
        artifact["path"] for artifact in required_missing if artifact.get("gate1_authored")
    ]
    execution_missing = [
        artifact["path"] for artifact in required_missing if not artifact.get("gate1_authored")
    ]
    return {
        "schema_version": DEPENDENCY_STATUS_VERSION,
        "ready": not required_missing,
        "missing": [artifact["path"] for artifact in required_missing],
        "execution_ready": not execution_missing,
        "execution_missing": execution_missing,
        "gate1_missing": gate1_missing,
        "dependency_digest": canonical_hash(entries),
    }


def gate1_dependency_digest(case_dir: Path) -> str:
    return str(gate1_dependency_snapshot(case_dir)["dependency_digest"])


def fact_check_expression_ids(checked: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for side in ("evidence_for", "evidence_against"):
        for evidence in checked.get(side, []) or []:
            for ref in evidence.get("source_expression_refs", []) or []:
                expression_id = ref.get("expression_id")
                if expression_id:
                    ids.add(str(expression_id))
    return ids


def claim_entries(
    findings: dict[str, Any],
    fact_check: dict[str, Any],
    source_expressions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    verdict_by_finding = {
        str(claim.get("finding_id")): claim
        for claim in fact_check_rows(fact_check)
        if claim.get("finding_id")
    }
    expressions_by_id = {
        str(expression.get("id")): expression
        for expression in (source_expressions or {}).get("expressions", [])
        if expression.get("id")
    }

    claims = []
    for finding in findings.get("findings", []):
        finding_id = str(finding.get("id", ""))
        checked = verdict_by_finding.get(finding_id, {})
        grounding = finding.get("grounding", {}) or {}
        checked_grounding = checked.get("grounding_assessment", {}) or {}
        entry = {
            "finding_id": finding_id,
            "claim_text": finding.get("claim") or checked.get("claim_text") or "",
            "confidence": finding.get("confidence", "unknown"),
            "fact_check_verdict": checked.get("verdict", "missing"),
            "support_type": (
                checked_grounding.get("support_type")
                or grounding.get("support_type")
                or "unknown"
            ),
            "evidence_refs": finding.get("evidence_bundle_refs", []),
        }
        if source_expressions is not None:
            finding_fingerprint = finding.get("finding_fingerprint")
            if not finding_fingerprint:
                raise ValueError(f"activated finding {finding_id} has no finding_fingerprint")
            entry["finding_fingerprint"] = finding_fingerprint
            entry["fact_check_fingerprint"] = canonical_hash(checked)
            expression_refs = []
            for expression_id in sorted(fact_check_expression_ids(checked)):
                expression = expressions_by_id.get(expression_id)
                if expression is None:
                    raise ValueError(
                        f"claim {finding_id} references missing source expression {expression_id}"
                    )
                if lifecycle_state(expression) != "activated":
                    raise ValueError(
                        f"claim {finding_id} references inactive source expression {expression_id}"
                    )
                links = [
                    link for link in expression.get("finding_links", [])
                    if str(link.get("finding_id")) == finding_id
                ]
                if len(links) != 1:
                    raise ValueError(
                        f"source expression {expression_id} must have exactly one link to {finding_id}"
                    )
                link = links[0]
                expression_refs.append({
                    "expression_id": expression_id,
                    "expression_fingerprint": expression["expression_fingerprint"],
                    "finding_fingerprint": link["finding_fingerprint"],
                    "relation": link["relation"],
                    "link_fingerprint": link["link_fingerprint"],
                    "anchor_sha256": expression["anchor_sha256"],
                    "original_evidence_bundle_id": expression["original_evidence_bundle_id"],
                    "original_artifact_sha256": expression["original_artifact_sha256"],
                })
            entry["source_expressions"] = expression_refs
        claims.append(entry)
    return claims


def source_entries(
    evidence_bundle: dict[str, Any], findings: dict[str, Any], case_dir: Path
) -> list[dict[str, Any]]:
    archive_by_url = {}
    for finding in findings.get("findings", []):
        for source in finding.get("sources", []):
            url = source.get("url")
            if url:
                archive_by_url[url] = source.get("archive_url", "")

    sources = []
    for item in evidence_bundle.get("items", []):
        url = item.get("source_url", "")
        entry = {
            "evidence_id": item.get("id", ""),
            "source_url": url,
            "accessed": item.get("accessed", ""),
            "acquisition_method": item.get("acquisition_method", ""),
            "human_verification_required": bool(item.get("human_verification_required", False)),
            "claim_links": item.get("claim_links", []),
        }
        for key in ("sha256", *ARTIFACT_PATH_KEYS):
            if item.get(key):
                entry[key] = item[key]
        for key, rel in direct_evidence_paths(item):
            artifact_path = (case_dir / rel).resolve()
            if artifact_path.is_file() and artifact_path.is_relative_to(case_dir):
                digest, size = sha256_file(artifact_path)
                entry[f"{key}_sha256"] = digest
                entry[f"{key}_bytes"] = size
        if archive_by_url.get(url):
            entry["archive_url"] = archive_by_url[url]
        sources.append(entry)
    return sources


def manifest_body(
    case_dir: Path,
    findings: dict[str, Any],
    fact_check: dict[str, Any],
    evidence_bundle: dict[str, Any],
    credential_id: str | None,
    endpoint: str | None,
    source_expressions: dict[str, Any] | None = None,
    case_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project = (
        findings.get("project")
        or fact_check.get("project")
        or evidence_bundle.get("project")
        or case_dir.name
    )
    return {
        "project": project,
        "generated_at": now_iso(),
        "status": "unsigned",
        "signing": {
            "profile": "noosphere-c2pa",
            "requires_api_key": True,
            "requires_signing_credential": True,
            "credential_id": credential_id,
            "endpoint": endpoint,
        },
        "case_artifacts": (
            case_artifacts if case_artifacts is not None else artifact_entries(case_dir)
        ),
        "claims": claim_entries(findings, fact_check, source_expressions),
        "sources": source_entries(evidence_bundle, findings, case_dir),
    }


def load_case(case_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        load_json(case_dir / "data/findings.json"),
        load_json(case_dir / "data/fact-check.json"),
        load_json(case_dir / "data/evidence-bundle.json"),
    )


def require_valid_activated_case(case_dir: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "validate-case.py"), str(case_dir), "--fact-check-only"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"activated case validation failed: {detail}")


def build_manifest(
    case_dir: Path, credential_id: str | None, endpoint: str | None
) -> dict[str, Any]:
    """Compatibility API: build an in-memory legacy manifest."""
    findings, fact_check, evidence_bundle = load_case(case_dir)
    return {
        "schema_version": "1.0",
        "input_set_hash": gate1_dependency_digest(case_dir),
        **manifest_body(
            case_dir, findings, fact_check, evidence_bundle, credential_id, endpoint
        ),
    }


def is_managed_endpoint(endpoint: str) -> bool:
    parsed = urllib.parse.urlsplit(endpoint)
    return (parsed.scheme, parsed.hostname) == MANAGED_ORIGIN and parsed.port in (None, 443)


def http_error_message(exc: urllib.error.HTTPError) -> str:
    if 300 <= exc.code < 400:
        return f"the signer redirected ({exc.code}); redirects are refused so the API key is never resent"
    message = HTTP_ERRORS.get(exc.code, f"the signer returned HTTP {exc.code}")
    retry_after = exc.headers.get("Retry-After") if exc.code == 429 and exc.headers else None
    if retry_after and retry_after.isdigit():
        message += f"; retry after {retry_after} s"
    return message


def post_for_signing(
    endpoint: str,
    manifest: dict[str, Any],
    artifact_path: str | None,
    credential_id: str | None,
) -> dict[str, Any]:
    api_key = os.environ.get(API_KEY_ENV)
    if api_key and not is_managed_endpoint(endpoint):
        # SIGN-05: the managed key never reaches a user-supplied origin.
        raise SigningResponseError(
            f"{API_KEY_ENV} is only sent to https://platform.noosphere.tech; "
            "unset it to use a custom signer"
        )
    payload = {
        # The signer records the request under this product profile and echoes it
        # back; without it the record falls back to the generic profile.
        "profile": SIGNING_PROFILE,
        "artifact_path": artifact_path,
        "provenance_manifest": manifest,
        "credential_id": credential_id,
    }
    headers = {"Content-Type": "application/json", "User-Agent": "Spotlight-C2PA/1.1"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    opener = urllib.request.build_opener(_RefuseRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(MAX_SIGNING_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SigningHTTPError(http_error_message(exc)) from None
    if len(body) > MAX_SIGNING_RESPONSE_BYTES:
        raise SigningResponseError(f"signing response exceeds {MAX_SIGNING_RESPONSE_BYTES} bytes")
    result = json.loads(body.decode("utf-8"))
    if not isinstance(result, dict):
        raise json.JSONDecodeError("signing receipt must be a JSON object", "", 0)
    problems = signing_response_problems(result, manifest)
    if problems:
        raise SigningResponseError("signing response rejected: " + "; ".join(problems))
    return result


def strict_base64(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True) or None
    except binascii.Error:
        return None


def signing_response_problems(response: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Check a signer response against the Noosphere 1.x contract and the
    manifest that was sent. An empty list means structurally sound, not
    cryptographically verified."""
    problems = []
    if str(response.get("contract_version", "")).split(".")[0] != CONTRACT_MAJOR:
        problems.append(f"contract_version is {response.get('contract_version')!r}, not {CONTRACT_MAJOR}.x")
    if response.get("status") != "signed":
        problems.append(f"status is {response.get('status')!r}, not 'signed'")
    if response.get("profile") != SIGNING_PROFILE:
        problems.append(f"profile is {response.get('profile')!r}, not {SIGNING_PROFILE!r}")
    if response.get("input_set_hash") != manifest.get("input_set_hash"):
        problems.append("input_set_hash does not match the manifest that was sent")
    for field, kind in (("manifest_id", str), ("signed_at", str), ("signer", dict), ("record", dict)):
        if not isinstance(response.get(field), kind):
            problems.append(f"{field} is missing or not a {kind.__name__}")

    record_bytes = strict_base64(response.get("record_b64"))
    if record_bytes is None:
        problems.append("record_b64 is missing or not valid base64")
    else:
        content_hash = str(response.get("content_hash", "")).removeprefix("sha256:")
        if content_hash != hashlib.sha256(record_bytes).hexdigest():
            problems.append("content_hash does not match the decoded record_b64 bytes")
        try:
            decoded_record = json.loads(record_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded_record = None
        if decoded_record != response.get("record"):
            problems.append("record_b64 does not decode to the returned record")

    if strict_base64(response.get("c2pa_manifest_b64", response.get("c2pa_manifest"))) is None:
        problems.append("C2PA sidecar is missing or not valid base64")
    chain = response.get("certificate_chain")
    grade = chain.get("grade") if isinstance(chain, dict) else None
    if grade != "production":
        problems.append(f"certificate chain grade is {grade!r}, not 'production'")
    if isinstance(response.get("record"), dict):
        problems.extend(projection_problems(response["record"], manifest))
    return problems


def projection_problems(record: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Every artifact, claim and source that was sent must appear in the signed
    record unchanged (HASHING.md section 5; PRD REC-11)."""
    def missing(sent: set, signed: set, label: str) -> list[str]:
        lost = sent - signed
        return [f"{len(lost)} {label} missing or changed in the signed record"] if lost else []

    def rows(value: Any) -> list[dict[str, Any]]:
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    sent_sources = rows(manifest.get("sources"))
    return (
        missing(
            {(a.get("path"), a.get("sha256"), a.get("bytes")) for a in rows(manifest.get("case_artifacts"))},
            {(a.get("path"), a.get("sha256"), a.get("bytes")) for a in rows(record.get("inventory"))},
            "artifact(s)",
        )
        + missing(
            {(c.get("finding_id"), c.get("claim_text"), c.get("fact_check_verdict")) for c in rows(manifest.get("claims"))},
            {(c.get("id"), c.get("text"), c.get("verdict")) for c in rows(record.get("claims"))},
            "claim(s)",
        )
        + missing(
            {(s.get("evidence_id"), s.get("source_url"), s.get("sha256")) for s in sent_sources},
            {(s.get("id"), s.get("url"), s.get("hash")) for s in rows(record.get("sources"))}
            | {(s.get("id"), s.get("url"), None) for s in rows(record.get("sources"))},
            "source(s)",
        )
    )


def sensitive_mode() -> bool:
    """Spotlight sensitive mode (AGENTS.md frontmatter or SPOTLIGHT_SENSITIVE)
    keeps every byte local, so remote signing is blocked (PRD PRIV-04)."""
    if os.environ.get("SPOTLIGHT_SENSITIVE", "").lower() == "true":
        return True
    agents = SCRIPT_DIR.parent / "AGENTS.md"
    if not agents.is_file():
        return False
    head = agents.read_text(encoding="utf-8").split("---", 2)
    return len(head) > 2 and any(line.strip() == "sensitive: true" for line in head[1].splitlines())


def read_pointer(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = load_json(path)
    if value.get("kind") != "provenance_current_pointer":
        return None
    return value


def current_input_hash(case_dir: Path) -> str:
    return gate1_dependency_digest(case_dir)


def check_current(case_dir: Path, output: Path) -> int:
    current = load_json(output)
    if current.get("kind") == "provenance_current_pointer":
        actual = current_input_hash(case_dir)
        revision = case_dir / str(current.get("revision_path", ""))
        revision_hash = sha256_file(revision)[0] if revision.is_file() else None
        is_current = (
            actual == current.get("input_set_hash")
            and revision_hash == current.get("revision_sha256")
        )
        desired = "current" if is_current else "stale"
        if current.get("derived_status") != desired:
            current["derived_status"] = desired
            current["updated_at"] = now_iso()
            atomic_replace(output, rendered_bytes(current))
        print(desired)
        return 0 if is_current else 1

    expected_hash = current.get("input_set_hash")
    if isinstance(expected_hash, str):
        is_current = expected_hash == current_input_hash(case_dir)
    else:
        expected = {
            item["path"]: item["sha256"] for item in current.get("case_artifacts", [])
        }
        actual = {item["path"]: item["sha256"] for item in artifact_entries(case_dir)}
        is_current = expected == actual
    status = "current" if is_current else "stale"
    print(status)
    return 0 if is_current else 1


def build_revision(
    case_dir: Path,
    output: Path,
    findings: dict[str, Any],
    fact_check: dict[str, Any],
    evidence_bundle: dict[str, Any],
    credential_id: str | None,
    endpoint: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expressions = load_json(case_dir / "data/source-expressions.json")
    artifacts = artifact_entries(case_dir)
    input_set_hash = gate1_dependency_digest(case_dir)
    previous = read_pointer(output)
    if previous and previous.get("input_set_hash") == input_set_hash:
        revision_path = case_dir / previous["revision_path"]
        revision = load_json(revision_path)
        if sha256_file(revision_path)[0] != previous.get("revision_sha256"):
            raise ValueError(f"immutable provenance revision was modified: {revision_path}")
        previous["derived_status"] = "current"
        previous["updated_at"] = now_iso()
        atomic_replace(output, rendered_bytes(previous))
        return revision, previous

    parent_revision_id = previous.get("revision_id") if previous else None
    parent_input_set_hash = previous.get("input_set_hash") if previous else None
    revision_id = "PM-" + canonical_hash({
        "input_set_hash": input_set_hash,
        "parent_revision_id": parent_revision_id,
    })
    revision_path = case_dir / "data" / "provenance-manifests" / f"{revision_id}.json"
    revision = {
        "schema_version": "1.1",
        "kind": "provenance_manifest_revision",
        "revision_id": revision_id,
        "input_set_hash": input_set_hash,
        "parent_revision_id": parent_revision_id,
        "parent_input_set_hash": parent_input_set_hash,
        **manifest_body(
            case_dir,
            findings,
            fact_check,
            evidence_bundle,
            credential_id,
            endpoint,
            expressions,
            artifacts,
        ),
    }
    revision_bytes = rendered_bytes(revision)
    write_immutable(revision_path, revision_bytes)
    pointer = {
        "schema_version": "1.1",
        "kind": "provenance_current_pointer",
        "project": revision["project"],
        "revision_id": revision_id,
        "revision_path": case_relative(case_dir, revision_path),
        "revision_sha256": hashlib.sha256(revision_bytes).hexdigest(),
        "input_set_hash": input_set_hash,
        "derived_status": "current",
        "signing_status": "unsigned",
        "updated_at": now_iso(),
    }
    atomic_replace(output, rendered_bytes(pointer))
    return revision, pointer


def record_signing_failure(
    case_dir: Path,
    pointer: dict[str, Any],
    endpoint: str,
    credential_id: str | None,
    error: str,
) -> None:
    attempt = {
        "schema_version": "1.0",
        "revision_id": pointer["revision_id"],
        "endpoint": endpoint,
        "credential_id_provided": credential_id is not None,
        "api_key_provided": bool(os.environ.get(API_KEY_ENV)),
        "status": "signing_failed",
        "error": error,
    }
    attempt_id = canonical_hash(attempt)
    path = (
        case_dir / "data" / "provenance-signing-attempts"
        / f"{pointer['revision_id']}-{attempt_id}.json"
    )
    write_immutable(path, rendered_bytes(attempt))
    pointer.update({
        "signing_status": "signing_failed",
        "attempt_path": case_relative(case_dir, path),
        "error": error,
        "updated_at": now_iso(),
    })


def sign_revision(
    case_dir: Path,
    pointer: dict[str, Any],
    revision: dict[str, Any],
    endpoint: str,
    artifact: str | None,
    credential_id: str | None,
    receipt_output: str | None,
) -> None:
    if (
        pointer.get("signing_status") == "signed"
        or pointer.get("receipt_status") == RECEIPT_RECEIVED_UNVERIFIED
    ):
        return
    try:
        receipt = post_for_signing(endpoint, revision, artifact, credential_id)
        receipt_bytes = rendered_bytes(receipt)
        if receipt_output:
            receipt_path = Path(receipt_output).resolve()
            case_relative(case_dir, receipt_path)
        else:
            receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
            receipt_path = (
                case_dir / "data" / "provenance-signing-receipts"
                / f"{pointer['revision_id']}-{receipt_hash}.json"
            )
        write_immutable(receipt_path, receipt_bytes)
        verification = noosphere_verify.verify_sidecar(receipt)
        pointer.update({
            "receipt_path": case_relative(case_dir, receipt_path),
            "verification": verification,
            "updated_at": now_iso(),
        })
        if verification["verified"]:
            pointer["signing_status"] = "signed"
            pointer.pop("receipt_status", None)
        else:
            pointer["signing_status"] = "unsigned"
            pointer["receipt_status"] = RECEIPT_RECEIVED_UNVERIFIED
        pointer.pop("attempt_path", None)
        pointer.pop("error", None)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        record_signing_failure(
            case_dir,
            pointer,
            endpoint,
            credential_id,
            f"{type(exc).__name__}: {exc}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to {CASE_DIR}")
    parser.add_argument(
        "--output", help="Output path. Defaults to {CASE_DIR}/data/provenance-manifest.json"
    )
    parser.add_argument("--credential-id", default=None, help="Noosphere signing credential id")
    parser.add_argument("--sign-endpoint", default=None, help="Optional Noosphere C2PA signing endpoint")
    parser.add_argument("--artifact", default=None, help="Optional artifact path to sign, e.g. review.html")
    parser.add_argument("--receipt-output", default=None, help="Optional path for signing receipt JSON")
    parser.add_argument(
        "--check-current",
        action="store_true",
        help="Check whether the current manifest still matches case inputs; mark an activated pointer stale on mismatch",
    )
    parser.add_argument(
        "--dependency-digest",
        action="store_true",
        help="Print the registry-owned Gate 1 dependency status without building a manifest",
    )
    args = parser.parse_args()

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        print(f"case directory not found: {case_dir}", file=sys.stderr)
        return 2
    if args.dependency_digest:
        try:
            print(json.dumps(gate1_dependency_snapshot(case_dir), sort_keys=True))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"cannot hash Gate 1 dependencies: {exc}", file=sys.stderr)
            return 2
        return 0
    output = Path(args.output).resolve() if args.output else case_dir / "data/provenance-manifest.json"
    if args.check_current:
        if not output.is_file():
            print(f"current provenance manifest not found: {output}", file=sys.stderr)
            return 2
        return check_current(case_dir, output)

    if args.sign_endpoint and sensitive_mode():
        print("sensitive mode: remote signing is blocked; building the unsigned manifest only", file=sys.stderr)
        args.sign_endpoint = None
    if args.sign_endpoint:
        from spotlight_safe import SafetyError, validate_url

        try:
            validate_url(args.sign_endpoint)
        except SafetyError as exc:
            print(f"invalid --sign-endpoint: {exc}", file=sys.stderr)
            return 2

    try:
        findings, fact_check, evidence_bundle = load_case(case_dir)
        activated = findings.get("schema_version") == "1.1"
        if activated:
            if not output.is_file():
                require_valid_activated_case(case_dir)
            revision, pointer = build_revision(
                case_dir,
                output,
                findings,
                fact_check,
                evidence_bundle,
                args.credential_id,
                args.sign_endpoint,
            )
            if args.sign_endpoint:
                sign_revision(
                    case_dir,
                    pointer,
                    revision,
                    args.sign_endpoint,
                    args.artifact,
                    args.credential_id,
                    args.receipt_output,
                )
                atomic_replace(output, rendered_bytes(pointer))
        else:
            manifest = {
                "schema_version": "1.0",
                "input_set_hash": gate1_dependency_digest(case_dir),
                **manifest_body(
                    case_dir,
                    findings,
                    fact_check,
                    evidence_bundle,
                    args.credential_id,
                    args.sign_endpoint,
                ),
            }
            if args.sign_endpoint:
                receipt_path = (
                    Path(args.receipt_output).resolve()
                    if args.receipt_output
                    else case_dir / "data/provenance-signing-receipt.json"
                )
                try:
                    receipt = post_for_signing(
                        args.sign_endpoint, manifest, args.artifact, args.credential_id
                    )
                    atomic_replace(receipt_path, rendered_bytes(receipt))
                    verification = noosphere_verify.verify_sidecar(receipt)
                    manifest["signing"]["receipt_path"] = str(receipt_path.relative_to(case_dir))
                    manifest["signing"]["verification"] = verification
                    if verification["verified"]:
                        manifest["status"] = "signed"
                        manifest["signing"]["signed_at"] = now_iso()
                    else:
                        manifest["signing"]["receipt_status"] = RECEIPT_RECEIVED_UNVERIFIED
                except (
                    urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, SigningResponseError
                ) as exc:
                    manifest["status"] = "signing_failed"
                    manifest["signing"]["error"] = f"{type(exc).__name__}: {exc}"
            atomic_replace(output, rendered_bytes(manifest))
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"cannot build provenance manifest: {exc}", file=sys.stderr)
        return 2

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
