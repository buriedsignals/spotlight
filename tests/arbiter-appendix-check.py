#!/usr/bin/env python3
"""Regression checks for the conditional Arbiter analytics section.

`integrations/arbiter/run_appendix.py` turns a case's saved
`research/arbiter-report-*.json` response into one self-contained HTML fragment,
and `scripts/render-report.py` splices that fragment in only when such a file
exists. Both halves of that contract are checked here: the fragment's content
and safety, and the renderer's promise that a case without Arbiter data produces
byte-for-byte the same report as before the integration existed.

Asserts:
  1. a good payload renders exactly one section carrying the engagement chart,
     story, theme table, actors, communities, and source posts, with exact
     numbers, ranking, and bar widths;
  2. `platform_reports` is deliberately never expanded;
  3. no Arbiter file, malformed JSON, a non-object payload, and a payload whose
     every block is empty all yield empty stdout and exit 0 with no traceback;
  4. hostile payload values are escaped or dropped, and only `http(s)` URLs link;
  5. a full report render is byte-identical when no usable Arbiter data exists,
     and demonstrably different when it does;
  6. `verified` / `confirmed` / `publishable` never appear in a fragment — the
     archive section reports engagement, never an editorial verdict.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APPENDIX = ROOT / "integrations" / "arbiter" / "run_appendix.py"
RENDERER = ROOT / "scripts" / "render-report.py"
FIXTURE = ROOT / "tests" / "fixtures" / "arbiter-report.sample.json"

# Statuses Spotlight reserves for its own editorial pipeline. Arbiter analytics
# describe engagement, so these words must never reach a rendered fragment even
# when the upstream payload volunteers them.
EDITORIAL_STATUSES = ("verified", "confirmed", "publishable")

HOSTILE_PAYLOAD: dict[str, Any] = {
    "root_theme": "Narrative <img src=x onerror=alert(1)>",
    "engagement_timeline": {
        "points": [{"date": "2026-07-06", "interactions": 10},
                   {"date": "2026-07-07", "interactions": 20}],
        "total_interactions": 30,
        "average_interactions": 15,
        "story": "- Bullet <b onmouseover=\"x\">bold</b> line.",
    },
    "themes": [{"theme": 'Theme <b onmouseover="x">', "post_count": 2,
                "engagement_total": 30, "top_actors": ["<svg/onload=1>"], "children": []}],
    "top_actors": [{
        "actor": "Actor <script>alert(1)</script>",
        "dominant_theme": "</table><script>alert(2)</script>",
        "engagement": {"total_posts": 2, "total_engagement": 30},
        "claims": [
            {"text": "Script claim <script>alert(3)</script>", "url": "javascript:alert(1)",
             "published_at": "2026-07-07T00:00:00Z", "engagement": 30},
            {"text": "Data-url claim", "url": "data:text/html;base64,PHNjcmlwdD4=",
             "published_at": "2026-07-07T00:00:00Z", "engagement": 20},
            {"text": "Quoted-url claim", "url": 'https://example.test/ok?q="x"',
             "published_at": "2026-07-07T00:00:00Z", "engagement": 10},
            {"text": "Plain http claim", "url": "https://example.test/plain",
             "published_at": "2026-07-07T00:00:00Z", "engagement": 5},
        ],
    }],
    "communities": [{"name": "Group <svg/onload=1>", "actor_count": 2, "total_posts": 1,
                     "total_engagement": 30, "actors": ["Member <iframe>"]}],
}

# Every "status"-ish field below is an upstream value the fragment must not surface.
STATUS_CLAIM = {"text": "A recorded source post.", "engagement": 10, "status": "confirmed",
                "published_at": "2026-07-07T00:00:00Z", "publication_state": "publishable",
                "url": "https://example.test/1"}
STATUS_PAYLOAD: dict[str, Any] = {
    "root_theme": "Status Narrative", "status": "verified", "review_state": "publishable",
    "themes": [{"theme": "Theme One", "post_count": 1, "engagement_total": 10,
                "top_actors": ["Actor A"], "children": [], "state": "confirmed"}],
    "top_actors": [{"actor": "Actor A", "dominant_theme": "Theme One",
                    "verification": "verified", "claims": [STATUS_CLAIM],
                    "engagement": {"total_posts": 1, "total_engagement": 10}}],
}


def load_build_case():
    """Reuse the canonical case fixture builder from render-report-check.py."""
    spec = importlib.util.spec_from_file_location(
        "render_report_check", ROOT / "tests" / "render-report-check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_case


def fragment_for(case: Path) -> subprocess.CompletedProcess[str]:
    """Run the appendix renderer for one case directory without a shell."""
    return subprocess.run(
        [sys.executable, str(APPENDIX), "--case-dir", str(case)],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )


def render(payload: Any, case: Path, name: str = "arbiter-report-topic_fixture.json") -> str:
    """Save one Arbiter payload into a case and return the rendered fragment."""
    research = case / "research"
    research.mkdir(parents=True, exist_ok=True)
    for stale in research.glob("arbiter-report-*.json"):
        stale.unlink()
    target = research / name
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")
    proc = fragment_for(case)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    return proc.stdout


def empty_render(payload: Any, case: Path, reason: str) -> None:
    """Require an unusable payload to yield empty stdout, exit 0, and silence.

    An unusable Arbiter file is an expected, tolerated condition — the common
    case is simply that the case never used Arbiter — so it must be handled
    inside the renderer, not escape as an exception the entry point has to note.
    """
    assert render(payload, case) == "", reason
    assert fragment_for(case).stderr == "", (reason, fragment_for(case).stderr)


def check_good_fixture(case: Path) -> None:
    """Verify the fixture's fragment carries every section with exact values."""
    shutil.copyfile(FIXTURE, case / "research" / "arbiter-report-topic_fixture.json")
    proc = fragment_for(case)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr
    body = proc.stdout
    assert body.count('<section class="arb-appendix"') == 1, "expected exactly one section"
    assert body.endswith("</section>"), body[-80:]
    assert "<h2 class=\"arb-title\">Fixture Narrative</h2>" in body, "root_theme titles the section"

    # Engagement: exact totals, a three-bucket bar chart (never a line), and a
    # labelled peak so the coloured marker is never an unexplained dot.
    assert "<h3>Interactions over time</h3>" in body
    assert ("Total interactions: 1,260 · Average: 420 per interval · "
            "3 interval(s) from Jul 06 to Jul 08") in body
    assert body.count('fill="#2E5C8A"/>') == 3, "one bar per dated bucket"
    assert "<polyline" not in body, "a three-bucket series must not be drawn as a line"
    assert "Peak · 900" in body, "the peak marker must carry its value"

    # Story: the plain line becomes a paragraph, the two bullets a list.
    assert "<h3>The Story at a Glance</h3>" in body
    assert '<p class="arb-story-text">Interactions peaked on Jul 07.</p>' in body
    assert body.count("<li>") == 2, "one list item per story bullet"

    # Themes: parents rank by interactions, each sub-theme sits under its parent,
    # and bar widths are the share of the largest parent (900).
    assert "<h3>Theme landscape</h3>" in body
    order = [body.find(label) for label in ("Theme One<", "Theme One A<", "Theme Two<")]
    assert -1 not in order and order == sorted(order), order
    for width in ("width:100.0%", "width:28.9%", "width:40.0%"):
        assert width in body, width
    assert '<td class="arb-voices">News Org B</td>' in body

    # Actors and communities: ranked, with exact counts and the member overflow.
    assert "<h3>Top actors</h3>" in body
    assert '<td class="arb-rank">1</td><td>Actor A</td><td class="arb-num">12</td>' in body
    assert "<h3>Communities</h3>" in body
    assert -1 < body.find("Community Alpha") < body.find("Community Beta"), (
        "communities must rank by interactions descending, not payload order"
    )
    assert "9 voices" in body and "+7 more" in body

    # Source posts: one card per claim, ranked by engagement across all actors.
    assert "<h3>Source posts recorded by Arbiter</h3>" in body
    assert body.count('class="arb-claim"') == 3, "one card per recorded claim"
    assert "The 3 highest-engagement source posts Arbiter recorded" in body
    assert "across the top actors)" not in body, "nothing was capped, so no overflow hint"
    ranked = [body.find(text) for text in ("highest-engagement post inside",
                                           "aggregation post about", "quieter follow-up")]
    assert -1 not in ranked and ranked == sorted(ranked), ranked

    # Only the cross-platform report renders; per-platform breakdowns do not.
    assert "PlatformOnlyActor" not in body, "platform_reports must never be expanded"
    assert "topic_fixture" not in body, "internal identifiers must not render"


