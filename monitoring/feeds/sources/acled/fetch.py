"""ACLED Conflict Events source.

Fetches armed conflict events from the ACLED API.
Requires ACLED_API_KEY and ACLED_EMAIL env vars (free registration at
https://developer.acleddata.com/).

Events covered: battles, violence against civilians, protests, riots,
strategic developments. Global coverage, daily updates.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.acleddata.com/acled/read"
USER_AGENT = "BuriedSignals-Monitor/1.0"
MAX_RECORDS = 100


def _parse_since(since: str) -> str:
    """Convert since string to ACLED event_date filter (YYYY-MM-DD)."""
    unit = since[-1]
    value = int(since[:-1])
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    elif unit == "d":
        delta = timedelta(days=value)
    else:
        delta = timedelta(days=7)
    cutoff = datetime.now(timezone.utc) - delta
    return cutoff.strftime("%Y-%m-%d")


def _topic_to_country(topics: list[dict]) -> str | None:
    """Extract the first country code from active topics (if any)."""
    for t in topics:
        country = t.get("country") or t.get("iso2")
        if country:
            return country.upper()
    return None


def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
    """Fetch conflict events from ACLED.

    Args:
        query: Optional keyword filter (matches actor/notes fields)
        topics: Active topics from _topics.json (used for country hint)
        since: Time window, e.g. "24h", "35m", "7d"

    Returns:
        List of signal dicts.
    """
    api_key = os.environ.get("ACLED_API_KEY")
    email = os.environ.get("ACLED_EMAIL")
    if not api_key or not email:
        print("[WARN] ACLED: ACLED_API_KEY or ACLED_EMAIL not set — skipping", file=sys.stderr)
        return []

    event_date = _parse_since(since)
    country = _topic_to_country(topics)

    params = {
        "key": api_key,
        "email": email,
        "event_date": f"{event_date}|{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "event_date_where": "BETWEEN",
        "limit": str(MAX_RECORDS),
    }
    if country:
        params["iso2"] = country
    if query:
        params["notes"] = query
        params["notes_where"] = "LIKE"

    url = f"{API_BASE}?{urlencode(params, quote_via=quote)}"

    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"[WARN] ACLED HTTP {e.code}: {e.reason}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[WARN] ACLED fetch error: {e}", file=sys.stderr)
        return []

    events = data.get("data") or []
    signals: list[dict] = []

    for event in events:
        title_parts = [
            event.get("event_type", ""),
            event.get("sub_event_type", ""),
            f"in {event.get('location', 'unknown')}",
        ]
        title = " — ".join([p for p in title_parts if p]).strip()

        actor1 = event.get("actor1", "")
        actor2 = event.get("actor2", "")
        notes = event.get("notes", "")

        summary_parts = []
        if actor1 and actor2:
            summary_parts.append(f"{actor1} vs {actor2}")
        elif actor1:
            summary_parts.append(actor1)
        if event.get("fatalities"):
            summary_parts.append(f"fatalities: {event['fatalities']}")
        if notes:
            summary_parts.append(notes[:300])
        summary = " — ".join(summary_parts) or title

        date_str = event.get("event_date", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            date_iso = dt.isoformat()
        except Exception:
            date_iso = date_str

        signals.append({
            "title": title or "ACLED event",
            "url": f"https://acleddata.com/data-export-tool/?id={event.get('event_id_cnty', '')}",
            "source_name": "ACLED",
            "source_domain": "acleddata.com",
            "date": date_iso,
            "summary": summary[:500],
            "category": "conflict",
            "relevance_score": 0,
            "matched_keywords": [],
            "language": "eng",
            "_raw": {
                "event_type": event.get("event_type"),
                "country": event.get("country"),
                "location": event.get("location"),
                "fatalities": event.get("fatalities"),
            },
        })

    return signals
