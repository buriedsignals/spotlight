"""coJournalist feed source plugin.

Pulls information units from coJournalist scouts into the OSINT monitoring framework.

- With query: semantic search via GET /api/v1/units/search?q={query}
  Returns units with similarity_score -> maps to relevance_score
  (framework skips its own scoring when score > 0)
- Without query: list units via GET /api/v1/units
  Framework scores them against topics
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen, Request


DEFAULT_API_URL = "https://www.cojournalist.ai/api/v1"
USER_AGENT = "BuriedSignals-Monitor/1.0"


def _get_config() -> tuple[str, str] | None:
    """Return (api_url, api_key) or None if no key configured."""
    api_url = os.environ.get("COJOURNALIST_API_URL", DEFAULT_API_URL).rstrip("/")
    api_key = os.environ.get("COJOURNALIST_API_KEY", "")
    if not api_key:
        print("[WARN] coJournalist: COJOURNALIST_API_KEY not set, skipping", file=sys.stderr)
        return None
    return api_url, api_key


def _api_get(path: str, api_url: str, api_key: str) -> dict | None:
    """Make authenticated GET request."""
    url = f"{api_url}{path}"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    })
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except HTTPError as e:
        print(f"[WARN] coJournalist HTTP {e.code}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[WARN] coJournalist fetch error: {e}", file=sys.stderr)
        return None


def _parse_since(since: str) -> datetime | None:
    """Parse duration string to UTC cutoff datetime."""
    match = re.match(r"^(\d+)([dhm])$", since)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return datetime.now(timezone.utc) - timedelta(days=value)
    elif unit == "h":
        return datetime.now(timezone.utc) - timedelta(hours=value)
    elif unit == "m":
        return datetime.now(timezone.utc) - timedelta(minutes=value)
    return None


def _unit_to_signal(unit: dict, from_search: bool = False) -> dict:
    """Map a coJournalist UnitResponse to a feed signal dict."""
    statement = unit.get("statement", "")
    return {
        "title": statement[:120] if statement else "untitled",
        "url": unit.get("source_url", ""),
        "source_name": unit.get("scout_name") or "coJournalist",
        "source_domain": unit.get("source_domain", ""),
        "date": unit.get("created_at", ""),
        "summary": statement[:500] if statement else "",
        "category": "local_news",
        "relevance_score": unit.get("similarity_score", 0) if from_search else 0,
        "matched_keywords": [],
        "language": "en",
    }


def _filter_since(units: list[dict], cutoff: datetime) -> list[dict]:
    """Client-side since filtering on created_at."""
    filtered = []
    for u in units:
        created = u.get("created_at", "")
        if not created:
            filtered.append(u)
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt >= cutoff:
                filtered.append(u)
        except Exception:
            filtered.append(u)
    return filtered


def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
    """Fetch information units from coJournalist.

    Args:
        query: Search query string (keywords) — triggers semantic search if provided
        topics: Active topics from _topics.json (used to build query if none given)
        since: Time window, e.g. "24h", "7d"

    Returns:
        List of signal dicts
    """
    config = _get_config()
    if config is None:
        return []

    api_url, api_key = config
    cutoff = _parse_since(since)

    if query and query.strip():
        # Semantic search mode
        params = {"q": query.strip(), "limit": "50"}
        path = f"/units/search?{urlencode(params)}"
        data = _api_get(path, api_url, api_key)
        if not data:
            return []

        units = data.get("units", [])
        if cutoff:
            units = _filter_since(units, cutoff)

        return [_unit_to_signal(u, from_search=True) for u in units if u.get("statement")]

    else:
        # List mode — get recent units, let framework score them
        params = {"limit": "50"}
        path = f"/units?{urlencode(params)}"
        data = _api_get(path, api_url, api_key)
        if not data:
            return []

        units = data.get("units", [])
        if cutoff:
            units = _filter_since(units, cutoff)

        return [_unit_to_signal(u, from_search=False) for u in units if u.get("statement")]
