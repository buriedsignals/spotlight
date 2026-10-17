"""Search dispatcher — sovereign-default web search for skills.

Mirrors the scraping seam's provider model. Default provider is SearXNG
(self-hosted, free). Firecrawl is:
  - an automatic fallback ONLY when SearXNG is unreachable and a Firecrawl CLI
    is present (so the default path stays sovereign but degrades gracefully), and
  - an explicit union engine (`union=True`) for exhaustive-recall investigations,
    since the two engines return ~36% disjoint sources
    (tools/searxng-entity-recall.md).

Provider is chosen by argument > `SEARCH_PROVIDER` env > "searxng".
Parallel Search MCP is an explicit, anonymous hosted option (`parallel`).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import firecrawl_provider, searxng_provider
from .search_types import SearchError, SearchHit

_CHECKOUT = Path(__file__).resolve().parents[2]


def _allow_search() -> None:
    """Honor local-only policy even when a search CLI is invoked through shell."""
    if os.environ.get("SPOTLIGHT_SENSITIVE", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise SearchError("sensitive mode blocks web search")
    try:
        manifest = (_CHECKOUT / "AGENTS.md").read_text(encoding="utf-8").split("---", 2)
        if len(manifest) > 2 and re.search(r"^sensitive:\s*true\s*$", manifest[1], re.M):
            raise SearchError("sensitive mode blocks web search")
        config_path = _CHECKOUT / ".spotlight-config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError()
            if config.get("sensitive") is True:
                raise SearchError("sensitive mode blocks web search")
    except (OSError, ValueError) as exc:
        raise SearchError("cannot verify local search permissions") from exc


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    out: list[SearchHit] = []
    for h in hits:
        if h.url and h.url not in seen:
            seen.add(h.url)
            out.append(h)
    return out


def search(
    query: str,
    *,
    limit: int = 10,
    provider: str | None = None,
    union: bool = False,
    categories: str | None = None,
    time_range: str | None = None,
) -> list[SearchHit]:
    _allow_search()
    name = (provider or os.environ.get("SEARCH_PROVIDER") or "searxng").strip().lower()

    if name == "parallel":
        if union or categories or time_range:
            raise SearchError("Parallel does not support --union, --categories or --time-range; select SearXNG for these options")
        from . import parallel_provider

        return parallel_provider.search(query, limit=limit)

    if name not in {"searxng", "firecrawl"}:
        raise SearchError(f"search provider is disabled or unknown: {name}")

    if name == "firecrawl":
        return firecrawl_provider.search(query, limit=limit)

    # SearXNG (sovereign default).
    try:
        hits = searxng_provider.search(
            query, limit=limit, categories=categories, time_range=time_range
        )
    except SearchError:
        # Sovereign engine down → optional Firecrawl escape hatch, only if present.
        if firecrawl_provider.available():
            return firecrawl_provider.search(query, limit=limit)
        raise

    if union and firecrawl_provider.available():
        try:
            hits = _dedupe(hits + firecrawl_provider.search(query, limit=limit))
        except SearchError:
            pass  # union is best-effort; keep the SearXNG results
    return hits[:limit] if not union else hits
