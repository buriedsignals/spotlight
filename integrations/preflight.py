#!/usr/bin/env python3
"""Preflight checker for external tool integrations.

Scans every integration manifest under integrations/, checks each
integration's required env vars, reports per-integration status:
green (ready), yellow (key set but smoke test failed), red (missing
env vars).

Mirrors monitoring/feeds/preflight.py — same status model, same CLI.

Usage:
    python3 integrations/preflight.py [--smoke-test] [--json|--text]

Exit code:
    0 — at least one integration green (or no integrations require keys)
    1 — all integrations red (nothing queryable)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

INTEGRATIONS_DIR = Path(__file__).parent


def discover_integrations() -> list[dict]:
    """Scan INTEGRATIONS_DIR for manifest.json files."""
    integrations: list[dict] = []
    if not INTEGRATIONS_DIR.is_dir():
        return integrations
    for d in sorted(INTEGRATIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)
        manifest["_dir"] = str(d)
        integrations.append(manifest)
    return integrations


def check_env_vars(manifest: dict) -> tuple[list[str], list[str]]:
    """Return (set_vars, missing_vars) for an integration."""
    required = manifest.get("env_vars") or []
    set_vars = [v for v in required if os.environ.get(v)]
    missing_vars = [v for v in required if not os.environ.get(v)]
    return set_vars, missing_vars


def smoke_test(manifest: dict) -> tuple[bool, str | None]:
    """Run a minimal probe to confirm the integration responds.

    Each integration type gets a different probe:
    - api: HEAD or GET against homepage / docs URL (shallow, no auth)
    - library: import-check (python -c "import <pkg>")
    - cli: `command -v <id>`
    - mcp: not implemented; returns True (assume ok)

    Returns (success, error_message).
    """
    kind = manifest.get("type", "api")

    if kind == "api":
        url = manifest.get("homepage") or manifest.get("docs")
        if not url:
            return True, None
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Spotlight-Preflight/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return (200 <= resp.status < 400), None
        except urllib.error.HTTPError as e:
            # HEAD may not be allowed; accept 405/403 as "reachable"
            return (400 <= e.code < 500), f"HTTP {e.code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    if kind == "library":
        # Treat browser-use specially for now; extend as library integrations are added
        mod = {"browser-use": "browser_use"}.get(manifest["id"])
        if not mod:
            return True, None
        import importlib.util
        found = importlib.util.find_spec(mod) is not None
        return found, None if found else f"python import '{mod}' failed"

    if kind == "cli":
        import shutil
        tool = manifest["id"]
        return shutil.which(tool) is not None, None if shutil.which(tool) else f"{tool} not on PATH"

    return True, None


def check_integration(manifest: dict, smoke: bool = False) -> dict:
    """Return a status report for a single integration."""
    requires_key = manifest.get("requires_key", False)
    set_vars, missing_vars = check_env_vars(manifest)

    report = {
        "id": manifest["id"],
        "name": manifest.get("name", manifest["id"]),
        "category": manifest.get("category", ""),
        "type": manifest.get("type", "api"),
        "requires_key": requires_key,
        "env_vars_required": manifest.get("env_vars") or [],
        "env_vars_set": set_vars,
        "env_vars_missing": missing_vars,
        "status": "green",
        "smoke_error": None,
    }

    if requires_key and missing_vars:
        report["status"] = "red"
        return report

    if smoke:
        ok, err = smoke_test(manifest)
        if not ok:
            report["status"] = "yellow"
            report["smoke_error"] = err
            return report

    return report


def main():
    parser = argparse.ArgumentParser(description="Preflight check for Spotlight external tool integrations")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Also run a minimal probe against each integration (HTTP HEAD for api, import for library, which for cli)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON (default)")
    parser.add_argument("--text", action="store_true", help="Emit human-readable table")
    args = parser.parse_args()

    integrations = discover_integrations()
    reports = [check_integration(i, smoke=args.smoke_test) for i in integrations]

    summary = {
        "green": sum(1 for r in reports if r["status"] == "green"),
        "yellow": sum(1 for r in reports if r["status"] == "yellow"),
        "red": sum(1 for r in reports if r["status"] == "red"),
    }

    output = {"integrations": reports, "summary": summary}

    if args.text:
        print(f"{'ID':<20} {'Type':<10} {'Status':<8} {'Missing env':<40}")
        print("-" * 78)
        for r in reports:
            missing = ", ".join(r["env_vars_missing"]) if r["env_vars_missing"] else "—"
            print(f"{r['id']:<20} {r['type']:<10} {r['status']:<8} {missing:<40}")
        print()
        print(f"green={summary['green']}  yellow={summary['yellow']}  red={summary['red']}")
    else:
        print(json.dumps(output, indent=2))

    sys.exit(0 if (summary["green"] > 0 or sum(summary.values()) == 0) else 1)


if __name__ == "__main__":
    main()
