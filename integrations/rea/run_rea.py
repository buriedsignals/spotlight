#!/usr/bin/env python3
"""Run one static REA analysis through Spotlight's bounded integration contract.

    python3 integrations/_runner.py rea request.json [--dry-run]

Only operations that read a local file without running it are allowed. REA's
runtime capture, browser, device (adb), native-decompiler, setup and update
commands are never reachable from here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _runner import (  # noqa: E402
    IntegrationError,
    RunManifest,
    case_dir,
    integration_run_dir,
    load_request,
    resolve_within,
    run_subprocess,
    validate_run_id,
    validate_slug,
    write_json_atomic,
)


# The reviewed release. REA ships breaking majors days apart; a different
# version is reported as a warning so the operator can see the drift.
REVIEWED_VERSION = "6.4.0"

# Spotlight operation -> REA subcommand. Each declares accessesNetwork: false in
# `rea capabilities` for the reviewed release.
OPERATIONS = {
    "javascript-application": "analyze-javascript-application",
    "network-capture": "inspect-web-network-capture",
    "managed-artifact": "inspect-managed-artifact",
    "managed-members": "inspect-managed-members",
    "android-package": "inspect-android-package",
}
CAPTURE_FORMATS = {"har", "mitmproxy"}


def validate_request(data: dict[str, Any]) -> dict[str, Any]:
    project = validate_slug(data.get("project"), "project")
    run_id = validate_run_id(data.get("run_id"))
    operation = data.get("operation")
    if operation not in OPERATIONS:
        raise IntegrationError(f"operation must be one of {sorted(OPERATIONS)}")
    raw_input = data.get("input")
    if not isinstance(raw_input, str) or not raw_input:
        raise IntegrationError("input must be a path relative to the case directory")
    # Containment is checked against the resolved path, so a symlink that
    # points outside the case directory is refused too.
    target = resolve_within(case_dir(project), raw_input)
    if not target.exists():
        raise IntegrationError(f"input does not exist: {raw_input}")

    capture_format = data.get("capture_format")
    if operation == "network-capture":
        if capture_format not in CAPTURE_FORMATS:
            raise IntegrationError("network-capture needs capture_format har or mitmproxy")
    elif capture_format is not None:
        raise IntegrationError("capture_format applies only to network-capture")

    timeout = data.get("timeout_seconds", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 10 <= timeout <= 3600:
        raise IntegrationError("timeout_seconds must be an integer between 10 and 3600")

    return {
        "project": project,
        "run_id": run_id,
        "operation": operation,
        "input": raw_input,
        "input_path": str(target),
        "capture_format": capture_format,
        "timeout_seconds": timeout,
    }


def build_command(request: dict[str, Any]) -> list[str]:
    command = ["rea", OPERATIONS[request["operation"]], request["input_path"]]
    if request["operation"] == "network-capture":
        command.append(request["capture_format"])
    return [*command, "--format", "json"]


def rea_version(manifest: RunManifest) -> str | None:
    try:
        proc = run_subprocess(["rea", "--version"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        manifest.warnings.append(f"rea --version failed: {exc}")
        return None
    version = proc.stdout.strip().splitlines()[0] if proc.returncode == 0 and proc.stdout.strip() else None
    if version is None:
        manifest.warnings.append("rea --version returned no version")
    elif version != REVIEWED_VERSION:
        manifest.warnings.append(f"rea {version} is not the reviewed release {REVIEWED_VERSION}")
    return version


def summarize(evidence: Any, request: dict[str, Any]) -> dict[str, Any]:
    subject = evidence.get("subject") if isinstance(evidence, dict) else None
    digest = subject.get("digest") if isinstance(subject, dict) else None
    return {
        "operation": request["operation"],
        "input": request["input"],
        "evidence_id": evidence.get("evidence_id") if isinstance(evidence, dict) else None,
        "subject_sha256": digest.get("sha256") if isinstance(digest, dict) else None,
        "evidence_path": "evidence.json",
        "verification_status": "unverified",
    }


def run(request_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    request = validate_request(load_request(request_path))
    output_dir = integration_run_dir(request["project"], "rea", request["run_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_dir / "request.json", request)

    manifest = RunManifest("rea", request["project"], request["run_id"], request)
    command = build_command(request)
    manifest.command = command
    summary: dict[str, Any] | None = None

    if dry_run:
        manifest.finish("dry_run", exit_code=0)
    elif shutil.which("rea") is None:
        manifest.finish("error", error=f"rea not found on PATH; install the reviewed release rea-agents@{REVIEWED_VERSION}")
    else:
        manifest.tool_version = rea_version(manifest)
        try:
            proc = run_subprocess(command, timeout=request["timeout_seconds"])
        except subprocess.TimeoutExpired:
            manifest.finish("error", error=f"rea timed out after {request['timeout_seconds']} s")
        else:
            # stdout is the Evidence JSON; stderr carries REA's progress lines.
            (output_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
            try:
                evidence = json.loads(proc.stdout)
            except ValueError:
                (output_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
                manifest.finish("error", exit_code=proc.returncode, error="rea did not return JSON evidence")
            else:
                write_json_atomic(output_dir / "evidence.json", evidence)
                summary = summarize(evidence, request)
                write_json_atomic(output_dir / "summary.json", summary)
                if proc.returncode == 0:
                    manifest.finish("ok", exit_code=0)
                else:
                    manifest.finish("error", exit_code=proc.returncode, error="rea exited non-zero")

    manifest.record_outputs(output_dir)
    write_json_atomic(output_dir / "run-manifest.json", manifest.as_dict())
    return {
        "status": manifest.status,
        "run_dir": str(output_dir),
        "manifest": str(output_dir / "run-manifest.json"),
        "summary": summary,
        "command": command,
        "errors": manifest.errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one static REA analysis on a case file.")
    parser.add_argument("request_json")
    parser.add_argument("--dry-run", action="store_true", help="Validate the request and print the command without running REA")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.request_json, dry_run=args.dry_run), indent=2))
        return 0
    except (OSError, IntegrationError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
