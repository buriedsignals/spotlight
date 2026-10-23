#!/usr/bin/env python3
"""Search queries and scraped URLs never name a protected source.

A fake Engine `bsig` stands in for `bsig wiki redact`; no network is used.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib  # noqa: E402

scrape_mod = importlib.import_module("integrations.scraping.scrape")
search_mod = importlib.import_module("integrations.search.search")
from integrations.scraping.scrape_types import ScrapeError  # noqa: E402
from integrations.search.search_types import SearchError  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        os.environ["HOME"] = str(tmp / "home")  # never this computer's own Engine link
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "bsig"
        fake.write_text(f"""#!{sys.executable}
import json, sys
texts = json.load(sys.stdin)["texts"]
masked = [t.replace("Deep Throat", "[protected]").replace("deep-throat", "[protected]") for t in texts]
print(json.dumps({{"event": "result", "data": {{"result": {{"texts": masked, "redacted": sum(a != b for a, b in zip(texts, masked))}}}}}}))
""")
        fake.chmod(0o755)
        config = tmp / "config.json"
        config.write_text(json.dumps({"knowledge_destination": {"workspace_path": str(tmp / "wiki")}}))
        os.environ["SPOTLIGHT_CONFIG"] = str(config)
        path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{path}"
        sent: list[str] = []
        search_mod.searxng_provider.search = lambda q, **_: sent.append(q) or []
        scrape_mod._provider_name = lambda _p: "crawl4ai"

        class Crawl:
            @staticmethod
            def fetch(url, **_):
                sent.append(url)
                raise AssertionError("fetch must not run")

        sys.modules["integrations.scraping.crawl4ai_provider"] = Crawl  # type: ignore[assignment]

        search_mod.search("port authority contracts")
        assert sent == ["port authority contracts"], sent
        for query in ("Deep Throat port contracts",):
            try:
                search_mod.search(query)
            except SearchError as exc:
                assert "protected source" in str(exc), exc
            else:
                raise AssertionError("a query naming a protected source was sent")
        try:
            scrape_mod.scrape("https://example.org/people/deep-throat", escalate=False)
        except ScrapeError as exc:
            assert "protected source" in str(exc), exc
        else:
            raise AssertionError("a URL naming a protected source was fetched")
        assert len(sent) == 1, sent

        # Engine names a wiki but its bsig is gone: nothing is sent.
        os.environ["PATH"] = path
        try:
            search_mod.search("port authority contracts")
        except SearchError as exc:
            assert "cannot be checked" in str(exc), exc
        else:
            raise AssertionError("a query was sent without the protected-source check")
        # No Engine at all: no register, and the request goes out unchanged.
        config.write_text("{}")
        search_mod.search("port authority contracts")
        assert sent[-1] == "port authority contracts" and len(sent) == 2, sent
    print("protected-egress-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
