"""Crawl4AI backend — the default fetcher (loop U14 / KTD7).

crawl4ai and its Playwright/Chromium runtime are imported LAZILY (inside the
fetch call), so the seam, its unit tests, and skills that never scrape don't
need the ~GB browser stack installed. The stack is provisioned by U15's
`crawl4ai-setup`; until then this provider raises a clear ScrapeError telling the
user to install it. Imported directly (no FastAPI sidecar — Spotlight is already
a local Python process, KTD7).
"""
from __future__ import annotations

import asyncio

from .mapping import crawl_failure_detail, map_crawl_result
from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_TIMEOUT_MS = 45_000


async def _afetch(url: str, timeout_ms: int, proxy: str | None = None) -> ScrapeResult:  # pragma: no cover - live path
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except ImportError as exc:  # not provisioned yet
        raise ScrapeError(
            "crawl4ai is not installed; run `crawl4ai-setup` (U15 installer) "
            "or select SCRAPE_PROVIDER=firecrawl"
        ) from exc

    # proxy (U7): a Tor SOCKS endpoint routes the browser's egress so the target
    # never sees the operator's IP. Passed straight to Playwright's launch proxy.
    browser_kwargs = {"headless": True, "verbose": False}
    if proxy:
        browser_kwargs["proxy"] = proxy
    async with AsyncWebCrawler(config=BrowserConfig(**browser_kwargs)) as crawler:
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=timeout_ms),
        )
    if not getattr(result, "success", True):
        raise ScrapeError(
            f"crawl4ai fetch failed for {url}: {crawl_failure_detail(result)}",
            status_code=getattr(result, "status_code", None),
        )
    return map_crawl_result(result, url, provider="crawl4ai")


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, proxy: str | None = None) -> ScrapeResult:  # pragma: no cover - live path
    return asyncio.run(_afetch(url, timeout_ms, proxy))
