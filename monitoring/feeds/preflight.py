#!/usr/bin/env python3
"""Preflight checker for OSINT feed sources.

Scans every source manifest under monitoring/feeds/sources/, checks
that each source's required env vars are set, and optionally runs a
smoke query. Reports per-source status: green (ready), yellow (key
set but smoke failed), red (missing env vars).

Shared machinery lives in _preflight_base.py.

Usage:
    python3 preflight.py [--project <slug>] [--smoke-test] [--json|--text]

Exit code:
    0 — at least one source green (or no sources require keys)
    1 — all sources red (nothing queryable)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from _preflight_base import run_preflight

SOURCES_DIR = Path(__file__).parent / "sources"


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


def smoke_test(manifest: dict) -> tuple[bool, str | None]:
    """Run a minimal fetch to confirm the source responds."""
    mod = load_fetch_module(manifest["_dir"])
    if mod is None or not hasattr(mod, "fetch"):
        return False, "fetch.py missing or has no fetch() function"
    since = manifest.get("default_since", "24h")
    try:
        results = mod.fetch(query=None, topics=[], since=since)
        if not isinstance(results, list):
            return False, f"fetch() returned {type(results).__name__}, expected list"
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    run_preflight(
        SOURCES_DIR,
        result_key="sources",
        smoke_fn=smoke_test,
        text_columns=[("id", "ID", 24), ("status", "Status", 8)],
        description="Preflight check for OSINT feed sources",
    )


if __name__ == "__main__":
    main()
