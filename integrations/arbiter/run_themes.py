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
  python3 integrations/arbiter/run_themes.py themes.json --format markdown \
    --out {CASE_DIR}/research/arbiter-themes-<slug>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Terminal escapes and other C0/C1 controls: a payload author who can place
# these in a theme or author name can repaint the CLI tree or hide text.
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Characters that let a URL escape its markdown link target or its HTML
# attribute. Parentheses close the `](...)` form, the rest break quoting.
URL_UNSAFE_RE = re.compile(r"""[\x00-\x20<>"'`\\()]""")


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
    """Collapse untrusted text to one control-free, whitespace-normalized line.

    Args:
        value: Any payload value; ``None`` renders empty.

    Returns:
        One line with C0/C1 control characters removed and whitespace collapsed.
    """
    stripped = CONTROL_CHARS_RE.sub("", str(value if value is not None else ""))
    return " ".join(stripped.split())


def clip(value: Any, limit: int) -> str:
    """Single-line, control-free, length-capped rendering of untrusted text."""
    return single_line(value)[:limit]


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
    url = single_line(value)
    if not url.startswith(("http://", "https://")):
        return ""
    return "" if URL_UNSAFE_RE.search(url) else url


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
    root = clip(payload.get("root_theme"), 200) or "Themes"
    lines.append(root)
    lines.append(
        f"  themes: {clip(payload.get('total_themes', '?'), 32)}"
        f" · levels: {clip(payload.get('theme_levels', '?'), 32)}"
        f" · posts analyzed: {clip(payload.get('total_posts', '?'), 32)}"
        f" · platform: {clip(payload.get('platform', '?'), 32)}"
    )
    lines.append("")

    def walk(node: dict[str, Any], prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        stats = (
            f"[{clip(node.get('post_count', 0), 32)} posts · "
            f"{clip(node.get('engagement_total', 0), 32)} engagement]"
        )
        lines.append(f"{prefix}{connector}{clip(node.get('theme', 'Unknown Theme'), 160)} {stats}")
        child_prefix = prefix + ("    " if is_last else "│   ")
        sentiment = sentiment_summary(node)
        if sentiment:
            lines.append(f"{child_prefix}sentiment: {sentiment}")
        for post in as_list(node.get("top_posts"))[:3]:
            if not isinstance(post, dict):
                continue
            text = clip(post.get("text", ""), 80)
            lines.append(
                f"{child_prefix}• {text} — {clip(post.get('author', '?'), 120)} "
                f"({clip(post.get('engagement', 0), 32)} eng) {safe_url(post.get('url'))}"
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
    """Render an Obsidian-ready themes note."""
    root = clip(payload.get("root_theme"), 200) or "Arbiter themes"
    # `root` is the raw single-line value: the frontmatter quotes it, the mermaid
    # label strips its own unsafe characters, and the H1 escapes it separately.

    mermaid_lines = ["```mermaid", "flowchart TD"]
    counter = 0
    root_id = mermaid_id(counter)
    mermaid_lines.append(f"  {root_id}[{mermaid_label(root)}]")

    sections: list[str] = []

    def walk(node: dict[str, Any], parent_id: str, depth: int) -> None:
        nonlocal counter
        counter += 1
        node_id = mermaid_id(counter)
        theme = clip(node.get("theme", "Unknown Theme"), 160) or "Unknown Theme"
        label = f"{theme} ({clip(node.get('post_count', 0), 32)})"
        mermaid_lines.append(f"  {node_id}[{mermaid_label(label)}]")
        mermaid_lines.append(f"  {parent_id} --> {node_id}")

        heading = "#" * min(6, depth + 2)
        sections.append(f"{heading} {md_text(theme, 160)}")
        sections.append("")
        sections.append(
            f"- **Posts:** {md_text(node.get('post_count', 0), 32)} · "
            f"**Engagement:** {md_text(node.get('engagement_total', 0), 32)}"
        )
        sentiment = sentiment_summary(node)
        if sentiment:
            sections.append(f"- **Sentiment:** {md_text(sentiment, 200)}")
        sample_ids = as_list(node.get("sample_post_ids"))
        if sample_ids:
            rendered_ids = ", ".join(f"`{md_code(pid, 512)}`" for pid in sample_ids)
            sections.append(f"- **Sample post ids:** {rendered_ids}")
        top_posts = [p for p in as_list(node.get("top_posts")) if isinstance(p, dict)]
        if top_posts:
            sections.append("- **Top posts:**")
            for post in top_posts:
                text = md_link_text(post.get("text", ""), 120)
                sections.append(
                    f"  - [{text}]({safe_url(post.get('url'))}) — "
                    f"{md_text(post.get('author', '?'), 120)}"
                    f" ({md_text(post.get('engagement', 0), 32)} engagement)"
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
        f"title: {yaml_value(f'Arbiter themes — {root}')}",
        "type: arbiter-themes",
        f"topic_id: {yaml_value(payload.get('topic_id', ''))}",
        f"platform: {yaml_value(payload.get('platform', ''))}",
        f"generated_at: {yaml_value(payload.get('generated_at', ''))}",
        f"source_file: {yaml_value(source_name)}",
        "tags: [arbiter, themes]",
        "---",
        "",
        f"# Arbiter themes — {md_text(root, 200)}",
        "",
        f"Curated case study `{md_code(payload.get('topic_id', ''), 128)}` · "
        f"platform `{md_code(payload.get('platform', ''), 32)}` · "
        f"{md_text(payload.get('total_themes', '?'), 32)} themes across "
        f"{md_text(payload.get('theme_levels', '?'), 32)} levels · "
        f"{md_text(payload.get('total_posts', '?'), 32)} posts analyzed.",
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
        out_path = resolve_out_path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(output + ("\n" if not output.endswith("\n") else ""))


if __name__ == "__main__":
    main()
