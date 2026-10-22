"""Case-contract writers: born activation and gate-approval binding."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .case_writer import atomic_write_files
from .contract import OrchestrationError

TOOL_VERSION = "spotlight-case-contract/1"
CONTRACT = "data/case-contract.json"
ARTIFACTS = {
    "findings_sha256": "data/findings.json",
    "fact_check_sha256": "data/fact-check.json",
    "evidence_bundle_sha256": "data/evidence-bundle.json",
    "source_expressions_sha256": "data/source-expressions.json",
}
# Optional: bound when the case has source positions at approval.
POSITIONS = "data/source-positions.json"


class ContractError(OrchestrationError):
    """A case-contract write that is refused; the CLI reports it like any
    other orchestration refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bytes(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _project(case: Path) -> str:
    for relative in ("data/methodology.json", "data/findings.json"):
        path = case / relative
        if path.is_file():
            try:
                project = json.loads(path.read_text(encoding="utf-8")).get("project")
            except (OSError, ValueError):
                continue
            if isinstance(project, str) and project:
                return project
    return case.name


def activate_new(case: Path, data_descriptor: int) -> dict[str, Any]:
    contract_path = case / CONTRACT
    expressions_path = case / "data" / "source-expressions.json"
    for relative in ("data/findings.json", "findings.json", "data/fact-check.json", "data/investigation-log.json"):
        if (case / relative).exists():
            raise ContractError(f"the case already has {relative}; migrate a legacy case instead of re-activating it")
    if contract_path.exists():
        try:
            existing = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError(f"cannot read the existing case contract: {exc}") from exc
        events = existing.get("activation_events") if isinstance(existing, dict) else None
        born_only = isinstance(events, list) and len(events) == 1 and events[0].get("kind") == "born"
        if not (born_only and not expressions_path.exists()):
            raise ContractError("the case already has a case contract")
        # An earlier run stopped between its two writes: finish it.
        doc = existing
    elif expressions_path.exists():
        raise ContractError("the case already has passages (data/source-expressions.json); a pilot case is migrated, not re-born")
    else:
        doc = {
            "schema_version": "1.0",
            "project": _project(case),
            "current_contract_version": "1.1",
            "activation_events": [{
                "event_id": f"born-{secrets.token_hex(8)}",
                "kind": "born",
                "activated_contract_version": "1.1",
                "activated_at": _now(),
                "tool_version": TOOL_VERSION,
            }],
        }
    # The passages file first, the contract last: the contract is the commit
    # point, so a crash between the two leaves an unactivated case.
    if not expressions_path.exists():
        expressions = {"schema_version": "1.0", "project": doc["project"], "created_at": _now(), "expressions": []}
        atomic_write_files(case, {"data/source-expressions.json": _bytes(expressions)}, data_descriptor=data_descriptor)
    if not contract_path.exists():
        atomic_write_files(case, {CONTRACT: _bytes(doc)}, data_descriptor=data_descriptor)
    return doc


def bind(case: Path, data_descriptor: int) -> dict[str, Any] | None:
    """Append a binding of the current artifacts; None when the case is not
    activated (legacy cases have nothing to bind)."""
    path = case / CONTRACT
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot read the case contract: {exc}") from exc
    hashes = {}
    for key, relative in ARTIFACTS.items():
        artifact = case / relative
        if not artifact.is_file():
            raise ContractError(f"cannot bind: {relative} is missing")
        hashes[key] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    positions = case / POSITIONS
    if positions.is_file():
        hashes["source_positions_sha256"] = hashlib.sha256(positions.read_bytes()).hexdigest()
    events = doc.get("activation_events")
    if not isinstance(events, list) or not events:
        raise ContractError("the case contract has no activation event")
    events.append({
        "event_id": f"binding-{secrets.token_hex(8)}",
        "kind": "binding",
        "bound_at": _now(),
        "tool_version": TOOL_VERSION,
        "artifact_hashes": hashes,
    })
    atomic_write_files(case, {CONTRACT: _bytes(doc)}, data_descriptor=data_descriptor)
    return doc
