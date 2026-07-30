#!/usr/bin/env python3
"""Regression checks for Arbiter report interaction rendering.

The payload is adversary-influenced — post authors choose their own display
names, URLs, and post text, and `story` is model prose over those posts — so the
markdown note is an injection surface as well as a rendering. Both halves are
checked: the interaction timeline, points, story, and module status survive
intact, and no payload value can forge markdown structure, a YAML key, a mermaid
node, a working unsafe link, or a terminal escape.
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
RENDERER = ROOT / "integrations" / "arbiter" / "run_report.py"

CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def run_report(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Render one saved report without invoking a shell."""
    return subprocess.run(
        [sys.executable, str(RENDERER), str(path), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def write_report(path: Path, **overrides: Any) -> None:
    """Write a minimal valid report payload with selected overrides."""
    payload: dict[str, Any] = {
        "topic_id": "topic-fixture",
        "platform": "reddit",
        "title": "Interaction fixture",
        "top_actors": [],
        "themes": [],
        "communities": [],
        "cross_theme_actors": [],
        "sections": {"actors": True, "themes": False, "engagement": True},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_injection_surfaces(report: Path, tmp: Path) -> None:
    """Verify no payload value can forge structure, a key, a node, or a link."""
    write_report(
        report,
        topic_id="tid\nevil_key: injected",
        platform="reddit: also-injected",
        generated_at="2026-07-20\n---\nnot_frontmatter: true",
        title="Title\ninjected_title: true",
        top_actors=[{
            "actor": "Actor \x1b[31mred\x1b[0m A\x07",
            "group": "Group\n## Forged Group Heading",
            "dominant_theme": "Theme ```fence```",
            "engagement": {"total_posts": 1, "total_engagement": 10},
            "active_themes": ["Theme\n### Forged Theme Heading"],
            "claims": [{"text": "Data-url claim",
                        "url": "data:text/html;base64,PHNjcmlwdD4=",
                        "post_id": "p`1`", "engagement": 10}],
        }],
        communities=[{"name": 'Cluster " Alpha', "basis": "group", "actor_count": 1,
                      "total_posts": 1, "total_engagement": 10,
                      "actors": ['Member " ] click c0 href "http://evil.test"']}],
    )
    proc = run_report(report, "--format", "markdown")
    assert proc.returncode == 0, proc.stderr
    body = proc.stdout

    # Control: the hostile values reached the note, collapsed to one line each.
    assert "### Actor [31mred[0m A" in body, body[:800]
    assert "**Group:** Group ## Forged Group Heading" in body

    # No control character survives either format.
    for output in (body, run_report(report).stdout):
        found = CONTROL_CHARS_RE.findall(output)
        assert found == [], [hex(ord(c)) for c in found]

    # No payload value forged a heading or a frontmatter key.
    for line in body.splitlines():
        assert not line.startswith("## Forged"), line
        assert not line.startswith("### Forged"), line
        assert not line.startswith("evil_key:"), line
        assert not line.startswith("injected_title:"), line
        assert not line.startswith("not_frontmatter:"), line
    assert 'topic_id: "tid evil_key: injected"' in body
    assert 'generated_at: "2026-07-20 --- not_frontmatter: true"' in body
    assert body.count("\n---\n") == 1, "only the frontmatter block may delimit"

    # A non-http scheme with no link-breaking characters still gets no target,
    # which isolates the scheme check from the character check.
    assert "  - [Data-url claim]() — 10 engagement · post `p1`\n" in body, body
    assert "data:text/html" not in body.split("## Top actors")[0], "no unsafe target"

    # Inline code spans and code fences stay balanced.
    assert body.count("```") == 2, "only the mermaid fence may fence"
    assert "**Dominant theme:** Theme \\`\\`\\`fence\\`\\`\\`" in body

    # The mermaid label keeps its own quotes and cannot add a click directive.
    block = body.split("```mermaid\n", 1)[1].split("\n```", 1)[0]
    for line in block.splitlines():
        assert not line.strip().startswith("click"), line
        if "[" in line:
            label = line[line.index("[") + 1:line.rindex("]")].strip("([)]")
            assert label.startswith('"') and label.endswith('"'), line
            assert '"' not in label[1:-1], f"label broke out of its quotes: {line}"

    # --out refuses to write through a symlink, which write_text would follow.
    target = tmp / "report-target.md"
    target.write_text("original\n", encoding="utf-8")
    link = tmp / "report-link.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return
    refused = run_report(report, "--format", "markdown", "--out", str(link))
    assert refused.returncode == 2, (refused.returncode, refused.stderr)
    assert "symlink" in refused.stderr, refused.stderr
    assert target.read_text(encoding="utf-8") == "original\n", "the target was overwritten"
    plain = tmp / "report-note.md"
    written = run_report(report, "--format", "markdown", "--out", str(plain))
    assert written.returncode == 0, written.stderr
    assert plain.read_text(encoding="utf-8").startswith("---\n"), "a plain path still writes"


def main() -> int:
    """Verify timeline numbers, points, story, and module status are retained."""
    with tempfile.TemporaryDirectory(prefix="spotlight-arbiter-report-") as tmp_dir:
        report = Path(tmp_dir) / "report.json"
        story = "First high-interaction post.\nSecond high-interaction post."
        write_report(
            report,
            engagement_timeline={
                "points": [
                    {"date": "2026-07-19", "interactions": 12},
                    {"date": "2026-07-20", "interactions": 30},
                ],
                "total_interactions": 42,
                "average_interactions": 21,
                "story": story,
            },
        )

        tree = run_report(report)
        assert tree.returncode == 0, tree.stderr
        assert tree.stderr == "", tree.stderr
        assert "modules: actors=yes themes=no engagement=yes" in tree.stdout
        assert "INTERACTIONS OVER TIME" in tree.stdout
        assert "total: 42 · average per interval: 21" in tree.stdout
        assert "2026-07-19: 12 interactions" in tree.stdout
        assert "2026-07-20: 30 interactions" in tree.stdout
        assert "Story at a Glance:\n    First high-interaction post.\n" in tree.stdout
        assert "    Second high-interaction post.\n" in tree.stdout

        markdown = run_report(report, "--format", "markdown")
        assert markdown.returncode == 0, markdown.stderr
        assert markdown.stderr == "", markdown.stderr
        assert "## Interactions over time\n" in markdown.stdout
        assert "- **Total interactions:** 42\n" in markdown.stdout
        assert "- **Average per interval:** 21\n" in markdown.stdout
        assert (
            "| Date | Interactions |\n"
            "| --- | ---: |\n"
            "| 2026-07-19 | 12 |\n"
            "| 2026-07-20 | 30 |\n"
        ) in markdown.stdout
        # The story is model prose over adversary-authored posts, so markdown
        # mode quotes it line by line instead of passing it through verbatim.
        assert (
            "### Story at a Glance\n\n"
            "> First high-interaction post.\n"
            "> Second high-interaction post.\n"
        ) in markdown.stdout

        # A hostile story cannot forge structure: it is model prose over
        # adversary-authored posts, so every line is quoted and the characters
        # that would open a code fence, a mermaid block, or raw HTML are removed.
        write_report(
            report,
            engagement_timeline={
                "points": [
                    {"date": "2026-07-19", "interactions": 12},
                    {"date": "2026-07-20", "interactions": 30},
                ],
                "total_interactions": 42,
                "average_interactions": 21,
                "story": (
                    "Normal opening line.\n"
                    "```mermaid\nflowchart TD\n  a-->b\n```\n"
                    "## Forged heading\n"
                    "<img src=x onerror=alert(1)>\n"
                    "Trailing `code` line."
                ),
            },
        )
        markdown = run_report(report, "--format", "markdown")
        assert markdown.returncode == 0, markdown.stderr
        body = markdown.stdout
        assert "### Story at a Glance\n\n> Normal opening line.\n" in body
        assert "> Trailing code line.\n" in body, "backticks must be stripped from the story"
        assert "```" not in body, "the story must not be able to open a code fence"
        assert "<img" not in body and "onerror" in body, (
            "angle brackets go, the remaining text stays visible"
        )
        for line in body.splitlines():
            assert not line.startswith("## Forged heading"), line
            assert line == "" or not line.startswith("flowchart"), line
        assert "> ## Forged heading" in body, "a forged heading survives only as quoted text"

        # Claim links: post authors choose their own URLs, so only a plain
        # http(s) URL becomes a link target. A `javascript:` scheme, a URL
        # carrying a quote, and a URL carrying parentheses (which would close
        # the target early and leave the tail as document text) all render with
        # an empty target rather than a working link.
        write_report(
            report,
            top_actors=[{
                "actor": "Actor A",
                "engagement": {"total_posts": 4, "total_engagement": 40},
                "claims": [
                    {"text": "Script claim", "url": "javascript:alert(1)",
                     "post_id": "p1", "engagement": 4},
                    {"text": "Quoted claim", "url": 'https://example.test/a?q="x"',
                     "post_id": "p2", "engagement": 3},
                    {"text": "Paren claim", "url": "https://example.test/a(b)c",
                     "post_id": "p3", "engagement": 2},
                    {"text": "Plain claim", "url": "https://example.test/plain",
                     "post_id": "p4", "engagement": 1},
                ],
            }],
        )
        markdown = run_report(report, "--format", "markdown")
        assert markdown.returncode == 0, markdown.stderr
        body = markdown.stdout
        assert "  - [Script claim]() — 4 engagement · post `p1`\n" in body
        assert "  - [Quoted claim]() — 3 engagement · post `p2`\n" in body
        assert "  - [Paren claim]() — 2 engagement · post `p3`\n" in body
        assert ("  - [Plain claim](https://example.test/plain) — 1 engagement · "
                "post `p4`\n") in body, body

        # A missing timeline is not fabricated and the engagement module's
        # false status remains visible instead of crashing the renderer.
        write_report(
            report,
            engagement_timeline=None,
            sections={"actors": False, "themes": False, "engagement": False},
        )
        tree = run_report(report)
        assert tree.returncode == 0, tree.stderr
        assert "modules: actors=no themes=no engagement=no" in tree.stdout
        assert "INTERACTIONS OVER TIME" not in tree.stdout
        markdown = run_report(report, "--format", "markdown")
        assert markdown.returncode == 0, markdown.stderr
        assert "## Interactions over time" not in markdown.stdout

        check_injection_surfaces(report, Path(tmp_dir))

    print("arbiter report: OK - interaction timeline retained, story quoted and "
          "defanged, injection surfaces closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
