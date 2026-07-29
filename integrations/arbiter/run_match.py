#!/usr/bin/env python3
"""Match a user's investigation query against Arbiter's curated case studies.

Users should not have to scan the whole `/topics` menu: they describe what
they want to investigate, and this matcher ranks the saved menu by lexical
similarity so the closest case studies can be offered first. Offline (reads a
previously fetched `/topics` JSON response, no network — safe in sensitive
mode) and deterministic, so the selection flow is testable.

Scoring: the query is tokenized (lowercase alphanumerics, stopwords dropped);
each query token that appears in a topic's title/slug/description counts
toward coverage, weighted by where it matched (title > slug > description).
A prefix match of 4+ characters (e.g. "egyptian" ~ "egypt") earns partial
credit. Topics scoring at or above the threshold are `matches`; the rest are
`others`, so callers always have the full menu to fall back to.

Usage:
  python3 integrations/arbiter/run_match.py {CASE_DIR}/research/arbiter-topics.json \
    --query-file {CASE_DIR}/research/arbiter-user-query.txt
  python3 integrations/arbiter/run_match.py topics.json --query "egypt fifa complaint" --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# High-frequency words that carry no case-study signal.
STOPWORDS = frozenset(
    """a about after against all an and any are as at be been before being but by can
    case cases claim claims could did do does fake false find for from had has have how
    i in into investigate investigation is it its me media news of on online or over
    people post posts real report reported rumor rumors say says social some story study
    tell that the their them then there these they this to told true truth under up us
    verify viral want was we were what when where which who why will with would you your""".split()
)

MIN_TOKEN_LENGTH = 2
PREFIX_MATCH_MIN_LENGTH = 4
PREFIX_MATCH_CREDIT = 0.7
FIELD_WEIGHTS = (("title", 3.0), ("slug", 2.0), ("description", 1.0))
# Generous by design: a third of the query's signal tokens matching is enough
# to OFFER the case study — the user always makes the final pick, and misses
# fall back to the full menu anyway.
DEFAULT_THRESHOLD = 0.3
DEFAULT_TOP = 4


def tokenize(text: Any) -> list[str]:
    """Lowercase alphanumeric tokens with stopwords and short tokens dropped."""
    raw = str(text if text is not None else "").lower()
    return [
        token
        for token in re.findall(r"[a-z0-9]+", raw)
        if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS
    ]


def token_hit(query_token: str, topic_tokens: set[str]) -> float:
    """1.0 for an exact token match, partial credit for a long prefix match."""
    if query_token in topic_tokens:
        return 1.0
    if len(query_token) >= PREFIX_MATCH_MIN_LENGTH:
        for topic_token in topic_tokens:
            if len(topic_token) >= PREFIX_MATCH_MIN_LENGTH and (
                topic_token.startswith(query_token) or query_token.startswith(topic_token)
            ):
                return PREFIX_MATCH_CREDIT
    return 0.0


def score_topic(query_tokens: list[str], topic: dict[str, Any]) -> tuple[float, list[str]]:
    """Weighted query-token coverage over the topic's text fields.

    Returns the score in [0, 1] and the query tokens that matched anywhere.
    """
    if not query_tokens:
        return 0.0, []
    field_tokens = {name: set(tokenize(topic.get(name))) for name, _ in FIELD_WEIGHTS}
    max_weight = max(weight for _, weight in FIELD_WEIGHTS)
    total = 0.0
    matched: list[str] = []
    for query_token in query_tokens:
        best = 0.0
        for name, weight in FIELD_WEIGHTS:
            hit = token_hit(query_token, field_tokens[name])
            if hit > 0:
                best = max(best, hit * (weight / max_weight))
        if best > 0:
            matched.append(query_token)
        total += best
    return total / len(query_tokens), matched


def load_topics(path: Path) -> list[dict[str, Any]]:
    """Load and minimally validate a saved /topics response file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read topics JSON at {path}: {exc}")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SystemExit(
            "error: file does not look like an Arbiter /topics response (missing items[])"
        )
    return [item for item in items if isinstance(item, dict)]


