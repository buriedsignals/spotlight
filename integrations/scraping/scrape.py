"""Provider-agnostic fetch seam (loop U14 / PRD Workstream G).

    from integrations.scraping import scrape
    result = scrape("https://example.gov/agenda.pdf")   # -> ScrapeResult

Default backend is **Crawl4AI** (open-source, local, no API key). Firecrawl is
opt-in via SCRAPE_PROVIDER=firecrawl (kept for parity, no longer required).
The Scrapling stealth escalation (Crawl4AI blocked/empty -> Scrapling) is U17.

Providers are imported lazily so selecting one never drags in the others' deps
(Crawl4AI's browser stack, Firecrawl's CLI). This is the only module skills
import — they never touch a vendor SDK directly.
"""
from __future__ import annotations

import os

from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_PROVIDER = "crawl4ai"
_KNOWN = ("crawl4ai", "firecrawl")


def _provider_name(explicit: str | None) -> str:
    return (explicit or os.environ.get("SCRAPE_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def scrape(url: str, *, provider: str | None = None, timeout_ms: int = 45_000) -> ScrapeResult:
    """Fetch url to a ScrapeResult via the selected backend. Raises ScrapeError
    on an unknown provider, a misconfigured/absent backend, or a fetch failure."""
    name = _provider_name(provider)
    if name == "crawl4ai":
        from . import crawl4ai_provider

        return crawl4ai_provider.fetch(url, timeout_ms=timeout_ms)
    if name == "firecrawl":
        from . import firecrawl_provider

        return firecrawl_provider.fetch(url, timeout_ms=timeout_ms)
    raise ScrapeError(f"unknown SCRAPE_PROVIDER {name!r} — choose one of {_KNOWN}")
