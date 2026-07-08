"""SearXNG search provider — the sovereign default.

Queries a local, self-hosted SearXNG JSON endpoint (no API key, no vendor). Reads
`SEARXNG_URL` (default `http://localhost:8899`). Paginates past the first page so
long-tail / obscure-entity sources stay reachable (the top-10 cap otherwise hides
them — entity-recall validation, tools/searxng-entity-recall.md, 2026-07-08).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .search_types import SearchError, SearchHit

DEFAULT_URL = "http://localhost:8899"
DEFAULT_PAGES = 3  # merged across engines this is ~40-70 results; enough for the tail


def _base_url() -> str:
    return os.environ.get("SEARXNG_URL", DEFAULT_URL).rstrip("/")


def search(
    query: str,
    *,
    limit: int = 10,
    categories: str | None = None,
    time_range: str | None = None,
    pages: int = DEFAULT_PAGES,
) -> list[SearchHit]:
    base = _base_url()
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        params = {"q": query, "format": "json", "pageno": page}
        if categories:
            params["categories"] = categories
        if time_range:
            params["time_range"] = time_range
        url = base + "/search?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "spotlight-search/1.0"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as exc:  # noqa: BLE001 - unreachable / bad response
            # A first-page failure is a hard error (caller may fall back); a
            # later-page failure just ends pagination with what we have.
            if page == 1:
                raise SearchError(f"SearXNG unreachable at {base}: {exc}") from exc
            break
        results = data.get("results") or []
        if not results:
            break
        for r in results:
            u = r.get("url")
            if not u or u in seen:
                continue
            seen.add(u)
            hits.append(
                SearchHit(
                    url=u,
                    title=r.get("title", "") or "",
                    snippet=(r.get("content") or "")[:300],
                    date=r.get("publishedDate"),
                    engine=r.get("engine"),
                )
            )
            if len(hits) >= limit:
                return hits
    return hits
