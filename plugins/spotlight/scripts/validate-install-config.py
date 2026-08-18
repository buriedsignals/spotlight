#!/usr/bin/env python3
"""Validate Spotlight knowledge activation without mutating install or case state."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
ASSURANCE_TIERS = {"local_conformance"}
MIGRATED_WORKFLOWS = {"investigator", "fact_checker", "dedup", "prior_verdict"}


def _graph_module() -> Any:
    path = Path(__file__).with_name("knowledge_destination.py")
    spec = importlib.util.spec_from_file_location("spotlight_activation_graph", path)
    if spec is None or spec.loader is None:
        raise ValueError("local graph adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_activation(config_path: Path) -> dict[str, Any]:
    blockers: list[str] = []
    try:
        config = _load(config_path)
        destination = config["knowledge_destination"]
        activation_ref = config.get("knowledge_activation", {
            "schema_version": "spotlight-activation-reference/v1",
            "receipt_path": ".knowledge-workspace/spotlight-activation.json",
        })
        workspace = Path(destination["workspace_path"]).expanduser().resolve()
        receipt_path = Path(activation_ref["receipt_path"]).expanduser()
        if not receipt_path.is_absolute():
            receipt_path = workspace / receipt_path
        if not _inside(workspace, receipt_path):
            raise ValueError("activation receipt escapes the configured workspace")
        receipt = _load(receipt_path)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"schema_version": "spotlight-activation-status/v1", "status": "inactive", "assurance": None, "suppress_new_claim_notes": False, "blockers": [str(exc)]}

    assurance = receipt.get("assurance")
    if receipt.get("schema_version") != "spotlight-knowledge-activation/v1" or receipt.get("status") != "active":
        blockers.append("activation receipt is not an active supported contract")
    if assurance not in ASSURANCE_TIERS:
        blockers.append("activation assurance tier is invalid")
    for key in ("destination_id", "project_id", "namespace", "projection_namespace", "story_namespace", "graph_database_path"):
        if receipt.get(key) != destination.get(key):
            blockers.append(f"activation {key} does not match configured destination")
    try:
        routes = _load(workspace / ".knowledge-workspace" / "routes.json")
        if routes.get("schema_version") != "knowledge-routes/v1" or routes.get("routes", {}).get("spotlight_verified") != destination.get("namespace"):
            raise ValueError("configured namespace differs from the sealed Spotlight route")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        blockers.append(str(exc))
    migration_ref = receipt.get("workflow_migration_receipt")
    if not isinstance(migration_ref, dict) or not SHA256_RE.fullmatch(str(migration_ref.get("sha256", ""))):
        blockers.append("workflow migration receipt binding is missing")
    else:
        migration_path = workspace / str(migration_ref.get("path", ""))
        try:
            if not _inside(workspace, migration_path):
                raise ValueError("workflow migration receipt escapes the workspace")
            raw = migration_path.read_bytes()
            migration = json.loads(raw)
            if hashlib.sha256(raw).hexdigest() != migration_ref["sha256"]:
                raise ValueError("workflow migration receipt hash mismatch")
            if migration.get("schema_version") != "spotlight-workflow-migration/v1":
                raise ValueError("workflow migration receipt schema is invalid")
            workflows = migration.get("workflows", {})
            if set(workflows) != MIGRATED_WORKFLOWS or any(workflows[name] != "migrated" for name in MIGRATED_WORKFLOWS):
                raise ValueError("all four workflows must be explicitly migrated")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            blockers.append(str(exc))

    provider = destination.get("provider_policy", {})
    provider_hash = hashlib.sha256(json.dumps(provider, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if receipt.get("provider_policy_sha256") != provider_hash:
        blockers.append("activation provider policy binding does not match configuration")
    if assurance == "local_conformance" and not (
        provider.get("provider_mode") == "local_device"
        and provider.get("model") == "bge-m3:567m"
        and provider.get("network_egress") == "denied"
        and provider.get("retention_days") == 0
    ):
        blockers.append("local activation requires the configured local BGE-M3 no-egress provider policy")
    return {
        "schema_version": "spotlight-activation-status/v1",
        "status": "active" if not blockers else "inactive",
        "assurance": assurance if assurance in ASSURANCE_TIERS else None,
        "suppress_new_claim_notes": not blockers,
        "production_security": False,
        "blockers": blockers,
    }


def issue_local_activation(config_path: Path) -> dict[str, Any]:
    """Bind local activation to destination config and the workflow migration."""
    config = _load(config_path)
    destination = config["knowledge_destination"]
    activation_ref = config.get("knowledge_activation", {
        "schema_version": "spotlight-activation-reference/v1",
        "receipt_path": ".knowledge-workspace/spotlight-activation.json",
    })
    workspace = Path(destination["workspace_path"]).expanduser().resolve()
    migration_path = workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json"
    if not _inside(workspace, migration_path):
        raise ValueError("workflow migration receipt escapes the workspace")
    migration_raw = migration_path.read_bytes()
    migration = json.loads(migration_raw)
    if migration.get("schema_version") != "spotlight-workflow-migration/v1" or set(migration.get("workflows", {})) != MIGRATED_WORKFLOWS or any(migration["workflows"][name] != "migrated" for name in MIGRATED_WORKFLOWS):
        raise ValueError("workflow migration receipt is incomplete")
    routes = _load(workspace / ".knowledge-workspace" / "routes.json")
    if routes.get("schema_version") != "knowledge-routes/v1" or routes.get("routes", {}).get("spotlight_verified") != destination.get("namespace"):
        raise ValueError("configured namespace differs from the sealed Spotlight route")
    provider = destination.get("provider_policy", {})
    if not (provider.get("provider_mode") == "local_device" and provider.get("model") == "bge-m3:567m" and provider.get("network_egress") == "denied" and provider.get("retention_days") == 0):
        raise ValueError("local activation requires the configured local BGE-M3 no-egress provider policy")
    receipt = {
        "schema_version": "spotlight-knowledge-activation/v1", "status": "active", "assurance": "local_conformance",
        **{key: destination[key] for key in ("destination_id", "project_id", "namespace", "projection_namespace", "story_namespace", "graph_database_path")},
        "provider_policy_sha256": hashlib.sha256(json.dumps(provider, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "workflow_migration_receipt": {
            "path": str(migration_path.relative_to(workspace)),
            "sha256": hashlib.sha256(migration_raw).hexdigest(),
        },
    }
    receipt_path = Path(activation_ref["receipt_path"]).expanduser()
    if not receipt_path.is_absolute():
        receipt_path = workspace / receipt_path
    if not _inside(workspace, receipt_path):
        raise ValueError("activation receipt escapes the configured workspace")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=receipt_path.parent, prefix=".activation-", delete=False) as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, receipt_path)
    return validate_activation(config_path)


def initialize_local_activation(config_path: Path) -> dict[str, Any]:
    """Record the installed graph-aware workflows, then activate local projection."""
    config = _load(config_path)
    workspace = Path(config["knowledge_destination"]["workspace_path"]).expanduser().resolve()
    migration_path = workspace / ".knowledge-workspace" / "spotlight-workflow-migration.json"
    if not _inside(workspace, migration_path):
        raise ValueError("workflow migration receipt escapes the workspace")
    migration = {
        "schema_version": "spotlight-workflow-migration/v1",
        "implementation": "spotlight-direct-local/v1",
        "workflows": {name: "migrated" for name in sorted(MIGRATED_WORKFLOWS)},
    }
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(migration, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=migration_path.parent, prefix=".migration-", delete=False) as handle:
        handle.write(raw)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, migration_path)
    return issue_local_activation(config_path)


def _case_projection_ready(destination: dict[str, Any], batch: dict[str, Any]) -> tuple[bool, str]:
    """Verify that the exact reviewed case batch has a current completed view."""
    workspace = Path(destination["workspace_path"]).expanduser().resolve()
    database = Path(destination["graph_database_path"]).expanduser()
    if not database.is_absolute():
        database = workspace / database
    if not _inside(workspace, database):
        return False, "configured graph database escapes the workspace"
    graph = _graph_module()
    connection = None
    try:
        connection = graph.open_existing_database(database)
        graph.verify_database(connection)
        project = batch["source_case"]["project"]
        case_id = graph._case_id_for_project(project)
        batch_row = connection.execute(
            "SELECT payload_sha256,source_project FROM batches WHERE batch_id=?",
            (batch["batch_id"],),
        ).fetchone()
        if (
            batch_row is None
            or batch_row["source_project"] != project
            or batch_row["payload_sha256"] != graph.payload_sha256(batch)
        ):
            return False, "case knowledge batch is not the exact committed graph batch"
        head = connection.execute(
            "SELECT job.*,receipt.workspace_receipt_ref,receipt.workspace_receipt_sha256 "
            "FROM projection_heads AS head JOIN projection_jobs AS job "
            "ON job.job_id=head.current_job_id LEFT JOIN projection_final_receipts AS receipt "
            "ON receipt.job_id=job.job_id WHERE head.case_id=? AND head.destination_id=?",
            (case_id, destination["destination_id"]),
        ).fetchone()
        if head is None or head["status"] != "completed" or not head["workspace_receipt_ref"] or not head["workspace_receipt_sha256"]:
            return False, "case has no current completed workspace projection"
        policy_row = connection.execute(
            "SELECT payload_json FROM case_policy_receipts WHERE case_id=? AND destination_id=? "
            "ORDER BY policy_revision DESC LIMIT 1",
            (case_id, destination["destination_id"]),
        ).fetchone()
        if policy_row is None:
            return False, "case has no signed destination policy"
        policy = json.loads(policy_row["payload_json"])
        issued = datetime.fromisoformat(policy["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if (
            policy.get("status") != "active"
            or destination["destination_id"] not in policy.get("allowed_destinations", [])
            or not issued <= now < expires
        ):
            return False, "case destination policy is not currently active"
        return True, ""
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, graph.ContractError) as exc:
        return False, f"case graph/projection verification failed: {exc}"
    finally:
        if connection is not None:
            connection.close()


def claim_note_gate(config_path: Path, case_dir: Path) -> dict[str, Any]:
    status = validate_activation(config_path)
    batch = case_dir / "data" / "knowledge-batch.json"
    graph_enabled = False
    try:
        value = _load(batch)
        decisions = value.get("review_decisions", [])
        graph_enabled = (
            value.get("schema_version") == "1.0"
            and isinstance(value.get("batch_id"), str)
            and isinstance(value.get("source_case"), dict)
            and any(
                isinstance(decision, dict)
                and decision.get("disposition") == "approved"
                and decision.get("subject", {}).get("kind") == "source_case"
                for decision in decisions
            )
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        graph_enabled = False
    projection_ready, projection_blocker = False, "case has no valid reviewed knowledge-batch.json"
    if graph_enabled and status["status"] == "active":
        try:
            config = _load(config_path)
            projection_ready, projection_blocker = _case_projection_ready(config["knowledge_destination"], value)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            projection_blocker = str(exc)
    if not graph_enabled or not projection_ready:
        status = dict(status)
        status["status"] = "inactive"
        status["suppress_new_claim_notes"] = False
        status["production_security"] = False
        status["blockers"] = [*status["blockers"], projection_blocker]
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--issue-local", action="store_true")
    parser.add_argument("--initialize-local", action="store_true")
    args = parser.parse_args()
    try:
        if args.issue_local and args.initialize_local:
            parser.error("--issue-local and --initialize-local are mutually exclusive")
        if args.initialize_local:
            if args.case_dir:
                parser.error("--initialize-local cannot be combined with --case-dir")
            result = initialize_local_activation(args.config)
        elif args.issue_local:
            if args.case_dir:
                parser.error("--issue-local cannot be combined with --case-dir")
            result = issue_local_activation(args.config)
        else:
            result = claim_note_gate(args.config, args.case_dir) if args.case_dir else validate_activation(args.config)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        result = {"schema_version": "spotlight-activation-status/v1", "status": "inactive", "assurance": None, "suppress_new_claim_notes": False, "production_security": False, "blockers": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "active" else 2


if __name__ == "__main__":
    raise SystemExit(main())
