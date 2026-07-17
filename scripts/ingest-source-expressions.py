#!/usr/bin/env python3
"""Deterministically attach case source-expression snapshots to vault claims.

The writer is deliberately separate from model-authored ingest.  It runs under
the existing vault ``.ingest-lock``, updates only eligible claim notes, and
writes a stable case-local receipt.  Repeating the same ingest is byte-identical.

Usage:
  ingest-source-expressions.py --case-dir /path/to/case --vault /path/to/vault
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from source_expression_contract import canonical_json_bytes, fact_check_rows, lifecycle_state


TOOL_VERSION = "spotlight-source-expression-ingest/1"
BLOCK_START = "<!-- spotlight-source-expressions:v1\n"
BLOCK_END = "\n-->"
ALLOWED_VERDICTS = {"verified", "partially_verified"}
SNAPSHOT_FIELDS = (
    "text",
    "anchor_ref",
    "anchor_sha256",
    "original_evidence_bundle_id",
    "original_artifact_sha256",
    "created_by",
    "cycle",
    "language",
    "attribution",
    "direct_quote",
    "supersedes_expression_id",
    "derived_from_expression_id",
    "derivative_type",
)


class IngestError(RuntimeError):
    """A deterministic precondition or publication failure."""


def rendered_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"unreadable or invalid JSON: {path} ({exc})") from exc
    if not isinstance(value, dict):
        raise IngestError(f"expected a JSON object: {path}")
    return value


def atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_frontmatter(text: str, path: Path) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise IngestError(f"claim note has no frontmatter: {path}")
    end = text.find("\n---", 4)
    if end == -1:
        raise IngestError(f"claim note has unterminated frontmatter: {path}")
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def parse_managed_block(text: str, path: Path) -> dict[str, Any]:
    start = text.find(BLOCK_START)
    if start == -1:
        return {"schema_version": "1.0", "snapshots": [], "ingest_events": []}
    payload_start = start + len(BLOCK_START)
    end = text.find(BLOCK_END, payload_start)
    if end == -1:
        raise IngestError(f"unterminated source-expression block: {path}")
    if text.find(BLOCK_START, payload_start) != -1:
        raise IngestError(f"multiple source-expression blocks: {path}")
    try:
        value = json.loads(text[payload_start:end])
    except json.JSONDecodeError as exc:
        raise IngestError(f"invalid source-expression block: {path} ({exc})") from exc
    if not isinstance(value, dict):
        raise IngestError(f"source-expression block must be an object: {path}")
    if value.get("schema_version") != "1.0":
        raise IngestError(f"unsupported source-expression block version: {path}")
    if not isinstance(value.get("snapshots"), list) or not isinstance(value.get("ingest_events"), list):
        raise IngestError(f"malformed source-expression block: {path}")
    return value


def render_managed_block(text: str, block: dict[str, Any]) -> bytes:
    payload = json.dumps(block, indent=2, ensure_ascii=False)
    rendered = f"{BLOCK_START}{payload}{BLOCK_END}"
    start = text.find(BLOCK_START)
    if start == -1:
        return (text.rstrip() + "\n\n## Source Expressions\n\n" + rendered + "\n").encode("utf-8")
    end = text.find(BLOCK_END, start + len(BLOCK_START))
    if end == -1:
        raise IngestError("unterminated source-expression block")
    return (text[:start] + rendered + text[end + len(BLOCK_END):]).encode("utf-8")


def expression_state(expression: dict[str, Any]) -> str:
    state = lifecycle_state(expression)
    if state is None:
        raise IngestError(f"expression {expression.get('id')} has invalid lifecycle state")
    return state


def eligibility_reason(finding: dict[str, Any] | None, checked: dict[str, Any] | None) -> str | None:
    if finding is None or checked is None:
        return "missing finding or fact-check entry"
    verdict = checked.get("verdict", checked.get("status"))
    if verdict not in ALLOWED_VERDICTS:
        return f"verdict {verdict or 'missing'}"
    finding_cap = (finding.get("grounding") or {}).get("confidence_cap")
    checked_cap = (checked.get("grounding_assessment") or {}).get("confidence_cap")
    if finding_cap == "low" or checked_cap == "low":
        return "grounding capped low"
    sources = finding.get("sources")
    if not isinstance(sources, list) or not sources:
        return "no sources"
    if finding.get("rlm_assisted") is True:
        return "RLM-derived"
    for source in sources:
        serialized = json.dumps(source, sort_keys=True).lower()
        if "rlm-analysis.json" in serialized or "rlm_artifact" in serialized:
            return "RLM-derived"
    return None


def snapshot_key(project: str, expression_id: str, fingerprint: str) -> str:
    return f"{project}:{expression_id}:{fingerprint}"


def resolve_claim_path(vault: Path, relative: str) -> Path:
    path = (vault / relative).resolve()
    try:
        path.relative_to(vault.resolve())
    except ValueError as exc:
        raise IngestError(f"claim registry path escapes the vault: {relative}") from exc
    return path


def build_snapshot(
    project: str,
    finding_id: str,
    expression: dict[str, Any],
    link: dict[str, Any],
) -> dict[str, Any]:
    expression_id = str(expression.get("id", ""))
    fingerprint = str(expression.get("expression_fingerprint", ""))
    if not expression_id or len(fingerprint) != 64:
        raise IngestError(f"expression has invalid identity: {expression_id or '<missing>'}")
    snapshot: dict[str, Any] = {
        "snapshot_id": snapshot_key(project, expression_id, fingerprint),
        "project": project,
        "expression_id": expression_id,
        "expression_fingerprint": fingerprint,
        "finding_id": finding_id,
        "finding_fingerprint": link.get("finding_fingerprint"),
        "relation": link.get("relation"),
        "link_fingerprint": link.get("link_fingerprint"),
        "lifecycle_state": expression_state(expression),
        "lifecycle_events": expression.get("lifecycle_events"),
    }
    for field in SNAPSHOT_FIELDS:
        if field in expression:
            snapshot[field] = expression[field]
    return snapshot


def verify_activation(case_dir: Path, source_hash: str, project: str) -> None:
    contract = load_object(case_dir / "data" / "case-contract.json")
    spec = importlib.util.spec_from_file_location(
        "spotlight_validate_case", SCRIPT_DIR / "validate-case.py"
    )
    if spec is None or spec.loader is None:
        raise IngestError("cannot load activated-case validator")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    contract_errors = validator.validate_case_contract(contract)
    if contract_errors:
        raise IngestError("invalid case contract: " + "; ".join(contract_errors))
    if contract.get("project") != project or contract.get("current_contract_version") != "1.1":
        raise IngestError("source-expression ingest requires a matching activated 1.1 case")
    events = contract.get("activation_events")
    if not isinstance(events, list) or not events:
        raise IngestError("case contract has no activation event")
    hashes = events[-1].get("activated_artifact_hashes", {})
    if hashes.get("source_expressions_sha256") != source_hash:
        raise IngestError("source-expressions.json does not match the activated artifact hash")
    artifact_paths = {
        "findings_sha256": case_dir / "data" / "findings.json",
        "fact_check_sha256": case_dir / "data" / "fact-check.json",
        "evidence_bundle_sha256": case_dir / "data" / "evidence-bundle.json",
        "source_expressions_sha256": case_dir / "data" / "source-expressions.json",
    }
    for key, path in artifact_paths.items():
        if not path.is_file() or hashes.get(key) != sha256_bytes(path.read_bytes()):
            raise IngestError(f"{path.name} does not match the activated artifact hash")


def build_updates(case_dir: Path, vault: Path) -> tuple[dict[Path, bytes], dict[str, Any]]:
    data_dir = case_dir / "data"
    source_path = data_dir / "source-expressions.json"
    source_bytes = source_path.read_bytes()
    source_hash = sha256_bytes(source_bytes)
    source_doc = load_object(source_path)
    project = str(source_doc.get("project", ""))
    if not project:
        raise IngestError("source-expressions.json has no project")
    verify_activation(case_dir, source_hash, project)

    findings_doc = load_object(data_dir / "findings.json")
    checked_doc = load_object(data_dir / "fact-check.json")
    if findings_doc.get("project") != project or checked_doc.get("project") != project:
        raise IngestError("case artifact project mismatch")
    findings = {str(item.get("id")): item for item in findings_doc.get("findings", [])}
    checked = {
        str(item.get("finding_id")): item
        for item in fact_check_rows(checked_doc)
        if item.get("finding_id")
    }

    registry = load_object(vault / "claims" / "_registry.json")
    registry_entries = {
        str(entry.get("id")): entry for entry in registry.get("claims", []) if entry.get("id")
    }
    claims_by_finding: dict[str, tuple[str, Path, str]] = {}
    for claim_id, entry in registry_entries.items():
        if entry.get("project") != project:
            continue
        if entry.get("verdict") not in ALLOWED_VERDICTS:
            raise IngestError(f"vault claim {claim_id} is not eligible")
        note_path = resolve_claim_path(
            vault, str(entry.get("file", f"claims/{claim_id}.md"))
        )
        try:
            note_text = note_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IngestError(f"cannot read claim note {note_path}: {exc}") from exc
        frontmatter = parse_frontmatter(note_text, note_path)
        finding_id = frontmatter.get("finding_id", "")
        reason = eligibility_reason(findings.get(finding_id), checked.get(finding_id))
        if reason:
            raise IngestError(f"vault claim {claim_id} no longer passes eligibility: {reason}")
        claims_by_finding[finding_id] = (claim_id, note_path, note_text)

    snapshots_by_claim: dict[str, list[dict[str, Any]]] = {}
    skipped: list[dict[str, str]] = []
    for expression in source_doc.get("expressions", []):
        if not isinstance(expression, dict):
            raise IngestError("source expression entry must be an object")
        expression_id = str(expression.get("id", ""))
        for link in expression.get("finding_links", []):
            finding_id = str(link.get("finding_id", ""))
            target = claims_by_finding.get(finding_id)
            if target is None:
                reason = eligibility_reason(findings.get(finding_id), checked.get(finding_id))
                skipped.append({
                    "expression_id": expression_id,
                    "finding_id": finding_id,
                    "reason": reason or "no eligible vault claim",
                })
                continue
            claim_id = target[0]
            snapshots_by_claim.setdefault(claim_id, []).append(
                build_snapshot(project, finding_id, expression, link)
            )
        if not expression.get("finding_links"):
            skipped.append({
                "expression_id": expression_id,
                "finding_id": "",
                "reason": "no finding links",
            })

    updates: dict[Path, bytes] = {}
    written_snapshot_ids: list[str] = []
    claim_ids: list[str] = []
    for finding_id, (claim_id, note_path, note_text) in sorted(claims_by_finding.items()):
        incoming = snapshots_by_claim.get(claim_id, [])
        if not incoming:
            continue  # Keep legacy expression-less claims byte-for-byte intact.
        block = parse_managed_block(note_text, note_path)
        existing = {item.get("snapshot_id"): item for item in block["snapshots"]}
        accepted: list[dict[str, Any]] = []
        for snapshot in incoming:
            key = snapshot["snapshot_id"]
            current = existing.get(key)
            if current is None and snapshot["lifecycle_state"] != "activated":
                skipped.append({
                    "expression_id": snapshot["expression_id"],
                    "finding_id": finding_id,
                    "reason": "inactive expression was never previously ingested",
                })
                continue
            if current is not None:
                # The passage core is immutable; only its append-only lifecycle
                # may advance when the case expression becomes inactive.
                immutable_current = dict(current)
                immutable_new = dict(snapshot)
                for field in ("lifecycle_state", "lifecycle_events"):
                    immutable_current.pop(field, None)
                    immutable_new.pop(field, None)
                if immutable_current != immutable_new:
                    raise IngestError(f"snapshot core changed for {key}")
                prior_events = current.get("lifecycle_events", [])
                new_events = snapshot.get("lifecycle_events", [])
                if new_events[: len(prior_events)] != prior_events:
                    raise IngestError(f"lifecycle history is not append-only for {key}")
            existing[key] = snapshot
            accepted.append(snapshot)
            written_snapshot_ids.append(key)
        if not accepted:
            continue
        snapshots = [existing[key] for key in sorted(existing)]
        event_payload = {
            "claim_id": claim_id,
            "project": project,
            "source_expression_input_sha256": source_hash,
            "snapshot_ids": sorted(snapshot["snapshot_id"] for snapshot in accepted),
        }
        event = {
            "event_id": sha256_bytes(canonical_json_bytes(event_payload)),
            "event": "source_expressions_ingested",
            **event_payload,
        }
        events = {item.get("event_id"): item for item in block["ingest_events"]}
        if event["event_id"] in events and events[event["event_id"]] != event:
            raise IngestError(f"ingest event collision for {claim_id}")
        events[event["event_id"]] = event
        updated_block = {
            "schema_version": "1.0",
            "snapshots": snapshots,
            "ingest_events": [events[key] for key in sorted(events)],
        }
        updates[note_path] = render_managed_block(note_text, updated_block)
        claim_ids.append(claim_id)

    skipped.sort(key=lambda item: (item["expression_id"], item["finding_id"], item["reason"]))
    receipt = load_object(data_dir / "ingestion.json") if (data_dir / "ingestion.json").exists() else {
        "schema_version": "1.0",
        "status": "completed",
    }
    receipt["source_expression_ingest"] = {
        "writer_version": TOOL_VERSION,
        "project": project,
        "source_expression_input_sha256": source_hash,
        "claim_ids": sorted(claim_ids),
        "written_snapshot_ids": sorted(set(written_snapshot_ids)),
        "skipped": skipped,
    }
    updates[data_dir / "ingestion.json"] = rendered_json(receipt)
    return updates, receipt["source_expression_ingest"]


def publish_updates(
    updates: dict[Path, bytes],
    writer: Callable[[Path, bytes], None] = atomic_replace,
) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in updates}
    written: list[Path] = []
    try:
        for path in sorted(updates, key=lambda item: str(item)):
            if originals[path] == updates[path]:
                continue
            writer(path, updates[path])
            written.append(path)
    except Exception as exc:
        for path in reversed(written):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_replace(path, original)
        raise IngestError(f"publication failed and was rolled back: {exc}") from exc


def ingest(
    case_dir: Path,
    vault: Path,
    writer: Callable[[Path, bytes], None] = atomic_replace,
    lock_held: bool = False,
) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    vault = vault.resolve()
    lock = vault / ".ingest-lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    owns_lock = not lock_held
    descriptor: int | None = None
    if lock_held:
        if not lock.is_file():
            raise IngestError("--lock-held requires the existing vault .ingest-lock")
        project = str(load_object(case_dir / "data" / "source-expressions.json").get("project", ""))
        if lock.read_text(encoding="utf-8").split(maxsplit=1)[0] != project:
            raise IngestError("existing .ingest-lock is not owned by this project")
    else:
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise IngestError(".ingest-lock present — another ingestion is in progress") from exc
    try:
        if descriptor is not None:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{TOOL_VERSION} {case_dir.name}\n")
                handle.flush()
                os.fsync(handle.fileno())
        updates, receipt = build_updates(case_dir, vault)
        publish_updates(updates, writer)
        return receipt
    finally:
        if owns_lock:
            lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument(
        "--lock-held",
        action="store_true",
        help="reuse a project-owned .ingest-lock; caller remains responsible for cleanup",
    )
    args = parser.parse_args()
    try:
        receipt = ingest(args.case_dir, args.vault, lock_held=args.lock_held)
    except (IngestError, OSError) as exc:
        print(f"FAIL {exc}")
        return 1
    print(
        "ok   source-expression ingest: "
        f"{len(receipt['written_snapshot_ids'])} snapshot(s), "
        f"{len(receipt['skipped'])} skipped"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
