"""Regional news RSS feeds.

17 feeds covering South Asia, Middle East, Africa, and global affairs.
Feed URLs sourced from ~/worldmonitor/server/worldmonitor/news/v1/_feeds.ts.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.request import urlopen, Request

FEEDS = {
    # South Asia
    "NDTV": "https://feeds.feedburner.com/ndtvnews-top-stories",
    "The Hindu": "https://www.thehindu.com/news/feeder/default.rss",
    "Scroll.in": "http://feeds.feedburner.com/ScrollinArticles.rss",
    # Middle East
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "BBC Middle East": "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    # Africa
    "BBC Africa": "http://feeds.bbci.co.uk/news/world/africa/rss.xml",
    "Africanews": "https://www.africanews.com/feed/",
    # Iran / Central Asia
    "BBC Persian": "https://feeds.bbci.co.uk/persian/rss.xml",
    # Crisis / Humanitarian
    "UN News": "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",
    "WHO": "https://www.who.int/rss-feeds/news-english.xml",
    # Environment / Resources
    "Mongabay": "https://news.mongabay.com/feed/",
    # Geopolitics / Security
    "Foreign Policy": "https://foreignpolicy.com/feed/",
    "War on the Rocks": "https://warontherocks.com/feed/",
    # Global generalist
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Guardian World": "https://www.theguardian.com/world/rss",
    "Reuters via GNews": "https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com&ceid=US:en&hl=en-US&gl=US",
}

USER_AGENT = "BuriedSignals-Monitor/1.0"


def _parse_feed(name: str, url: str, since_dt: datetime | None) -> list[dict]:
    """Fetch and parse a single RSS/Atom feed."""
    articles = []
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().lstrip()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[WARN] Failed to fetch {name}: {e}", file=sys.stderr)
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub_date = _parse_rss_date(item.findtext("pubDate"))
        if since_dt and pub_date and pub_date < since_dt:
            continue
        articles.append(_make_signal(name, title, link, desc, pub_date))

    # Atom
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        desc = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
        pub_date = _parse_atom_date(entry.findtext("atom:updated", namespaces=ns))
        if since_dt and pub_date and pub_date < since_dt:
            continue
        articles.append(_make_signal(name, title, link, desc, pub_date))

    return articles


def _parse_rss_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


def _parse_atom_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _make_signal(source: str, title: str, url: str, desc: str, pub_date: datetime | None) -> dict:
    return {
        "title": title,
        "url": url,
        "source_name": source,
        "source_domain": _extract_domain(url),
        "date": pub_date.isoformat() if pub_date else "",
        "summary": desc[:500],
        "category": "news",
        "relevance_score": 0,
        "matched_keywords": [],
        "language": "en",
    }


def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
    """Fetch articles from regional news RSS feeds.

    Args:
        query: Ignored for RSS (feeds are fetched in full, scored by framework)
        topics: Active topics (used only by framework for scoring)
        since: Time window, e.g. "24h", "35m", "7d"

    Returns:
        List of signal dicts
    """
    since_dt = None
    if since:
        try:
            match = re.match(r"^(\d+)([hmd])$", since)
            if match:
                value = int(match.group(1))
                unit = match.group(2)
                if unit == "h":
                    delta = timedelta(hours=value)
                elif unit == "m":
                    delta = timedelta(minutes=value)
                elif unit == "d":
                    delta = timedelta(days=value)
                since_dt = datetime.now(timezone.utc) - delta
        except Exception:
            pass

    all_signals = []
    for name, url in FEEDS.items():
        signals = _parse_feed(name, url, since_dt)
        all_signals.extend(signals)

    return all_signals