def check_unusable_payloads(case: Path) -> None:
    """Verify every unusable Arbiter input degrades to empty stdout and exit 0."""
    for stale in (case / "research").glob("arbiter-report-*.json"):
        stale.unlink()
    absent = fragment_for(case)
    assert absent.returncode == 0 and absent.stdout == "", (absent.returncode, absent.stdout)
    missing_dir = fragment_for(case / "does-not-exist")
    assert missing_dir.returncode == 0 and missing_dir.stdout == "", missing_dir.stdout

    empty_render("{not json", case, "malformed JSON")
    empty_render("", case, "an empty file")
    empty_render([1, 2, 3], case, "a non-object payload")
    empty_render({}, case, "an empty object")
    empty_render({"root_theme": "Title only"}, case, "a payload with no renderable block")
    empty_render({"root_theme": "T", "themes": [], "top_actors": [], "communities": [],
                  "engagement_timeline": {}}, case, "a payload whose every block is empty")
    # Scalars where the payload should carry collections must not crash either.
    empty_render({"root_theme": "T", "themes": 7, "top_actors": "x", "communities": None,
                  "engagement_timeline": 3}, case, "scalar collections")

    # The newest (lexicographically last) saved response wins, so an unusable
    # later save suppresses the section rather than falling back to an older one.
    research = case / "research"
    for stale in research.glob("arbiter-report-*.json"):
        stale.unlink()
    shutil.copyfile(FIXTURE, research / "arbiter-report-1.json")
    (research / "arbiter-report-2.json").write_text("{}", encoding="utf-8")
    assert fragment_for(case).stdout == "", "the newest save must win, even when unusable"
    (research / "arbiter-report-2.json").unlink()
    assert fragment_for(case).stdout != "", "the remaining usable save must render"
    (research / "arbiter-report-1.json").unlink()


