"""Provider-agnostic fetch seam (loop U14 / PRD Workstream G).

    from integrations.scraping import scrape
    result = scrape("https://example.gov/agenda.pdf")   # -> ScrapeResult

Default backend is **Crawl4AI** (open-source, local, no API key). On a Crawl4AI
bot-block/empty the seam escalates to **Firecrawl**'s managed proxy pool — the
optional paid escape hatch (KTD6) — but only when FIRECRAWL_API_KEY is present;
with no key the seam stays sovereign and surfaces the block honestly. (The
Scrapling stealth middle rung was dropped 2026-07-08 — ladder is Crawl4AI ->
optional Firecrawl.)

Tor (U7 / KTD8): pass ``anonymize=True`` (or set ``SPOTLIGHT_ANONYMIZE_FETCH=true``
for a whole run) to route the Crawl4AI fetch through the local Tor SOCKS proxy, so
scraping a target-of-investigation never reveals the operator's IP. A Tor-proxied
fetch NEVER silently falls back to a direct (de-anonymizing) fetch — on a Tor
failure it raises, and the operator chooses to re-run direct or abort.

Providers are imported lazily so selecting one never drags in the others' deps
(Crawl4AI's browser stack, Firecrawl's CLI). This is the only module skills
import — they never touch a vendor SDK directly.
"""
from __future__ import annotations

import os

from .scrape_types import ScrapeError, ScrapeResult

DEFAULT_PROVIDER = "crawl4ai"
_KNOWN = ("crawl4ai", "firecrawl")
_BLOCK_STATUSES = {401, 403, 429}
_TRUTHY = {"1", "true", "yes", "on"}

# Local Tor SOCKS endpoint the installer provisions (U5/U7). Overridable for a
# non-standard Tor port; operator-only (never taken from query/agent input).
TOR_SOCKS = os.environ.get("TOR_SOCKS", "socks5://127.0.0.1:9050")


def _provider_name(explicit: str | None) -> str:
    return (explicit or os.environ.get("SCRAPE_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def _resolve_proxy(anonymize: bool | None) -> str | None:
    """Tor proxy for this fetch, or None for a direct fetch.

    Per-run default: SPOTLIGHT_ANONYMIZE_FETCH=true anonymizes every fetch. The
    per-fetch ``anonymize`` arg (--tor/--no-tor) overrides it; None defers to env.
    """
    if anonymize is None:
        anonymize = os.environ.get("SPOTLIGHT_ANONYMIZE_FETCH", "").strip().lower() in _TRUTHY
    return TOR_SOCKS if anonymize else None


def _blocked(result: ScrapeResult) -> bool:
    # Crawl4AI returned, but the site defeated it: empty body or a bot-block
    # status. This is the escalation signal (KTD6).
    return result.is_empty() or (result.status_code in _BLOCK_STATUSES)


def scrape(url: str, *, provider: str | None = None, timeout_ms: int = 45_000,
           escalate: bool = True, anonymize: bool | None = None) -> ScrapeResult:
    """Fetch url to a ScrapeResult via the selected backend.

    Default ladder (KTD6): Crawl4AI primary -> Firecrawl (managed proxy pool) only
    when Crawl4AI is bot-blocked/empty AND FIRECRAWL_API_KEY is set. escalate=False,
    or selecting a non-default provider, disables the ladder. ``anonymize`` routes
    the Crawl4AI fetch through Tor (U7). Raises ScrapeError on an unknown provider,
    an absent backend, or a fetch failure that isn't a recoverable bot-block.
    """
    name = _provider_name(provider)
    proxy = _resolve_proxy(anonymize)
    if name == "crawl4ai":
        from . import crawl4ai_provider

        try:
            result = crawl4ai_provider.fetch(url, timeout_ms=timeout_ms, proxy=proxy)
        except ScrapeError as exc:
            # A Tor-proxied fetch must never silently retry direct (that would
            # de-anonymize). Surface it; the operator re-runs --no-tor or aborts.
            if proxy is not None:
                raise ScrapeError(
                    f"anonymized (Tor) fetch of {url} failed via {TOR_SOCKS}: {exc}. "
                    "Not retrying direct — re-run with --no-tor to fetch directly "
                    "(reveals your IP), or abort.",
                    status_code=exc.status_code,
                ) from exc
            # A bot-block error escalates to Firecrawl; anything else (crawl4ai not
            # installed, a real network failure) re-raises — Firecrawl wouldn't help.
            if escalate and exc.status_code in _BLOCK_STATUSES:
                return _escalate(url, timeout_ms)
            raise
        if escalate and proxy is None and _blocked(result):
            return _escalate(url, timeout_ms)
        return result
    if name == "firecrawl":
        from . import firecrawl_provider

        return firecrawl_provider.fetch(url, timeout_ms=timeout_ms)
    raise ScrapeError(f"unknown SCRAPE_PROVIDER {name!r} — choose one of {_KNOWN}")


def _escalate(url: str, timeout_ms: int) -> ScrapeResult:
    # Crawl4AI was bot-blocked/empty on our own IP. Escalate to Firecrawl's managed
    # proxy pool (KTD6) — but only if its key is present. No key ⇒ stay sovereign and
    # surface the block rather than pretending we can reach a hard anti-bot target.
    if not os.environ.get("FIRECRAWL_API_KEY"):
        raise ScrapeError(
            f"crawl4ai was bot-blocked/empty on {url} and no FIRECRAWL_API_KEY is "
            "set for the optional Firecrawl escape hatch — target not reachable "
            "sovereignly (try `browse` for interactive pages)"
        )
    from . import firecrawl_provider

    return firecrawl_provider.fetch(url, timeout_ms=timeout_ms)
