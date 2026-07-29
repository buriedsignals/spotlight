#!/usr/bin/env python3
"""Render the optional Arbiter analytics section of a Spotlight case report.

Spotlight's deterministic renderer (``scripts/render-report.py``) checks whether
a case used the Arbiter integration and, when it did, asks this script for the
HTML that renders that data. Everything Arbiter-specific — markup, CSS, and the
chart geometry — lives here rather than in the core renderer.

The input is the case's newest saved ``GET /topics/{id}/report`` response,
``{CASE_DIR}/research/arbiter-report-*.json``. Reading is offline and read-only;
nothing is fetched and nothing in the case is written.

The output is one self-contained HTML fragment on stdout: a ``<section>`` with
its own scoped ``<style>`` block, every class prefixed ``arb-``, no external
assets, no fonts, and no JavaScript. Empty stdout means there is nothing to
render, which is the normal outcome for a case that never used Arbiter.

Every value interpolated from the payload is length-clipped and HTML-escaped,
links render only for ``http(s)`` URLs, and any missing or malformed field drops
its own block instead of failing: a corrupt Arbiter file must never be able to
break a report render.

Usage:
  python3 integrations/arbiter/run_appendix.py --case-dir {CASE_DIR}
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from typing import Any


MAX_ACTORS = 10
MAX_COMMUNITIES = 8
MAX_COMMUNITY_MEMBERS = 6
MAX_THEME_ROWS = 24
MAX_THEME_VOICES = 4
MAX_CLAIMS = 12
MAX_STORY_LINES = 12

CLIP_TITLE = 160
CLIP_LABEL = 120
CLIP_NAME = 80
CLIP_CLAIM = 240
CLIP_STORY = 400
CLIP_URL = 300

# A spline through three points implies a continuity the data does not support,
# so short series render as bars instead of a line.
CHART_BAR_MAX_BUCKETS = 4
CHART_WIDTH = 720.0
CHART_HEIGHT = 250.0
CHART_PAD_LEFT = 60.0
CHART_PAD_RIGHT = 18.0
CHART_PAD_TOP = 34.0
CHART_PAD_BOTTOM = 36.0
CHART_SEGMENTS = 4

PAPER_HEX = "#FAF9F6"
CHART_HEX = "#2E5C8A"
CHART_PEAK_HEX = "#B23A3A"
GRID_HEX = "#D8D2C7"

SANS = 'system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif'
MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'

SUBTITLE = (
    "Social-media analytics for this case study, supplied by the Arbiter archive: "
    "how engagement moved over time, the most active voices, the narrative themes "
    "they drive, and the highest-engagement source posts behind them. Context from "
    "the source archive, reported separately from the editorial findings above."
)

APPENDIX_CSS = f"""
.arb-appendix{{margin:2.4em 0;print-color-adjust:exact;-webkit-print-color-adjust:exact}}
.arb-appendix .arb-kicker{{font-family:{SANS};font-weight:700;font-size:.66rem;letter-spacing:.12em;text-transform:uppercase;color:#8a7f70;margin-bottom:.3em}}
.arb-appendix .arb-title{{margin:0 0 .3em}}
.arb-appendix .arb-subtitle{{color:#6b6258;max-width:64ch}}
.arb-appendix .arb-sect{{margin:1.6em 0;break-inside:avoid}}
.arb-appendix h3{{margin:0 0 .2em;break-after:avoid}}
.arb-appendix .arb-hint{{font-family:{SANS};font-size:.8rem;color:#8a7f70;margin:.2em 0 .7em;break-after:avoid}}
.arb-appendix table{{width:100%;border-collapse:collapse;font-size:.86rem}}
.arb-appendix th{{font-family:{SANS};font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;color:#6E675D;text-align:left;border-bottom:1px solid {GRID_HEX};padding:4px 8px 5px}}
.arb-appendix td{{border-bottom:1px dashed {GRID_HEX};padding:6px 8px;vertical-align:top}}
.arb-appendix .arb-num{{text-align:right;font-family:{MONO};font-variant-numeric:tabular-nums;white-space:nowrap}}
.arb-appendix .arb-rank{{font-family:{MONO};font-size:.76rem;color:#6E675D;width:2.6em;text-align:right}}
.arb-appendix .arb-bar{{display:block;height:5px;margin-top:5px;background:rgba(0,0,0,.06);border-radius:2px}}
.arb-appendix .arb-bar-fill{{display:block;height:100%;background:{CHART_HEX};border-radius:2px;min-width:2px}}
.arb-appendix .arb-chart{{display:block;width:100%;height:auto;background:{PAPER_HEX};border:1px solid {GRID_HEX};border-radius:6px}}
.arb-appendix .arb-chart-meta{{font-family:{SANS};font-size:.85rem;color:#8a7f70;margin:.2em 0 .6em}}
.arb-appendix .arb-tick{{font-family:{SANS};font-size:11px;fill:#8a7f70}}
.arb-appendix .arb-peak-label{{font-family:{SANS}}}
.arb-appendix .arb-story-text{{font-family:{SANS};font-size:.85rem;color:#2a2620}}
.arb-appendix ul.arb-story{{font-family:{SANS};font-size:.85rem;color:#2a2620;padding-left:1.2em;margin:.4em 0}}
.arb-appendix ul.arb-story li{{margin:.35em 0}}
.arb-appendix .arb-theme-parent td{{font-weight:700}}
.arb-appendix .arb-theme-sub td:first-child{{padding-left:32px;font-weight:400}}
.arb-appendix .arb-voices{{color:#6E675D;font-style:italic;font-weight:400}}
.arb-appendix .arb-comm{{padding:9px 0 10px;border-bottom:1px dashed {GRID_HEX};break-inside:avoid}}
.arb-appendix .arb-comm-name{{font-weight:600;font-size:.95rem;color:#1a1815}}
.arb-appendix .arb-comm-scale{{display:flex;align-items:center;gap:8px;margin:6px 0 4px}}
.arb-appendix .arb-comm-track{{position:relative;display:inline-block;width:58%;height:5px;background:rgba(0,0,0,.06);border-radius:2px;flex:none}}
.arb-appendix .arb-comm-fill{{position:absolute;left:0;top:0;height:100%;background:{CHART_HEX};border-radius:2px;min-width:2px}}
.arb-appendix .arb-comm-eng{{font-family:{SANS};font-size:.76rem;color:#1a1815;font-variant-numeric:tabular-nums;white-space:nowrap}}
.arb-appendix .arb-comm-unit{{color:#8a7f70}}
.arb-appendix .arb-comm-meta{{font-family:{SANS};font-size:.72rem;color:#8a7f70;margin-bottom:3px}}
.arb-appendix .arb-comm-members{{font-family:{SANS};font-size:.74rem;line-height:1.5;color:#6b6258}}
.arb-appendix .arb-sep{{color:{GRID_HEX};padding:0 2px}}
.arb-appendix .arb-more{{color:#8a7f70;font-style:italic}}
.arb-appendix .arb-claims{{border-top:1px dashed {GRID_HEX}}}
.arb-appendix .arb-claim{{padding:7px 0 8px;border-bottom:1px dashed {GRID_HEX};break-inside:avoid}}
.arb-appendix .arb-claim-text{{font-size:.92rem;color:#1a1815;margin:0 0 3px}}
.arb-appendix .arb-claim-meta{{font-family:{SANS};font-size:.74rem;color:#8a7f70;margin:0}}
.arb-appendix .arb-claim-actor{{font-weight:700;color:#2a2620}}
.arb-appendix .arb-claim-link{{overflow-wrap:anywhere}}
@media print{{.arb-appendix .arb-sect{{break-inside:avoid}}}}
"""


def h(value: Any) -> str:
    """Escape untrusted text for either an HTML text or attribute context."""
    return html.escape("" if value is None else str(value), quote=True)


def clip(value: Any, limit: int) -> str:
    """Collapse untrusted payload text to one line of at most ``limit`` chars."""
    collapsed = " ".join(str(value or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(limit - 1, 0)].rstrip() + "…"


def as_list(value: Any) -> list[Any]:
    """Tolerate nulls and scalars where the payload should carry a list."""
    return value if isinstance(value, list) else []


def as_dicts(value: Any) -> list[dict[str, Any]]:
    """Only the object entries of a payload array, in order."""
    return [item for item in as_list(value) if isinstance(item, dict)]


def as_dict(value: Any) -> dict[str, Any]:
    """Tolerate nulls and scalars where the payload should carry an object."""
    return value if isinstance(value, dict) else {}


def as_num(value: Any) -> float:
    """Numeric tolerance: non-numbers count as zero, and ``True`` is not a magnitude."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def format_count(value: Any) -> str:
    """Thousands-separated integer for display (unknowns render ``0``)."""
    return f"{int(round(as_num(value))):,}"


def safe_url(value: Any) -> str:
    """Only pass through plain ``http(s)`` URLs; every other value renders empty."""
    url = clip(value, CLIP_URL)
    if not url.startswith(("http://", "https://")):
        return ""
    if re.search(r"""[\x00-\x20<>"'`\\]""", url):
        return ""
    return url


def parse_iso_date(value: str) -> date_type | None:
    """Leading ``YYYY-MM-DD`` of an ISO-ish stamp, or ``None`` when unparseable."""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return None
    try:
        return date_type(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def format_chart_date(value: str) -> str:
    """``YYYY-MM-DD`` as ``Jul 01`` for an axis label (falls back to the raw prefix)."""
    parsed = parse_iso_date(value)
    if parsed is None:
        return value[:10]
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[parsed.month - 1]} {parsed.day:02d}"


def bucket_axis_label(start: str, gap_days: int) -> str:
    """``Jul 06`` for a daily bucket, ``Jul 05 - Jul 11`` for a wider one."""
    parsed = parse_iso_date(start)
    if gap_days <= 1 or parsed is None:
        return format_chart_date(start)
    end = parsed + timedelta(days=gap_days - 1)
    return f"{format_chart_date(start)} - {format_chart_date(end.isoformat())}"


def nice_axis_top(peak: float) -> float:
    """Axis ceiling above ``peak`` that puts every gridline on a round number.

    Returns ``CHART_SEGMENTS`` times a 1/2/2.5/5×10^k step.
    """
    if peak <= 0:
        return float(CHART_SEGMENTS)
    raw_step = peak / CHART_SEGMENTS
    magnitude = 1.0
    while magnitude * 10 <= raw_step:
        magnitude *= 10
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step >= raw_step:
            return step * CHART_SEGMENTS
    return 10 * magnitude * CHART_SEGMENTS


def chart_series(timeline: dict[str, Any]) -> list[tuple[str, float]]:
    """``(date, interactions)`` buckets in payload order, dropping undated entries."""
    series: list[tuple[str, float]] = []
    for point in as_dicts(timeline.get("points")):
        stamp = clip(point.get("date"), 32)
        if stamp:
            series.append((stamp, max(0.0, as_num(point.get("interactions")))))
    return series


def median_gap_days(series: list[tuple[str, float]]) -> int:
    """Bucket width in days: the median gap between bucket starts, never below one."""
    gaps: list[int] = []
    for (start, _), (following, _) in zip(series, series[1:]):
        first, second = parse_iso_date(start), parse_iso_date(following)
        if first and second:
            gaps.append((second - first).days)
    if not gaps:
        return 1
    gaps.sort()
    return max(gaps[len(gaps) // 2], 1)


def peak_label_svg(x: float, baseline_y: float, value: float,
                   min_x: float, max_x: float) -> str:
    """``Peak · N`` label for the peak marker, clamped inside the plot box.

    An unexplained coloured dot is noise, so the peak marker always carries its
    value. ``baseline_y`` is the preferred text baseline and is clamped down to
    stay on canvas; ``min_x``/``max_x`` are edges the label may not cross.
    """
    label = f"Peak · {format_count(value)}"
    half = len(label) * 3.4
    anchor, text_x = "middle", x
    if x - half < min_x:
        anchor, text_x = "start", min_x
    elif x + half > max_x:
        anchor, text_x = "end", max_x
    return (
        f'<text class="arb-peak-label" x="{text_x:.1f}" y="{max(12.0, baseline_y):.1f}"'
        f' text-anchor="{anchor}" font-size="11" font-weight="700"'
        f' fill="{CHART_PEAK_HEX}">{h(label)}</text>'
    )


def engagement_chart_svg(timeline: dict[str, Any]) -> str:
    """Static interactions-over-time chart: bars for short series, a line above.

    Pure SVG with no script and no external asset, so it renders identically on
    screen and on paper. Returns "" when the timeline holds fewer than two
    dated buckets, since a single point is not a trend.
    """
    series = chart_series(timeline)
    if len(series) < 2:
        return ""

    plot_w = CHART_WIDTH - CHART_PAD_LEFT - CHART_PAD_RIGHT
    plot_h = CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM
    right_edge = CHART_WIDTH - CHART_PAD_RIGHT
    baseline = CHART_PAD_TOP + plot_h
    axis_top = nice_axis_top(max(value for _, value in series) or 1.0)
    peak_index = max(range(len(series)), key=lambda index: series[index][1])
    gap_days = median_gap_days(series)

    grid: list[str] = []
    for index in range(CHART_SEGMENTS + 1):
        value = axis_top * index / CHART_SEGMENTS
        y = baseline - (value / axis_top) * plot_h
        grid.append(
            f'<line x1="{CHART_PAD_LEFT:.1f}" y1="{y:.1f}" x2="{right_edge:.1f}"'
            f' y2="{y:.1f}" stroke="{GRID_HEX}" stroke-width="1" stroke-dasharray="3 4"/>'
            f'<text x="{CHART_PAD_LEFT - 8:.1f}" y="{y + 4:.1f}" text-anchor="end"'
            f' class="arb-tick">{h(format_count(value))}</text>'
        )

    open_svg = (
        f'<svg class="arb-chart" viewBox="0 0 {int(CHART_WIDTH)} {int(CHART_HEIGHT)}"'
        ' role="img" aria-label="Interactions over time">'
    )
    labels: list[str] = []

    if len(series) <= CHART_BAR_MAX_BUCKETS:
        slot = plot_w / len(series)
        bar_width = min(slot * 0.62, 110.0)
        bars: list[str] = []
        for index, (stamp, value) in enumerate(series):
            centre = CHART_PAD_LEFT + slot * (index + 0.5)
            bar_height = (value / axis_top) * plot_h
            bars.append(
                f'<rect x="{centre - bar_width / 2:.1f}" y="{baseline - bar_height:.1f}"'
                f' width="{bar_width:.1f}" height="{max(bar_height, 0.8):.1f}" rx="2"'
                f' fill="{CHART_HEX}"/>'
            )
            labels.append(
                f'<text x="{centre:.1f}" y="{CHART_HEIGHT - 12:.1f}" text-anchor="middle"'
                f' class="arb-tick">{h(bucket_axis_label(stamp, gap_days))}</text>'
            )
        peak_centre = CHART_PAD_LEFT + slot * (peak_index + 0.5)
        peak_top = baseline - (series[peak_index][1] / axis_top) * plot_h
        marker = (
            f'<circle cx="{peak_centre:.1f}" cy="{peak_top - 9:.1f}" r="4"'
            f' fill="{CHART_PEAK_HEX}" stroke="{PAPER_HEX}" stroke-width="1.6"/>'
        )
        return (
            open_svg + "".join(grid) + "".join(bars) + marker
            + peak_label_svg(peak_centre, peak_top - 19, series[peak_index][1],
                             CHART_PAD_LEFT, right_edge)
            + "".join(labels) + "</svg>"
        )

    step = plot_w / (len(series) - 1)
    coords = [
        (CHART_PAD_LEFT + index * step, baseline - (value / axis_top) * plot_h)
        for index, (_, value) in enumerate(series)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{line} {coords[-1][0]:.1f},{baseline:.1f} {coords[0][0]:.1f},{baseline:.1f}"

    stride = max(1, (len(series) + 5) // 6)
    for index, (stamp, _) in enumerate(series):
        if index % stride and index != len(series) - 1:
            continue
        anchor = "start" if index == 0 else "end" if index == len(series) - 1 else "middle"
        labels.append(
            f'<text x="{coords[index][0]:.1f}" y="{CHART_HEIGHT - 12:.1f}"'
            f' text-anchor="{anchor}" class="arb-tick">'
            f"{h(bucket_axis_label(stamp, gap_days))}</text>"
        )

    # Endpoint markers only: one dot per bucket on a long series is noise.
    dots = [
        f'<circle cx="{coords[index][0]:.1f}" cy="{coords[index][1]:.1f}" r="4"'
        f' fill="{PAPER_HEX}" stroke="{CHART_HEX}" stroke-width="2"/>'
        for index in dict.fromkeys((0, len(coords) - 1))
        if index != peak_index
    ]
    dots.append(
        f'<circle cx="{coords[peak_index][0]:.1f}" cy="{coords[peak_index][1]:.1f}"'
        f' r="5" fill="{CHART_PEAK_HEX}" stroke="{PAPER_HEX}" stroke-width="2"/>'
    )
    return (
        open_svg + "".join(grid)
        + f'<polygon points="{area}" fill="{CHART_HEX}" fill-opacity="0.12"/>'
        + f'<polyline points="{line}" fill="none" stroke="{CHART_HEX}"'
        ' stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        + "".join(dots)
        + peak_label_svg(coords[peak_index][0], coords[peak_index][1] - 12,
                         series[peak_index][1], CHART_PAD_LEFT, right_edge)
        + "".join(labels) + "</svg>"
    )


def story_html(timeline: dict[str, Any]) -> str:
    """"Story at a Glance" block from the timeline narrative, or "" when absent."""
    story = timeline.get("story")
    if not isinstance(story, str) or not story.strip():
        return ""
    bullets: list[str] = []
    paragraphs: list[str] = []
    for raw in story.splitlines()[: MAX_STORY_LINES * 3]:
        line = raw.strip()
        if not line:
            continue
        if line[0] in "•-*":
            bullets.append(clip(line.lstrip("•-* "), CLIP_STORY))
        else:
            paragraphs.append(clip(line, CLIP_STORY))
        if len(bullets) + len(paragraphs) >= MAX_STORY_LINES:
            break
    parts = ["<h3>The Story at a Glance</h3>"]
    parts += [f'<p class="arb-story-text">{h(item)}</p>' for item in paragraphs if item]
    kept = [item for item in bullets if item]
    if kept:
        parts.append(
            '<ul class="arb-story">'
            + "".join(f"<li>{h(item)}</li>" for item in kept)
            + "</ul>"
        )
    return "".join(parts) if len(parts) > 1 else ""


def engagement_html(timeline: dict[str, Any]) -> str:
    """Engagement block — totals line, static chart, story — or "" when neither draws."""
    chart = engagement_chart_svg(timeline)
    story = story_html(timeline)
    if not chart and not story:
        return ""
    series = chart_series(timeline)
    window = ""
    if series:
        window = (
            f" · {len(series)} interval(s) from "
            f"{format_chart_date(series[0][0])} to {format_chart_date(series[-1][0])}"
        )
    meta = (
        f"Total interactions: {format_count(timeline.get('total_interactions'))}"
        f" · Average: {format_count(timeline.get('average_interactions'))}"
        f" per interval{window}"
    )
    return (
        '<div class="arb-sect">'
        "<h3>Interactions over time</h3>"
        f'<p class="arb-chart-meta">{h(meta)}</p>'
        f"{chart}{story}</div>"
    )


def voices(value: Any, limit: int) -> str:
    """Up to ``limit`` actor names from a payload list, clipped and comma-joined."""
    names = [clip(name, CLIP_NAME) for name in as_list(value)[:limit]]
    return ", ".join(name for name in names if name)


def theme_rows(themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the theme tree into interaction-ranked parent and sub-theme rows.

    Rows carry ``label``, ``posts``, ``value``, ``voices`` and ``child``, ordered
    by parent interactions with each parent's sub-themes directly beneath it and
    capped at ``MAX_THEME_ROWS``.
    """
    parents: list[dict[str, Any]] = []
    for theme in themes:
        label = clip(theme.get("theme"), CLIP_LABEL)
        if not label:
            continue
        parents.append({
            "label": label,
            "posts": as_num(theme.get("post_count")),
            "value": as_num(theme.get("engagement_total")),
            "voices": voices(theme.get("top_actors"), MAX_THEME_VOICES),
            "child": False,
            "children": as_dicts(theme.get("children")),
        })
    parents.sort(key=lambda row: -row["value"])

    rows: list[dict[str, Any]] = []
    for parent in parents:
        if len(rows) >= MAX_THEME_ROWS:
            break
        children = []
        for child in parent.pop("children"):
            label = clip(child.get("theme"), CLIP_LABEL)
            if not label:
                continue
            children.append({
                "label": label,
                "posts": as_num(child.get("post_count")),
                "value": as_num(child.get("engagement_total")),
                "voices": voices(child.get("top_actors"), MAX_THEME_VOICES),
                "child": True,
            })
        children.sort(key=lambda row: -row["value"])
        rows.append(parent)
        rows.extend(children[: max(MAX_THEME_ROWS - len(rows), 0)])
    return rows[:MAX_THEME_ROWS]


def themes_html(themes: list[dict[str, Any]]) -> str:
    """Ranked theme table: posts, interactions with a magnitude bar, loudest voices.

    A bar length cannot encode who drives a theme, so every theme and sub-theme
    is listed with the voices behind it. Returns "" when no theme renders.
    """
    rows = theme_rows(themes)
    if not rows:
        return ""
    peak = max((row["value"] for row in rows if not row["child"]), default=0.0)
    rendered: list[str] = []
    rank = 0
    for row in rows:
        if row["child"]:
            rank_cell = '<td class="arb-rank"></td>'
        else:
            rank += 1
            rank_cell = f'<td class="arb-rank">{rank}</td>'
        share = row["value"] / peak if peak > 0 else 0.0
        fill = f"{max(min(share, 1.0), 0.02) * 100:.1f}"
        css = "arb-theme-sub" if row["child"] else "arb-theme-parent"
        rendered.append(
            f'<tr class="{css}">{rank_cell}'
            f'<td>{h(row["label"])}</td>'
            f'<td class="arb-num">{h(format_count(row["posts"]))}</td>'
            f'<td class="arb-num">{h(format_count(row["value"]))}'
            f'<span class="arb-bar"><span class="arb-bar-fill" style="width:{fill}%">'
            "</span></span></td>"
            f'<td class="arb-voices">{h(row["voices"]) or "—"}</td></tr>'
        )
    return (
        '<div class="arb-sect">'
        "<h3>Theme landscape</h3>"
        '<p class="arb-hint">Narrative themes ranked by their share of '
        "interactions, each followed by its sub-themes and the loudest voices "
        "behind it.</p>"
        '<table><thead><tr><th class="arb-rank">#</th><th>Theme / sub-theme</th>'
        '<th class="arb-num">Posts</th><th class="arb-num">Interactions</th>'
        "<th>Loudest voices</th></tr></thead>"
        f'<tbody>{"".join(rendered)}</tbody></table></div>'
    )


def actors_html(actors: list[dict[str, Any]]) -> str:
    """Top-actors table: rank, actor, posts, an interaction bar, dominant theme.

    The interaction cell carries a bar scaled to the largest value in the table,
    which turns a column of numbers into a ranked visual at no extra vertical
    cost. Returns "" when no actor renders.
    """
    totals = [as_num(as_dict(actor.get("engagement")).get("total_engagement"))
              for actor in actors]
    peak = max(totals, default=0.0)
    rows: list[str] = []
    for rank, (actor, total) in enumerate(zip(actors, totals), 1):
        name = clip(actor.get("actor"), CLIP_NAME)
        if not name:
            continue
        share = total / peak if peak > 0 else 0.0
        fill = f"{max(min(share, 1.0), 0.02) * 100:.1f}"
        posts = as_dict(actor.get("engagement")).get("total_posts")
        rows.append(
            f'<tr><td class="arb-rank">{rank}</td>'
            f"<td>{h(name)}</td>"
            f'<td class="arb-num">{h(format_count(posts))}</td>'
            f'<td class="arb-num">{h(format_count(total))}'
            f'<span class="arb-bar"><span class="arb-bar-fill" style="width:{fill}%">'
            "</span></span></td>"
            f'<td>{h(clip(actor.get("dominant_theme"), CLIP_LABEL)) or "—"}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<div class="arb-sect">'
        "<h3>Top actors</h3>"
        '<p class="arb-hint">The accounts driving the most interactions in this '
        "discourse, with the theme each is most active in.</p>"
        '<table><thead><tr><th class="arb-rank">#</th><th>Actor</th>'
        '<th class="arb-num">Posts</th><th class="arb-num">Interactions</th>'
        "<th>Dominant theme</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def communities_html(communities: list[dict[str, Any]]) -> str:
    """Ranked community list — name, interaction bar, size meta, member voices."""
    ranked = sorted(communities, key=lambda item: -as_num(item.get("total_engagement")))
    peak = max((as_num(item.get("total_engagement")) for item in ranked), default=0.0)
    bands: list[str] = []
    for community in ranked:
        name = clip(community.get("name"), CLIP_LABEL)
        if not name:
            continue
        total = as_num(community.get("total_engagement"))
        share = total / peak if peak > 0 else 0.0
        fill = f"{max(min(share, 1.0), 0.02) * 100:.1f}"
        members = [clip(member, CLIP_NAME) for member in
                   as_list(community.get("actors"))[:MAX_COMMUNITY_MEMBERS]]
        members = [member for member in members if member]
        members_html = ' <span class="arb-sep">·</span> '.join(h(m) for m in members)
        overflow = int(as_num(community.get("actor_count"))) - len(members)
        if overflow > 0:
            members_html += f' <span class="arb-more">+{overflow} more</span>'
        bands.append(
            '<div class="arb-comm">'
            f'<div class="arb-comm-name">{h(name)}</div>'
            '<div class="arb-comm-scale">'
            '<span class="arb-comm-track">'
            f'<span class="arb-comm-fill" style="width:{fill}%"></span></span>'
            f'<span class="arb-comm-eng"><b>{h(format_count(total))}</b>'
            ' <span class="arb-comm-unit">interactions</span></span></div>'
            f'<div class="arb-comm-meta">'
            f'{h(format_count(community.get("actor_count")))} voices'
            ' <span class="arb-sep">·</span> '
            f'{h(format_count(community.get("total_posts")))} posts</div>'
            f'<div class="arb-comm-members">{members_html}</div></div>'
        )
    if not bands:
        return ""
    return (
        '<div class="arb-sect">'
        "<h3>Communities</h3>"
        '<p class="arb-hint">Clusters of voices posting together — by platform '
        "group, or by the theme they share when no group structure exists. Bar "
        "length is each cluster's share of the largest cluster's interactions.</p>"
        f'<div class="arb-comm-list">{"".join(bands)}</div></div>'
    )


def claims_html(actors: list[dict[str, Any]]) -> str:
    """Highest-engagement source posts Arbiter recorded, as print-friendly cards.

    Flattens every actor's claims (the archive's source posts), ranks them by
    engagement, and renders the top ``MAX_CLAIMS`` as quoted cards with an
    actor/date/engagement/link meta line. Cards never split across page breaks.
    Takes the unsliced actor list so the ranking sees every claim.
    """
    claims: list[dict[str, Any]] = []
    for actor in actors:
        name = clip(actor.get("actor"), CLIP_NAME) or "—"
        for claim in as_dicts(actor.get("claims")):
            body = clip(claim.get("text"), CLIP_CLAIM)
            if not body:
                continue
            claims.append({
                "actor": name,
                "text": body,
                "url": safe_url(claim.get("url")),
                "published_at": clip(claim.get("published_at"), 32)[:10],
                "engagement": as_num(claim.get("engagement")),
            })
    if not claims:
        return ""
    claims.sort(key=lambda claim: -claim["engagement"])
    shown = claims[:MAX_CLAIMS]

    cards: list[str] = []
    for claim in shown:
        meta = [
            f'<span class="arb-claim-actor">{h(claim["actor"])}</span>',
            h(claim["published_at"] or "—"),
            f'{h(format_count(claim["engagement"]))} interactions',
        ]
        if claim["url"]:
            meta.append(
                f'<a class="arb-claim-link" href="{h(claim["url"])}" rel="noreferrer">'
                f'{h(claim["url"])}</a>'
            )
        cards.append(
            '<div class="arb-claim">'
            f'<p class="arb-claim-text">“{h(claim["text"])}”</p>'
            f'<p class="arb-claim-meta">{" · ".join(meta)}</p></div>'
        )
    hint = (
        f"The {len(shown)} highest-engagement source posts Arbiter recorded for "
        "this case study"
        + (f" (of {len(claims)} across the top actors)" if len(claims) > len(shown) else "")
        + ". Archive material for context, reported separately from the "
        "editorial findings above."
    )
    return (
        '<div class="arb-sect">'
        "<h3>Source posts recorded by Arbiter</h3>"
        f'<p class="arb-hint">{h(hint)}</p>'
        f'<div class="arb-claims">{"".join(cards)}</div></div>'
    )


def newest_report_file(case_dir: Path) -> Path | None:
    """Lexicographically last ``research/arbiter-report-*.json``, or ``None``.

    ``None`` means the case never used the Arbiter integration.
    """
    try:
        candidates = sorted(
            path for path in (case_dir / "research").glob("arbiter-report-*.json")
            if path.is_file()
        )
    except OSError:
        return None
    return candidates[-1] if candidates else None


def load_payload(case_dir: Path) -> dict[str, Any]:
    """Parsed Arbiter report payload for a case, or ``{}``.

    ``{}`` covers every unusable case: no saved report, an unreadable file, a
    file that is not JSON, and JSON that is not an object.
    """
    path = newest_report_file(case_dir)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def render_appendix(case_dir: Path) -> str:
    """Build the self-contained Arbiter analytics fragment for a case.

    Only the cross-platform (global) report renders; the per-platform
    breakdowns under ``platform_reports`` are deliberately not expanded, so the
    section stays one linear, printable read. Returns "" when the case has no
    usable Arbiter report or every block came back empty.
    """
    payload = load_payload(case_dir)
    if not payload:
        return ""

    claim_actors = as_dicts(payload.get("top_actors"))
    blocks = [
        engagement_html(as_dict(payload.get("engagement_timeline"))),
        themes_html(as_dicts(payload.get("themes"))),
        actors_html(claim_actors[:MAX_ACTORS]),
        communities_html(as_dicts(payload.get("communities"))[:MAX_COMMUNITIES]),
        claims_html(claim_actors),
    ]
    body = "".join(block for block in blocks if block)
    if not body:
        return ""

    title = (clip(payload.get("root_theme"), CLIP_TITLE)
             or clip(payload.get("title"), CLIP_TITLE)
             or "Case-study analytics")
    return (
        '<section class="arb-appendix" aria-label="Arbiter case-study analytics">'
        f"<style>{APPENDIX_CSS.strip()}</style>"
        '<div class="arb-kicker">Case-study analytics · data from Arbiter</div>'
        f'<h2 class="arb-title">{h(title)}</h2>'
        f'<p class="arb-subtitle">{h(SUBTITLE)}</p>'
        f"{body}"
        "</section>"
    )


def main() -> int:
    """Print the Arbiter analytics fragment for a case directory.

    Exits ``0`` always, including when nothing renders: an unusable Arbiter file
    yields empty stdout and a one-line note on stderr rather than a failure that
    would take the whole report render down with it.
    """
    parser = argparse.ArgumentParser(
        description="Render the Arbiter analytics section of a Spotlight case report."
    )
    parser.add_argument("--case-dir", required=True, help="the case directory to read")
    args = parser.parse_args()

    case_dir = Path(args.case_dir)
    try:
        fragment = render_appendix(case_dir)
    except Exception as exc:  # noqa: BLE001 - rendering must never fail a report
        print(f"note: arbiter appendix skipped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 0
    if fragment:
        sys.stdout.write(fragment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
