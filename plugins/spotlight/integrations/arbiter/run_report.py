#!/usr/bin/env python3
"""Render an Arbiter report response (GET /topics/{id}/report) for humans.

The report is the full case-study surface in one payload: top actors with the
claims (posts) each is making, the theme/sub-theme hierarchy with per-theme
actors, community clustering, and actors posting across multiple related
sub-themes. Two output formats, both offline (reads a previously fetched JSON
file, no network — safe in sensitive mode):

  tree      (default) a sectioned CLI report — actors with claims, theme tree,
            communities, cross-theme actors — printed to stdout.
  markdown  an Obsidian-ready note (frontmatter, mermaid community diagram,
            per-actor and per-theme sections with evidence links) printed to
            stdout or written with --out; drop it into the knowledge vault
            next to the investigation notes during ingest.

Usage:
  python3 integrations/arbiter/run_report.py {CASE_DIR}/research/arbiter-report-<id>.json
  python3 integrations/arbiter/run_report.py report.json --format markdown --out vault/arbiter-report-<slug>.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_payload(path: Path) -> dict[str, Any]:
    """Load and minimally validate a report response file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read report JSON at {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("top_actors"), list):
        raise SystemExit(
            "error: file does not look like an Arbiter /topics/{id}/report response "
            "(missing a top_actors[] array)"
        )
    return data


def clip(text: Any, limit: int) -> str:
    """Single-line, length-capped rendering of untrusted text."""
    return " ".join(str(text or "").split())[:limit]


def as_list(value: Any) -> list[Any]:
    """Tolerate explicit nulls/scalars where the contract promises a list."""
    return value if isinstance(value, list) else []


def md_link_text(value: Any, limit: int) -> str:
    """Link-text-safe rendering: single line, capped, square brackets escaped."""
    return clip(value, limit).replace("[", "\\[").replace("]", "\\]")


def safe_url(value: Any) -> str:
    """Only pass through http(s) URLs; anything else renders empty."""
    url = clip(value, 2048)
    return url if url.startswith(("http://", "https://")) else ""


def render_actor_lines(actor: dict[str, Any], indent: str) -> list[str]:
    """CLI lines for one top actor: profile, claims, theme activity."""
    lines: list[str] = []
    engagement = actor.get("engagement", {}) if isinstance(actor.get("engagement"), dict) else {}
    header = (
        f"{actor.get('actor', '?')} "
        f"[{engagement.get('total_posts', 0)} posts · {engagement.get('total_engagement', 0)} engagement]"
    )
    if actor.get("group"):
        header += f" · group: {actor['group']}"
    if actor.get("dominant_theme"):
        header += f" · theme: {actor['dominant_theme']}"
    lines.append(f"{indent}{header}")
    if actor.get("narrative"):
        lines.append(f"{indent}  narrative: {clip(actor['narrative'], 160)}")
    for claim in as_list(actor.get("claims")):
        if not isinstance(claim, dict):
            continue
        lines.append(
            f"{indent}  • {clip(claim.get('text'), 100)} "
            f"({claim.get('engagement', 0)} eng) {claim.get('url', '')}"
        )
    active = as_list(actor.get("active_themes"))
    if active:
        lines.append(f"{indent}  active in: {', '.join(str(t) for t in active)}")
    return lines


