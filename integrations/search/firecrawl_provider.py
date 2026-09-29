"""Firecrawl search provider — optional escape hatch, not the default.

Used only when SearXNG is unreachable and a Firecrawl CLI is present, or when
explicitly requested (`--provider firecrawl` / `--union`). Sovereign-first: the
default path never touches this. See KTD4 in tools/GOING_LOCAL.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[2]
if str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))
from integrations._cli import run_captured, strip_ansi  # noqa: E402
from integrations._credentials import executable, redact, subprocess_env  # noqa: E402

from .search_types import SearchError, SearchHit


def available() -> bool:
    return executable("firecrawl") is not None


def search(query: str, *, limit: int = 10) -> list[SearchHit]:
    binary = executable("firecrawl")
    if binary is None:
        raise SearchError("firecrawl CLI not on PATH")
    env = subprocess_env(["FIRECRAWL_API_KEY"])
    secrets = [env.get("FIRECRAWL_API_KEY", "")]
    try:
        proc = run_captured([binary, "search", query, "--limit", str(limit), "--json"], env=env, timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise SearchError("firecrawl search timed out after 90s") from exc
    except OSError as exc:
        raise SearchError(f"firecrawl search failed: {exc}") from exc
    if proc.returncode != 0:
        detail = redact(strip_ansi(proc.stderr).strip(), secrets)
        raise SearchError(detail[:300] or "firecrawl non-zero exit")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        # An unauthenticated or outdated CLI prints text, not JSON: report it, never crash.
        detail = redact(strip_ansi(proc.stdout).strip(), secrets)[:300]
        raise SearchError(f"firecrawl search returned no JSON: {detail or 'empty output'}") from exc
    web = ((payload if isinstance(payload, dict) else {}).get("data") or {}).get("web", []) or []
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
