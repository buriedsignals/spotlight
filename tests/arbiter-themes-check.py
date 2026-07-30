#!/usr/bin/env python3
"""Regression checks for the Arbiter theme-tree renderer (run_themes.py).

`integrations/arbiter/run_themes.py` turns a saved `GET /topics/{id}/themes`
response into either a CLI tree or an Obsidian-ready markdown note. Every string
in that payload is adversary-influenced — post authors choose their own display
names and post text, and theme labels are model output over those posts — so the
renderer is a markdown- and terminal-injection surface, not a formatter.

Asserts:
  1. the tree renders the hierarchy, stats, sentiment summary, and top posts;
  2. the markdown note renders frontmatter, a mermaid hierarchy, and per-theme
     sections at the right heading depth;
  3. a newline-bearing theme, root theme, or author name cannot forge a heading,
     a list item, or a frontmatter key — everything collapses to one line;
  4. frontmatter values are JSON-quoted, so no payload value can add a key,
     close the block, or start a nested mapping;
  5. a `javascript:` URL (and any URL carrying link-breaking characters) renders
     with an empty target instead of a working link;
  6. terminal control sequences are stripped from both output formats;
  7. a mermaid label carrying quotes, backticks, and a `click` directive stays
     inside its own quoted node text and cannot close the fence;
  8. `--out` refuses to write through an existing symlink;
  9. a payload that is not a themes response fails loudly instead of rendering.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "integrations" / "arbiter" / "run_themes.py"

CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

GOOD_PAYLOAD: dict[str, Any] = {
    "topic_id": "topic_fixture",
    "platform": "youtube",
    "root_theme": "Fixture Narrative",
    "generated_at": "2026-07-20T00:00:00Z",
    "total_themes": 2,
    "theme_levels": 2,
    "total_posts": 20,
    "themes": [{
        "theme": "Theme One",
        "post_count": 14,
        "engagement_total": 900,
        "sentiment_distribution": {"negative": 12, "neutral": 4, "positive": 2},
        "sample_post_ids": ["IDfIYCNsmMI", "dZj9yXtff_U"],
        "top_posts": [{"text": "A loud post", "author": "Actor A",
                       "engagement": 700, "url": "https://example.test/1"}],
        "children": [{"theme": "Theme One A", "post_count": 4,
                      "engagement_total": 260, "children": []}],
    }],
}

# Every hostile value below is shaped like real payload content: a display name,
# a theme label, a post body, a URL.
HOSTILE_PAYLOAD: dict[str, Any] = {
    "topic_id": "abc\nevil_key: injected",
    "platform": "youtube: also-injected",
    "generated_at": "2026-07-20\n---\nnot_frontmatter: true",
    "root_theme": "Root Narrative\ninjected_root: true",
    "total_themes": 1,
    "theme_levels": 1,
    "total_posts": 3,
    "themes": [{
        "theme": "Real Theme\n## Forged Heading\n- forged bullet",
        "post_count": 3,
        "engagement_total": 12,
        "sample_post_ids": ["ok_id", "bad`id`\nmore"],
        "top_posts": [
            {"text": "Script post [link](javascript:alert(1))",
             "author": "Author\n### Forged Author Heading",
             "engagement": 9, "url": "javascript:alert(1)"},
            {"text": "Control \x1b[31mred\x1b[0m post\x07",
             "author": "Bell\x07Author", "engagement": 4,
             "url": "https://example.test/ok"},
            {"text": "Paren post", "author": "Paren Author", "engagement": 1,
             "url": "https://example.test/a(b)c"},
        ],
        "children": [{
            "theme": 'Quote " Theme ```fence``` click n1 href "http://evil.test"',
            "post_count": 1,
            "engagement_total": 1,
            "children": [],
        }],
    }],
}


def render(payload: Any, path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Write one payload to disk and run the renderer over it without a shell."""
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RENDERER), str(path), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )


def ok_stdout(proc: subprocess.CompletedProcess[str]) -> str:
    """Require a clean run and return its stdout."""
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert proc.stderr == "", proc.stderr
    return proc.stdout


def front_matter_lines(markdown: str) -> list[str]:
    """The lines between the opening and closing frontmatter delimiters."""
    assert markdown.startswith("---\n"), markdown[:40]
    body = markdown[4:]
    end = body.index("\n---\n")
    return body[:end].splitlines()


