"""Read-only dependency and output checks used by the resolver."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .contract import DEPENDENCY_STATUS_VERSION, OrchestrationError, SCRIPT_DIR
from .storage import case_path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_hashes(case: Path, inputs: tuple[str, ...]) -> dict[str, str]:
    paths = {relative: case_path(case, relative) for relative in inputs}
    missing = [relative for relative, path in paths.items() if not path.is_file()]
    if missing:
        raise OrchestrationError(f"required case inputs are missing: {', '.join(missing)}")
    return {relative: sha256(path) for relative, path in paths.items()}


def receipt_matches(receipt: object, hashes: dict[str, str]) -> bool:
    return isinstance(receipt, dict) and receipt.get("input_sha256") == hashes


def digest_receipt_matches(receipt: object, dependency_digest: str) -> bool:
    return isinstance(receipt, dict) and receipt.get("dependency_digest") == dependency_digest


def validate_approval(actor: str, approved_at: str) -> None:
    if not actor.strip():
        raise OrchestrationError("approved_by must name the approving human")
    try:
        parsed = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OrchestrationError("approved_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OrchestrationError("approved_at must include a timezone")


def attributable_approval(receipt: dict[str, Any]) -> bool:
    actor = receipt.get("approved_by")
    approved_at = receipt.get("approved_at")
    if not isinstance(actor, str) or not isinstance(approved_at, str):
        return False
    try:
        validate_approval(actor, approved_at)
    except OrchestrationError:
        return False
    return True


def approval_matches(receipt: object, hashes: dict[str, str]) -> bool:
    return (
        isinstance(receipt, dict)
        and receipt_matches(receipt, hashes)
        and attributable_approval(receipt)
    )


def gate1_approval_matches(receipt: object, dependency_digest: str) -> bool:
    return (
        isinstance(receipt, dict)
        and digest_receipt_matches(receipt, dependency_digest)
        and attributable_approval(receipt)
    )


def outputs_match(case: Path, receipt: object) -> bool:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("output_sha256"), dict):
        return False
    outputs = receipt["output_sha256"]
    if not outputs:
        return False
    for relative, expected in outputs.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            return False
        output_path = case_path(case, relative)
        if not output_path.is_file() or sha256(output_path) != expected:
            return False
    return True


def gate1_dependencies(case: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "build-provenance-manifest.py"),
                str(case),
                "--dependency-digest",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise OrchestrationError(f"cannot hash Gate 1 dependencies: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "dependency builder failed"
        raise OrchestrationError(detail)
    try:
        snapshot = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OrchestrationError("dependency builder returned invalid JSON") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != DEPENDENCY_STATUS_VERSION
        or not isinstance(snapshot.get("ready"), bool)
        or not isinstance(snapshot.get("missing"), list)
        or not isinstance(snapshot.get("execution_ready"), bool)
        or not isinstance(snapshot.get("execution_missing"), list)
        or not isinstance(snapshot.get("gate1_missing"), list)
        or not isinstance(snapshot.get("dependency_digest"), str)
    ):
        raise OrchestrationError("dependency builder returned an unsupported contract")
    return snapshot


def require_gate1_digest(case: Path) -> str:
    snapshot = gate1_dependencies(case)
    if not snapshot["ready"]:
        raise OrchestrationError(
            f"required Gate 1 inputs are missing: {', '.join(snapshot['missing'])}"
        )
    return str(snapshot["dependency_digest"])


def provenance_matches(case: Path, dependency_digest: str) -> bool:
    path = case_path(case, "data/provenance-manifest.json")
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("input_set_hash") == dependency_digest


def review_is_fresh(case: Path, approval: object) -> bool:
    path = case_path(case, "review.html")
    if not path.is_file():
        return False
    prior_hash = approval.get("review_sha256_at_approval") if isinstance(approval, dict) else None
    return not isinstance(prior_hash, str) or sha256(path) != prior_hash
