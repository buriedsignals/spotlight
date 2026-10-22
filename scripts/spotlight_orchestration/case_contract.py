"""Case-contract writers: born activation and gate-approval binding."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .case_writer import atomic_write_files

TOOL_VERSION = "spotlight-case-contract/1"
CONTRACT = "data/case-contract.json"
ARTIFACTS = {
    "findings_sha256": "data/findings.json",
    "fact_check_sha256": "data/fact-check.json",
    "evidence_bundle_sha256": "data/evidence-bundle.json",
    "source_expressions_sha256": "data/source-expressions.json",
}


class ContractError(Exception):
    pass


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
    if (case / CONTRACT).exists():
        raise ContractError("the case already has a case contract")
    findings = case / "data" / "findings.json"
    if findings.exists():
        raise ContractError("the case already has findings; migrate a legacy case instead of re-activating it")
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
    expressions = {"schema_version": "1.0", "project": doc["project"], "created_at": _now(), "expressions": []}
    outputs = {CONTRACT: _bytes(doc)}
    if not (case / "data" / "source-expressions.json").exists():
        outputs["data/source-expressions.json"] = _bytes(expressions)
    atomic_write_files(case, outputs, data_descriptor=data_descriptor)
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
