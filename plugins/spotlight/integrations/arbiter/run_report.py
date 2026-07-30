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
  python3 integrations/arbiter/run_report.py report.json --format markdown \
    --out {CASE_DIR}/research/arbiter-report-<slug>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Terminal escapes and other C0/C1 controls: a payload author who can place
# these in a name can repaint the CLI tree or hide text from a reader.
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Characters that let a URL escape its markdown link target or its HTML
# attribute. Parentheses close the `](...)` form, the rest break quoting.
URL_UNSAFE_RE = re.compile(r"""[\x00-\x20<>"'`\\()]""")

# Angle brackets forge HTML, backticks open code and mermaid fences.
STORY_UNSAFE_RE = re.compile(r"[`<>]")


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
    """Single-line, control-free, length-capped rendering of untrusted text.

    Args:
        text: Any payload value; ``None`` renders empty, everything else is
            stringified.
        limit: Maximum number of characters to keep.

    Returns:
        One line with C0/C1 control characters removed and whitespace collapsed.
    """
    stripped = CONTROL_CHARS_RE.sub("", str(text if text is not None else ""))
    return " ".join(stripped.split())[:limit]


def as_list(value: Any) -> list[Any]:
    """Tolerate explicit nulls/scalars where the contract promises a list."""
    return value if isinstance(value, list) else []


def md_link_text(value: Any, limit: int) -> str:
    """Link-text-safe rendering: single line, capped, link syntax escaped.

    Brackets and parentheses are both escaped: escaping only the brackets would
    leave a payload-supplied ``](…)`` sequence inside the label, which a lenient
    markdown renderer can read as a nested link with its own target.

    Args:
        value: Any payload value destined for a ``[…](…)`` label.
        limit: Maximum number of characters to keep.

    Returns:
        Text that cannot introduce or close a link.
    """
    text = clip(value, limit)
    for char in "[]()":
        text = text.replace(char, f"\\{char}")
    return text


def md_text(value: Any, limit: int) -> str:
    """Body-text-safe markdown rendering: single line, capped, backticks escaped.

    A payload label placed in a heading or a list item can otherwise open a
    fenced code block with three backticks, swallowing the rest of the note and
    unbalancing the mermaid fence above it.

    Args:
        value: Any payload value destined for markdown body text.
        limit: Maximum number of characters to keep.

    Returns:
        Text that cannot open or close a code fence.
    """
    return clip(value, limit).replace("`", "\\`")


def md_code(value: Any, limit: int) -> str:
    """Inline-code-safe rendering: single line, capped, backticks removed.

    Args:
        value: Any payload value destined for a `` `…` `` span.
        limit: Maximum number of characters to keep.

    Returns:
        Text that cannot close its own code span.
    """
    return clip(value, limit).replace("`", "")


def md_cell(value: Any, limit: int) -> str:
    """Table-cell-safe rendering: single line, capped, pipes and backticks escaped."""
    return md_text(value, limit).replace("|", "\\|")


def yaml_value(value: Any) -> str:
    """JSON-quoted scalar for a YAML frontmatter value.

    YAML 1.2 is a JSON superset, so a ``json.dumps`` string is always a valid
    scalar and can carry no structure: a payload value cannot add a frontmatter
    key, close the ``---`` block, or start a nested mapping.

    Args:
        value: Any payload value to render as a frontmatter scalar.

    Returns:
        A double-quoted, fully escaped one-line scalar.
    """
    return json.dumps(clip(value, 512))


def safe_url(value: Any) -> str:
    """Only pass through plain http(s) URLs; every other value renders empty.

    Args:
        value: A payload URL, which post authors control.

    Returns:
        The URL when it is an ``http(s)`` URL free of characters that could
        break out of a markdown link target or an HTML attribute, else ``""``.
    """
    url = clip(value, 2048)
    if not url.startswith(("http://", "https://")):
        return ""
    return "" if URL_UNSAFE_RE.search(url) else url


