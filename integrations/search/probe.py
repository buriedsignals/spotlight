"""Preflight probe of the search backing the `search` verb will use:

    scripts/spotlight-python -m integrations.search.probe

With ``SEARCH_PROVIDER=firecrawl`` every search goes to Firecrawl, so SearXNG is
not queried; the probe reports whether Firecrawl may run (sensitive mode,
``firecrawl_mode``, the CLI on PATH) without spending a query. Otherwise it sends one real SearXNG query
(``SEARXNG_URL``, default ``http://localhost:8899``): an HTTP 200 with zero
results (every engine suspended, CAPTCHA-blocked or crashing) is a failure.

Prints one line. Exit 0 when the backing is usable, 1 when it is not.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[2]
if str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))
from integrations._credentials import executable  # noqa: E402
from integrations._firecrawl_policy import refusal  # noqa: E402

from .searxng_provider import _base_url  # noqa: E402

TIMEOUT_S = 30


def probe_firecrawl() -> tuple[bool, str]:
    if reason := refusal():
        return False, f"search: firecrawl (SEARCH_PROVIDER) refused: {reason}"
    if executable("firecrawl") is None:
        return False, "search: firecrawl (SEARCH_PROVIDER) unavailable: firecrawl CLI not on PATH"
    return True, "search: firecrawl (SEARCH_PROVIDER); SearXNG not probed"


def probe_searxng() -> tuple[bool, str]:
    base = _base_url()
    url = base + "/search?" + urllib.parse.urlencode({"q": "wikipedia", "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": "spotlight-search/1.0"})
    try:
        data = json.loads(urllib.request.urlopen(request, timeout=TIMEOUT_S).read())
    except Exception as exc:  # noqa: BLE001 - unreachable or not JSON
        return False, f"searxng unreachable at {base}: {exc}"
    if not isinstance(data, dict):
        return False, f"searxng at {base} did not answer with a JSON object"
    results = data.get("results") or []
    unresponsive = data.get("unresponsive_engines") or []
    return bool(results), f"searxng results: {len(results)} unresponsive: {unresponsive}"


def main() -> int:
    provider = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    ok, line = probe_firecrawl() if provider == "firecrawl" else probe_searxng()
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
