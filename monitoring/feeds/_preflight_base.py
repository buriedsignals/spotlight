"""Shared preflight helpers used by both monitoring/feeds/preflight.py
and integrations/preflight.py.

Both scripts have the same shape: scan a directory for manifest.json
files, check their declared env vars against the environment, optionally
run a per-kind smoke test, and report green/yellow/red per entry.

The specifics that differ (smoke-test function, display columns, exit-code
thresholds) are passed in by the caller.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable


def _load_dotenv(root: Path) -> None:
    """Load repo-root .env into os.environ without requiring python-dotenv.

    Uses a minimal parser that handles KEY=VALUE and KEY='VALUE' lines,
    skipping comments and blanks. Existing env vars are NOT overwritten
    (same semantics as `set -a; source .env; set +a` with pre-existing exports).
    """
    env_path = root / ".env"
    if not env_path.is_file():
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val


def discover_manifests(root: Path) -> list[dict]:
    """Scan `root` for subdirectories containing manifest.json. Returns
    each parsed manifest with an injected `_dir` field pointing at the
    source directory (as a string)."""
    entries: list[dict] = []
    if not root.is_dir():
        return entries
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)
        manifest["_dir"] = str(d)
        entries.append(manifest)
    return entries


def check_env_vars(manifest: dict) -> tuple[list[str], list[str]]:
    """Return (set_vars, missing_vars) for a manifest. Reads either
    `env_vars` or the legacy `required_env_vars` alias."""
    required = manifest.get("env_vars") or manifest.get("required_env_vars") or []
    set_vars = [v for v in required if os.environ.get(v)]
    missing_vars = [v for v in required if not os.environ.get(v)]
    return set_vars, missing_vars


def build_report(
    manifest: dict,
    smoke_fn: Callable[[dict], tuple[bool, str | None]] | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """Build a status report for one manifest. Status: red (missing env
    when requires_key), yellow (env set but smoke failed), green (ok)."""
    requires_key = manifest.get("requires_key", False)
    set_vars, missing_vars = check_env_vars(manifest)

    report = {
        "id": manifest["id"],
        "name": manifest.get("name", manifest["id"]),
        "category": manifest.get("category", ""),
        "requires_key": requires_key,
        "env_vars_required": manifest.get("env_vars") or manifest.get("required_env_vars") or [],
        "env_vars_set": set_vars,
        "env_vars_missing": missing_vars,
        "status": "green",
        "smoke_error": None,
    }
    if extra_fields:
        report.update(extra_fields)

    if requires_key and missing_vars:
        report["status"] = "red"
        return report

    if smoke_fn is not None:
        ok, err = smoke_fn(manifest)
        if not ok:
            report["status"] = "yellow"
            report["smoke_error"] = err
            return report

    return report


def summarize(reports: list[dict]) -> dict:
    return {
        "green": sum(1 for r in reports if r["status"] == "green"),
        "yellow": sum(1 for r in reports if r["status"] == "yellow"),
        "red": sum(1 for r in reports if r["status"] == "red"),
    }


def print_text_table(reports: list[dict], columns: list[tuple[str, str, int]]) -> None:
    """Emit a human-readable table with a trailing 'Missing env' column."""
    header = ""
    sep_width = 0
    for _field, label, width in columns:
        header += f"{label:<{width}} "
        sep_width += width + 1
    header += f"{'Missing env':<40}"
    sep_width += 40
    print(header)
    print("-" * sep_width)

    for r in reports:
        line = ""
        for field, _label, width in columns:
            val = str(r.get(field, ""))
            line += f"{val:<{width}} "
        missing = ", ".join(r["env_vars_missing"]) if r["env_vars_missing"] else "—"
        line += f"{missing:<40}"
        print(line)

    summary = summarize(reports)
    print()
    print(f"green={summary['green']}  yellow={summary['yellow']}  red={summary['red']}")


def run_preflight(
    root: Path,
    *,
    result_key: str,
    smoke_fn: Callable[[dict], tuple[bool, str | None]] | None = None,
    report_extra_fields: Callable[[dict], dict] | None = None,
    text_columns: list[tuple[str, str, int]] | None = None,
    description: str = "Spotlight preflight",
) -> None:
    """Full preflight entry point — argparse, discovery, reports, exit."""
    # Auto-load .env from repo root so preflight is self-contained regardless
    # of whether the caller sourced .env in the shell beforehand.
    # Walk up from `root` until we find a .env file or exhaust the tree.
    _dotenv_dir = root.resolve()
    while _dotenv_dir != _dotenv_dir.parent:
        if (_dotenv_dir / ".env").is_file():
            break
        _dotenv_dir = _dotenv_dir.parent
    _load_dotenv(_dotenv_dir)

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--smoke-test", action="store_true",
                        help="Also run a minimal probe against each entry")
    parser.add_argument("--json", action="store_true", help="Emit JSON (default)")
    parser.add_argument("--text", action="store_true", help="Emit human-readable table")
    parser.add_argument("--project", type=str, help="Project context (feeds only)")
    args = parser.parse_args()

    manifests = discover_manifests(root)
    reports = []
    for m in manifests:
        extra = report_extra_fields(m) if report_extra_fields else None
        effective_smoke = smoke_fn if args.smoke_test else None
        reports.append(build_report(m, smoke_fn=effective_smoke, extra_fields=extra))

    summary = summarize(reports)
    output = {result_key: reports, "summary": summary}

    if args.text:
        cols = text_columns or [("id", "ID", 24), ("name", "Name", 32)]
        print_text_table(reports, cols)
    else:
        print(json.dumps(output, indent=2))

    sys.exit(0 if (summary["green"] > 0 or sum(summary.values()) == 0) else 1)
