#!/usr/bin/env python3
"""Preflight checker for external tool integrations.

Scans every integration manifest under integrations/, checks each
integration's required env vars, reports per-integration status:
green (ready), yellow (key set but smoke test failed), red (missing
env vars).

Shared machinery lives in integrations/_preflight_base.py.

Usage:
    python3 integrations/preflight.py [--smoke-test] [--json|--text]

Exit code:
    0 — at least one integration green (or no integrations require keys)
    1 — all integrations red (nothing queryable)
"""

from __future__ import annotations

import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Import the shared helpers from integrations/ — local single source of truth
_BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(_BASE_DIR))
from _preflight_base import run_preflight  # noqa: E402

INTEGRATIONS_DIR = Path(__file__).parent


def smoke_test(manifest: dict) -> tuple[bool, str | None]:
    """Per-integration probe. Each integration `type` gets a different check:
      - api:     HEAD or GET against homepage/docs URL (shallow, no auth)
      - library: import check
      - cli:     binary on PATH
      - mcp:     not implemented; assume ok
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
            # HEAD may not be allowed; 4xx accepted as "reachable"
            return (400 <= e.code < 500), f"HTTP {e.code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    if kind == "library":
        # Only browser-use at the moment; extend as library integrations are added
        mod = {"browser-use": "browser_use"}.get(manifest["id"])
        if not mod:
            return True, None
        import importlib.util
        found = importlib.util.find_spec(mod) is not None
        return found, None if found else f"python import '{mod}' failed"

    if kind == "cli":
        return shutil.which(manifest["id"]) is not None, \
               None if shutil.which(manifest["id"]) else f"{manifest['id']} not on PATH"

    return True, None


def main():
    run_preflight(
        INTEGRATIONS_DIR,
        result_key="integrations",
        smoke_fn=smoke_test,
        report_extra_fields=lambda m: {"type": m.get("type", "api")},
        text_columns=[("id", "ID", 20), ("type", "Type", 10), ("status", "Status", 8)],
        description="Preflight check for Spotlight external tool integrations",
    )


if __name__ == "__main__":
    main()