def story_quote_lines(story: Any, limit: int) -> list[str]:
    """Render the timeline narrative as inert markdown blockquote lines.

    ``story`` is model-authored prose over adversary-authored posts, so it is
    never passed through as markdown. Every line is stripped of backticks
    (which would open a code or mermaid fence) and angle brackets (which would
    forge HTML), collapsed to a single line so it cannot forge a heading or a
    frontmatter key, and emitted behind a ``>`` marker.

    Args:
        story: The payload's ``engagement_timeline.story`` value.
        limit: Maximum characters kept per rendered line.

    Returns:
        One ``> …`` line per non-empty source line; empty when nothing survives.
    """
    lines: list[str] = []
    for raw in str(story if story is not None else "").splitlines():
        line = clip(STORY_UNSAFE_RE.sub("", raw), limit)
        if line:
            lines.append(f"> {line}")
    return lines


def resolve_out_path(value: str) -> Path:
    """Validate a ``--out`` destination before anything is written to it.

    Args:
        value: The raw ``--out`` path from the command line.

    Returns:
        The path to write.

    Raises:
        SystemExit: Exit code 2 when the destination is an existing symlink;
            ``write_text`` follows symlinks, so writing would land on the
            link's target — possibly inside the vault or outside the case.
    """
    out_path = Path(value)
    if out_path.is_symlink():
        print(f"error: refusing to write through a symlink: {out_path}", file=sys.stderr)
        raise SystemExit(2)
    return out_path


def render_actor_lines(actor: dict[str, Any], indent: str) -> list[str]:
    """CLI lines for one top actor: profile, claims, theme activity."""
    lines: list[str] = []
    engagement = actor.get("engagement", {}) if isinstance(actor.get("engagement"), dict) else {}
    header = (
        f"{clip(actor.get('actor', '?'), 120)} "
        f"[{clip(engagement.get('total_posts', 0), 32)} posts · "
        f"{clip(engagement.get('total_engagement', 0), 32)} engagement]"
    )
    if actor.get("group"):
        header += f" · group: {clip(actor['group'], 80)}"
    if actor.get("dominant_theme"):
        header += f" · theme: {clip(actor['dominant_theme'], 120)}"
    lines.append(f"{indent}{header}")
    if actor.get("narrative"):
        lines.append(f"{indent}  narrative: {clip(actor['narrative'], 160)}")
    for claim in as_list(actor.get("claims")):
        if not isinstance(claim, dict):
            continue
        lines.append(
            f"{indent}  • {clip(claim.get('text'), 100)} "
            f"({clip(claim.get('engagement', 0), 32)} eng) {safe_url(claim.get('url'))}"
        )
    active = as_list(actor.get("active_themes"))
    if active:
        lines.append(f"{indent}  active in: {', '.join(clip(t, 120) for t in active)}")
    return lines


