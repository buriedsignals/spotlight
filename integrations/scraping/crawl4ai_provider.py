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
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

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


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DELEGATED = "SPOTLIGHT_CRAWL4AI_DELEGATED"


def _importable() -> bool:
    return importlib.util.find_spec("crawl4ai") is not None


def _tool_python() -> str | None:
    """Interpreter of the Engine-installed Crawl4AI uv tool, whose isolated
    environment the calling python3 cannot import from."""
    override = os.environ.get("SPOTLIGHT_CRAWL4AI_PYTHON")
    if override:
        return override if os.path.isfile(override) else None
    uv = shutil.which("uv")
    if not uv:
        return None
    try:
        tool_dir = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    for rel in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = Path(tool_dir, "crawl4ai", *rel)
        if candidate.is_file():
            return str(candidate)
    return None


def _delegate(python: str, url: str, timeout_ms: int, proxy: str | None) -> ScrapeResult:
    args = [python, "-m", "integrations.scraping", url, "--provider", "crawl4ai",
            "--no-escalate", "--json", "--tor" if proxy else "--no-tor"]
    env = {**os.environ, _DELEGATED: "1"}
    try:
        done = subprocess.run(args, cwd=_REPO_ROOT, env=env, capture_output=True, text=True,
                              timeout=timeout_ms / 1000 + 120)
    except subprocess.TimeoutExpired as exc:
        raise ScrapeError(f"crawl4ai fetch timed out for {url}") from exc
    if done.returncode != 0:
        detail = done.stderr.strip().removeprefix("scrape failed: ") or f"exit {done.returncode}"
        raise ScrapeError(detail)
    payload = json.loads(done.stdout)
    payload.pop("content_sha256", None)
    return ScrapeResult(**payload)


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, proxy: str | None = None) -> ScrapeResult:  # pragma: no cover - live path
    if not _importable() and not os.environ.get(_DELEGATED):
        python = _tool_python()
        if python:
            return _delegate(python, url, timeout_ms, proxy)
    return asyncio.run(_afetch(url, timeout_ms, proxy))