def check_hostile_payload(case: Path) -> None:
    """Verify untrusted payload values are escaped and only http(s) URLs link."""
    body = render(HOSTILE_PAYLOAD, case)
    assert body, "a hostile payload must still render, escaped"
    # No payload value may reopen markup: every tag-ish form survives only as
    # escaped text, which is inert, and no attribute injection can land.
    for raw in ("<script", "<iframe", "<img", "<svg/onload", 'onmouseover="x"',
                "javascript:", "data:text/html"):
        assert raw not in body, raw
    for escaped in ("&lt;script&gt;alert(1)&lt;/script&gt;", "&lt;svg/onload=1&gt;",
                    "&lt;b onmouseover=&quot;x&quot;&gt;",
                    "&lt;img src=x onerror=alert(1)&gt;"):
        assert escaped in body, escaped

    # Only plain http(s) links survive; the two unsafe URLs render as plain text
    # cards, and a URL carrying a quote is dropped rather than escaped into an
    # attribute where it could break out.
    hrefs = re.findall(r'href="([^"]*)"', body)
    assert hrefs == ["https://example.test/plain"], hrefs
    assert "Script claim" in body and "Data-url claim" in body, "unsafe URLs drop the link only"
    assert body.count('class="arb-claim"') == 4, "every claim still renders as a card"

    # Long untrusted text is clipped rather than trusted at any length.
    long_claim = {**HOSTILE_PAYLOAD, "top_actors": [{
        "actor": "A" * 200,
        "engagement": {"total_posts": 1, "total_engagement": 1},
        "claims": [{"text": "B" * 400, "engagement": 1, "url": "https://example.test/1"}],
    }]}
    clipped = render(long_claim, case)
    assert "A" * 200 not in clipped, "actor names must be clipped"
    assert "B" * 400 not in clipped, "claim text must be clipped"
    assert "…" in clipped, "clipped text must be marked"


