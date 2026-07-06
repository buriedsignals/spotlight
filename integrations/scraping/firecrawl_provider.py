"""Firecrawl backend — kept, opt-in via SCRAPE_PROVIDER=firecrawl (loop U14).

No longer the default: Crawl4AI removes the paid FIRECRAWL_API_KEY from the
critical path. Shells out to the repo's established `firecrawl` CLI rather than
adding an SDK dependency. Lazy/self-contained so it's only reached when selected.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone

from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_TIMEOUT_MS = 45_000


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ScrapeResult:  # pragma: no cover - live path
    if shutil.which("firecrawl") is None:
        raise ScrapeError(
            "the `firecrawl` CLI is not installed; the default provider is crawl4ai (no key needed)"
        )
    try:
        proc = subprocess.run(
            ["firecrawl", "scrape", url],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScrapeError(f"firecrawl scrape timed out for {url}") from exc
    if proc.returncode != 0:
        raise ScrapeError(f"firecrawl scrape failed for {url}: {proc.stderr.strip()}")
    return ScrapeResult(
        markdown=proc.stdout,
        requested_url=url,
        source_url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        provider="firecrawl",
    )
