"""GDACS disaster alert source.

Fetches active disaster events from the GDACS API.
Reference: ~/worldmonitor/scripts/seed-natural-events.mjs
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request

API_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP"
USER_AGENT = "BuriedSignals-Monitor/1.0"

EVENT_TYPE_MAP = {
    "FL": "flood",
    "TC": "tropical cyclone",
    "VO": "volcano",
    "WF": "wildfire",
    "DR": "drought",
    "EQ": "earthquake",
}


def _parse_since(since: str) -> datetime:
    """Parse since string to UTC datetime."""
    match = re.match(r"^(\d+)([hmd])$", since)
    if not match:
        return datetime.now(timezone.utc) - timedelta(days=7)
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    elif unit == "d":
        delta = timedelta(days=value)
    return datetime.now(timezone.utc) - delta


def _matches_topics(event: dict, topics: list[dict]) -> bool:
    """Check if event country/region overlaps with topic entities or keywords."""
    if not topics:
        return True  # no filter, include all

    country = (event.get("country") or "").lower()
    name = (event.get("name") or "").lower()
    description = (event.get("description") or "").lower()
    text = f"{country} {name} {description}"

    for topic in topics:
        for kw in topic.get("keywords", []):
            if kw.lower() in text:
                return True
        for ent in topic.get("entities", []):
            if ent.lower() in text:
                return True

    return False


def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
    """Fetch active disaster events from GDACS.

    Args:
        query: Ignored (GDACS doesn't support text search)
        topics: Active topics for geographic filtering
        since: Time window for filtering events

    Returns:
        List of signal dicts
    """
    try:
        req = Request(API_URL, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[WARN] GDACS fetch error: {e}", file=sys.stderr)
        return []

    features = data.get("features", [])
    since_dt = _parse_since(since)

    signals = []
    for feature in features:
        props = feature.get("properties", {})

        # Parse event date
        date_str = props.get("fromdate", "")
        event_date = None
        if date_str:
            try:
                event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                try:
                    event_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    pass
            # Ensure timezone-aware for comparison
            if event_date and event_date.tzinfo is None:
                event_date = event_date.replace(tzinfo=timezone.utc)

        if event_date and event_date < since_dt:
            continue

        event_type = props.get("eventtype", "")
        type_name = EVENT_TYPE_MAP.get(event_type, event_type)
        name = props.get("name", "") or props.get("eventname", "")
        country = props.get("country", "")
        alert_level = props.get("alertlevel", "")
        severity = props.get("severity", {})
        severity_text = severity.get("severity_text", "") if isinstance(severity, dict) else str(severity)
        description = props.get("description", "")
        url = props.get("url", {})
        detail_url = url.get("detail", "") if isinstance(url, dict) else str(url)
        report_url = url.get("report", "") if isinstance(url, dict) else ""

        # Use report URL if available, else detail, else GDACS homepage
        link = report_url or detail_url or "https://www.gdacs.org"

        # Build summary
        parts = [f"{type_name.title()} in {country}" if country else type_name.title()]
        if name:
            parts[0] = f"{name} — {parts[0]}"
        if alert_level:
            parts.append(f"Alert: {alert_level}")
        if severity_text:
            parts.append(severity_text)
        if description:
            parts.append(description)
        summary = ". ".join(parts)[:500]

        title = f"[{alert_level or 'INFO'}] {name or type_name.title()}"
        if country:
            title += f" — {country}"

        event = {
            "title": title,
            "url": link,
            "source_name": "GDACS",
            "source_domain": "gdacs.org",
            "date": event_date.isoformat() if event_date else "",
            "summary": summary,
            "category": "crisis",
            "relevance_score": 0,
            "matched_keywords": [],
            "language": "en",
            "country": country,
            "name": name,
            "description": description,
        }

        # If topics provided, only include matching events
        if topics and not _matches_topics(event, topics):
            continue

        # Clean up extra fields before returning
        event.pop("country", None)
        event.pop("name", None)
        event.pop("description", None)

        signals.append(event)

    return signals
