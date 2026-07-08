"""Firecrawl search provider — optional escape hatch, not the default.

Used only when SearXNG is unreachable and a Firecrawl CLI is present, or when
explicitly requested (`--provider firecrawl` / `--union`). Sovereign-first: the
default path never touches this. See KTD4 in tools/GOING_LOCAL.md.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from .search_types import SearchError, SearchHit


def available() -> bool:
    return shutil.which("firecrawl") is not None


def search(query: str, *, limit: int = 10) -> list[SearchHit]:
    if not available():
        raise SearchError("firecrawl CLI not on PATH")
    try:
        proc = subprocess.run(
            ["firecrawl", "search", query, "--limit", str(limit), "--json"],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"firecrawl search failed: {exc}") from exc
    if proc.returncode != 0:
        raise SearchError(proc.stderr.strip()[:300] or "firecrawl non-zero exit")
    web = (json.loads(proc.stdout).get("data") or {}).get("web", []) or []
    return [
        SearchHit(
            url=r.get("url", ""),
            title=r.get("title", "") or "",
            snippet=(r.get("description") or "")[:300],
            date=r.get("date"),
            engine="firecrawl",
        )
        for r in web
        if r.get("url")
    ][:limit]
