#!/usr/bin/env python3
"""Parse RSS feeds and score against active investigation topics.

Usage:
    python3 parse-rss.py                    # Fetch all feeds, score against topics
    python3 parse-rss.py --feeds-only       # Just fetch and print articles (no scoring)
    python3 parse-rss.py --since 2h         # Only articles from last 2 hours
    python3 parse-rss.py --json             # Output as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from email.utils import parsedate_to_datetime

FEEDS = {
    "OCCRP": "https://www.occrp.org/en/rss",
    "Bellingcat": "https://www.bellingcat.com/feed/",
    "ICIJ": "https://www.icij.org/feed/",
    "The Intercept": "https://theintercept.com/feed/?rss",
    "TBIJ": "https://www.thebureauinvestigates.com/feed",
}

TOPICS_PATH = Path.home() / "buried_signals" / "newsroom" / "spotlight" / "cases" / "_topics.json"
USER_AGENT = "BuriedSignals-Monitor/1.0"


def fetch_feed(name: str, url: str, since: datetime | None = None) -> list[dict]:
    """Fetch and parse a single RSS/Atom feed."""
    articles = []
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=15) as resp:
            tree = ET.parse(resp)
    except Exception as e:
        print(f"[WARN] Failed to fetch {name}: {e}", file=sys.stderr)
        return []

    root = tree.getroot()
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub_date_str = item.findtext("pubDate")
        pub_date = None
        if pub_date_str:
            try:
                pub_date = parsedate_to_datetime(pub_date_str)
            except Exception:
                pass
        if since and pub_date and pub_date < since:
            continue
        articles.append({
            "source": name,
            "title": title,
            "url": link,
            "description": desc[:500],
            "published": pub_date.isoformat() if pub_date else None,
        })

    # Atom
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        desc = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
        pub_date_str = entry.findtext("atom:updated", namespaces=ns)
        pub_date = None
        if pub_date_str:
            try:
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
            except Exception:
                pass
        if since and pub_date and pub_date < since:
            continue
        articles.append({
            "source": name,
            "title": title,
            "url": link,
            "description": desc[:500],
            "published": pub_date.isoformat() if pub_date else None,
        })

    return articles


def load_topics() -> list[dict]:
    """Load active investigation topics."""
    if not TOPICS_PATH.exists():
        return []
    with open(TOPICS_PATH) as f:
        data = json.load(f)
    return data.get("active_topics", [])


def score_article(article: dict, topics: list[dict]) -> float:
    """Score article relevance against active topics. Returns 0.0-1.0."""
    if not topics:
        return 0.0

    text = f"{article['title']} {article['description']}".lower()
    best_score = 0.0

    for topic in topics:
        keyword_hits = sum(1 for kw in topic.get("keywords", []) if kw.lower() in text)
        keyword_total = max(len(topic.get("keywords", [])), 1)
        topic_score = min(keyword_hits / keyword_total, 1.0) * 0.4

        entity_hits = sum(1 for ent in topic.get("entities", []) if ent.lower() in text)
        entity_total = max(len(topic.get("entities", [])), 1)
        entity_score = min(entity_hits / entity_total, 1.0) * 0.3

        method_keywords = ["satellite", "document", "court", "filing", "leak", "osint", "analysis"]
        method_hits = sum(1 for mk in method_keywords if mk in text)
        method_score = min(method_hits / 3, 1.0) * 0.2

        recency_score = 0.1  # default full recency score
        if article.get("published"):
            try:
                pub = datetime.fromisoformat(article["published"])
                age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
                recency_score = max(0, 1 - age_hours / 168) * 0.1  # decay over 1 week
            except Exception:
                pass

        total = topic_score + entity_score + method_score + recency_score
        best_score = max(best_score, total)

    return round(best_score, 3)


def parse_since(since_str: str) -> datetime:
    """Parse duration string like '2h', '30m', '1d'."""
    unit = since_str[-1]
    value = int(since_str[:-1])
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    elif unit == "d":
        delta = timedelta(days=value)
    else:
        raise ValueError(f"Unknown unit: {unit}. Use h/m/d.")
    return datetime.now(timezone.utc) - delta


def main():
    parser = argparse.ArgumentParser(description="Parse RSS feeds for Buried Signals monitoring")
    parser.add_argument("--feeds-only", action="store_true", help="Just fetch articles, no scoring")
    parser.add_argument("--since", type=str, help="Only articles since duration (e.g., 2h, 30m, 1d)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    since = parse_since(args.since) if args.since else None

    all_articles = []
    for name, url in FEEDS.items():
        articles = fetch_feed(name, url, since=since)
        all_articles.extend(articles)

    if args.feeds_only:
        if args.json:
            json.dump(all_articles, sys.stdout, indent=2)
        else:
            for a in all_articles:
                print(f"[{a['source']}] {a['title']}")
                print(f"  {a['url']}")
                print(f"  {a['published'] or 'no date'}")
                print()
        return

    topics = load_topics()
    scored = []
    for article in all_articles:
        article["relevance_score"] = score_article(article, topics)
        scored.append(article)

    scored.sort(key=lambda a: a["relevance_score"], reverse=True)

    high = [a for a in scored if a["relevance_score"] >= 0.7]
    medium = [a for a in scored if 0.4 <= a["relevance_score"] < 0.7]

    if args.json:
        json.dump({"high_priority": high, "review_later": medium, "total_fetched": len(scored)}, sys.stdout, indent=2)
    else:
        if high:
            print("=== HIGH PRIORITY (score >= 0.7) ===")
            for a in high:
                print(f"  [{a['source']}] {a['title']} (score: {a['relevance_score']})")
                print(f"    {a['url']}")
            print()
        if medium:
            print("=== REVIEW LATER (score 0.4-0.7) ===")
            for a in medium:
                print(f"  [{a['source']}] {a['title']} (score: {a['relevance_score']})")
            print()
        print(f"Total fetched: {len(scored)}, High: {len(high)}, Medium: {len(medium)}")


if __name__ == "__main__":
    main()
