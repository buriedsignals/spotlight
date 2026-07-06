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
_KNOWN = ("crawl4ai", "firecrawl", "scrapling")
_BLOCK_STATUSES = {401, 403, 429}


def _provider_name(explicit: str | None) -> str:
    return (explicit or os.environ.get("SCRAPE_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def _blocked(result: ScrapeResult) -> bool:
    # Crawl4AI returned, but the site defeated it: empty body or a bot-block
    # status. This is the U17 escalation signal.
    return result.is_empty() or (result.status_code in _BLOCK_STATUSES)


def scrape(url: str, *, provider: str | None = None, timeout_ms: int = 45_000, escalate: bool = True) -> ScrapeResult:
    """Fetch url to a ScrapeResult via the selected backend.

    Default ladder (U17): Crawl4AI primary -> Scrapling (Camoufox stealth) only
    when Crawl4AI is bot-blocked or returns empty. escalate=False, or selecting a
    non-default provider, disables the ladder. Raises ScrapeError on an unknown
    provider, an absent backend, or a fetch failure that isn't a bot-block.
    """
    name = _provider_name(provider)
    if name == "crawl4ai":
        from . import crawl4ai_provider

        try:
            result = crawl4ai_provider.fetch(url, timeout_ms=timeout_ms)
        except ScrapeError as exc:
            # A bot-block error escalates to stealth; anything else (crawl4ai not
            # installed, a real network failure) re-raises — Scrapling wouldn't help.
            if escalate and exc.status_code in _BLOCK_STATUSES:
                return _escalate(url, timeout_ms)
            raise
        if escalate and _blocked(result):
            return _escalate(url, timeout_ms)
        return result
    if name == "firecrawl":
        from . import firecrawl_provider

        return firecrawl_provider.fetch(url, timeout_ms=timeout_ms)
    if name == "scrapling":
        return _escalate(url, timeout_ms)
    raise ScrapeError(f"unknown SCRAPE_PROVIDER {name!r} — choose one of {_KNOWN}")


def _escalate(url: str, timeout_ms: int) -> ScrapeResult:
    from . import scrapling_provider

    return scrapling_provider.fetch(url, timeout_ms=timeout_ms)