def summarize(topic: dict[str, Any], score: float, matched: list[str]) -> dict[str, Any]:
    """Projection of one topic for the match output (display fields only)."""
    window = topic.get("window") if isinstance(topic.get("window"), dict) else {}
    return {
        "id": str(topic.get("id", "")),
        "slug": str(topic.get("slug", "")),
        "title": str(topic.get("title", "")),
        "description": str(topic.get("description", "")),
        "platforms": [str(p) for p in topic.get("platforms", []) if p]
        if isinstance(topic.get("platforms"), list)
        else [],
        "post_count": topic.get("post_count", 0),
        "entity_count": topic.get("entity_count", 0),
        "starred": topic.get("starred", False),
        "window": {"from": str(window.get("from", "")), "to": str(window.get("to", ""))},
        "score": round(score, 3),
        "matched_terms": matched,
    }


def match_topics(
    query: str,
    topics: list[dict[str, Any]],
    threshold: float,
    top: int,
    include_zero_post: bool = False,
) -> dict[str, Any]:
    """Rank topics against the query and split them into matches vs the rest."""
    query_tokens = tokenize(query)
    hidden_zero_post = 0
    scored = []
    for topic in topics:
        if not include_zero_post and topic.get("post_count") in (None, 0):
            hidden_zero_post += 1
            continue
        score, matched = score_topic(query_tokens, topic)
        scored.append((score, matched, topic))
    scored.sort(key=lambda entry: (-entry[0], str(entry[2].get("title", ""))))

    matches = [
        summarize(topic, score, matched)
        for score, matched, topic in scored
        if score >= threshold
    ][:top]
    matched_ids = {entry["id"] for entry in matches}
    others = [
        summarize(topic, score, matched)
        for score, matched, topic in scored
        if str(topic.get("id", "")) not in matched_ids
    ]
    return {
        "query": query,
        "query_tokens": query_tokens,
        "threshold": threshold,
        "hidden_zero_post": hidden_zero_post,
        "matches": matches,
        "others": others,
    }


def render_text(result: dict[str, Any]) -> str:
    """Human-readable rendering for the terminal."""
    lines = [f"query: {result['query']}"]
    if result["hidden_zero_post"]:
        hidden = result["hidden_zero_post"]
        noun = "case study" if hidden == 1 else "case studies"
        lines.append(f"Hid {hidden} {noun} with 0 posts.")
    if result["matches"]:
        lines.append(f"\nCLOSEST CASE STUDIES ({len(result['matches'])})")
        for entry in result["matches"]:
            lines.append(
                f"  {entry['title']} — score {entry['score']}"
                f" (matched: {', '.join(entry['matched_terms']) or '—'})"
            )
            lines.append(f"    id: {entry['id']} · {entry['post_count']} posts")
    else:
        lines.append("\nNO CASE STUDY MATCHES THIS QUERY")
    if result["others"]:
        lines.append(f"\nOTHER AVAILABLE CASE STUDIES ({len(result['others'])})")
        for entry in result["others"]:
            lines.append(f"  {entry['title']} (id: {entry['id']})")
    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", help="path to a saved /topics JSON response")
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--query", help="the user's investigation query")
    query_group.add_argument(
        "--query-file",
        help="file containing the query (preferred: free text never touches shell quoting)",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument(
        "--include-zero-post",
        action="store_true",
        help="include topics whose post_count is zero, missing, or null",
    )
    parser.add_argument("--out", help="write output to this file instead of stdout")
    args = parser.parse_args()

    if args.query_file:
        try:
            query = Path(args.query_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"error: cannot read query file: {exc}")
    else:
        query = (args.query or "").strip()

    topics = load_topics(Path(args.input))
    result = match_topics(
        query,
        topics,
        args.threshold,
        max(1, args.top),
        include_zero_post=args.include_zero_post,
    )
    output = (
        json.dumps(result, indent=2) + "\n" if args.format == "json" else render_text(result) + "\n"
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
