"""CLI for the search seam so skills discover sources without importing Python:

    python3 -m integrations.search "<query>"                 # numbered results to stdout
    python3 -m integrations.search "<query>" --limit 20
    python3 -m integrations.search "<query>" --json          # structured hits
    python3 -m integrations.search "<query>" --union         # SearXNG + Firecrawl, deduped
    python3 -m integrations.search "<query>" --provider firecrawl
    python3 -m integrations.search "<query>" --categories news --time-range month

Default provider is SearXNG (self-hosted, free; set SEARXNG_URL). Firecrawl is an
optional fallback (used only if SearXNG is down and a key is present) or an explicit
union engine. Exit 0 on success; 3 on failure with the error on stderr.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .search import search
from .search_types import SearchError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m integrations.search",
        description="Web search for source discovery via the sovereign search seam (SearXNG default).",
    )
    ap.add_argument("query", help="search query")
    ap.add_argument("--limit", type=int, default=10, help="max results (default 10)")
    ap.add_argument("--provider", default=None, help="searxng (default) | firecrawl")
    ap.add_argument("--union", action="store_true", help="merge SearXNG + Firecrawl results (exhaustive)")
    ap.add_argument("--categories", default=None, help="SearXNG categories, e.g. news")
    ap.add_argument("--time-range", dest="time_range", default=None, help="SearXNG time_range, e.g. month")
    ap.add_argument("--json", action="store_true", help="emit structured SearchHit JSON")
    args = ap.parse_args(argv)

    try:
        hits = search(
            args.query,
            limit=args.limit,
            provider=args.provider,
            union=args.union,
            categories=args.categories,
            time_range=args.time_range,
        )
    except SearchError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps([dataclasses.asdict(h) for h in hits], indent=2))
    else:
        for i, h in enumerate(hits, 1):
            date = f" ({h.date[:10]})" if h.date else ""
            print(f"{i}. {h.title}{date}\n   {h.url}")
            if h.snippet:
                print(f"   {h.snippet}")
    if not hits:
        print("no results", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
