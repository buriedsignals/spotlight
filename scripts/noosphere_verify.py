"""Local verification of a Noosphere signing receipt (HASHING.md, contract 1.x).

The structural checks in build-provenance-manifest.py establish that a receipt
matches what was sent. This module adds the cryptographic step: c2patool
verifies the C2PA sidecar over the decoded record bytes against pinned trust
anchors. Only a ``Trusted`` result lets a package be marked ``signed``.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# The official C2PA trust list, pinned (source and commit in
# integrations/noosphere-c2pa/integration.md). Noosphere's development CA is
# not on it, so development-grade receipts stay unverified.
DEFAULT_TRUST_ANCHORS = (
    Path(__file__).resolve().parent.parent / "integrations" / "noosphere-c2pa" / "c2pa-trust-list.pem"
)
VERIFIER_TIMEOUT_SECONDS = 60


def c2patool_path() -> str | None:
    return os.environ.get("C2PATOOL") or shutil.which("c2patool")


def trust_anchors_path() -> Path:
    override = os.environ.get("NOOSPHERE_C2PA_TRUST_ANCHORS")
    return Path(override) if override else DEFAULT_TRUST_ANCHORS


def verify_sidecar(response: dict[str, Any]) -> dict[str, Any]:
    """Run c2patool over the receipt's record bytes and sidecar.

    Returns a verification record with ``verified`` true only when c2patool
    reports the manifest ``Trusted`` against the pinned anchors. Never raises
    for a missing tool or a failed verification; the caller keeps the receipt
    as received_unverified instead.
    """
    tool = c2patool_path()
    anchors = trust_anchors_path()
    result: dict[str, Any] = {"verifier": "c2patool", "verified": False}
    if not tool:
        result["reason"] = "c2patool not found"
        return result
    if not anchors.is_file():
        result["reason"] = f"trust anchors not found: {anchors.name}"
        return result
    try:
        record = base64.b64decode(response["record_b64"], validate=True)
        sidecar = base64.b64decode(
            response.get("c2pa_manifest_b64", response.get("c2pa_manifest")), validate=True
        )
    except (KeyError, TypeError, ValueError):
        result["reason"] = "record_b64 or C2PA sidecar is not valid base64"
        return result

    with tempfile.TemporaryDirectory() as raw_tmp:
        record_path = Path(raw_tmp) / "record.bin"
        sidecar_path = Path(raw_tmp) / "manifest.c2pa"
        record_path.write_bytes(record)
        sidecar_path.write_bytes(sidecar)
        try:
            version = subprocess.run(
                [tool, "--version"], capture_output=True, text=True, timeout=VERIFIER_TIMEOUT_SECONDS
            ).stdout.strip()
            run = subprocess.run(
                [
                    tool, str(record_path), "--external-manifest", str(sidecar_path),
                    "trust", "--trust_anchors", str(anchors),
                ],
                capture_output=True,
                text=True,
                timeout=VERIFIER_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"c2patool did not run: {type(exc).__name__}"
            return result

    result["version"] = version
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError:
        result["reason"] = f"c2patool exited {run.returncode} without a JSON report"
        return result
    state = report.get("validation_state")
    active = (report.get("validation_results") or {}).get("activeManifest") or {}
    result.update({
        "validation_state": state,
        "failures": sorted({item.get("code", "") for item in active.get("failure", [])}),
        "trust_anchors": anchors.name,
    })
    result["verified"] = state == "Trusted"
    if not result["verified"]:
        result["reason"] = f"c2patool validation_state is {state!r}, not 'Trusted'"
    return result
