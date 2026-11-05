"""Firecrawl modes and the sensitive-case limit, on both seams, through the real provider code.

A fake `firecrawl` binary on PATH logs every call, so "nothing reached Firecrawl" is read at the process boundary.
SearXNG and Crawl4AI are stood in for, since their own behaviour is not what is under test.

    python3 tests/test_firecrawl_policy.py
"""
import json
import os
import pathlib
import stat
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import integrations._firecrawl_policy as policy  # noqa: E402
import integrations.scraping.crawl4ai_provider as c4a  # noqa: E402
import integrations.search.searxng_provider as searxng  # noqa: E402
from integrations.scraping.scrape import scrape  # noqa: E402
from integrations.scraping.scrape_types import ScrapeError, ScrapeResult  # noqa: E402
from integrations.search.search import search  # noqa: E402
from integrations.search.search_types import SearchError, SearchHit  # noqa: E402

FAKE = """#!/bin/sh
echo "$@" >> "$FIRECRAWL_LOG"
if [ "$1" = search ]; then echo '{"data":{"web":[{"url":"https://firecrawl.example/1","title":"From Firecrawl"}]}}'; else echo '# recovered'; fi
"""
HIT = SearchHit(url="https://searxng.example/1", title="From SearXNG", snippet="", date=None, engine="searxng")


class Box:
    """A temp config, the fake binary and the stand-ins, for one case."""

    def __init__(self, config=None, *, key="fc-test-key", hits=(), down=False, blocked=True):
        self.config, self.key, self.hits, self.down, self.blocked = config, key, list(hits), down, blocked

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.log = base / "calls.log"
        fake = base / "firecrawl"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.path = base / "config.json"
        self.write(self.config)
        self.saved = dict(os.environ)
        self.seams = searxng.search, c4a.fetch, policy.ROOT
        os.environ.update(PATH=f"{base}:{self.saved['PATH']}", SPOTLIGHT_CONFIG=str(self.path), FIRECRAWL_LOG=str(self.log))
        for name in ("SPOTLIGHT_SENSITIVE", "SEARCH_PROVIDER", "SCRAPE_PROVIDER", "FIRECRAWL_API_KEY"):
            os.environ.pop(name, None)
        if self.key:
            os.environ["FIRECRAWL_API_KEY"] = self.key
        policy.ROOT = base  # no manifest here unless a test writes one

        def stand_in(query, **_):
            if self.down:
                raise SearchError("searxng is down")
            return list(self.hits)

        def fetched(url, timeout_ms=45_000, proxy=None):
            return ScrapeResult(markdown="" if self.blocked else "# page", requested_url=url, source_url=url, fetched_at="2026-11-05T00:00:00Z", provider="crawl4ai", status_code=200)

        searxng.search, c4a.fetch = stand_in, fetched
        return self

    def write(self, config):
        if config is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_text(config if isinstance(config, str) else json.dumps(config))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def __exit__(self, *_):
        searxng.search, c4a.fetch, policy.ROOT = self.seams
        os.environ.clear()
        os.environ.update(self.saved)
        self.tmp.cleanup()


def urls(hits):
    return [h.url for h in hits]


def attempts(box):
    """Every route to Firecrawl on both seams; each may answer or refuse, none may reach the binary."""
    box.hits, box.down = [], False
    search("Acme filing")                                     # zero hits: backup's trigger
    box.hits = [HIT]
    search("Acme filing")                                     # hits: always's trigger
    search("Acme filing", union=True)                         # explicit union
    box.down = True
    try:
        search("Acme filing")                                 # today's error fallback
    except SearchError:
        pass
    box.down = False
    try:
        search("Acme filing", provider="firecrawl")           # explicit provider
    except SearchError:
        pass
    try:
        scrape("https://example.org/page")                    # fetch ladder on a blocked page
    except ScrapeError:
        pass
    try:
        scrape("https://example.org/page", provider="firecrawl")  # explicit fetch provider
    except ScrapeError:
        pass


def test_sensitive_case_never_reaches_firecrawl_in_any_mode():
    for mode in ("backup", "always", "off", None):
        extra = {"firecrawl_mode": mode} if mode else {}
        for marked in ("config", "env", "manifest"):
            with Box({**extra, **({"sensitive": True} if marked == "config" else {})}) as box:
                if marked == "env":
                    os.environ["SPOTLIGHT_SENSITIVE"] = "true"
                if marked == "manifest":
                    (policy.ROOT / "AGENTS.md").write_text("---\nsensitive: true\n---\n")
                attempts(box)
                assert box.calls() == [], (mode, marked, box.calls())


def test_a_case_marked_sensitive_after_the_chat_started_is_blocked_on_both_seams():
    with Box({"firecrawl_mode": "always"}, hits=[HIT]) as box:
        assert "https://firecrawl.example/1" in urls(search("Acme filing")), "open before the change"
        assert scrape("https://example.org/page").provider == "firecrawl"
        before = len(box.calls())
        box.write({"firecrawl_mode": "always", "sensitive": True})   # the key stays present
        assert urls(search("Acme filing")) == [HIT.url]
        try:
            scrape("https://example.org/page")
            raise AssertionError("the blocked fetch must not fall through")
        except ScrapeError as exc:
            assert "sensitive" in str(exc)
        assert len(box.calls()) == before, box.calls()


