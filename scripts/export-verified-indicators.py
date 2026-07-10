#!/usr/bin/env python3
"""Export explicit, fact-checked technical indicators from a Spotlight case."""

from __future__ import annotations

import argparse
import csv
import ipaddress
import io
import json
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ALLOWED_TYPES = {
    "ipv4",
    "ipv6",
    "domain",
    "url",
    "md5",
    "sha1",
    "sha256",
    "bitcoin",
    "ethereum",
}
HASH_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64}
BASE58_RE = re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$")
BECH32_RE = re.compile(r"^(?:bc1|tb1)[ac-hj-np-z02-9]{11,71}$", re.IGNORECASE)
ETHEREUM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
# Producer-owned namespace for deterministic UUIDv5 identifiers. STIX 2.1
# forbids its reserved SCO namespace for UUIDv5 SDO and Bundle identifiers.
SPOTLIGHT_STIX_NAMESPACE = uuid.UUID("f8c4c4c5-93d5-4b65-8b87-6d2a914e92ef")


class ExportError(ValueError):
    """A deterministic validation or boundary failure."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export only explicit indicators whose linked finding is fully verified."
    )
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--fact-check", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "csv", "stix"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"{label} must be a JSON object")
    return value


def case_paths(findings: Path, fact_check: Path, output: Path) -> tuple[Path, Path, Path, Path]:
    findings = findings.resolve(strict=True)
    fact_check = fact_check.resolve(strict=True)
    if findings.parent != fact_check.parent or findings.parent.name != "data":
        raise ExportError("findings and fact-check must be in the same CASE_DIR/data directory")
    case_dir = findings.parent.parent
    output = output.resolve(strict=False)
    try:
        output.relative_to(case_dir)
    except ValueError as exc:
        raise ExportError("output must remain inside the same CASE_DIR") from exc
    if output in {findings, fact_check}:
        raise ExportError("output cannot overwrite an input file")
    return case_dir, findings, fact_check, output


def normalize_domain(value: str) -> str:
    candidate = value.rstrip(".")
    if not candidate or "://" in candidate or "/" in candidate or "@" in candidate:
        raise ExportError("invalid domain value")
    try:
        ascii_domain = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ExportError("invalid internationalized domain") from exc
    if len(ascii_domain) > 253:
        raise ExportError("domain exceeds 253 characters")
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        raise ExportError("invalid domain labels")
    return ascii_domain


def normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        username = parts.username
        password = parts.password
    except ValueError as exc:
        raise ExportError("invalid URL") from exc
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        raise ExportError("URL must use http or https and include a hostname")
    if username is not None or password is not None:
        raise ExportError("URL credentials are not exportable")
    if parts.query or parts.fragment:
        raise ExportError("URL query strings and fragments are not exportable in V1")
    host = normalize_domain(hostname) if not _is_ip(hostname) else hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    try:
        port = parts.port
    except ValueError as exc:
        raise ExportError("invalid URL port") from exc
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, parts.fragment))


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_indicator(kind: str, value: str) -> str:
    if not isinstance(kind, str) or kind not in ALLOWED_TYPES:
        raise ExportError(f"indicator type {kind!r} is not allowed")
    if not isinstance(value, str) or not value.strip():
        raise ExportError("indicator value must be a non-empty string")
    value = value.strip()
    if kind in {"ipv4", "ipv6"}:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ExportError(f"invalid {kind} address") from exc
        expected_version = 4 if kind == "ipv4" else 6
        if address.version != expected_version:
            raise ExportError(f"value does not match type {kind}")
        return address.compressed
    if kind == "domain":
        return normalize_domain(value)
    if kind == "url":
        return normalize_url(value)
    if kind in HASH_LENGTHS:
        if len(value) != HASH_LENGTHS[kind] or not HEX_RE.fullmatch(value):
            raise ExportError(f"invalid {kind} hash")
        return value.lower()
    if kind == "bitcoin":
        if not (BASE58_RE.fullmatch(value) or BECH32_RE.fullmatch(value)):
            raise ExportError("invalid Bitcoin address shape")
        return value.lower() if value.lower().startswith(("bc1", "tb1")) else value
    if kind == "ethereum":
        if not ETHEREUM_RE.fullmatch(value):
            raise ExportError("invalid Ethereum address shape")
        return value.lower()
    raise AssertionError("unreachable indicator type")


def verified_findings(fact_check: dict) -> set[str]:
    claims = fact_check.get("claims", [])
    if not isinstance(claims, list):
        raise ExportError("fact-check claims must be a list")
    verdicts: dict[str, list[str]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ExportError(f"fact-check claim {index} must be an object")
        finding_id = claim.get("finding_id")
        verdict = claim.get("verdict")
        if isinstance(finding_id, str) and finding_id:
            verdicts.setdefault(finding_id, []).append(verdict)
    return {
        finding_id
        for finding_id, linked_verdicts in verdicts.items()
        if linked_verdicts and all(verdict == "verified" for verdict in linked_verdicts)
    }


def technical_indicator_claims(fact_check: dict) -> dict[str, list[dict]]:
    """Index explicit claim-to-indicator links, rejecting malformed mappings."""
    claims = fact_check.get("claims", [])
    if not isinstance(claims, list):
        raise ExportError("fact-check claims must be a list")
    links: dict[str, list[dict]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ExportError(f"fact-check claim {index} must be an object")
        indicator_ids = claim.get("technical_indicator_ids", [])
        if not isinstance(indicator_ids, list) or not all(
            isinstance(indicator_id, str) and indicator_id for indicator_id in indicator_ids
        ):
            raise ExportError(
                f"fact-check claim {index} technical_indicator_ids must be a list of non-empty strings"
            )
        if len(indicator_ids) != len(set(indicator_ids)):
            raise ExportError(f"fact-check claim {index} contains duplicate technical_indicator_ids")
        for indicator_id in indicator_ids:
            links.setdefault(indicator_id, []).append(claim)
    return links


def collect_indicators(findings: dict, fact_check: dict) -> list[dict]:
    if findings.get("project") != fact_check.get("project"):
        raise ExportError("findings and fact-check project values differ")
    finding_rows = findings.get("findings", [])
    if not isinstance(finding_rows, list):
        raise ExportError("findings must be a list")
    findings_by_id: dict[str, dict] = {}
    for index, finding in enumerate(finding_rows):
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            raise ExportError(f"finding {index} must have a string id")
        if finding["id"] in findings_by_id:
            raise ExportError(f"duplicate finding id {finding['id']!r}")
        findings_by_id[finding["id"]] = finding

    indicators = findings.get("technical_indicators", [])
    if not isinstance(indicators, list):
        raise ExportError("technical_indicators must be a list")
    eligible_findings = verified_findings(fact_check)
    claim_links = technical_indicator_claims(fact_check)
    seen_ids: set[str] = set()
    result: list[dict] = []
    for index, indicator in enumerate(indicators):
        if not isinstance(indicator, dict):
            raise ExportError(f"technical indicator {index} must be an object")
        required = ("id", "finding_id", "type", "value", "context", "sources")
        missing = [key for key in required if key not in indicator]
        if missing:
            raise ExportError(f"technical indicator {index} missing {', '.join(missing)}")
        indicator_id = indicator["id"]
        finding_id = indicator["finding_id"]
        if not isinstance(indicator_id, str) or not indicator_id:
            raise ExportError(f"technical indicator {index} has invalid id")
        if indicator_id in seen_ids:
            raise ExportError(f"duplicate technical indicator id {indicator_id!r}")
        seen_ids.add(indicator_id)
        if not isinstance(finding_id, str) or not finding_id:
            raise ExportError(f"technical indicator {indicator_id!r} has invalid finding_id")
        if finding_id not in findings_by_id:
            raise ExportError(f"technical indicator {indicator_id!r} references unknown finding")
        if not isinstance(indicator["context"], str) or not indicator["context"].strip():
            raise ExportError(f"technical indicator {indicator_id!r} needs context")
        sources = indicator["sources"]
        if not isinstance(sources, list) or not sources or not all(isinstance(item, str) and item for item in sources):
            raise ExportError(f"technical indicator {indicator_id!r} needs non-empty source references")
        normalized = normalize_indicator(indicator["type"], indicator["value"])
        linked_claims = claim_links.pop(indicator_id, [])
        for claim in linked_claims:
            if claim.get("finding_id") != finding_id:
                raise ExportError(
                    f"fact-check link for technical indicator {indicator_id!r} uses a different finding_id"
                )
            claim_text = claim.get("claim_text")
            if not isinstance(claim_text, str) or indicator["value"] not in claim_text:
                raise ExportError(
                    f"fact-check claim for technical indicator {indicator_id!r} must contain its exact value"
                )
        if finding_id not in eligible_findings or not any(
            claim.get("verdict") == "verified" for claim in linked_claims
        ):
            continue
        finding = findings_by_id[finding_id]
        confidence = finding.get("confidence")
        if confidence not in {"high", "medium", "low", "disputed"}:
            raise ExportError(f"finding {finding_id!r} has invalid confidence")
        exported = {
            "id": indicator_id,
            "finding_id": finding_id,
            "type": indicator["type"],
            "value": indicator["value"],
            "normalized_value": normalized,
            "context": indicator["context"].strip(),
            "sources": sorted(set(sources)),
            "confidence": confidence,
            "fact_check_verdict": "verified",
        }
        observed: dict[str, str] = {}
        for key in ("first_observed", "last_observed"):
            if key in indicator:
                observed[key] = canonical_timestamp(
                    indicator[key], f"technical indicator {indicator_id!r} {key}"
                )
                exported[key] = observed[key]
        if (
            "first_observed" in observed
            and "last_observed" in observed
            and observed["last_observed"] < observed["first_observed"]
        ):
            raise ExportError(
                f"technical indicator {indicator_id!r} last_observed precedes first_observed"
            )
        result.append(exported)
    if claim_links:
        unknown = ", ".join(sorted(claim_links))
        raise ExportError(f"fact-check references unknown technical indicator IDs: {unknown}")
    return sorted(result, key=lambda row: (row["type"], row["normalized_value"], row["finding_id"], row["id"]))


def json_payload(project: str, indicators: list[dict]) -> str:
    payload = {
        "schema_version": "1.0",
        "project": project,
        "selection_policy": "explicit claim-linked technical indicator; mapped claim and every finding verdict are verified",
        "indicator_count": len(indicators),
        "indicators": indicators,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def csv_payload(indicators: list[dict]) -> str:
    stream = io.StringIO(newline="")
    fields = (
        "id",
        "finding_id",
        "type",
        "value",
        "normalized_value",
        "context",
        "sources",
        "confidence",
        "fact_check_verdict",
        "first_observed",
        "last_observed",
    )
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for indicator in indicators:
        row = {key: indicator.get(key, "") for key in fields}
        row["sources"] = json.dumps(indicator["sources"], ensure_ascii=False, separators=(",", ":"))
        row = {key: csv_safe(value) for key, value in row.items()}
        writer.writerow(row)
    return stream.getvalue()


def csv_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def canonical_timestamp(value: object, label: str = "STIX export timestamp") -> str:
    if not isinstance(value, str) or not value:
        raise ExportError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportError(f"{label} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ExportError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stix_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def stix_pattern(kind: str, value: str) -> str:
    escaped = stix_escape(value)
    object_paths = {
        "ipv4": "ipv4-addr:value",
        "ipv6": "ipv6-addr:value",
        "domain": "domain-name:value",
        "url": "url:value",
        "md5": "file:hashes.'MD5'",
        "sha1": "file:hashes.'SHA-1'",
        "sha256": "file:hashes.'SHA-256'",
        "bitcoin": "x-spotlight-cryptocurrency-address:value",
        "ethereum": "x-spotlight-cryptocurrency-address:value",
    }
    return f"[{object_paths[kind]} = '{escaped}']"


def stix_payload(project: str, indicators: list[dict], timestamp: str) -> str:
    objects = []
    for indicator in indicators:
        stable_name = f"{indicator['type']}:{indicator['normalized_value']}"
        stable_identity = f"{stable_name}:{indicator['finding_id']}:{indicator['id']}"
        object_id = f"indicator--{uuid.uuid5(SPOTLIGHT_STIX_NAMESPACE, stable_identity)}"
        references = []
        for source in indicator["sources"]:
            if source.startswith(("https://", "http://")):
                references.append({"source_name": "spotlight-source", "url": source})
            else:
                references.append({"source_name": "spotlight-case-artifact", "description": source})
        objects.append(
            {
                "type": "indicator",
                "spec_version": "2.1",
                "id": object_id,
                "created": timestamp,
                "modified": timestamp,
                "name": stable_name,
                "description": indicator["context"],
                "indicator_types": ["unknown"],
                "pattern": stix_pattern(indicator["type"], indicator["normalized_value"]),
                "pattern_type": "stix",
                "pattern_version": "2.1",
                "valid_from": timestamp,
                "external_references": references,
                "x_spotlight_project": project,
                "x_spotlight_indicator_id": indicator["id"],
                "x_spotlight_finding_id": indicator["finding_id"],
                "x_spotlight_confidence": indicator["confidence"],
                "x_spotlight_fact_check_verdict": "verified",
                **(
                    {"x_spotlight_cryptocurrency": indicator["type"]}
                    if indicator["type"] in {"bitcoin", "ethereum"}
                    else {}
                ),
            }
        )
    object_ids = ",".join(item["id"] for item in objects)
    bundle_id = uuid.uuid5(SPOTLIGHT_STIX_NAMESPACE, f"{project}:{timestamp}:{object_ids}")
    payload = {"type": "bundle", "id": f"bundle--{bundle_id}", "objects": objects}
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        _, findings_path, fact_check_path, output_path = case_paths(
            args.findings, args.fact_check, args.output
        )
        findings = load_object(findings_path, "findings")
        fact_check = load_object(fact_check_path, "fact-check")
        indicators = collect_indicators(findings, fact_check)
        project = findings.get("project")
        if not isinstance(project, str) or not project:
            raise ExportError("findings project must be a non-empty string")
        if args.format == "json":
            content = json_payload(project, indicators)
        elif args.format == "csv":
            content = csv_payload(indicators)
        else:
            timestamp = canonical_timestamp(fact_check.get("checked_at") or findings.get("investigated_at"))
            content = stix_payload(project, indicators, timestamp)
        atomic_write(output_path, content)
    except (ExportError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"exported {len(indicators)} verified technical indicator(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
