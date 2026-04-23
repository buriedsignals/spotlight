"""Shared preflight helpers for Spotlight integrations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable


def _load_dotenv(root: Path) -> None:
    """Load repo-root .env into os.environ without external dependencies."""
    env_path = root / ".env"
    if not env_path.is_file():
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def discover_manifests(root: Path) -> list[dict]:
    """Discover manifest.json files one directory below root."""
    entries: list[dict] = []
    if not root.is_dir():
        return entries
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        manifest["_dir"] = str(directory)
        entries.append(manifest)
    return entries


def check_env_vars(manifest: dict) -> tuple[list[str], list[str]]:
    """Return set and missing env vars for a manifest."""
    required = manifest.get("env_vars") or manifest.get("required_env_vars") or []
    set_vars = [name for name in required if os.environ.get(name)]
    missing_vars = [name for name in required if not os.environ.get(name)]
    return set_vars, missing_vars


def build_report(
    manifest: dict,
    smoke_fn: Callable[[dict], tuple[bool, str | None]] | None = None,
    extra_fields: dict | None = None,
) -> dict:
    """Build a status report for one manifest."""
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
        ok, error = smoke_fn(manifest)
        if not ok:
            report["status"] = "yellow"
            report["smoke_error"] = error
            return report

    return report


def summarize(reports: list[dict]) -> dict:
    return {
        "green": sum(1 for report in reports if report["status"] == "green"),
        "yellow": sum(1 for report in reports if report["status"] == "yellow"),
        "red": sum(1 for report in reports if report["status"] == "red"),
    }


def print_text_table(reports: list[dict], columns: list[tuple[str, str, int]]) -> None:
    """Emit a human-readable table."""
    header = ""
    sep_width = 0
    for _field, label, width in columns:
        header += f"{label:<{width}} "
        sep_width += width + 1
    header += f"{'Missing env':<40}"
    sep_width += 40
    print(header)
    print("-" * sep_width)

    for report in reports:
        line = ""
        for field, _label, width in columns:
            value = str(report.get(field, ""))
            line += f"{value:<{width}} "
        missing = ", ".join(report["env_vars_missing"]) if report["env_vars_missing"] else "—"
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
    """CLI entry point for manifest-based preflight scripts."""
    dotenv_dir = root.resolve()
    while dotenv_dir != dotenv_dir.parent:
        if (dotenv_dir / ".env").is_file():
            break
        dotenv_dir = dotenv_dir.parent
    _load_dotenv(dotenv_dir)

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--smoke-test", action="store_true", help="Also run a minimal probe against each entry")
    parser.add_argument("--json", action="store_true", help="Emit JSON (default)")
    parser.add_argument("--text", action="store_true", help="Emit human-readable table")
    args = parser.parse_args()

    manifests = discover_manifests(root)
    reports = []
    for manifest in manifests:
        extra = report_extra_fields(manifest) if report_extra_fields else None
        effective_smoke = smoke_fn if args.smoke_test else None
        reports.append(build_report(manifest, smoke_fn=effective_smoke, extra_fields=extra))

    summary = summarize(reports)
    output = {result_key: reports, "summary": summary}

    if args.text:
        columns = text_columns or [("id", "ID", 24), ("name", "Name", 32)]
        print_text_table(reports, columns)
    else:
        print(json.dumps(output, indent=2))

    sys.exit(0 if (summary["green"] > 0 or sum(summary.values()) == 0) else 1)
