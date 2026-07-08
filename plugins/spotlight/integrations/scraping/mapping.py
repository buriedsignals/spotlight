"""Map Crawl4AI's CrawlResult -> ScrapeResult (KTD7 raw-markdown mapping).

Lifted from tools/scoutpost-scrape/scrape-service/app/mapping.py. The mapping is
the ONE place upstream Crawl4AI API drift surfaces — it fails LOUD on an
unexpected markdown shape rather than hashing repr() garbage into evidence
dedup. raw_markdown, never fit_markdown (onlyMainContent-style filtering is what
wrecked civic evidence pages under Firecrawl).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .scrape_types import ScrapeResult


def _extract_markdown(raw: Any) -> str:
    # Crawl4AI's markdown field is a MarkdownGenerationResult (raw_markdown /
    # fit_markdown attributes), a plain string on some paths, or absent.
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    raw_markdown = getattr(raw, "raw_markdown", None)
    if isinstance(raw_markdown, str):
        return raw_markdown
    raise ValueError(f"unexpected crawl markdown shape: {type(raw).__name__}")


def map_crawl_result(result: Any, requested_url: str, provider: str = "crawl4ai") -> ScrapeResult:
    metadata = getattr(result, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return ScrapeResult(
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


def crawl_failure_detail(result: Any) -> str:
    message = getattr(result, "error_message", None)
    status = getattr(result, "status_code", None)
    parts = [p for p in (f"status {status}" if status else None, message) if p]
    return "; ".join(parts) or "crawl failed without detail"