def render_tree(payload: dict[str, Any]) -> str:
    """Render the report as a sectioned CLI view."""
    lines: list[str] = []
    title = payload.get("title") or payload.get("root_theme") or "Arbiter case-study report"
    sections = payload.get("sections", {}) if isinstance(payload.get("sections"), dict) else {}
    lines.append(clip(title, 200))
    lines.append(
        f"  topic: {clip(payload.get('topic_id', '?'), 128)} ·"
        f" platform: {clip(payload.get('platform', '?'), 32)}"
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
                f"  total: {clip(timeline.get('total_interactions', 0), 32)}"
                f" · average per interval: {clip(timeline.get('average_interactions', 0), 32)}",
            ]
        )
        for point in as_list(timeline.get("points")):
            if isinstance(point, dict):
                lines.append(
                    f"  {clip(point.get('date', '?'), 32)}: "
                    f"{clip(point.get('interactions', 0), 32)} interactions"
                )
        story = timeline.get("story")
        if isinstance(story, str) and story.strip():
            lines.append("  Story at a Glance:")
            lines.extend(
                f"    {clip(line, 400)}"
                for line in story.strip().splitlines()
                if clip(line, 400)
            )

    actors = [a for a in as_list(payload.get("top_actors")) if isinstance(a, dict)]
    lines.append("")
    lines.append(f"TOP ACTORS ({len(actors)})")
    for actor in actors:
        lines.extend(render_actor_lines(actor, "  "))

    def walk(node: dict[str, Any], prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        stats = (
            f"[{clip(node.get('post_count', 0), 32)} posts · "
            f"{clip(node.get('engagement_total', 0), 32)} engagement]"
        )
        line = f"{prefix}{connector}{clip(node.get('theme', 'Unknown Theme'), 160)} {stats}"
        top_actors = as_list(node.get("top_actors"))
        if top_actors:
            line += f" — {', '.join(clip(a, 120) for a in top_actors)}"
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
            f"  {clip(community.get('name', '?'), 120)} "
            f"({clip(community.get('basis', '?'), 40)}) — "
            f"{clip(community.get('actor_count', 0), 32)} actors · "
            f"{clip(community.get('total_posts', 0), 32)} posts · "
            f"{clip(community.get('total_engagement', 0), 32)} engagement"
        )
        members = as_list(community.get("actors"))
        if members:
            lines.append(f"    members: {', '.join(clip(m, 120) for m in members)}")
        themes_list = as_list(community.get("themes"))
        if themes_list:
            lines.append(f"    themes: {', '.join(clip(t, 120) for t in themes_list)}")

    cross = [x for x in as_list(payload.get("cross_theme_actors")) if isinstance(x, dict)]
    lines.append("")
    lines.append(f"CROSS-THEME ACTORS ({len(cross)}) — posting repeatedly across related sub-themes")
    for entry in cross:
        lines.append(
            f"  {clip(entry.get('actor', '?'), 120)} — "
            f"{clip(entry.get('theme_count', 0), 32)} themes · "
            f"{clip(entry.get('post_count', 0), 32)} posts: "
            f"{', '.join(clip(t, 120) for t in as_list(entry.get('themes')))}"
        )
    return "\n".join(lines)


def mermaid_label(text: Any) -> str:
    """Quoted mermaid node label that cannot break out of its own quotes.

    A newline ends a mermaid statement and a bare double quote closes the label,
    either of which would let payload text append its own nodes or a ``click``
    directive. The text is therefore collapsed to one line, double quotes are
    downgraded to apostrophes, and backticks and backslashes are dropped.

    Args:
        text: Any payload value to use as a node label.

    Returns:
        The label wrapped in double quotes, ready to place inside a node shape.
    """
    label = clip(text, 200).replace('"', "'").replace("`", "").replace("\\", "")
    return f'"{label}"'