def test_an_unreadable_config_counts_as_sensitive():
    with Box("{not json") as box:
        attempts(box)
        assert box.calls() == []


def test_backup_uses_firecrawl_only_when_searxng_finds_nothing():
    with Box({"firecrawl_mode": "backup"}, hits=[HIT]) as box:
        assert urls(search("Acme filing")) == [HIT.url] and box.calls() == []
        box.hits = []
        assert urls(search("Acme filing")) == ["https://firecrawl.example/1"]
    with Box({}) as box:   # backup is the default
        assert urls(search("Acme filing")) == ["https://firecrawl.example/1"]


def test_always_joins_both_engines_and_a_firecrawl_failure_keeps_searxng():
    with Box({"firecrawl_mode": "always"}, hits=[HIT]) as box:
        assert urls(search("Acme filing")) == [HIT.url, "https://firecrawl.example/1"]
        assert len(box.calls()) == 1
        (pathlib.Path(box.tmp.name) / "firecrawl").write_text("#!/bin/sh\nexit 3\n")
        assert urls(search("Acme filing")) == [HIT.url]


def test_off_never_uses_firecrawl_on_either_seam_even_when_asked_for():
    with Box({"firecrawl_mode": "off"}) as box:
        assert search("Acme filing") == []
        box.down = True
        try:
            search("Acme filing")
            raise AssertionError("searxng's failure must surface")
        except SearchError:
            pass
        attempts(box)
        assert box.calls() == []


def test_always_reads_pages_with_firecrawl_and_a_failure_keeps_crawl4ai():
    with Box({"firecrawl_mode": "always"}, blocked=False) as box:
        assert scrape("https://example.org/page").provider == "firecrawl"
        assert box.calls() == ["scrape https://example.org/page"], box.calls()
        binary = pathlib.Path(box.tmp.name) / "firecrawl"
        binary.write_text('#!/bin/sh\necho "$@" >> "$FIRECRAWL_LOG"\nexit 3\n')
        assert scrape("https://example.org/page").provider == "crawl4ai"
        box.blocked = True
        assert scrape("https://example.org/page").provider == "crawl4ai"
        assert len(box.calls()) == 3, "a failed Firecrawl read is not tried twice for one page"
        box.blocked = False
        binary.write_text(FAKE)
        # The operator's explicit provider and a Tor fetch keep their route.
        assert scrape("https://example.org/page", provider="crawl4ai").provider == "crawl4ai"
        os.environ["SCRAPE_PROVIDER"] = "crawl4ai"
        assert scrape("https://example.org/page").provider == "crawl4ai"
        os.environ.pop("SCRAPE_PROVIDER")
        assert scrape("https://example.org/page", anonymize=True).provider == "crawl4ai"
        assert len(box.calls()) == 3, box.calls()


def test_always_stays_on_crawl4ai_for_a_sensitive_case_or_a_withheld_key():
    with Box({"firecrawl_mode": "always", "sensitive": True}, blocked=False) as box:
        assert scrape("https://example.org/page").provider == "crawl4ai"
        assert box.calls() == []
    with Box({"firecrawl_mode": "always"}, key=None, blocked=False) as box:
        assert scrape("https://example.org/page").provider == "crawl4ai"
        assert box.calls() == []


def test_backup_reads_pages_with_crawl4ai_and_falls_back_on_a_block():
    with Box({"firecrawl_mode": "backup"}, blocked=False) as box:
        assert scrape("https://example.org/page").provider == "crawl4ai"
        assert box.calls() == []
    with Box({"firecrawl_mode": "backup"}, blocked=True) as box:
        assert scrape("https://example.org/page").provider == "firecrawl"
        assert box.calls() == ["scrape https://example.org/page"], box.calls()


def test_a_named_config_that_is_missing_counts_as_sensitive():
    with Box(None) as box:   # SPOTLIGHT_CONFIG names a file that is not there
        attempts(box)
        assert box.calls() == []


def test_key_withheld_falls_to_searxng_with_no_error_in_any_mode():
    for mode in ("always", "backup"):
        with Box({"firecrawl_mode": mode}, key=None, hits=[HIT]) as box:
            assert urls(search("Acme filing")) == [HIT.url]
            box.hits = []
            assert search("Acme filing") == []
            assert box.calls() == [], (mode, box.calls())


def test_a_protected_source_stops_before_any_engine():
    module = sys.modules["integrations.search.search"]
    saved = module.check_outgoing

    def refuse(_):
        raise module.ProtectedSourceError("protected")

    module.check_outgoing = refuse
    try:
        with Box({"firecrawl_mode": "always"}, hits=[HIT]) as box:
            try:
                search("a protected name")
                raise AssertionError("the protected query must be refused")
            except SearchError:
                pass
            assert box.calls() == []
    finally:
        module.check_outgoing = saved


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"firecrawl policy: {len(tests)} tests passed")
