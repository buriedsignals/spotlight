#!/usr/bin/env python3
"""OSINT feed monitor — pluggable framework for agent consumption.

Usage:
    python3 monitor.py list [--project assam]
    python3 monitor.py query gdelt [--project assam] [--query "custom terms"] [--since 24h] [--json]
    python3 monitor.py check-all --project assam [--since 35m] [--json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from _shared import (
    load_topics,
    build_query_from_topics,
    score_signal,
    deduplicate,
    parse_since,
    source_relevance,
    format_output,
)

SOURCES_DIR = Path(__file__).parent / "sources"


def discover_sources() -> list[dict]:
    """Find all source plugins with valid manifests."""
    sources = []
    if not SOURCES_DIR.is_dir():
        return sources
    for d in sorted(SOURCES_DIR.iterdir()):
        manifest_path = d / "manifest.json"
        fetch_path = d / "fetch.py"
        if manifest_path.exists() and fetch_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            manifest["_dir"] = str(d)
            sources.append(manifest)
    return sources


def load_fetch_module(source_dir: str):
    """Dynamically load a source's fetch.py module."""
    fetch_path = Path(source_dir) / "fetch.py"
    spec = importlib.util.spec_from_file_location("fetch", fetch_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_list(args):
    """List available sources with optional relevance hints."""
    sources = discover_sources()
    topics = load_topics(args.project) if args.project else []

    result = []
    for s in sources:
        entry = {
            "id": s["id"],
            "name": s["name"],
            "category": s.get("category", ""),
            "regions": s.get("regions", []),
        }
        if topics:
            hints = source_relevance(s, topics)
            entry.update(hints)
        result.append(entry)

    print(json.dumps(result, indent=2))


def cmd_query(args):
    """Query a specific source."""
    sources = {s["id"]: s for s in discover_sources()}
    if args.source not in sources:
        print(json.dumps({"error": f"Unknown source: {args.source}", "available": list(sources.keys())}), file=sys.stderr)
        sys.exit(1)

    source = sources[args.source]
    topics = load_topics(args.project) if args.project else load_topics()
    since = args.since or source.get("default_since", "24h")

    # Build query
    query = args.query
    if not query and topics:
        query = build_query_from_topics(topics)

    # Fetch
    mod = load_fetch_module(source["_dir"])
    try:
        raw_signals = mod.fetch(query=query, topics=topics, since=since)
    except Exception as e:
        print(json.dumps({"error": f"Fetch failed for {args.source}: {e}", "signals": []}))
        sys.exit(1)

    # Score against topics
    for s in raw_signals:
        if topics and s.get("relevance_score", 0) == 0:
            score, matches = score_signal(s, topics)
            s["relevance_score"] = score
            s["matched_keywords"] = matches

    # Sort by relevance
    raw_signals.sort(key=lambda s: s.get("relevance_score", 0), reverse=True)

    result = {
        "source": args.source,
        "query": query,
        "since": since,
        "signals": raw_signals,
    }

    if args.json:
        print(format_output(result, as_json=True))
    else:
        print(format_output(result, as_json=False))


def cmd_check_all(args):
    """Run all relevant sources, merge and deduplicate."""
    sources = discover_sources()
    topics = load_topics(args.project) if args.project else load_topics()
    since = args.since or "24h"

    query = None
    if topics:
        query = build_query_from_topics(topics)

    all_signals = []
    errors = []

    for source in sources:
        # Skip low-relevance sources if project specified
        if args.project and topics:
            hints = source_relevance(source, topics)
            if hints.get("relevance") == "low":
                continue

        mod = load_fetch_module(source["_dir"])
        source_since = since or source.get("default_since", "24h")

        try:
            raw = mod.fetch(query=query, topics=topics, since=source_since)
            for s in raw:
                s["_source_id"] = source["id"]
                if topics and s.get("relevance_score", 0) == 0:
                    score, matches = score_signal(s, topics)
                    s["relevance_score"] = score
                    s["matched_keywords"] = matches
            all_signals.extend(raw)
        except Exception as e:
            errors.append({"source": source["id"], "error": str(e)})

    # Deduplicate and sort
    all_signals = deduplicate(all_signals)
    all_signals.sort(key=lambda s: s.get("relevance_score", 0), reverse=True)

    # Clean up internal keys
    for s in all_signals:
        s.pop("_source_id", None)

    result = {
        "project": args.project,
        "query": query,
        "since": since,
        "total_signals": len(all_signals),
        "signals": all_signals,
    }
    if errors:
        result["errors"] = errors

    if args.json:
        print(format_output(result, as_json=True))
    else:
        print(format_output(result, as_json=False))


def main():
    parser = argparse.ArgumentParser(description="OSINT feed monitor for Spotlight investigations")
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="List available sources")
    p_list.add_argument("--project", type=str, help="Filter/hint relevance for a project")

    # query
    p_query = sub.add_parser("query", help="Query a specific source")
    p_query.add_argument("source", type=str, help="Source ID (e.g., gdelt, rss_investigative)")
    p_query.add_argument("--project", type=str, help="Project to derive query from")
    p_query.add_argument("--query", type=str, help="Custom search query (overrides topic-derived)")
    p_query.add_argument("--since", type=str, help="Time window (e.g., 24h, 35m, 7d)")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")

    # check-all
    p_all = sub.add_parser("check-all", help="Run all relevant sources")
    p_all.add_argument("--project", type=str, help="Project to filter sources by")
    p_all.add_argument("--since", type=str, help="Time window (e.g., 24h, 35m, 7d)")
    p_all.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "check-all":
        cmd_check_all(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
