#!/usr/bin/env python3
"""Render an Arbiter themes response (GET /topics/{id}/themes) for humans.

Two output formats, both offline (reads a previously fetched JSON file, no
network — safe in sensitive mode):

  tree      (default) an indented theme tree with post counts, engagement,
            sentiment summaries, and top-post links, printed to stdout for
            reading directly in the CLI.
  markdown  an Obsidian-ready note (frontmatter, mermaid hierarchy diagram,
            per-theme sections with evidence links) printed to stdout or
            written with --out; drop it into the knowledge vault next to the
            investigation notes during ingest.

Usage:
  python3 integrations/arbiter/run_themes.py {CASE_DIR}/research/arbiter-themes-<id>.json
  python3 integrations/arbiter/run_themes.py themes.json --format markdown --out vault/arbiter-themes-<slug>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def load_payload(path: Path) -> dict[str, Any]:
    """Load and minimally validate a themes response file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read themes JSON at {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise SystemExit(
            "error: file does not look like an Arbiter /topics/{id}/themes response "
            "(missing a themes[] array)"
        )
    return data


def as_list(value: Any) -> list[Any]:
    """Tolerate explicit nulls/scalars where the contract promises a list."""
    return value if isinstance(value, list) else []


def single_line(value: Any) -> str:
    """Collapse untrusted text to one whitespace-normalized line."""
    return " ".join(str(value if value is not None else "").split())


def md_link_text(value: Any, limit: int) -> str:
    """Link-text-safe rendering: single line, capped, square brackets escaped."""
    return single_line(value)[:limit].replace("[", "\\[").replace("]", "\\]")


def safe_url(value: Any) -> str:
    """Only pass through http(s) URLs; anything else renders empty."""
    url = single_line(value)
    return url if url.startswith(("http://", "https://")) else ""


def sentiment_summary(node: dict[str, Any]) -> str:
    """One-line sentiment summary like 'negative 12 · neutral 4 · positive 2'."""
    distribution = node.get("sentiment_distribution")
    if not isinstance(distribution, dict) or not distribution:
        return ""
    # Counts are untrusted: null / non-numeric values are dropped, not crashed on.
    entries = [
        (single_line(label), value)
        for label, value in distribution.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not entries:
        return ""
    ordered = sorted(entries, key=lambda item: -item[1])
    return " · ".join(f"{label} {count}" for label, count in ordered)


def render_tree(payload: dict[str, Any]) -> str:
    """Render the theme hierarchy as an indented CLI tree."""
    lines: list[str] = []
    root = payload.get("root_theme") or "Themes"
    lines.append(f"{root}")
    lines.append(
        f"  themes: {payload.get('total_themes', '?')} · levels: {payload.get('theme_levels', '?')}"
        f" · posts analyzed: {payload.get('total_posts', '?')} · platform: {payload.get('platform', '?')}"
    )
    lines.append("")

    def walk(node: dict[str, Any], prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        stats = f"[{node.get('post_count', 0)} posts · {node.get('engagement_total', 0)} engagement]"
        lines.append(f"{prefix}{connector}{node.get('theme', 'Unknown Theme')} {stats}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        sentiment = sentiment_summary(node)
        if sentiment:
            lines.append(f"{child_prefix}sentiment: {sentiment}")
        for post in as_list(node.get("top_posts"))[:3]:
            if not isinstance(post, dict):
                continue
            text = single_line(post.get("text", ""))[:80]
            lines.append(
                f"{child_prefix}• {text} — {post.get('author', '?')} "
                f"({post.get('engagement', 0)} eng) {post.get('url', '')}"
            )
        children = [c for c in as_list(node.get("children")) if isinstance(c, dict)]
        for index, child in enumerate(children):
            walk(child, child_prefix, index == len(children) - 1)

    themes = [t for t in as_list(payload.get("themes")) if isinstance(t, dict)]
    for index, theme in enumerate(themes):
        walk(theme, "", index == len(themes) - 1)
    return "\n".join(lines)


def mermaid_id(index: int) -> str:
    """Stable mermaid node id."""
    return f"n{index}"


def mermaid_label(text: str) -> str:
    """Quote-safe, single-line mermaid node label (newlines break the diagram)."""
    return '"' + single_line(text).replace('"', "'") + '"'


def render_markdown(payload: dict[str, Any], source_name: str) -> str:
    """Render an Obsidian-ready themes note."""
    root = single_line(payload.get("root_theme")) or "Arbiter themes"
    slug = re.sub(r"[^a-z0-9]+", "-", str(root).lower()).strip("-") or "themes"

    mermaid_lines = ["```mermaid", "flowchart TD"]
    counter = 0
    root_id = mermaid_id(counter)
    mermaid_lines.append(f"  {root_id}[{mermaid_label(str(root))}]")

    sections: list[str] = []

    def walk(node: dict[str, Any], parent_id: str, depth: int) -> None:
        nonlocal counter
        counter += 1
        node_id = mermaid_id(counter)
        theme = single_line(node.get("theme", "Unknown Theme")) or "Unknown Theme"
        label = f"{theme} ({node.get('post_count', 0)})"
        mermaid_lines.append(f"  {node_id}[{mermaid_label(label)}]")
        mermaid_lines.append(f"  {parent_id} --> {node_id}")

        heading = "#" * min(6, depth + 2)
        sections.append(f"{heading} {theme}")
        sections.append("")
        sections.append(
            f"- **Posts:** {node.get('post_count', 0)} · **Engagement:** {node.get('engagement_total', 0)}"
        )
        sentiment = sentiment_summary(node)
        if sentiment:
            sections.append(f"- **Sentiment:** {sentiment}")
        sample_ids = as_list(node.get("sample_post_ids"))
        if sample_ids:
            sections.append(
                f"- **Sample post ids:** {', '.join(f'`{single_line(pid)}`' for pid in sample_ids)}"
            )
        top_posts = [p for p in as_list(node.get("top_posts")) if isinstance(p, dict)]
        if top_posts:
            sections.append("- **Top posts:**")
            for post in top_posts:
                text = md_link_text(post.get("text", ""), 120)
                sections.append(
                    f"  - [{text}]({safe_url(post.get('url'))}) — {single_line(post.get('author', '?'))}"
                    f" ({post.get('engagement', 0)} engagement)"
                )
        sections.append("")
        for child in as_list(node.get("children")):
            if isinstance(child, dict):
                walk(child, node_id, depth + 1)

    for theme in as_list(payload.get("themes")):
        if isinstance(theme, dict):
            walk(theme, root_id, 1)
    mermaid_lines.append("```")

    front_matter = [
        "---",
        f"title: Arbiter themes — {root}",
        "type: arbiter-themes",
        f"topic_id: {payload.get('topic_id', '')}",
        f"platform: {payload.get('platform', '')}",
        f"generated_at: {payload.get('generated_at', '')}",
        f"source_file: {source_name}",
        "tags: [arbiter, themes]",
        "---",
        "",
        f"# Arbiter themes — {root}",
        "",
        f"Curated case study `{payload.get('topic_id', '')}` · platform `{payload.get('platform', '')}` · "
        f"{payload.get('total_themes', '?')} themes across {payload.get('theme_levels', '?')} levels · "
        f"{payload.get('total_posts', '?')} posts analyzed.",
        "",
        "> Post ids resolve to full archived records via Arbiter `GET /posts/{id}` "
        "(cite as `access_method: archive_copy`).",
        "",
        "## Hierarchy",
        "",
    ]

    return "\n".join(front_matter + mermaid_lines + ["", "## Themes", ""] + sections) + "\n"


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="path to a saved /topics/{id}/themes JSON response")
    parser.add_argument("--format", choices=["tree", "markdown"], default="tree")
    parser.add_argument("--out", help="write output to this file instead of stdout (markdown mode)")
    args = parser.parse_args()

    payload = load_payload(Path(args.input))
    if args.format == "tree":
        output = render_tree(payload)
    else:
        output = render_markdown(payload, Path(args.input).name)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(output + ("\n" if not output.endswith("\n") else ""))


if __name__ == "__main__":
    main()