def check_happy_paths(source: Path) -> None:
    """Verify both formats render the hierarchy, stats, and evidence links."""
    tree = ok_stdout(render(GOOD_PAYLOAD, source))
    assert tree.startswith("Fixture Narrative\n"), tree[:60]
    assert "  themes: 2 · levels: 2 · posts analyzed: 20 · platform: youtube\n" in tree
    assert "└── Theme One [14 posts · 900 engagement]\n" in tree
    assert "    sentiment: negative 12 · neutral 4 · positive 2\n" in tree
    assert "    • A loud post — Actor A (700 eng) https://example.test/1\n" in tree
    assert "    └── Theme One A [4 posts · 260 engagement]" in tree

    markdown = ok_stdout(render(GOOD_PAYLOAD, source, "--format", "markdown"))
    assert front_matter_lines(markdown) == [
        'title: "Arbiter themes \\u2014 Fixture Narrative"',
        "type: arbiter-themes",
        'topic_id: "topic_fixture"',
        'platform: "youtube"',
        'generated_at: "2026-07-20T00:00:00Z"',
        f'source_file: "{source.name}"',
        "tags: [arbiter, themes]",
    ], front_matter_lines(markdown)
    assert "# Arbiter themes — Fixture Narrative\n" in markdown
    assert ("Curated case study `topic_fixture` · platform `youtube` · 2 themes "
            "across 2 levels · 20 posts analyzed.\n") in markdown
    assert '  n1["Theme One (14)"]\n  n0 --> n1\n' in markdown
    assert '  n2["Theme One A (4)"]\n  n1 --> n2\n' in markdown
    assert "### Theme One\n" in markdown
    assert "#### Theme One A\n" in markdown, "a child theme sits one heading deeper"
    assert "- **Posts:** 14 · **Engagement:** 900\n" in markdown
    assert "- **Sample post ids:** `IDfIYCNsmMI`, `dZj9yXtff_U`\n" in markdown
    assert "  - [A loud post](https://example.test/1) — Actor A (700 engagement)\n" in markdown


def check_no_forged_structure(source: Path) -> None:
    """Verify no payload value can forge a heading, a bullet, or a YAML key."""
    markdown = ok_stdout(render(HOSTILE_PAYLOAD, source, "--format", "markdown"))
    # Control: the hostile text really did reach the output, collapsed to one
    # line, so the structural assertions below are not reading an empty note.
    assert "### Real Theme ## Forged Heading - forged bullet\n" in markdown, markdown[:600]

    for line in markdown.splitlines():
        assert not line.startswith("## Forged Heading"), line
        assert not line.startswith("### Forged Author Heading"), line
        assert not line.startswith("- forged bullet"), line
        assert not line.startswith("injected_root:"), line
        assert not line.startswith("evil_key:"), line
        assert not line.startswith("not_frontmatter:"), line

    # Frontmatter: exactly the expected keys, every payload-derived value a
    # quoted JSON scalar, so nothing can add a key or reopen the block.
    keys = [line.split(":", 1)[0] for line in front_matter_lines(markdown)]
    assert keys == ["title", "type", "topic_id", "platform", "generated_at",
                    "source_file", "tags"], keys
    assert 'topic_id: "abc evil_key: injected"' in markdown, markdown[:400]
    assert 'platform: "youtube: also-injected"' in markdown
    assert 'generated_at: "2026-07-20 --- not_frontmatter: true"' in markdown
    assert markdown.count("\n---\n") == 1, "only the frontmatter block may delimit"

    # Inline code spans: a backtick inside a sample post id would close its own
    # span and let the rest of the id render as markdown, so backticks are
    # dropped rather than escaped inside a span.
    assert "- **Sample post ids:** `ok_id`, `badid more`\n" in markdown, markdown[-500:]

    # The tree format collapses the same values, so a CLI reader cannot be shown
    # a forged branch either.
    tree = ok_stdout(render(HOSTILE_PAYLOAD, source))
    assert "Root Narrative injected_root: true\n" in tree
    for line in tree.splitlines():
        assert "Forged Heading" not in line or "Real Theme" in line, line


def check_urls_neutralized(source: Path) -> None:
    """Verify only plain http(s) URLs become link targets, in both formats."""
    markdown = ok_stdout(render(HOSTILE_PAYLOAD, source, "--format", "markdown"))
    targets = re.findall(r"^  - \[.*?\]\((.*?)\)", markdown, re.MULTILINE)
    assert targets == ["", "https://example.test/ok", ""], targets
    # The scheme may survive as escaped body text, but never as a link target.
    assert re.search(r"(?<!\\)\(javascript:", markdown) is None, (
        "a javascript: URL must never open a link target"
    )
    assert "](https://example.test/a(b)c)" not in markdown, (
        "a parenthesised URL would close the link target early"
    )
    # The post text itself keeps its literal characters, with every bracket and
    # parenthesis escaped so the label cannot carry a nested link of its own.
    assert "Script post \\[link\\]\\(javascript:alert\\(1\\)\\)" in markdown
    assert "](javascript:" not in markdown

    # The tree is terminal output, so post text stays literal; what matters is
    # that the URL slot itself is empty for both unsafe URLs and populated for
    # the safe one, so a reader is never handed a clickable hostile link.
    tree = ok_stdout(render(HOSTILE_PAYLOAD, source))
    assert "(9 eng) \n" in tree, "the javascript: URL slot must render empty"
    assert "(1 eng) \n" in tree, "the parenthesised URL slot must render empty"
    assert "(4 eng) https://example.test/ok\n" in tree


