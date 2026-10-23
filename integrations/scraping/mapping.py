"""Map Crawl4AI's CrawlResult -> ScrapeResult (KTD7 raw-markdown mapping).

Lifted from tools/scoutpost-scrape/scrape-service/app/mapping.py. The mapping is
the ONE place upstream Crawl4AI API drift surfaces — it fails LOUD on an
unexpected markdown shape rather than hashing repr() garbage into evidence
dedup. raw_markdown, never fit_markdown (onlyMainContent-style filtering is what
wrecked civic evidence pages under Firecrawl).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .scrape_types import ScrapeError, ScrapeResult

_WALL_STATUSES = {401, 403, 429}
# An access wall (bot check, rate limit, "403: Access limited") is a short page.
# Crawl4AI can report it as a success with the status of an earlier redirect hop,
# so the body is checked too. Long pages are never treated as walls.
_WALL_MAX_CHARS = 1500
_WALL_TEXT = re.compile(
    r"^\W*(401|403|429)\b|access (denied|limited)|just a moment\.\.\.|attention required"
    r"|verify (that )?you are (a )?human|are you a robot|checking your browser"
    r"|enable javascript and cookies to continue|request blocked|too many requests",
    re.IGNORECASE,
)


def _extract_markdown(raw: Any) -> str:
    # Crawl4AI's markdown field is a MarkdownGenerationResult (raw_markdown /
    # fit_markdown attributes), a plain string on some paths, or absent.
    # Crawl4AI 0.9 wraps it in StringCompatibleMarkdown, a str subclass that
    # does not survive dataclasses.asdict, so always return a plain str.
    if raw is None:
        return ""
    raw_markdown = getattr(raw, "raw_markdown", None)
    if isinstance(raw_markdown, str):
        return str(raw_markdown)
    if isinstance(raw, str):
        return str(raw)
    raise ValueError(f"unexpected crawl markdown shape: {type(raw).__name__}")


def map_crawl_result(result: Any, requested_url: str, provider: str = "crawl4ai") -> ScrapeResult:
    """Map a successful crawl. An access-wall page raises ScrapeError (status
    403 unless a wall status was reported), so it is never saved as evidence."""
    metadata = getattr(result, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    mapped = ScrapeResult(
        markdown=_extract_markdown(getattr(result, "markdown", None)),
        requested_url=requested_url,
        source_url=getattr(result, "url", None) or requested_url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        status_code=getattr(result, "status_code", None),
        title=metadata.get("title") if isinstance(metadata, dict) else None,
        html=getattr(result, "cleaned_html", None),
        raw_html=getattr(result, "html", None),
        metadata=metadata,
    )
    wall = access_wall(mapped)
    if wall:
        status = mapped.status_code if mapped.status_code in _WALL_STATUSES else 403
        raise ScrapeError(f"{requested_url} returned an access wall, not the page: {wall}", status_code=status)
    return mapped


def access_wall(result: ScrapeResult) -> str | None:
    """Why this result is an access wall rather than the page, or None."""
    if result.status_code in _WALL_STATUSES:
        return f"HTTP {result.status_code}"
    body = result.markdown.strip()
    if body and len(body) <= _WALL_MAX_CHARS:
        match = _WALL_TEXT.search(body)
        if match:
            return f"page text {match.group(0).strip()!r}"
    return None


def crawl_failure_detail(result: Any) -> str:
    message = getattr(result, "error_message", None)
    status = getattr(result, "status_code", None)
    parts = [p for p in (f"status {status}" if status else None, message) if p]
    return "; ".join(parts) or "crawl failed without detail"