def render_tree(payload: dict[str, Any]) -> str:
    """Render the report as a sectioned CLI view."""
    lines: list[str] = []
    title = payload.get("title") or payload.get("root_theme") or "Arbiter case-study report"
    sections = payload.get("sections", {}) if isinstance(payload.get("sections"), dict) else {}
    lines.append(str(title))
    lines.append(
        f"  topic: {payload.get('topic_id', '?')} · platform: {payload.get('platform', '?')}"
        f" · modules: actors={'yes' if sections.get('actors') else 'no'}"
        f" themes={'yes' if sections.get('themes') else 'no'}"
        f" engagement={'yes' if sections.get('engagement') else 'no'}"
    )

    timeline = payload.get("engagement_timeline")
    if isinstance(timeline, dict) and timeline:
        lines.extend(
            [
                "",
                "INTERACTIONS OVER TIME",
                f"  total: {timeline.get('total_interactions', 0)}"
                f" · average per interval: {timeline.get('average_interactions', 0)}",
            ]
        )
        for point in as_list(timeline.get("points")):
            if isinstance(point, dict):
                lines.append(
                    f"  {point.get('date', '?')}: {point.get('interactions', 0)} interactions"
                )
        story = timeline.get("story")
        if isinstance(story, str) and story.strip():
            lines.append("  Story at a Glance:")
            lines.extend(f"    {line}" for line in story.strip().splitlines())

    actors = [a for a in as_list(payload.get("top_actors")) if isinstance(a, dict)]
    lines.append("")
    lines.append(f"TOP ACTORS ({len(actors)})")
    for actor in actors:
        lines.extend(render_actor_lines(actor, "  "))

    def walk(node: dict[str, Any], prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        stats = f"[{node.get('post_count', 0)} posts · {node.get('engagement_total', 0)} engagement]"
        line = f"{prefix}{connector}{node.get('theme', 'Unknown Theme')} {stats}"
        top_actors = as_list(node.get("top_actors"))
        if top_actors:
            line += f" — {', '.join(str(a) for a in top_actors)}"
        lines.append(line)
        child_prefix = prefix + ("    " if is_last else "│   ")
        children = [c for c in as_list(node.get("children")) if isinstance(c, dict)]
        for index, child in enumerate(children):
            walk(child, child_prefix, index == len(children) - 1)

    themes = [t for t in as_list(payload.get("themes")) if isinstance(t, dict)]
    lines.append("")
    lines.append(f"THEMES ({len(themes)} top-level) — actors shown per theme")
    for index, theme in enumerate(themes):
        walk(theme, "", index == len(themes) - 1)

    communities = [c for c in as_list(payload.get("communities")) if isinstance(c, dict)]
    lines.append("")
    lines.append(f"COMMUNITIES ({len(communities)})")
    for community in communities:
        lines.append(
            f"  {community.get('name', '?')} ({community.get('basis', '?')}) — "
            f"{community.get('actor_count', 0)} actors · {community.get('total_posts', 0)} posts · "
            f"{community.get('total_engagement', 0)} engagement"
        )
        members = as_list(community.get("actors"))
        if members:
            lines.append(f"    members: {', '.join(str(m) for m in members)}")
        themes_list = as_list(community.get("themes"))
        if themes_list:
            lines.append(f"    themes: {', '.join(str(t) for t in themes_list)}")

    cross = [x for x in as_list(payload.get("cross_theme_actors")) if isinstance(x, dict)]
    lines.append("")
    lines.append(f"CROSS-THEME ACTORS ({len(cross)}) — posting repeatedly across related sub-themes")
    for entry in cross:
        lines.append(
            f"  {entry.get('actor', '?')} — {entry.get('theme_count', 0)} themes · "
            f"{entry.get('post_count', 0)} posts: {', '.join(str(t) for t in as_list(entry.get('themes')))}"
        )
    return "\n".join(lines)


def mermaid_label(text: str) -> str:
    """Quote-safe, single-line mermaid node label (newlines break the diagram)."""
    return '"' + clip(text, 200).replace('"', "'") + '"'


def render_markdown(payload: dict[str, Any], source_name: str) -> str:
    """Render an Obsidian-ready report note."""
    title = clip(payload.get("title") or payload.get("root_theme") or "Arbiter report", 160) or "Arbiter report"

    front_matter = [
        "---",
        f"title: Arbiter report — {title}",
        "type: arbiter-report",
        f"topic_id: {payload.get('topic_id', '')}",
        f"platform: {payload.get('platform', '')}",
        f"generated_at: {payload.get('generated_at', '')}",
        f"source_file: {source_name}",
        "tags: [arbiter, report, actors, communities]",
        "---",
        "",
        f"# Arbiter report — {title}",
        "",
        f"Curated case study `{payload.get('topic_id', '')}` · platform `{payload.get('platform', '')}`.",
        "",
        "> Claim post ids resolve to full archived records via Arbiter `GET /posts/{id}` "
        "(cite as `access_method: archive_copy`).",
        "",
    ]

    body: list[str] = []
    timeline = payload.get("engagement_timeline")
    if isinstance(timeline, dict) and timeline:
        body.extend(
            [
                "## Interactions over time",
                "",
                f"- **Total interactions:** {timeline.get('total_interactions', 0)}",
                f"- **Average per interval:** {timeline.get('average_interactions', 0)}",
                "",
            ]
        )
        points = [point for point in as_list(timeline.get("points")) if isinstance(point, dict)]
        if points:
            body.extend(["| Date | Interactions |", "| --- | ---: |"])
            for point in points:
                date = str(point.get("date", "?")).replace("|", "\\|")
                interactions = str(point.get("interactions", 0)).replace("|", "\\|")
                body.append(f"| {date} | {interactions} |")
            body.append("")
        story = timeline.get("story")
        if isinstance(story, str) and story.strip():
            body.extend(["### Story at a Glance", "", story.strip(), ""])

    body.extend(["## Top actors", ""])
    for actor in as_list(payload.get("top_actors")):
        if not isinstance(actor, dict):
            continue
        engagement = actor.get("engagement", {}) if isinstance(actor.get("engagement"), dict) else {}
        body.append(f"### {clip(actor.get('actor', '?'), 120) or '?'}")
        body.append("")
        profile = (
            f"- **Posts:** {engagement.get('total_posts', 0)} · "
            f"**Engagement:** {engagement.get('total_engagement', 0)}"
        )
        if actor.get("group"):
            profile += f" · **Group:** {actor['group']}"
        if actor.get("dominant_theme"):
            profile += f" · **Dominant theme:** {actor['dominant_theme']}"
        body.append(profile)
        if actor.get("narrative"):
            body.append(f"- **Narrative:** {clip(actor['narrative'], 400)}")
        active = as_list(actor.get("active_themes"))
        if active:
            body.append(f"- **Active themes:** {', '.join(str(t) for t in active)}")
        claims = [c for c in as_list(actor.get("claims")) if isinstance(c, dict)]
        if claims:
            body.append("- **Claims (top posts):**")
            for claim in claims:
                text = md_link_text(claim.get("text"), 120)
                body.append(
                    f"  - [{text}]({safe_url(claim.get('url'))}) — {claim.get('engagement', 0)} engagement"
                    f" · post `{claim.get('post_id', '')}`"
                )
        body.append("")

    communities = [c for c in as_list(payload.get("communities")) if isinstance(c, dict)]
    body.extend(["## Communities", ""])
    if communities:
        mermaid = ["```mermaid", "flowchart TD"]
        for c_index, community in enumerate(communities):
            community_id = f"c{c_index}"
            label = f"{community.get('name', '?')} ({community.get('actor_count', 0)} actors)"
            mermaid.append(f"  {community_id}[{mermaid_label(label)}]")
            for a_index, member in enumerate(as_list(community.get("actors"))):
                member_id = f"c{c_index}a{a_index}"
                mermaid.append(f"  {member_id}([{mermaid_label(str(member))}])")
                mermaid.append(f"  {community_id} --> {member_id}")
        mermaid.append("```")
        body.extend(mermaid)
        body.append("")
        for community in communities:
            body.append(
                f"- **{community.get('name', '?')}** ({community.get('basis', '?')}): "
                f"{community.get('actor_count', 0)} actors, {community.get('total_posts', 0)} posts, "
                f"{community.get('total_engagement', 0)} engagement"
                + (
                    f" — themes: {', '.join(str(t) for t in as_list(community.get('themes')))}"
                    if as_list(community.get("themes"))
                    else ""
                )
            )
        body.append("")
    else:
        body.extend(["_No community structure available for this platform._", ""])

    def walk(node: dict[str, Any], depth: int) -> None:
        heading = "#" * min(6, depth + 2)
        body.append(f"{heading} {clip(node.get('theme', 'Unknown Theme'), 160) or 'Unknown Theme'}")
        body.append("")
        body.append(
            f"- **Posts:** {node.get('post_count', 0)} · **Engagement:** {node.get('engagement_total', 0)}"
        )
        top_actors = as_list(node.get("top_actors"))
        if top_actors:
            body.append(f"- **Top actors:** {', '.join(str(a) for a in top_actors)}")
        body.append("")
        for child in as_list(node.get("children")):
            if isinstance(child, dict):
                walk(child, depth + 1)

    body.extend(["## Themes and sub-themes", ""])
    themes = [t for t in as_list(payload.get("themes")) if isinstance(t, dict)]
    if themes:
        for theme in themes:
            walk(theme, 1)
    else:
        body.extend(["_No theme analysis available for this platform._", ""])

    cross = [x for x in as_list(payload.get("cross_theme_actors")) if isinstance(x, dict)]
    body.extend(["## Cross-theme actors", ""])
    if cross:
        body.append("Actors posting repeatedly across related sub-themes:")
        body.append("")
        for entry in cross:
            body.append(
                f"- **{entry.get('actor', '?')}** — {entry.get('theme_count', 0)} themes, "
                f"{entry.get('post_count', 0)} posts: {', '.join(str(t) for t in as_list(entry.get('themes')))}"
            )
        body.append("")
    else:
        body.extend(["_No actor spans more than one theme in this corpus._", ""])

    return "\n".join(front_matter + body) + "\n"


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="path to a saved /topics/{id}/report JSON response")
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
