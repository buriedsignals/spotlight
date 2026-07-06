#!/usr/bin/env python3
"""Build a Spotlight provenance manifest for optional Noosphere C2PA signing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACTS = [
    ("summary", "summary.md"),
    ("summary_json", "data/summary.json"),
    ("findings", "data/findings.json"),
    ("fact_check", "data/fact-check.json"),
    ("evidence_bundle", "data/evidence-bundle.json"),
    ("investigation_log", "data/investigation-log.json"),
    ("review_html", "review.html")
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def artifact_entries(case_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for kind, rel in ARTIFACTS:
        path = case_dir / rel
        if not path.exists():
            continue
        digest, size = sha256_file(path)
        entries.append({
            "kind": kind,
            "path": rel,
            "sha256": digest,
            "bytes": size,
        })
    return entries


def claim_entries(findings: dict[str, Any], fact_check: dict[str, Any]) -> list[dict[str, Any]]:
    verdict_by_finding = {
        str(claim.get("finding_id")): claim
        for claim in fact_check.get("claims", [])
        if claim.get("finding_id")
    }

    claims = []
    for finding in findings.get("findings", []):
        finding_id = str(finding.get("id", ""))
        checked = verdict_by_finding.get(finding_id, {})
        grounding = finding.get("grounding", {}) or {}
        checked_grounding = checked.get("grounding_assessment", {}) or {}
        claims.append({
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
        })
    return claims


# Path-valued evidence_item fields whose referenced files we hash so the captured
# artifact bytes (e.g. Tier-0 MHTML + full-page screenshot) are tamper-evident inside
# the signed manifest, not just the item's self-reported sha256.
ARTIFACT_PATH_KEYS = ("raw_path", "screenshot_path", "downloaded_document_path")


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
        # Hash the referenced artifact files (case-relative). Emit <key>_sha256/_bytes so
        # the actual captured bytes are covered by the signature. Missing files are skipped
        # (path is still recorded) — a stale path must not break manifest generation.
        for key in ARTIFACT_PATH_KEYS:
            rel = item.get(key)
            if not rel:
                continue
            artifact_path = (case_dir / rel).resolve()
            if artifact_path.is_file():
                digest, size = sha256_file(artifact_path)
                entry[f"{key}_sha256"] = digest
                entry[f"{key}_bytes"] = size
        if archive_by_url.get(url):
            entry["archive_url"] = archive_by_url[url]
        sources.append(entry)
    return sources


def build_manifest(case_dir: Path, credential_id: str | None, endpoint: str | None) -> dict[str, Any]:
    findings = load_json(case_dir / "data/findings.json")
    fact_check = load_json(case_dir / "data/fact-check.json")
    evidence_bundle = load_json(case_dir / "data/evidence-bundle.json")

    project = (
        findings.get("project")
        or fact_check.get("project")
        or evidence_bundle.get("project")
        or case_dir.name
    )

    return {
        "schema_version": "1.0",
        "project": project,
        "generated_at": now_iso(),
        "status": "unsigned",
        "signing": {
            "profile": "noosphere-c2pa",
            "requires_api_key": False,
            "requires_signing_credential": True,
            "credential_id": credential_id,
            "endpoint": endpoint,
        },
        "case_artifacts": artifact_entries(case_dir),
        "claims": claim_entries(findings, fact_check),
        "sources": source_entries(evidence_bundle, findings, case_dir),
    }


def post_for_signing(
    endpoint: str,
    manifest: dict[str, Any],
    artifact_path: str | None,
    credential_id: str | None,
) -> dict[str, Any]:
    payload = {
        "artifact_path": artifact_path,
        "provenance_manifest": manifest,
        "credential_id": credential_id,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Spotlight-C2PA/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to {CASE_DIR}")
    parser.add_argument(
        "--output",
        help="Output path. Defaults to {CASE_DIR}/data/provenance-manifest.json",
    )
    parser.add_argument("--credential-id", default=None, help="Noosphere signing credential id")
    parser.add_argument("--sign-endpoint", default=None, help="Optional Noosphere C2PA signing endpoint")
    parser.add_argument("--artifact", default=None, help="Optional artifact path to sign, e.g. review.html")
    parser.add_argument("--receipt-output", default=None, help="Optional path for signing receipt JSON")
    args = parser.parse_args()

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        print(f"case directory not found: {case_dir}", file=sys.stderr)
        return 2

    if args.sign_endpoint:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from spotlight_safe import SafetyError, validate_url

        try:
            validate_url(args.sign_endpoint)
        except SafetyError as exc:
            print(f"invalid --sign-endpoint: {exc}", file=sys.stderr)
            return 2

    output = Path(args.output).resolve() if args.output else case_dir / "data/provenance-manifest.json"
    try:
        manifest = build_manifest(case_dir, args.credential_id, args.sign_endpoint)
    except FileNotFoundError as exc:
        print(f"missing required case file: {exc}", file=sys.stderr)
        return 2

    if args.sign_endpoint:
        receipt_path = (
            Path(args.receipt_output).resolve()
            if args.receipt_output
            else case_dir / "data/provenance-signing-receipt.json"
        )
        try:
            receipt = post_for_signing(args.sign_endpoint, manifest, args.artifact, args.credential_id)
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            manifest["status"] = "signed"
            manifest["signing"]["signed_at"] = now_iso()
            manifest["signing"]["receipt_path"] = str(receipt_path.relative_to(case_dir))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            manifest["status"] = "signing_failed"
            manifest["signing"]["error"] = f"{type(exc).__name__}: {exc}"

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
