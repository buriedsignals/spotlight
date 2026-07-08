"""U14 scraping-seam tests — run WITHOUT the crawl4ai browser stack installed.

The Crawl4AI provider imports crawl4ai lazily (inside the live fetch), so the
seam, the KTD7 raw-markdown mapping, the determinism contract, and provider
dispatch are all exercisable here with a duck-typed fake CrawlResult and a
monkeypatched provider. The live browser fetch is covered by U15's install.

Ladder as of 2026-07-08 (GOING_LOCAL KTD6): Crawl4AI -> optional Firecrawl
(Scrapling middle rung dropped). Firecrawl escalation fires only when
FIRECRAWL_API_KEY is present; otherwise the seam stays sovereign and raises.
Tor (U7): anonymize routes Crawl4AI through the local Tor SOCKS proxy and never
silently falls back to a direct fetch.

    python3 tests/test_scraping_seam.py     # standalone
    python3 -m pytest tests/test_scraping_seam.py
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from integrations.scraping.mapping import _extract_markdown, map_crawl_result  # noqa: E402
from integrations.scraping.scrape import TOR_SOCKS, scrape  # noqa: E402
from integrations.scraping.scrape_types import ScrapeError, ScrapeResult  # noqa: E402
import integrations.scraping.crawl4ai_provider as c4a  # noqa: E402
import integrations.scraping.firecrawl_provider as fc  # noqa: E402


def _mk(markdown, provider="crawl4ai", status=200):
    return ScrapeResult(markdown=markdown, requested_url="u", source_url="u", fetched_at="t", provider=provider, status_code=status)


def _run_with(c4a_fetch, fc_fetch, call, *, key="test-key"):
    """Patch the two providers + FIRECRAWL_API_KEY (present by default so the
    Firecrawl escalation is reachable; pass key=None for the sovereign case)."""
    o1, o2, okey = c4a.fetch, fc.fetch, os.environ.get("FIRECRAWL_API_KEY")
    c4a.fetch, fc.fetch = c4a_fetch, fc_fetch
    if key is None:
        os.environ.pop("FIRECRAWL_API_KEY", None)
    else:
        os.environ["FIRECRAWL_API_KEY"] = key
    try:
        return call()
    finally:
        c4a.fetch, fc.fetch = o1, o2
        if okey is None:
            os.environ.pop("FIRECRAWL_API_KEY", None)
        else:
            os.environ["FIRECRAWL_API_KEY"] = okey


_FALLBACK = ScrapeResult(markdown="recovered via firecrawl", requested_url="u", source_url="u", fetched_at="t", provider="firecrawl")
_BOOM = lambda *a, **k: (_ for _ in ()).throw(AssertionError("firecrawl must not be called"))  # noqa: E731


class _FakeMarkdown:
    """Mirrors Crawl4AI's MarkdownGenerationResult: raw_markdown + fit_markdown."""

    def __init__(self, raw: str, fit: str) -> None:
        self.raw_markdown = raw
        self.fit_markdown = fit


class _FakeResult:
    def __init__(self, markdown, url="https://example.gov/a", status=200):
        self.markdown = markdown
        self.url = url
        self.status_code = status
        self.success = True
        self.html = "<html>raw</html>"
        self.cleaned_html = "<html>clean</html>"
        self.metadata = {"title": "Agenda"}


def test_mapping_uses_raw_markdown_not_fit():
    r = map_crawl_result(_FakeResult(_FakeMarkdown(raw="# Real evidence", fit="filtered")), "https://example.gov/a")
    assert r.markdown == "# Real evidence", "must use raw_markdown, never fit_markdown (KTD7)"
    assert r.title == "Agenda" and r.status_code == 200 and r.provider == "crawl4ai"
    assert r.raw_html == "<html>raw</html>" and r.html == "<html>clean</html>"


def test_extract_markdown_plain_string_and_absent():
    assert _extract_markdown("plain") == "plain"
    assert _extract_markdown(None) == ""


def test_mapping_fails_loud_on_drift():
    try:
        _extract_markdown(object())  # unexpected shape -> must raise, not repr() garbage
    except ValueError:
        return
    raise AssertionError("expected ValueError on unexpected markdown shape")


