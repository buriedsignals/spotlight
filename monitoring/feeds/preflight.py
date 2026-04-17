#!/usr/bin/env python3
"""Preflight checker for OSINT feed sources.

Scans every source manifest under monitoring/feeds/sources/, checks
that each source's required env vars are set, and optionally runs a
smoke query. Reports per-source status: green (ready), yellow (key
set but smoke failed), red (missing env vars).

Usage:
    python3 preflight.py [--project <slug>] [--smoke-test] [--json]

Exit code:
    0 — at least one source green (or no sources require keys)
    1 — all sources red (nothing queryable)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

SOURCES_DIR = Path(__file__).parent / "sources"


def discover_sources() -> list[dict]:
    """Scan SOURCES_DIR for manifest.json files."""
    sources: list[dict] = []
    if not SOURCES_DIR.is_dir():
        return sources
    for d in sorted(SOURCES_DIR.iterdir()):
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)
        manifest["_dir"] = str(d)
        sources.append(manifest)
    return sources


def check_env_vars(manifest: dict) -> tuple[list[str], list[str]]:
    """Return (set_vars, missing_vars) for a source."""
    required = manifest.get("env_vars") or manifest.get("required_env_vars") or []
    set_vars = [v for v in required if os.environ.get(v)]
    missing_vars = [v for v in required if not os.environ.get(v)]
    return set_vars, missing_vars


def load_fetch_module(source_dir: str):
    """Dynamically load a source's fetch.py module."""
    fetch_path = Path(source_dir) / "fetch.py"
    if not fetch_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"fetch_{Path(source_dir).name}", fetch_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"[WARN] Failed to load {fetch_path}: {e}", file=sys.stderr)
        return None


def smoke_test(manifest: dict, project: str | None = None) -> tuple[bool, str | None]:
    """Run a minimal fetch to confirm the source responds.

    Returns (success, error_message).
    """
    mod = load_fetch_module(manifest["_dir"])
    if mod is None or not hasattr(mod, "fetch"):
        return False, "fetch.py missing or has no fetch() function"

    # Minimal inputs — empty topics, tiny since window
    since = manifest.get("default_since", "24h")
    try:
        results = mod.fetch(query=None, topics=[], since=since)
        if not isinstance(results, list):
            return False, f"fetch() returned {type(results).__name__}, expected list"
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_source(manifest: dict, smoke: bool = False, project: str | None = None) -> dict:
    """Return a status report for a single source."""
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

    # Red: missing required env vars
    if requires_key and missing_vars:
        report["status"] = "red"
        return report

    # If smoke-test requested, run it
    if smoke:
        ok, err = smoke_test(manifest, project=project)
        if not ok:
            report["status"] = "yellow"
            report["smoke_error"] = err
            return report

    return report


def main():
    parser = argparse.ArgumentParser(description="Preflight check for OSINT feed sources")
    parser.add_argument("--project", type=str, help="Project slug (for context)")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run a minimal fetch against each source to confirm connectivity",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON (default: yes)")
    parser.add_argument("--text", action="store_true", help="Emit human-readable table")
    args = parser.parse_args()

    sources = discover_sources()
    reports = [check_source(s, smoke=args.smoke_test, project=args.project) for s in sources]

    summary = {
        "green": sum(1 for r in reports if r["status"] == "green"),
        "yellow": sum(1 for r in reports if r["status"] == "yellow"),
        "red": sum(1 for r in reports if r["status"] == "red"),
    }

    output = {"sources": reports, "summary": summary}

    if args.text:
        print(f"{'ID':<24} {'Status':<8} {'Missing env':<40}")
        print("-" * 72)
        for r in reports:
            missing = ", ".join(r["env_vars_missing"]) if r["env_vars_missing"] else "—"
            print(f"{r['id']:<24} {r['status']:<8} {missing:<40}")
        print()
        print(f"green={summary['green']}  yellow={summary['yellow']}  red={summary['red']}")
    else:
        print(json.dumps(output, indent=2))

    # Exit code: non-zero only if NOTHING is green
    sys.exit(0 if summary["green"] > 0 or (summary["green"] + summary["yellow"] + summary["red"] == 0) else 1)


if __name__ == "__main__":
    main()
