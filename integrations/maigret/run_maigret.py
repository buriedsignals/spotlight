#!/usr/bin/env python3
"""Run Maigret through Spotlight's bounded integration contract."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _runner import (  # noqa: E402
    IntegrationError,
    RunManifest,
    forbid_verified_statuses,
    integration_run_dir,
    load_request,
    run_subprocess,
    validate_run_id,
    validate_slug,
    write_json_atomic,
)


USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
ALLOWED_FORMATS = {"json", "csv", "txt"}


def validate_request(data: dict[str, Any]) -> dict[str, Any]:
    project = validate_slug(data.get("project"), "project")
    run_id = validate_run_id(data.get("run_id"))
    usernames = data.get("usernames")
    if not isinstance(usernames, list) or not usernames:
        raise IntegrationError("usernames must be a non-empty list")
    clean_usernames = []
    for username in usernames:
        if not isinstance(username, str) or not USERNAME_RE.match(username):
            raise IntegrationError(f"invalid username: {username!r}")
        clean_usernames.append(username)

    site_tags = data.get("site_tags", [])
    if site_tags is None:
        site_tags = []
    if not isinstance(site_tags, list):
        raise IntegrationError("site_tags must be a list")
    clean_tags = []
    for tag in site_tags:
        if not isinstance(tag, str) or not TAG_RE.match(tag):
            raise IntegrationError(f"invalid site tag: {tag!r}")
        clean_tags.append(tag)

    formats = data.get("formats", ["json"])
    if not isinstance(formats, list) or not formats:
        raise IntegrationError("formats must be a non-empty list")
    clean_formats = []
    for fmt in formats:
        if fmt not in ALLOWED_FORMATS:
            raise IntegrationError(f"unsupported format: {fmt!r}")
        clean_formats.append(fmt)

    if data.get("use_ai") or data.get("ai"):
        raise IntegrationError("Maigret --ai is prohibited in Spotlight-managed runs")
    if data.get("tor"):
        raise IntegrationError("Tor mode is not part of the bounded Spotlight Maigret profile")
    if data.get("scan_all_sites", False) is not False:
        raise IntegrationError("scan_all_sites must remain false until an explicit approval path exists")

    timeout = int(data.get("timeout_seconds", 45))
    if timeout < 5 or timeout > 600:
        raise IntegrationError("timeout_seconds must be between 5 and 600")

    return {
        "project": project,
        "run_id": run_id,
        "usernames": clean_usernames,
        "site_tags": clean_tags,
        "formats": clean_formats,
        "scan_all_sites": False,
        "timeout_seconds": timeout,
    }


def build_command(request: dict[str, Any], output_dir: Path) -> list[str]:
    command = ["maigret", *request["usernames"], "--timeout", str(request["timeout_seconds"])]
    for tag in request["site_tags"]:
        command.extend(["--tags", tag])
    for fmt in request["formats"]:
        command.extend([f"--{fmt}", str(output_dir / f"maigret.{fmt}")])
    return command


def normalize_json_output(path: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    leads: list[dict[str, Any]] = []
    for username, sites in raw.items() if isinstance(raw, dict) else []:
        if not isinstance(sites, dict):
            continue
        for site_name, value in sites.items():
            if not isinstance(value, dict):
                continue
            url = value.get("url_user") or value.get("url") or value.get("profile_url")
            status = value.get("status")
            if not url:
                continue
            leads.append(
                {
                    "username": username,
                    "site": site_name,
                    "profile_url": url,
                    "maigret_status": status,
                    "tags": value.get("tags", []),
                    "run_id": request["run_id"],
                    "verification_status": "unverified",
                }
            )
    forbid_verified_statuses(leads, "maigret_leads")
    return leads


def maigret_version(manifest: RunManifest) -> str | None:
    """Record which Maigret produced the run; a failed probe is a warning, not an error."""
    try:
        proc = run_subprocess(["maigret", "--version"], timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        manifest.warnings.append(f"maigret --version failed: {exc}")
        return None
    text = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0 or not text:
        manifest.warnings.append("maigret --version returned no version")
        return None
    return text.splitlines()[0][:200]


def run(request_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    request = validate_request(load_request(request_path))
    output_dir = integration_run_dir(request["project"], "maigret", request["run_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_dir / "request.json", request)

    manifest = RunManifest("maigret", request["project"], request["run_id"], request)
    command = build_command(request, output_dir)
    manifest.command = command

    if shutil.which("maigret") is None and not dry_run:
        manifest.finish("error", error="maigret binary not found on PATH")
        manifest.record_outputs(output_dir)
        write_json_atomic(output_dir / "run-manifest.json", manifest.as_dict())
        return {"status": "error", "error": "maigret binary not found on PATH", "run_dir": str(output_dir)}

    if dry_run:
        leads: list[dict[str, Any]] = []
        manifest.finish("dry_run", exit_code=0)
    else:
        manifest.tool_version = maigret_version(manifest)
        proc = run_subprocess(command, timeout=request["timeout_seconds"] + 30)
        (output_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (output_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            manifest.finish("error", exit_code=proc.returncode, error="maigret exited non-zero")
            leads = []
        else:
            leads = normalize_json_output(output_dir / "maigret.json", request)
            manifest.finish("ok", exit_code=proc.returncode)

    write_json_atomic(output_dir / "normalized-leads.json", {"leads": leads})
    manifest.record_outputs(output_dir)
    write_json_atomic(output_dir / "run-manifest.json", manifest.as_dict())
    return {
        "status": manifest.status,
        "run_dir": str(output_dir),
        "manifest": str(output_dir / "run-manifest.json"),
        "normalized_leads": str(output_dir / "normalized-leads.json"),
        "lead_count": len(leads),
        "command": command,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded Maigret account discovery.")
    parser.add_argument("request_json")
    parser.add_argument("--dry-run", action="store_true", help="Validate request and print command without invoking Maigret")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.request_json, dry_run=args.dry_run), indent=2))
        return 0
    except (OSError, IntegrationError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
