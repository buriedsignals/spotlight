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
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .mapping import crawl_failure_detail, map_crawl_result
from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_TIMEOUT_MS = 45_000
_BROWSER_PROXY = "SPOTLIGHT_BROWSER_PROXY"
_PDF_MAX_BYTES = 100 * 1024 * 1024
_PDF_USER_AGENT = "Mozilla/5.0 (compatible; Spotlight evidence fetch)"


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
    # Otherwise SPOTLIGHT_BROWSER_PROXY, set only by a harness (never from agent
    # input), sends every connection the browser makes through the harness's
    # egress-guard proxy. "<-loopback>" stops Chromium's implicit loopback
    # bypass, so loopback requests reach the proxy (and its refusal) too.
    browser_kwargs = {"headless": True, "verbose": False}
    browser_proxy = proxy or os.environ.get(_BROWSER_PROXY, "").strip()
    if browser_proxy:
        browser_kwargs["proxy"] = browser_proxy
        browser_kwargs["extra_args"] = ["--proxy-bypass-list=<-loopback>"]
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


def _is_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def _fetch_pdf(url: str, timeout_ms: int, proxy: str | None) -> ScrapeResult:
    """Download a PDF and extract its text with pdftotext (no OCR).

    A headless browser cannot render a PDF to Markdown, so a .pdf URL is
    downloaded instead. The download takes the same route as the browser: the
    harness's egress proxy when one is set. Tor is a SOCKS proxy this download
    cannot use, so an anonymized PDF fetch is refused rather than sent direct.
    """
    if proxy:
        raise ScrapeError(f"PDF download through Tor is not supported for {url}")
    browser_proxy = os.environ.get(_BROWSER_PROXY, "").strip()
    if browser_proxy and urlparse(browser_proxy).scheme != "http":
        raise ScrapeError(f"{_BROWSER_PROXY} must be an http:// proxy for a PDF download")
    handler = urllib.request.ProxyHandler({"http": browser_proxy, "https": browser_proxy} if browser_proxy else {})
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(url, headers={"User-Agent": _PDF_USER_AGENT})
    try:
        with opener.open(request, timeout=timeout_ms / 1000) as response:
            final_url = response.geturl()
            status = response.status
            body = response.read(_PDF_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ScrapeError(f"PDF download failed for {url}: HTTP {exc.code}", status_code=exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ScrapeError(f"PDF download failed for {url}: {exc}") from exc
    if len(body) > _PDF_MAX_BYTES:
        raise ScrapeError(f"PDF at {url} is larger than {_PDF_MAX_BYTES} bytes")
    if not body.lstrip().startswith(b"%PDF-"):
        # A wall or an HTML error page served in place of the document.
        raise ScrapeError(f"{url} did not return a PDF (HTTP {status}); likely an access wall", status_code=403)

    from .pdfparse import parse_pdf

    with tempfile.TemporaryDirectory(prefix="spotlight-pdf-") as tmp:
        path = Path(tmp) / "document.pdf"
        path.write_bytes(body)
        parsed = parse_pdf(str(path))
    return ScrapeResult(
        markdown=parsed.markdown,
        requested_url=url,
        source_url=final_url,
        fetched_at=parsed.fetched_at,
        provider=parsed.provider,
        status_code=status,
        metadata={**parsed.metadata, "pdf_sha256": hashlib.sha256(body).hexdigest(), "pdf_bytes": len(body)},
    )


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, proxy: str | None = None) -> ScrapeResult:  # pragma: no cover - live path
    if _is_pdf_url(url):
        return _fetch_pdf(url, timeout_ms, proxy)
    if not _importable() and not os.environ.get(_DELEGATED):
        python = _tool_python()
        if python:
            return _delegate(python, url, timeout_ms, proxy)
    return asyncio.run(_afetch(url, timeout_ms, proxy))