def check_no_editorial_status(case: Path) -> None:
    """Verify Spotlight's verification vocabulary never appears in a fragment."""
    shutil.copyfile(FIXTURE, case / "research" / "arbiter-report-topic_fixture.json")
    for source, body in (("the fixture", fragment_for(case).stdout),
                         ("a status-bearing payload", render(STATUS_PAYLOAD, case))):
        assert body, source
        # Control: the scan reads real rendered content, so a miss below is a
        # genuine absence rather than an empty haystack.
        assert "interactions" in body.lower(), source
        for word in EDITORIAL_STATUSES:
            assert word not in body.lower(), f"{word!r} rendered as a status in {source}"
    for stale in (case / "research").glob("arbiter-report-*.json"):
        stale.unlink()


def check_report_byte_identity(tmp: Path, build_case) -> None:
    """Verify a case without usable Arbiter data renders a byte-identical report."""
    def render_report(case: Path) -> tuple[str, str]:
        proc = subprocess.run([sys.executable, str(RENDERER), str(case)],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        report = (case / "report.html").read_bytes()
        return hashlib.sha256(report).hexdigest(), proc.stderr

    control = build_case(tmp / "control")
    baseline, _ = render_report(control)
    # Determinism control: the renderer must be reproducible before the absence
    # of Arbiter data can mean anything.
    assert render_report(control)[0] == baseline, "the base renderer is not deterministic"
    assert render_report(build_case(tmp / "twin"))[0] == baseline, "two identical cases diverged"

    # The hook adds zero bytes when there is no fragment, which a hash comparison
    # alone cannot prove — a uniform stray byte would shift every render equally.
    # Pin it at the splice instead: the findings must run straight into the
    # methodology section with no indentation-only line left behind.
    control_html = (control / "report.html").read_text(encoding="utf-8")
    assert '\n  <section id="method">' in control_html, "splice point not found"
    assert '\n  \n  <section id="method">' not in control_html, "the hook left a blank line"
    assert "arb-" not in control_html and "Arbiter" not in control_html, control_html[:200]

    subject = build_case(tmp / "subject")
    assert render_report(subject)[0] == baseline, "the subject case starts from the baseline"
    for name, payload in (("arbiter-report-broken.json", "{not json"),
                          ("arbiter-report-empty.json", "{}"),
                          ("arbiter-report-blank.json", '{"root_theme": "Title only"}')):
        (subject / "research" / name).write_text(payload, encoding="utf-8")
        digest, stderr = render_report(subject)
        assert digest == baseline, f"{name} changed the report bytes"
        assert "Traceback" not in stderr, stderr
        html = (subject / "report.html").read_text(encoding="utf-8")
        assert '\n  \n  <section id="method">' not in html, f"{name} left a blank line"
        (subject / "research" / name).unlink()

    # Negative control: real Arbiter data must change the report, otherwise the
    # identity assertions above would pass for the wrong reason.
    shutil.copyfile(FIXTURE, subject / "research" / "arbiter-report-topic_fixture.json")
    digest, stderr = render_report(subject)
    assert digest != baseline, "usable Arbiter data must change the report"
    html = (subject / "report.html").read_text(encoding="utf-8")
    assert html.count('<section class="arb-appendix"') == 1, "expected one spliced section"
    assert "Source posts recorded by Arbiter" in html
    assert stderr == "", stderr
    for marker in ('id="method"', 'id="caveats"', "<footer>"):
        assert marker in html, f"the spliced section disturbed {marker}"


def main() -> int:
    """Run the Arbiter analytics-section regression suite."""
    build_case = load_build_case()
    with tempfile.TemporaryDirectory(prefix="spotlight-arbiter-appendix-") as tmp_dir:
        tmp = Path(tmp_dir)
        case = build_case(tmp / "fragment")
        check_good_fixture(case)
        check_unusable_payloads(case)
        check_hostile_payload(case)
        check_no_editorial_status(case)
        check_report_byte_identity(tmp, build_case)
    print("arbiter appendix: OK - sections, escaping, degradation, and byte identity verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
