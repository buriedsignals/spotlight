"""Shared utilities for the OSINT feed framework.

Topic loading, relevance scoring, signal formatting, deduplication, output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOPICS_PATH = Path(__file__).resolve().parents[2] / "cases" / "_topics.json"


@dataclass
class Signal:
    title: str
    url: str
    source_name: str
    source_domain: str
    date: str  # ISO 8601
    summary: str  # max 500 chars
    category: str  # news | conflict | humanitarian | crisis
    relevance_score: float  # 0.0-1.0
    matched_keywords: list[str]
    language: str  # ISO 639-1

    def to_dict(self) -> dict:
        return asdict(self)


def load_topics(project: str | None = None) -> list[dict]:
    """Load active investigation topics, optionally filtered to one project."""
    if not TOPICS_PATH.exists():
        return []
    with open(TOPICS_PATH) as f:
        data = json.load(f)
    topics = data.get("active_topics", [])
    if project:
        topics = [t for t in topics if t.get("project") == project]
    return topics


def build_query_from_topics(topics: list[dict]) -> str:
    """Build a search query string from topic keywords."""
    all_keywords = []
    for topic in topics:
        all_keywords.extend(topic.get("keywords", []))
    return " ".join(all_keywords)


def score_signal(signal: dict, topics: list[dict]) -> tuple[float, list[str]]:
    """Score signal relevance against active topics.

    Returns (score 0.0-1.0, list of matched keywords).
    Port of parse-rss.py scoring algorithm.
    """
    if not topics:
        return 0.0, []

    text = f"{signal.get('title', '')} {signal.get('summary', '')}".lower()
    best_score = 0.0
    best_matches = []

    for topic in topics:
        keywords = topic.get("keywords", [])
        keyword_matches = [kw for kw in keywords if kw.lower() in text]
        keyword_total = max(len(keywords), 1)
        keyword_score = min(len(keyword_matches) / keyword_total, 1.0) * 0.4

        entities = topic.get("entities", [])
        entity_matches = [ent for ent in entities if ent.lower() in text]
        entity_total = max(len(entities), 1)
        entity_score = min(len(entity_matches) / entity_total, 1.0) * 0.3

        method_keywords = ["satellite", "document", "court", "filing", "leak", "osint", "analysis"]
        method_hits = sum(1 for mk in method_keywords if mk in text)
        method_score = min(method_hits / 3, 1.0) * 0.2

        recency_score = 0.1
        date_str = signal.get("date")
        if date_str:
            try:
                pub = datetime.fromisoformat(date_str)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
                recency_score = max(0, 1 - age_hours / 168) * 0.1
            except Exception:
                pass

        total = keyword_score + entity_score + method_score + recency_score
        if total > best_score:
            best_score = total
            best_matches = keyword_matches + entity_matches

    return round(best_score, 3), best_matches


def deduplicate(signals: list[dict]) -> list[dict]:
    """Deduplicate signals by URL."""
    seen = set()
    unique = []
    for s in signals:
        url = s.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(s)
    return unique


def parse_since(since_str: str) -> datetime:
    """Parse duration string like '24h', '35m', '7d' to a UTC datetime."""
    match = re.match(r"^(\d+)([hmd])$", since_str)
    if not match:
        raise ValueError(f"Invalid since format: {since_str}. Use e.g. 24h, 35m, 7d.")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    elif unit == "d":
        delta = timedelta(days=value)
    return datetime.now(timezone.utc) - delta


def source_relevance(manifest: dict, topics: list[dict]) -> dict:
    """Compute relevance hint for a source given active topics."""
    if not topics:
        return {"relevance": "unknown", "reason": "no active topics"}

    regions = set(manifest.get("regions", []))
    category = manifest.get("category", "")

    # Build keyword context from topics
    all_keywords = []
    all_entities = []
    for t in topics:
        all_keywords.extend(t.get("keywords", []))
        all_entities.extend(t.get("entities", []))

    text = " ".join(all_keywords + all_entities).lower()

    # Region matching heuristics
    region_indicators = {
        "south-asia": ["india", "assam", "bangladesh", "pakistan", "nepal", "sri lanka"],
        "middle-east": ["israel", "palestine", "gaza", "iran", "iraq", "syria", "lebanon", "yemen"],
        "africa": ["nigeria", "kenya", "ethiopia", "congo", "sudan", "sahel"],
        "europe": ["ukraine", "russia", "eu", "nato"],
        "global": [],  # always matches
    }

    matched_regions = []
    for region in regions:
        if region == "global":
            matched_regions.append(region)
            continue
        indicators = region_indicators.get(region, [])
        if any(ind in text for ind in indicators):
            matched_regions.append(region)

    if matched_regions or "global" in regions:
        return {"relevance": "high", "reason": _build_reason(manifest, matched_regions, all_keywords)}
    return {"relevance": "low", "reason": f"regions {list(regions)} don't overlap with topic geography"}


def _build_reason(manifest: dict, matched_regions: list[str], keywords: list[str]) -> str:
    """Build a human-readable relevance reason."""
    parts = []
    name = manifest.get("name", manifest.get("id", ""))
    if "global" in matched_regions:
        parts.append("global coverage")
    elif matched_regions:
        parts.append(f"covers {', '.join(matched_regions)}")
    if keywords:
        parts.append(f"topic keywords: {', '.join(keywords[:3])}")
    return "; ".join(parts) if parts else name


def format_output(result: dict, as_json: bool = True) -> str:
    """Format output as JSON or human-readable text."""
    if as_json:
        return json.dumps(result, indent=2, ensure_ascii=False)

    lines = []
    signals = result.get("signals", [])
    source = result.get("source", "unknown")
    lines.append(f"=== {source} ({len(signals)} signals) ===")
    for s in signals:
        score = s.get("relevance_score", 0)
        lines.append(f"  [{score:.2f}] {s.get('title', 'untitled')}")
        lines.append(f"         {s.get('url', '')}")
        if s.get("matched_keywords"):
            lines.append(f"         keywords: {', '.join(s['matched_keywords'])}")
    return "\n".join(lines)
