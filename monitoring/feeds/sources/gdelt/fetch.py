"""GDELT Document API source.

Fetches global news articles via the GDELT Doc API.
Rate limited: 5s between requests, exponential backoff on 429.
Reference: ~/worldmonitor/scripts/seed-gdelt-intel.mjs
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import urlopen, Request

API_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "BuriedSignals-Monitor/1.0"
MAX_RECORDS = 25
REQUEST_DELAY = 5  # seconds between requests
MAX_RETRIES = 3
BACKOFF_BASE = 60  # seconds


def _parse_since(since: str) -> str:
    """Convert since string to GDELT timespan format."""
    # GDELT uses minutes for timespan
    unit = since[-1]
    value = int(since[:-1])
    if unit == "h":
        return f"{value * 60}min"
    elif unit == "m":
        return f"{value}min"
    elif unit == "d":
        return f"{value * 24 * 60}min"
    return "1440min"  # default 24h


def _fetch_with_backoff(url: str) -> dict | None:
    """Fetch URL with retry and exponential backoff on 429."""
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")
                if not data.strip():
                    return None
                return json.loads(data)
        except HTTPError as e:
            if e.code == 429:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"[WARN] GDELT 429 — backing off {wait}s (attempt {attempt + 1})", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[WARN] GDELT HTTP {e.code}: {e.reason}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[WARN] GDELT fetch error: {e}", file=sys.stderr)
            return None
    return None


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
    """Fetch articles from GDELT Document API.

    Args:
        query: Search query string (keywords)
        topics: Active topics from _topics.json
        since: Time window, e.g. "24h", "35m", "7d"

    Returns:
        List of signal dicts
    """
    if not query:
        if not topics:
            return []
        # Build from topics
        all_kw = []
        for t in topics:
            all_kw.extend(t.get("keywords", []))
        query = " ".join(all_kw)

    if not query.strip():
        return []

    timespan = _parse_since(since)

    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(MAX_RECORDS),
        "format": "json",
        "sort": "date",
        "timespan": timespan,
    }

    url = f"{API_BASE}?{urlencode(params, quote_via=quote)}"

    time.sleep(REQUEST_DELAY)  # respect rate limit
    data = _fetch_with_backoff(url)

    if not data:
        return []

    articles = data.get("articles", [])
    signals = []

    for article in articles:
        title = (article.get("title") or "").strip()
        art_url = (article.get("url") or "").strip()
        if not title or not art_url:
            continue

        # Parse date
        date_str = article.get("seendate", "")
        date_iso = ""
        if date_str:
            try:
                # GDELT format: "20260320T123456Z"
                dt = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                date_iso = dt.isoformat()
            except Exception:
                date_iso = date_str

        source_name = (article.get("domain") or "").replace("www.", "")
        language = article.get("language", "eng") or "eng"

        signals.append({
            "title": title,
            "url": art_url,
            "source_name": "GDELT",
            "source_domain": source_name,
            "date": date_iso,
            "summary": title[:500],  # GDELT artlist doesn't provide summaries
            "category": "news",
            "relevance_score": 0,  # scored by framework
            "matched_keywords": [],
            "language": language[:3],
        })

    return signals
