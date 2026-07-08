"""Provider-agnostic scraping contract for Spotlight (loop U14 / PRD Workstream G).

One result shape regardless of backend — Crawl4AI (default, open-source, no API
key), Firecrawl (opt-in bot-block escape hatch) — so skills and the
evidence pipeline never couple to a vendor. Lifted from the Scoutpost migration
(tools/scoutpost-scrape): raw_markdown, NOT fit_markdown (KTD7) — main-content
filtering destroys evidence pages, and Spotlight persists scraped markdown as
evidence, so determinism (content hashing) is load-bearing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


class ScrapeError(RuntimeError):
    """A fetch failed or a provider was misconfigured. Carries an optional
    status_code so callers can apply 4xx/'removed' semantics."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ScrapeResult:
    markdown: str                 # raw_markdown — the evidence body
    requested_url: str
    source_url: str               # final URL after redirects
    fetched_at: str               # ISO-8601 UTC
    provider: str                 # which backend produced this
    status_code: int | None = None
    title: str | None = None
    html: str | None = None       # cleaned_html
    raw_html: str | None = None   # raw page HTML (civic link extraction)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_sha256(self) -> str:
        """Deterministic evidence key — hashes the raw markdown only, so the
        same page fetched via ANY provider dedups to the same evidence card."""
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()

    def is_empty(self) -> bool:
        """True when the fetch returned no usable body — the U17 escalation
        signal (Crawl4AI bot-blocked/empty -> optional Firecrawl)."""
        return not self.markdown.strip()
