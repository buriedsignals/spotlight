#!/usr/bin/env python3
"""Plan or apply an exact-anchor-only source-expression migration.

The default mode writes ``data/source-expression-migration.json`` as a dry-run
audit proposal. ``--apply`` consumes that proposal, refuses stale inputs, and
publishes the validated findings/fact-check/source-expression bundle before
writing the authoritative ``data/case-contract.json`` last.

The migration never searches for or invents passages. Only an evidence item
with an exact ``quote``, an exact line/JSON ``source_ref``, and an intact,
explicit evidence-bundle lineage can become a source expression.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_anchors import (  # noqa: E402
    case_evidence_path,
    rlm_lead_failure,
    selected_source_text,
    sha256_file,
)


TOOL_VERSION = "spotlight-source-expression-migration/1"
INPUT_FILES = {
    "findings_sha256": "findings.json",
    "fact_check_sha256": "fact-check.json",
    "evidence_bundle_sha256": "evidence-bundle.json",
}
OUTPUT_FILES = {
    **INPUT_FILES,
    "source_expressions_sha256": "source-expressions.json",
}
POSITIVE_VERDICTS = {"verified", "partially_verified"}
PASSAGE_CORE_FIELDS = (
    "text",
    "anchor_ref",
    "anchor_sha256",
    "original_evidence_bundle_id",
    "original_artifact_sha256",
    "language",
    "attribution",
    "direct_quote",
    "derived_from_expression_id",
    "derivative_type",
)
ORIGINAL_PATH_KEYS = ("raw_path", "downloaded_document_path", "screenshot_path", "path")


def load_validator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "spotlight_validate_case", SCRIPT_DIR / "validate-case.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load validate-case.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_validator()


class MigrationError(Exception):
    """An expected refusal that must leave the case unmodified."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MigrationError(f"missing required input {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise MigrationError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"{path.name} must contain a JSON object")
    return value


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.stage.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def input_hashes(data_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, name in INPUT_FILES.items():
        path = data_dir / name
        if not path.is_file():
            raise MigrationError(f"missing required input data/{name}")
        hashes[key] = sha256_file(path)
    return hashes


def contains_expression_refs(value: Any) -> bool:
    if isinstance(value, dict):
        if "source_expression_refs" in value:
            return True
        return any(contains_expression_refs(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_expression_refs(item) for item in value)
    return False


def detect_state(case_dir: Path, findings: dict[str, Any], fact_check: dict[str, Any]) -> str:
    data = case_dir / "data"
    contract = data / "case-contract.json"
    expressions = data / "source-expressions.json"
    if contract.exists():
        return "activated"
    if findings.get("schema_version", "1.0") == "1.1" or expressions.exists() or contains_expression_refs(fact_check):
        raise MigrationError(
            "interrupted or partial migration detected: activated artifacts exist without "
            "data/case-contract.json; restore the legacy inputs before retrying"
        )
    return "legacy"


def validate_legacy_inputs(
    findings: dict[str, Any], fact_check: dict[str, Any], bundle: dict[str, Any]
) -> None:
    if findings.get("schema_version", "1.0") != "1.0":
        raise MigrationError("migration accepts only legacy findings contract 1.0")
    errors: list[str] = []
    errors.extend(VALIDATOR.validate_findings(findings))
    errors.extend(VALIDATOR.validate_fact_check(fact_check))
    errors.extend(VALIDATOR.validate_evidence_bundle(bundle))
    errors.extend(VALIDATOR.cross_reference(findings, fact_check))
    if errors:
        raise MigrationError("legacy inputs do not validate: " + "; ".join(errors))


def fact_rows(document: dict[str, Any]) -> tuple[str, list[Any]]:
    container, rows = VALIDATOR.fact_check_rows(document)
    return container, rows if isinstance(rows, list) else []


def row_values(container: str, row: dict[str, Any]) -> tuple[str, str]:
    claim, verdict = VALIDATOR.fact_check_values(container, row)
    return str(claim or ""), str(verdict or "")


def exact_source_ref(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) - {
        "path", "line_start", "line_end", "json_pointer"
    }:
        return False
    line_selector = (
        isinstance(value.get("line_start"), int)
        and isinstance(value.get("line_end"), int)
        and "json_pointer" not in value
    )
    pointer_selector = (
        isinstance(value.get("json_pointer"), str)
        and "line_start" not in value
        and "line_end" not in value
    )
    return isinstance(value.get("path"), str) and bool(value["path"].strip()) and (
        line_selector != pointer_selector
    )


def skip(
    path: str, reason: str, detail: str, finding_id: str | None = None
) -> dict[str, str]:
    item = {"fact_check_path": path, "reason": reason, "detail": detail}
    if finding_id:
        item["finding_id"] = finding_id
    return item


def bundle_original(
    case_dir: Path, bundle: dict[str, Any]
) -> tuple[str | None, set[Path], str | None]:
    expected = str(bundle.get("sha256") or "").lower()
    if len(expected) != 64:
        return None, set(), "evidence bundle has no lowercase SHA-256 original artifact hash"
    owned: set[Path] = set()
    originals: set[Path] = set()
    for key in ORIGINAL_PATH_KEYS:
        path = case_evidence_path(case_dir, bundle.get(key))
        if path is not None:
            owned.add(path)
            if sha256_file(path) == expected:
                originals.add(path)
    if not originals:
        return expected, owned, "evidence bundle original artifact path/hash is not intact"
    return expected, owned, None


def extract_candidate(
    case_dir: Path,
    finding_id: str,
    relation: str,
    entry: dict[str, Any],
    entry_path: str,
    bundles: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    quote = entry.get("quote")
    if not isinstance(quote, str) or not quote:
        return None, skip(entry_path, "missing_exact_quote", "quote is absent or empty", finding_id)
    source_ref = entry.get("source_ref")
    if source_ref is None:
        return None, skip(entry_path, "missing_source_ref", "source_ref is absent", finding_id)
    if not exact_source_ref(source_ref):
        return None, skip(
            entry_path, "invalid_source_ref", "source_ref is not one exact line range or JSON Pointer", finding_id
        )
    anchor_path = case_evidence_path(case_dir, source_ref.get("path"))
    if anchor_path is None:
        return None, skip(entry_path, "invalid_source_ref", "source_ref does not resolve case-locally", finding_id)
    lead_failure = rlm_lead_failure(anchor_path)
    if lead_failure:
        return None, skip(entry_path, "invalid_source_ref", lead_failure, finding_id)
    selected, selection_error = selected_source_text(anchor_path, source_ref)
    if selection_error:
        return None, skip(entry_path, "invalid_source_ref", selection_error, finding_id)
    if selected != quote:
        return None, skip(
            entry_path,
            "quote_not_exact_selected_text",
            "quote is not byte-for-text identical to the selected source_ref value",
            finding_id,
        )

    bundle_id = entry.get("evidence_bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        return None, skip(
            entry_path, "missing_evidence_bundle_id", "evidence_bundle_id is required", finding_id
        )
    bundle = bundles.get(bundle_id)
    if bundle is None:
        return None, skip(
            entry_path, "unknown_evidence_bundle", f"evidence bundle {bundle_id!r} does not resolve", finding_id
        )
    if bundle.get("human_verification_required") is not False:
        return None, skip(
            entry_path, "pending_human_verification", "evidence bundle is not human-verified", finding_id
        )
    gate = bundle.get("missing_source_gate")
    if isinstance(gate, dict) and gate.get("fallback_required") is not False:
        return None, skip(
            entry_path, "pending_human_verification", "evidence bundle still requires source fallback", finding_id
        )
    links = bundle.get("claim_links")
    matching_links = [
        link
        for link in links if isinstance(link, dict) and link.get("finding_id") == finding_id
    ] if isinstance(links, list) else []
    allowed_support = {"direct", "indirect"} if relation == "supports" else {"contradicted"}
    if not matching_links or not any(link.get("support_type") in allowed_support for link in matching_links):
        return None, skip(
            entry_path,
            "missing_structured_finding_link",
            f"evidence bundle lacks a structured {relation} link to {finding_id}",
            finding_id,
        )

    original_hash, owned_paths, original_error = bundle_original(case_dir, bundle)
    if original_error:
        reason = "stale_original_hash" if original_hash else "missing_original_artifact"
        return None, skip(entry_path, reason, original_error, finding_id)
    derivatives = bundle.get("text_derivatives", [])
    matching_derivative = next(
        (
            derivative
            for derivative in derivatives
            if isinstance(derivative, dict)
            and case_evidence_path(case_dir, derivative.get("path")) == anchor_path
        ),
        None,
    ) if isinstance(derivatives, list) else None
    if matching_derivative is not None:
        if matching_derivative.get("human_verification_required") is not False:
            return None, skip(
                entry_path, "pending_human_verification", "text derivative is not human-verified", finding_id
            )
        derivative_hash = str(matching_derivative.get("sha256") or "").lower()
        if derivative_hash != sha256_file(anchor_path):
            return None, skip(
                entry_path, "stale_anchor_hash", "text derivative SHA-256 is stale", finding_id
            )
        owned_paths.add(anchor_path)
    if anchor_path not in owned_paths:
        return None, skip(
            entry_path, "unowned_anchor", "source_ref path is not owned by its evidence bundle", finding_id
        )

    core: dict[str, Any] = {
        "text": quote,
        "anchor_ref": copy.deepcopy(source_ref),
        "anchor_sha256": sha256_file(anchor_path),
        "original_evidence_bundle_id": bundle_id,
        "original_artifact_sha256": original_hash,
        "direct_quote": True,
    }
    if isinstance(matching_derivative, dict) and isinstance(matching_derivative.get("language"), str):
        core["language"] = matching_derivative["language"]
    return {
        "core": core,
        "finding_id": finding_id,
        "relation": relation,
        "entry_path": entry_path,
    }, None


def build_documents(
    case_dir: Path,
    findings: dict[str, Any],
    fact_check: dict[str, Any],
    bundle: dict[str, Any],
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    updated_findings = copy.deepcopy(findings)
    updated_fact = copy.deepcopy(fact_check)
    finding_index = {
        finding.get("id"): finding
        for finding in updated_findings.get("findings", [])
        if isinstance(finding, dict) and isinstance(finding.get("id"), str)
    }
    bundles = {
        item.get("id"): item
        for item in bundle.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    container, rows = fact_rows(updated_fact)
    candidates: list[dict[str, Any]] = []
    skips: list[dict[str, str]] = []
    positive_findings: set[str] = set()

    for row_index, row in enumerate(rows):
        row_path = f"{container}[{row_index}]"
        if not isinstance(row, dict):
            continue
        finding_id = row.get("finding_id")
        _, verdict = row_values(container, row)
        if not isinstance(finding_id, str) or not finding_id:
            skips.append(skip(row_path, "missing_finding_id", "fact-check row has no finding_id"))
            continue
        if finding_id not in finding_index:
            skips.append(skip(row_path, "unknown_finding", "finding_id does not resolve", finding_id))
            continue
        if verdict in POSITIVE_VERDICTS:
            positive_findings.add(finding_id)
        for evidence_key, relation in (("evidence_for", "supports"), ("evidence_against", "contradicts")):
            evidence = row.get(evidence_key)
            if not isinstance(evidence, list):
                continue
            for evidence_index, entry in enumerate(evidence):
                entry_path = f"{row_path}.{evidence_key}[{evidence_index}]"
                if not isinstance(entry, dict):
                    skips.append(skip(entry_path, "missing_exact_quote", "evidence entry is not an object", finding_id))
                    continue
                if evidence_key == "evidence_for" and verdict not in POSITIVE_VERDICTS:
                    skips.append(skip(
                        entry_path,
                        "ambiguous_relation",
                        "evidence_for on a non-positive verdict does not determine passage polarity",
                        finding_id,
                    ))
                    continue
                candidate, rejected = extract_candidate(
                    case_dir, finding_id, relation, entry, entry_path, bundles
                )
                if rejected:
                    skips.append(rejected)
                elif candidate:
                    candidates.append(candidate)

    conflicts = {
        (fingerprint(candidate["core"]), candidate["finding_id"])
        for candidate in candidates
        if len({
            item["relation"]
            for item in candidates
            if fingerprint(item["core"]) == fingerprint(candidate["core"])
            and item["finding_id"] == candidate["finding_id"]
        }) > 1
    }
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (fingerprint(candidate["core"]), candidate["finding_id"])
        if key in conflicts:
            skips.append(skip(
                candidate["entry_path"],
                "relation_conflict",
                "the same passage has conflicting relations to this finding",
                candidate["finding_id"],
            ))
        else:
            accepted.append(candidate)

    grouped: dict[str, dict[str, Any]] = {}
    for candidate in accepted:
        expression_fp = fingerprint(candidate["core"])
        group = grouped.setdefault(expression_fp, {"core": candidate["core"], "links": set(), "usages": []})
        group["links"].add((candidate["finding_id"], candidate["relation"]))
        usage = (candidate["entry_path"], candidate["relation"], candidate["finding_id"])
        if usage not in group["usages"]:
            group["usages"].append(usage)

    expressions: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    refs_by_path: dict[str, list[dict[str, str]]] = {}
    supporting_findings: set[str] = set()
    for expression_fp, group in sorted(grouped.items()):
        expression_id = f"SX-{expression_fp[:20]}"
        links: list[dict[str, str]] = []
        for finding_id, relation in sorted(group["links"]):
            finding_fp = fingerprint({"claim": finding_index[finding_id]["claim"]})
            link = {
                "finding_id": finding_id,
                "finding_fingerprint": finding_fp,
                "relation": relation,
            }
            link["link_fingerprint"] = fingerprint({
                "expression_fingerprint": expression_fp,
                **link,
            })
            links.append(link)
            if relation == "supports":
                supporting_findings.add(finding_id)
        expression = {
            "id": expression_id,
            **group["core"],
            "expression_fingerprint": expression_fp,
            "finding_links": links,
            "lifecycle_events": [{
                "event": "activated",
                "timestamp": created_at,
                "actor": "fact-checker",
                "reason": "Recovered from an exact legacy fact-check anchor.",
            }],
            "created_by": "fact-checker",
            "cycle": max(1, int(updated_fact.get("cycle") or updated_findings.get("cycle") or 1)),
        }
        expressions.append(expression)
        usages = []
        for entry_path, relation, finding_id in sorted(group["usages"]):
            link = next(item for item in links if item["finding_id"] == finding_id and item["relation"] == relation)
            ref = {
                "expression_id": expression_id,
                "expression_fingerprint": expression_fp,
                "finding_fingerprint": link["finding_fingerprint"],
                "link_fingerprint": link["link_fingerprint"],
            }
            refs_by_path.setdefault(entry_path, []).append(ref)
            usages.append({"fact_check_path": entry_path, "relation": relation})
        mappings.append({
            "expression_id": expression_id,
            "expression_fingerprint": expression_fp,
            "anchor_ref": group["core"]["anchor_ref"],
            "anchor_sha256": group["core"]["anchor_sha256"],
            "original_evidence_bundle_id": group["core"]["original_evidence_bundle_id"],
            "finding_ids": sorted({finding_id for finding_id, _ in group["links"]}),
            "usages": usages,
        })

    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for evidence_key in ("evidence_for", "evidence_against"):
            evidence = row.get(evidence_key)
            if not isinstance(evidence, list):
                continue
            for evidence_index, entry in enumerate(evidence):
                if not isinstance(entry, dict):
                    continue
                entry_path = f"{container}[{row_index}].{evidence_key}[{evidence_index}]"
                refs = refs_by_path.get(entry_path)
                if refs:
                    entry["source_expression_refs"] = sorted(
                        refs, key=lambda item: (item["expression_id"], item["link_fingerprint"])
                    )

    for finding in updated_findings.get("findings", []):
        if isinstance(finding, dict):
            finding["finding_fingerprint"] = fingerprint({"claim": finding.get("claim")})
    updated_findings["schema_version"] = "1.1"
    expressions_doc = {
        "schema_version": "1.0",
        "project": updated_findings.get("project"),
        "created_at": created_at,
        "expressions": expressions,
    }
    blocking = [
        {
            "finding_id": finding_id,
            "reason": "positive_verdict_without_exact_support",
            "detail": "no recoverable active supporting expression remains for this positive verdict",
        }
        for finding_id in sorted(positive_findings - supporting_findings)
    ]
    skips.sort(key=lambda item: (
        item.get("fact_check_path", ""), item.get("reason", ""), item.get("detail", "")
    ))
    return updated_findings, updated_fact, expressions_doc, mappings, skips, blocking


def build_plan(
    case_dir: Path,
    findings: dict[str, Any],
    fact_check: dict[str, Any],
    bundle: dict[str, Any],
    hashes: dict[str, str],
    created_at: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    project = str(findings.get("project") or "")
    migration_id = f"SEM-{fingerprint({'project': project, 'input_hashes': hashes, 'tool': TOOL_VERSION})[:20]}"
    updated_findings, updated_fact, expressions, mappings, skips, blocking = build_documents(
        case_dir, findings, fact_check, bundle, created_at
    )
    outputs = {
        "findings_sha256": canonical_bytes(updated_findings),
        "fact_check_sha256": canonical_bytes(updated_fact),
        "evidence_bundle_sha256": (case_dir / "data/evidence-bundle.json").read_bytes(),
        "source_expressions_sha256": canonical_bytes(expressions),
    }
    activated_hashes = {key: bytes_sha256(value) for key, value in outputs.items()}
    contract = {
        "schema_version": "1.0",
        "project": project,
        "current_contract_version": "1.1",
        "activation_events": [{
            "event_id": f"ACT-{migration_id[4:]}",
            "previous_contract_version": "1.0",
            "activated_contract_version": "1.1",
            "activated_at": created_at,
            "tool_version": TOOL_VERSION,
            "prior_input_hashes": hashes,
            "activated_artifact_hashes": activated_hashes,
        }],
    }
    contract_bytes = canonical_bytes(contract)
    outputs["case_contract_sha256"] = contract_bytes
    proposed_hashes = {key: bytes_sha256(value) for key, value in outputs.items()}
    audit = {
        "schema_version": "1.0",
        "project": project,
        "migration_id": migration_id,
        "tool_version": TOOL_VERSION,
        "source_contract_version": "1.0",
        "target_contract_version": "1.1",
        "created_at": created_at,
        "input_hashes": hashes,
        "eligible": not blocking,
        "candidate_mappings": mappings,
        "skips": skips,
        "blocking_reasons": blocking,
        "proposed_output_hashes": proposed_hashes,
    }
    return audit, {
        "findings.json": outputs["findings_sha256"],
        "fact-check.json": outputs["fact_check_sha256"],
        "evidence-bundle.json": outputs["evidence_bundle_sha256"],
        "source-expressions.json": outputs["source_expressions_sha256"],
        "case-contract.json": contract_bytes,
    }


def validate_candidate(case_dir: Path, documents: dict[str, bytes]) -> None:
    decoded = {
        name: json.loads(value.decode("utf-8"))
        for name, value in documents.items()
        if name.endswith(".json")
    }
    errors: list[str] = []
    errors.extend(VALIDATOR.validate_findings(decoded["findings.json"]))
    errors.extend(VALIDATOR.validate_fact_check(decoded["fact-check.json"]))
    errors.extend(VALIDATOR.validate_evidence_bundle(decoded["evidence-bundle.json"]))
    errors.extend(VALIDATOR.cross_reference(decoded["findings.json"], decoded["fact-check.json"]))
    errors.extend(VALIDATOR.validate_source_expressions(
        case_dir,
        decoded["source-expressions.json"],
        decoded["findings.json"],
        decoded["fact-check.json"],
        decoded["evidence-bundle.json"],
    ))
    errors.extend(VALIDATOR.validate_case_contract(decoded["case-contract.json"]))
    if errors:
        raise MigrationError("candidate bundle does not validate: " + "; ".join(errors))


def restore_files(data_dir: Path, backups: dict[str, bytes | None]) -> None:
    for name, previous in backups.items():
        path = data_dir / name
        if previous is None:
            if path.exists():
                path.unlink()
        else:
            atomic_write(path, previous)


def apply_bundle(data_dir: Path, documents: dict[str, bytes]) -> None:
    publish_order = ("findings.json", "fact-check.json", "source-expressions.json")
    backups = {
        name: ((data_dir / name).read_bytes() if (data_dir / name).exists() else None)
        for name in (*publish_order, "case-contract.json")
    }
    try:
        for name in publish_order:
            atomic_write(data_dir / name, documents[name])
        # This is deliberately the final write: file presence never activates a case.
        atomic_write(data_dir / "case-contract.json", documents["case-contract.json"])
    except Exception:
        restore_files(data_dir, backups)
        raise


def activated_idempotent(
    case_dir: Path, audit: dict[str, Any] | None
) -> bool:
    if audit is None:
        raise MigrationError("case is already activated; downgrade migration is refused")
    data = case_dir / "data"
    contract = read_object(data / "case-contract.json")
    contract_errors = VALIDATOR.validate_case_contract(contract)
    if contract_errors:
        raise MigrationError("activated case contract is invalid: " + "; ".join(contract_errors))
    latest = contract["activation_events"][-1]
    expected_event = f"ACT-{str(audit.get('migration_id', ''))[4:]}"
    if latest.get("event_id") != expected_event:
        raise MigrationError("case is already activated by a different migration; downgrade is refused")
    for key, name in OUTPUT_FILES.items():
        path = data / name
        if not path.is_file() or sha256_file(path) != latest["activated_artifact_hashes"].get(key):
            raise MigrationError("activated case artifacts are stale or partial; repair before retrying")
    if sha256_file(data / "case-contract.json") != audit.get("proposed_output_hashes", {}).get("case_contract_sha256"):
        raise MigrationError("activated case contract differs from this migration proposal")
    return True


def load_audit(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_object(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="apply a prior dry-run proposal")
    args = parser.parse_args()
    case_dir = args.case_dir.resolve()
    data_dir = case_dir / "data"
    audit_path = data_dir / "source-expression-migration.json"
    try:
        if not data_dir.is_dir():
            raise MigrationError(f"case has no data directory: {data_dir}")
        findings = read_object(data_dir / "findings.json")
        fact_check = read_object(data_dir / "fact-check.json")
        bundle = read_object(data_dir / "evidence-bundle.json")
        existing_audit = load_audit(audit_path)
        state = detect_state(case_dir, findings, fact_check)
        if state == "activated":
            if args.apply and activated_idempotent(case_dir, existing_audit):
                print("OK already applied; activated artifacts are byte-identical")
                return 0
            raise MigrationError("case is already activated; downgrade migration is refused")
        validate_legacy_inputs(findings, fact_check, bundle)
        hashes = input_hashes(data_dir)

        if args.apply:
            if existing_audit is None:
                raise MigrationError("apply requires a prior data/source-expression-migration.json dry run")
            if existing_audit.get("tool_version") != TOOL_VERSION:
                raise MigrationError("dry-run tool version differs; run a new dry run")
            if existing_audit.get("input_hashes") != hashes:
                raise MigrationError("stale dry run: legacy input hashes changed")
            created_at = str(existing_audit.get("created_at") or "")
            audit, documents = build_plan(
                case_dir, findings, fact_check, bundle, hashes, created_at
            )
            if audit != existing_audit:
                raise MigrationError("dry-run proposal is stale or non-deterministic; run a new dry run")
            if not audit["eligible"]:
                raise MigrationError("migration is blocked; see blocking_reasons in the audit record")
            validate_candidate(case_dir, documents)
            apply_bundle(data_dir, documents)
            print(
                f"APPLIED {audit['migration_id']}: "
                f"{len(audit['candidate_mappings'])} expressions; case contract written last"
            )
            return 0

        created_at = utc_now()
        if (
            existing_audit is not None
            and existing_audit.get("tool_version") == TOOL_VERSION
            and existing_audit.get("input_hashes") == hashes
            and isinstance(existing_audit.get("created_at"), str)
        ):
            created_at = existing_audit["created_at"]
        audit, _ = build_plan(case_dir, findings, fact_check, bundle, hashes, created_at)
        atomic_write(audit_path, canonical_bytes(audit))
        status = "READY" if audit["eligible"] else "BLOCKED"
        print(
            f"DRY-RUN {status} {audit['migration_id']}: "
            f"{len(audit['candidate_mappings'])} expressions, "
            f"{len(audit['skips'])} skips, {len(audit['blocking_reasons'])} blockers"
        )
        return 0
    except MigrationError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
