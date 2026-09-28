"""Firecrawl backend — kept, opt-in via SCRAPE_PROVIDER=firecrawl (loop U14).

No longer the default: Crawl4AI removes the paid FIRECRAWL_API_KEY from the
critical path. Shells out to the repo's established `firecrawl` CLI rather than
adding an SDK dependency. Lazy/self-contained so it's only reached when selected.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[2]
if str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))
from integrations._credentials import executable, redact, subprocess_env  # noqa: E402

from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_TIMEOUT_MS = 45_000


def fetch(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ScrapeResult:  # pragma: no cover - live path
    binary = executable("firecrawl")
    if binary is None:
        raise ScrapeError(
            "the `firecrawl` CLI is not installed; the default provider is crawl4ai (no key needed)"
        )
    env = subprocess_env(["FIRECRAWL_API_KEY"])
    try:
        proc = subprocess.run(
            [binary, "scrape", url],
            capture_output=True,
            text=True,
            # The CLI writes UTF-8; Windows would otherwise decode it as cp1252 and crash.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScrapeError(f"firecrawl scrape timed out for {url}") from exc
    if proc.returncode != 0:
        detail = redact(proc.stderr.strip(), [env.get("FIRECRAWL_API_KEY", "")])
        raise ScrapeError(f"firecrawl scrape failed for {url}: {detail}")
    return ScrapeResult(
        markdown=proc.stdout,
        requested_url=url,
        source_url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        provider="firecrawl",
    )
