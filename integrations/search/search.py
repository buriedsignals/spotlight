"""Search dispatcher — sovereign-default web search for skills.

Mirrors the scraping seam's provider model. Default provider is SearXNG
(self-hosted, free). Firecrawl is:
  - an automatic fallback ONLY when SearXNG is unreachable and a Firecrawl CLI
    is present (so the default path stays sovereign but degrades gracefully), and
  - an explicit union engine (`union=True`) for exhaustive-recall investigations,
    since the two engines return ~36% disjoint sources
    (tools/searxng-entity-recall.md).

Provider is chosen by argument > `SEARCH_PROVIDER` env > "searxng".
"""
from __future__ import annotations

import os

from . import firecrawl_provider, searxng_provider
from .search_types import SearchError, SearchHit


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
    name = (provider or os.environ.get("SEARCH_PROVIDER") or "searxng").strip().lower()

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
