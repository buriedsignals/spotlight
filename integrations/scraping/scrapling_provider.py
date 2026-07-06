"""Scrapling (Camoufox stealth) — the U17 escalation fetcher (KTD7).

Reached only when Crawl4AI is bot-blocked or returns empty (the adversarial tail
Spotlight cares about most) — never the default: it is heavier and slower than
Crawl4AI's clean markdown path. scrapling + its Camoufox browser are imported
lazily and provisioned by U15's installer; until then this raises a clear
ScrapeError. Same ScrapeResult contract + raw-text determinism as the primary
path so content_sha256 dedup still holds.

NOTE: Scrapling's exact fetcher API (StealthyFetcher, the response object's text
accessors) is confirmed against the installed package in U15 — the extraction
below is written defensively (tries several accessors) so minor API drift
degrades to raw HTML rather than crashing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_TIMEOUT_MS = 45_000
_BLOCK_STATUSES = {401, 403, 429}


def _extract_text(page: Any) -> str:
    fn = getattr(page, "get_all_text", None)
    if callable(fn):
        try:
            t = fn()
            if isinstance(t, str) and t.strip():
                return t
        except Exception:  # pragma: no cover - defensive against API drift
            pass
    for attr in ("markdown", "clean_text", "text"):
        v = getattr(page, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    html = getattr(page, "html_content", None) or getattr(page, "body", None)
    return html if isinstance(html, str) else ""


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ScrapeResult:  # pragma: no cover - live path
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError as exc:
        raise ScrapeError(
            "scrapling is not installed; run the U15 installer (Camoufox stealth escalation)"
        ) from exc

    page = StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout_ms)
    status = getattr(page, "status", None)
    if isinstance(status, int) and status in _BLOCK_STATUSES:
        raise ScrapeError(f"scrapling stealth still blocked for {url}: HTTP {status}", status_code=status)
    text = _extract_text(page)
    raw_html = getattr(page, "html_content", None) or getattr(page, "body", None)
    return ScrapeResult(
        markdown=text,
        requested_url=url,
        source_url=getattr(page, "url", None) or url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        provider="scrapling",
        status_code=status if isinstance(status, int) else None,
        raw_html=raw_html if isinstance(raw_html, str) else None,
    )