def check_control_chars_stripped(source: Path) -> None:
    """Verify terminal escapes never reach either output format."""
    # The escape's remaining `[31m` tail is inert text once the ESC that
    # introduced it is gone; markdown mode additionally escapes its brackets.
    surviving = {(): "Control [31mred[0m post",
                 ("--format", "markdown"): "Control \\[31mred\\[0m post"}
    for args, expected in surviving.items():
        output = ok_stdout(render(HOSTILE_PAYLOAD, source, *args))
        found = CONTROL_CHARS_RE.findall(output)
        assert found == [], (args, [hex(ord(c)) for c in found])
        assert "\x1b" not in output and "\x07" not in output, args
        # The surrounding text survives, so only the control bytes were removed
        # rather than the whole value being dropped.
        assert expected in output, args
        assert "BellAuthor" in output, args


def check_mermaid_label_inert(source: Path) -> None:
    """Verify a hostile label stays inside its quoted node and keeps the fence."""
    markdown = ok_stdout(render(HOSTILE_PAYLOAD, source, "--format", "markdown"))
    assert markdown.count("```") == 2, "the mermaid fence must open and close exactly once"
    block = markdown.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
    node_lines = [line for line in block.splitlines() if "[" in line]
    assert node_lines, block
    for line in node_lines:
        label = line[line.index("[") + 1:line.rindex("]")]
        assert label.startswith('"') and label.endswith('"'), line
        assert '"' not in label[1:-1], f"label broke out of its quotes: {line}"
        assert "`" not in label, f"a backtick in a label can close the fence: {line}"
    for line in block.splitlines():
        assert not line.strip().startswith("click"), f"forged click directive: {line}"
    # Control: the hostile label is present, with its quotes downgraded.
    assert "Quote ' Theme fence click n1 href 'http://evil.test'" in block, block

    # The same label reaches a heading further down the note, where a triple
    # backtick would open a code fence and swallow everything after it. Escaped
    # there rather than stripped, so the reader still sees the real label.
    assert ('#### Quote " Theme \\`\\`\\`fence\\`\\`\\` click n1 '
            'href "http://evil.test"') in markdown, markdown[-400:]


def check_out_symlink_refused(source: Path, tmp: Path) -> None:
    """Verify --out will not write through an existing symlink."""
    target = tmp / "symlink-target.md"
    target.write_text("original\n", encoding="utf-8")
    link = tmp / "note-link.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        print("arbiter themes: symlink case skipped (unsupported filesystem)")
        return
    proc = render(GOOD_PAYLOAD, source, "--format", "markdown", "--out", str(link))
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "symlink" in proc.stderr, proc.stderr
    assert target.read_text(encoding="utf-8") == "original\n", "the target was overwritten"

    # A plain destination still works, so the guard is not refusing everything.
    plain = tmp / "note.md"
    proc = render(GOOD_PAYLOAD, source, "--format", "markdown", "--out", str(plain))
    assert proc.returncode == 0, proc.stderr
    assert "# Arbiter themes — Fixture Narrative" in plain.read_text(encoding="utf-8")


def check_bad_payloads(source: Path) -> None:
    """Verify a non-themes payload fails loudly rather than rendering nothing."""
    for payload, reason in ((["not", "a", "dict"], "a JSON array"),
                            ({"themes": "not-a-list"}, "a scalar themes field"),
                            ({}, "an object with no themes"),
                            ("{not json", "malformed JSON")):
        proc = render(payload, source)
        assert proc.returncode != 0, (reason, proc.stdout)
        assert "error:" in proc.stderr, (reason, proc.stderr)

    # Nulls where the contract promises collections are tolerated, not fatal:
    # a thin response still renders its header.
    thin = ok_stdout(render({"themes": [], "root_theme": None}, source))
    assert thin.startswith("Themes\n"), thin[:40]


def main() -> int:
    """Run the Arbiter theme-renderer regression suite."""
    with tempfile.TemporaryDirectory(prefix="spotlight-arbiter-themes-") as tmp_dir:
        tmp = Path(tmp_dir)
        source = tmp / "arbiter-themes-topic_fixture.json"
        check_happy_paths(source)
        check_no_forged_structure(source)
        check_urls_neutralized(source)
        check_control_chars_stripped(source)
        check_mermaid_label_inert(source)
        check_out_symlink_refused(source, tmp)
        check_bad_payloads(source)
    print("arbiter themes: OK - hierarchy, frontmatter quoting, link and mermaid "
          "safety, control stripping, and symlink refusal verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
