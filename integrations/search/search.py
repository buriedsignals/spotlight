"""Search dispatcher — sovereign-default web search for skills.

Mirrors the scraping seam's provider model. Default provider is SearXNG
(self-hosted, free). Firecrawl is:
  - a fallback when SearXNG is unreachable and a Firecrawl CLI is present,
  - with its key, a backup when SearXNG finds nothing (a blocked engine set
    answers 200 with no results), or joined on every search in `always` mode
    (`firecrawl_mode`, _firecrawl_policy.py), and
  - an explicit union engine (`union=True`) for exhaustive-recall investigations,
    since the two engines return ~36% disjoint sources
    (tools/searxng-entity-recall.md).
Mode `off` and sensitive mode never use it.

Provider is chosen by argument > `SEARCH_PROVIDER` env > "searxng".
"""
from __future__ import annotations

import os

from .._credentials import credential
from .._firecrawl_policy import mode
from .._protected import ProtectedSourceError, check_outgoing
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
    try:
        check_outgoing(query)  # a query never names a protected source
    except ProtectedSourceError as exc:
        raise SearchError(str(exc)) from None
    name = (provider or os.environ.get("SEARCH_PROVIDER") or "searxng").strip().lower()

    if name == "firecrawl":
        return firecrawl_provider.search(query, limit=limit)

    chosen = mode()
    usable = chosen != "off" and firecrawl_provider.available()
    keyed = usable and bool(credential("FIRECRAWL_API_KEY"))

    # SearXNG (sovereign default).
    try:
        hits = searxng_provider.search(
            query, limit=limit, categories=categories, time_range=time_range
        )
    except SearchError:
        # Sovereign engine down → optional Firecrawl escape hatch, only if present.
        if usable:
            return firecrawl_provider.search(query, limit=limit)
        raise

    joined = (union and usable) or (chosen == "always" and keyed)
    if joined or (not hits and chosen == "backup" and keyed):
        try:
            hits = _dedupe(hits + firecrawl_provider.search(query, limit=limit))
        except SearchError:
            pass  # best-effort; keep the SearXNG results
    return hits if union or joined else hits[:limit]
