#!/usr/bin/env python3
"""Build a Spotlight provenance manifest for optional Noosphere C2PA signing.

Legacy cases retain the mutable 1.0 manifest. Source-expression-aware cases use
immutable 1.1 revision files plus a derived ``provenance-manifest.json`` pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_expression_contract import canonical_fingerprint, fact_check_rows, lifecycle_state


ARTIFACTS = [
    ("summary", "summary.md"),
    ("summary_json", "data/summary.json"),
    ("findings", "data/findings.json"),
    ("fact_check", "data/fact-check.json"),
    ("source_expressions", "data/source-expressions.json"),
    ("evidence_bundle", "data/evidence-bundle.json"),
    ("investigation_log", "data/investigation-log.json"),
    ("review_html", "review.html"),
    ("report_markdown", "findings-report.md"),
    ("report_html", "report.html"),
    ("evidence_map", "evidence-map.json"),
]
ARTIFACT_PATH_KEYS = ("raw_path", "screenshot_path", "downloaded_document_path")


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
    for kind, rel in ARTIFACTS:
        path = case_dir / rel
        if not path.exists():
            continue
        digest, size = sha256_file(path)
        entries.append({"kind": kind, "path": rel, "sha256": digest, "bytes": size})
    return entries


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
        for key in ARTIFACT_PATH_KEYS:
            rel = item.get(key)
            if not rel:
                continue
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
        **manifest_body(
            case_dir, findings, fact_check, evidence_bundle, credential_id, endpoint
        ),
    }


def post_for_signing(
    endpoint: str,
    manifest: dict[str, Any],
    artifact_path: str | None,
    credential_id: str | None,
    api_key: str | None = None,
) -> dict[str, Any]:
    payload = {
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
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise json.JSONDecodeError("signing receipt must be a JSON object", "", 0)
    return result


def read_pointer(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = load_json(path)
    if value.get("kind") != "provenance_current_pointer":
        return None
    return value


def current_input_hash(case_dir: Path) -> str:
    return canonical_hash(artifact_entries(case_dir))


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

    expected = {
        item["path"]: item["sha256"] for item in current.get("case_artifacts", [])
    }
    actual = {item["path"]: item["sha256"] for item in artifact_entries(case_dir)}
    status = "current" if expected == actual else "stale"
    print(status)
    return 0 if status == "current" else 1


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
    input_set_hash = canonical_hash(artifacts)
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
    api_key: str | None = None,
) -> None:
    attempt = {
        "schema_version": "1.0",
        "revision_id": pointer["revision_id"],
        "endpoint": endpoint,
        "credential_id_provided": credential_id is not None,
        "api_key_provided": bool(api_key),
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
    api_key: str | None = None,
) -> None:
    if pointer.get("signing_status") == "signed":
        return
    try:
        receipt = post_for_signing(endpoint, revision, artifact, credential_id, api_key)
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
        pointer.update({
            "signing_status": "signed",
            "receipt_path": case_relative(case_dir, receipt_path),
            "updated_at": now_iso(),
        })
        pointer.pop("attempt_path", None)
        pointer.pop("error", None)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        record_signing_failure(
            case_dir,
            pointer,
            endpoint,
            credential_id,
            f"{type(exc).__name__}: {exc}",
            api_key,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to {CASE_DIR}")
    parser.add_argument(
        "--output", help="Output path. Defaults to {CASE_DIR}/data/provenance-manifest.json"
    )
    parser.add_argument("--credential-id", default=None, help="Noosphere signing credential id")
    parser.add_argument("--sign-endpoint", default=None, help="Optional Noosphere C2PA signing endpoint")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("NOOSPHERE_PROVENANCE_API_KEY"),
        help="Noosphere signing API key (X-API-Key). Defaults to $NOOSPHERE_PROVENANCE_API_KEY",
    )
    parser.add_argument("--artifact", default=None, help="Optional artifact path to sign, e.g. review.html")
    parser.add_argument("--receipt-output", default=None, help="Optional path for signing receipt JSON")
    parser.add_argument(
        "--check-current",
        action="store_true",
        help="Check whether the current manifest still matches case inputs; mark an activated pointer stale on mismatch",
    )
    args = parser.parse_args()

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        print(f"case directory not found: {case_dir}", file=sys.stderr)
        return 2
    output = Path(args.output).resolve() if args.output else case_dir / "data/provenance-manifest.json"
    if args.check_current:
        if not output.is_file():
            print(f"current provenance manifest not found: {output}", file=sys.stderr)
            return 2
        return check_current(case_dir, output)

    if args.sign_endpoint:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
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
                    args.api_key,
                )
                atomic_replace(output, rendered_bytes(pointer))
        else:
            manifest = {
                "schema_version": "1.0",
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
                        args.sign_endpoint, manifest, args.artifact, args.credential_id, args.api_key
                    )
                    atomic_replace(receipt_path, rendered_bytes(receipt))
                    manifest["status"] = "signed"
                    manifest["signing"]["signed_at"] = now_iso()
                    manifest["signing"]["receipt_path"] = str(receipt_path.relative_to(case_dir))
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
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