def render_markdown(payload: dict[str, Any], source_name: str) -> str:
    """Render an Obsidian-ready report note."""
    title = clip(payload.get("title") or payload.get("root_theme") or "Arbiter report", 160) or "Arbiter report"

    front_matter = [
        "---",
        f"title: {yaml_value(f'Arbiter report — {title}')}",
        "type: arbiter-report",
        f"topic_id: {yaml_value(payload.get('topic_id', ''))}",
        f"platform: {yaml_value(payload.get('platform', ''))}",
        f"generated_at: {yaml_value(payload.get('generated_at', ''))}",
        f"source_file: {yaml_value(source_name)}",
        "tags: [arbiter, report, actors, communities]",
        "---",
        "",
        f"# Arbiter report — {md_text(title, 160)}",
        "",
        f"Curated case study `{md_code(payload.get('topic_id', ''), 128)}` · "
        f"platform `{md_code(payload.get('platform', ''), 32)}`.",
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
                f"- **Total interactions:** {md_text(timeline.get('total_interactions', 0), 32)}",
                f"- **Average per interval:** {md_text(timeline.get('average_interactions', 0), 32)}",
                "",
            ]
        )
        points = [point for point in as_list(timeline.get("points")) if isinstance(point, dict)]
        if points:
            body.extend(["| Date | Interactions |", "| --- | ---: |"])
            for point in points:
                date = md_cell(point.get("date", "?"), 32)
                interactions = md_cell(point.get("interactions", 0), 32)
                body.append(f"| {date} | {interactions} |")
            body.append("")
        quoted_story = story_quote_lines(timeline.get("story"), 400)
        if quoted_story:
            body.extend(["### Story at a Glance", ""] + quoted_story + [""])

    body.extend(["## Top actors", ""])
    for actor in as_list(payload.get("top_actors")):
        if not isinstance(actor, dict):
            continue
        engagement = actor.get("engagement", {}) if isinstance(actor.get("engagement"), dict) else {}
        body.append(f"### {md_text(actor.get('actor', '?'), 120) or '?'}")
        body.append("")
        profile = (
            f"- **Posts:** {md_text(engagement.get('total_posts', 0), 32)} · "
            f"**Engagement:** {md_text(engagement.get('total_engagement', 0), 32)}"
        )
        if actor.get("group"):
            profile += f" · **Group:** {md_text(actor['group'], 80)}"
        if actor.get("dominant_theme"):
            profile += f" · **Dominant theme:** {md_text(actor['dominant_theme'], 120)}"
        body.append(profile)
        if actor.get("narrative"):
            body.append(f"- **Narrative:** {md_text(actor['narrative'], 400)}")
        active = as_list(actor.get("active_themes"))
        if active:
            body.append(f"- **Active themes:** {', '.join(md_text(t, 120) for t in active)}")
        claims = [c for c in as_list(actor.get("claims")) if isinstance(c, dict)]
        if claims:
            body.append("- **Claims (top posts):**")
            for claim in claims:
                text = md_link_text(claim.get("text"), 120)
                body.append(
                    f"  - [{text}]({safe_url(claim.get('url'))}) — "
                    f"{md_text(claim.get('engagement', 0), 32)} engagement"
                    f" · post `{md_code(claim.get('post_id', ''), 512)}`"
                )
        body.append("")

    communities = [c for c in as_list(payload.get("communities")) if isinstance(c, dict)]
    body.extend(["## Communities", ""])
    if communities:
        mermaid = ["```mermaid", "flowchart TD"]
        for c_index, community in enumerate(communities):
            community_id = f"c{c_index}"
            label = (
                f"{clip(community.get('name', '?'), 120)} "
                f"({clip(community.get('actor_count', 0), 32)} actors)"
            )
            mermaid.append(f"  {community_id}[{mermaid_label(label)}]")
            for a_index, member in enumerate(as_list(community.get("actors"))):
                member_id = f"c{c_index}a{a_index}"
                mermaid.append(f"  {member_id}([{mermaid_label(member)}])")
                mermaid.append(f"  {community_id} --> {member_id}")
        mermaid.append("```")
        body.extend(mermaid)
        body.append("")
        for community in communities:
            body.append(
                f"- **{md_text(community.get('name', '?'), 120)}** "
                f"({md_text(community.get('basis', '?'), 40)}): "
                f"{md_text(community.get('actor_count', 0), 32)} actors, "
                f"{md_text(community.get('total_posts', 0), 32)} posts, "
                f"{md_text(community.get('total_engagement', 0), 32)} engagement"
                + (
                    " — themes: "
                    + ", ".join(md_text(t, 120) for t in as_list(community.get("themes")))
                    if as_list(community.get("themes"))
                    else ""
                )
            )
        body.append("")
    else:
        body.extend(["_No community structure available for this platform._", ""])

    def walk(node: dict[str, Any], depth: int) -> None:
        heading = "#" * min(6, depth + 2)
        body.append(f"{heading} {md_text(node.get('theme', 'Unknown Theme'), 160) or 'Unknown Theme'}")
        body.append("")
        body.append(
            f"- **Posts:** {md_text(node.get('post_count', 0), 32)} · "
            f"**Engagement:** {md_text(node.get('engagement_total', 0), 32)}"
        )
        top_actors = as_list(node.get("top_actors"))
        if top_actors:
            body.append(f"- **Top actors:** {', '.join(md_text(a, 120) for a in top_actors)}")
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
                f"- **{md_text(entry.get('actor', '?'), 120)}** — "
                f"{md_text(entry.get('theme_count', 0), 32)} themes, "
                f"{md_text(entry.get('post_count', 0), 32)} posts: "
                f"{', '.join(md_text(t, 120) for t in as_list(entry.get('themes')))}"
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
        out_path = resolve_out_path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(output + ("\n" if not output.endswith("\n") else ""))


if __name__ == "__main__":
    main()
