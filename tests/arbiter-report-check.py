#!/usr/bin/env python3
"""Regression checks for Arbiter report interaction rendering."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "integrations" / "arbiter" / "run_report.py"


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
        assert f"### Story at a Glance\n\n{story}\n" in markdown.stdout

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

    print("arbiter report: OK - interaction timeline and story retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
