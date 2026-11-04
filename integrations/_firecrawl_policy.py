"""When Firecrawl may be used, for the search and scraping seams.

Sensitive mode keeps a case's queries and URLs off every outside service, and
this seam is the only guard: the Engine delivers the key whatever the case. So
each answer is read at call time from the install config, the checkout manifest
and the environment, and a case marked sensitive after a chat began is covered.
A config that cannot be read counts as sensitive, because a wrong "no" would
send a query out.

``firecrawl_mode`` in the install config: ``backup`` (default) uses Firecrawl
when SearXNG finds nothing, ``always`` joins both on every search, ``off`` never
uses it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ._credentials import ROOT, config_path

MODES = ("backup", "always", "off")
SENSITIVE_REFUSAL = "sensitive mode: nothing from this case goes to Firecrawl"


def _config() -> dict | None:
    """The install config, {} when there is none, None when it cannot be read."""
    try:
        path = config_path(ROOT)
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return None
    return value if isinstance(value, dict) else None


def _manifest_sensitive() -> bool:
    try:
        head = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("---", 2)
    except OSError:
        return False
    return len(head) > 2 and any(line.strip() == "sensitive: true" for line in head[1].splitlines())


def sensitive() -> bool:
    value = os.environ.get("SPOTLIGHT_SENSITIVE", "").strip().lower()
    if value not in ("", "false"):
        return True
    config = _config()
    return config is None or config.get("sensitive") is True or _manifest_sensitive()


def mode() -> str:
    value = (_config() or {}).get("firecrawl_mode")
    return value if value in MODES else "backup"