def test_content_sha256_deterministic_across_providers():
    a = ScrapeResult(markdown="same body", requested_url="u", source_url="u", fetched_at="t", provider="crawl4ai")
    b = ScrapeResult(markdown="same body", requested_url="u", source_url="u", fetched_at="DIFFERENT", provider="firecrawl")
    assert a.content_sha256 == b.content_sha256, "same body -> same evidence key regardless of provider/timestamp"
    assert ScrapeResult(markdown="", requested_url="u", source_url="u", fetched_at="t", provider="x").is_empty()


def test_seam_dispatch_selects_provider():
    sentinel = ScrapeResult(markdown="ok", requested_url="u", source_url="u", fetched_at="t", provider="crawl4ai")
    orig = c4a.fetch
    c4a.fetch = lambda url, timeout_ms=45_000, proxy=None: sentinel  # noqa: E731
    try:
        assert scrape("https://x", provider="crawl4ai") is sentinel
    finally:
        c4a.fetch = orig


def test_seam_unknown_provider_raises():
    try:
        scrape("https://x", provider="bogus")
    except ScrapeError:
        return
    raise AssertionError("expected ScrapeError for an unknown provider")


def test_escalation_on_empty_result():
    r = _run_with(lambda url, timeout_ms=45_000, proxy=None: _mk("   "),
                  lambda url, timeout_ms=45_000: _FALLBACK,
                  lambda: scrape("https://x"))
    assert r.provider == "firecrawl" and r.markdown == "recovered via firecrawl"


def test_escalation_on_block_status():
    r = _run_with(lambda url, timeout_ms=45_000, proxy=None: _mk("body", status=403),
                  lambda url, timeout_ms=45_000: _FALLBACK,
                  lambda: scrape("https://x"))
    assert r.provider == "firecrawl"


def test_escalation_on_block_error():
    def raise_block(url, timeout_ms=45_000, proxy=None):
        raise ScrapeError("blocked", status_code=429)
    r = _run_with(raise_block, lambda url, timeout_ms=45_000: _FALLBACK, lambda: scrape("https://x"))
    assert r.provider == "firecrawl"


def test_no_firecrawl_key_stays_sovereign_and_raises():
    # Bot-blocked with no FIRECRAWL_API_KEY: no escape hatch, surface honestly.
    def blocked(url, timeout_ms=45_000, proxy=None):
        raise ScrapeError("blocked", status_code=403)
    try:
        _run_with(blocked, _BOOM, lambda: scrape("https://x"), key=None)
    except ScrapeError as e:
        assert "FIRECRAWL_API_KEY" in str(e)
        return
    raise AssertionError("sovereign mode (no key) must raise, not escalate")


def test_no_escalation_on_success():
    r = _run_with(lambda url, timeout_ms=45_000, proxy=None: _mk("clean body", status=200), _BOOM, lambda: scrape("https://x"))
    assert r.provider == "crawl4ai" and r.markdown == "clean body"


def test_non_block_error_does_not_escalate():
    def raise_other(url, timeout_ms=45_000, proxy=None):
        raise ScrapeError("crawl4ai is not installed")  # no status_code -> not a block
    try:
        _run_with(raise_other, _BOOM, lambda: scrape("https://x"))
    except ScrapeError as e:
        assert "not installed" in str(e)
        return
    raise AssertionError("a non-block ScrapeError must propagate, not escalate")


def test_escalate_false_disables_ladder():
    r = _run_with(lambda url, timeout_ms=45_000, proxy=None: _mk(""), _BOOM, lambda: scrape("https://x", escalate=False))
    assert r.is_empty() and r.provider == "crawl4ai"


def test_tor_anonymize_passes_proxy():
    seen = {}

    def record(url, timeout_ms=45_000, proxy=None):
        seen["proxy"] = proxy
        return _mk("body")
    _run_with(record, _BOOM, lambda: scrape("https://x", anonymize=True))
    assert seen["proxy"] == TOR_SOCKS, "anonymize=True must route Crawl4AI through the Tor SOCKS proxy"


def test_tor_failure_never_falls_back_direct():
    # A Tor-proxied fetch that fails must NOT escalate to Firecrawl (that would
    # leak the URL off the anonymized path) nor silently retry direct.
    def tor_boom(url, timeout_ms=45_000, proxy=None):
        assert proxy == TOR_SOCKS
        raise ScrapeError("tor exit blocked", status_code=403)
    try:
        _run_with(tor_boom, _BOOM, lambda: scrape("https://x", anonymize=True))
    except ScrapeError as e:
        assert "Tor" in str(e) and "no-tor" in str(e)
        return
    raise AssertionError("an anonymized fetch failure must raise, never de-anonymize")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
