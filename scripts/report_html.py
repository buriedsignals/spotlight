"""Reader layout for Spotlight's ``report.html``.

``render-report.py`` owns the evidence: verdicts, confidence caps, source
records and compiled diagrams. This module only lays those records out as one
page: a summary with counts, the diagrams, a findings table whose rows open
onto their evidence, the limitations, the methodology and a source index.

Every case-controlled string is escaped here. The page names no case-local
path and none of the fact-checker's working notes; those stay in
``findings-report.md`` and ``evidence-map.json``. The output is a pure
function of its inputs, so the same case renders the same bytes.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# The Spotlight mark, as drawn by the review app (harness/review/shell.mjs).
LOGO_PATH = (
    "M6 2h4v4h-4zM10 2h4v4h-4zM14 2h4v4h-4zM6 6h4v4h-4zM6 10h4v4h-4zM14 10h4v4h-4z"
    "M14 14h4v4h-4zM6 18h4v4h-4zM10 18h4v4h-4zM14 18h4v4h-4z"
)
POSITIVE = {"verified", "partially_verified"}
FULL_TEXT_ACCESS = {"full_text", "open_access", "archive_copy"}
ACCESS_LABEL = {
    "full_text": "read in full",
    "open_access": "read in full",
    "archive_copy": "archived copy read",
    "abstract_only": "abstract or snippet only",
    "inaccessible": "not accessible",
}
# Order of the verdict bars and filters; a status shows only when a finding has it.
VERDICT_ORDER = (
    "verified", "partially_verified", "unverified", "disputed", "false",
    "mischaracterized", "not_checkable",
)
BASIS_LEGEND = {
    "record": "public records",
    "reported": "reported",
    "inferred": "inferred, not proven",
}


@dataclass(frozen=True)
class ReportPage:
    """Everything the page shows, already resolved by the renderer."""

    case_name: str
    language: str
    title: str
    deck: str
    report_date: str
    cycle: str
    records: list[dict[str, Any]]
    verdict_labels: dict[str, str]
    sections: list[tuple[str, list[str]]]
    framing_ids: list[str]
    diagrams: list[dict[str, Any]]
    caveats: list[dict[str, Any]]
    next_steps: list[dict[str, Any]]
    directions: list[dict[str, Any]]
    limitations: list[str]
    ai_notice: str
    local_warning: str
    hashes: dict[str, str]
    css: str
    appendix: str = ""
    mermaid_url: str = ""
    elk_url: str = ""
    anchors: dict[str, str] = field(default_factory=dict)


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def one_line(value: Any, limit: int = 160) -> str:
    label = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def finding_anchor(index: int, finding_id: str) -> str:
    """Stable anchor for a finding row; case IDs never become raw HTML ids."""
    return f"finding-{index}-{hashlib.sha256(finding_id.encode()).hexdigest()[:8]}"


def host(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.") or url


def html_source(source: dict[str, str]) -> str:
    """A source as readers see it. report.html is for publication, so it names
    no case-local path, and the fact-checker's descriptions (its working notes
    about its own downloads) are replaced by the source's host. The paths and
    descriptions stay in findings-report.md and evidence-map.json."""
    url = source.get("url") or source.get("archive_url")
    if source.get("origin") == "finding" and source.get("description"):
        label = source["description"]
    elif url:
        label = host(url)
    else:
        label = "Document held in the case file"
    if url:
        return f'<a href="{h(url)}" rel="noreferrer">{h(label)}</a>'
    return h(label)


def verdict_label(page: ReportPage, status: str) -> str:
    return page.verdict_labels.get(status, status.replace("_", " ").title())


def pill_verdict(page: ReportPage, status: str) -> str:
    return f'<span class="pill v-{h(status)}">{h(verdict_label(page, status))}</span>'


def pill_confidence(confidence: str) -> str:
    return f'<span class="tag conf-{h(confidence)}">{h(confidence.title())}</span>'


def evidence_reads(record: dict[str, Any]) -> tuple[int, int]:
    """(read in full, all) over the fact-checker's supporting evidence."""
    items = [entry for check in record["verdict"]["checks"] for entry in check["evidence_for"]]
    full = sum(1 for entry in items if str(entry.get("access_method") or "") in FULL_TEXT_ACCESS)
    return full, len(items)


def public_url(source: dict[str, str]) -> str:
    return source.get("url") or source.get("archive_url") or ""


def source_index(page: ReportPage) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    """Number every public source once, in finding order, with the findings citing it."""
    entries: list[dict[str, Any]] = []
    by_url: dict[str, dict[str, Any]] = {}
    per_finding: dict[str, list[int]] = {}
    for record in page.records:
        numbers: list[int] = []
        for source in record["sources"]:
            url = public_url(source)
            if not url:
                continue
            entry = by_url.get(url)
            if entry is None:
                entry = {"n": len(entries) + 1, "source": source, "findings": [], "access": set()}
                entries.append(entry)
                by_url[url] = entry
            if record["id"] not in entry["findings"]:
                entry["findings"].append(record["id"])
            if source.get("access_method"):
                entry["access"].add(source["access_method"])
            if entry["n"] not in numbers:
                numbers.append(entry["n"])
        per_finding[record["id"]] = numbers
    return entries, per_finding


def finding_links(page: ReportPage, finding_ids: list[str]) -> str:
    return " ".join(
        f'<a href="#{h(page.anchors[fid])}">{h(fid)}</a>' for fid in finding_ids if fid in page.anchors
    )


# ───── page parts ─────

def masthead(page: ReportPage) -> str:
    case_meta = " · ".join(part for part in ("Investigation report", page.case_name, page.report_date) if part)
    return f'''<header class="masthead">
  <span class="brand"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="{LOGO_PATH}"/></svg>Spotlight</span>
  <span class="byline">by Buried Signals</span>
  <span class="case">{h(case_meta)}</span>
</header>'''


def hero(page: ReportPage, entries: list[dict[str, Any]]) -> str:
    counts = verdict_counts(page)
    total = len(page.records)
    verified = counts.get("verified", 0)
    open_count = sum(n for status, n in counts.items() if status not in POSITIVE)
    full = sum(evidence_reads(record)[0] for record in page.records)
    items = sum(evidence_reads(record)[1] for record in page.records)
    meta = []
    if page.report_date:
        meta.append(f"<span><b>Investigated</b> {h(page.report_date)}</span>")
    if page.cycle:
        meta.append(f"<span><b>Cycles</b> {h(page.cycle)}</span>")
    meta.append("<span><b>Fact-check</b> a separate AI agent checked every finding</span>")
    local = f'<p class="warning">{h(page.local_warning)}</p>' if page.local_warning else ""
    read_stat = (
        f'<div class="stat"><b>{full} of {items}</b><span>Evidence items read in full</span></div>'
        if items else ""
    )
    return f'''<header class="hero">
    <p class="kicker">Spotlight investigation</p>
    <h1>{h(page.title)}</h1>
    <p class="deck">{h(page.deck)}</p>
    <div class="meta">{"".join(meta)}</div>
    <section class="honesty" aria-label="AI assistance notice">{local}<p><strong>AI assistance notice:</strong> {h(page.ai_notice)}</p><p>A model wrote the framing and the summaries. Code placed every verdict and confidence beside the finding it belongs to; it does not check that the prose follows from the evidence.</p></section>
    <div class="stats">
      <div class="stat"><b>{total}</b><span>Findings</span></div>
      <div class="stat"><b>{verified}</b><span>Verified</span></div>
      <div class="stat"><b>{open_count}</b><span>Not verified</span></div>
      <div class="stat"><b>{len(entries)}</b><span>Public sources</span></div>
      {read_stat}
    </div>
  </header>'''


def verdict_counts(page: ReportPage) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in page.records:
        status = record["verdict"]["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def contents(page: ReportPage) -> str:
    items = [("summary", "Summary")]
    if page.diagrams:
        items.append(("diagrams", "Diagrams"))
    items += [("findings", "Findings"), ("caveats", "Limitations"), ("method", "Methodology"),
              ("sources", "Source index")]
    links = "".join(f'<li><a href="#{anchor}">{h(label)}</a></li>' for anchor, label in items)
    return f'<nav class="toc" aria-label="Contents"><ol>{links}</ol></nav>'


def summary(page: ReportPage) -> str:
    verdict_by_id = {record["id"]: record for record in page.records}
    framing = "".join(
        f'<li><a href="#{h(page.anchors[fid])}">{h(verdict_by_id[fid]["treatment"].get("headline"))}</a>'
        f'{pill_verdict(page, verdict_by_id[fid]["verdict"]["status"])}</li>'
        for fid in page.framing_ids if fid in verdict_by_id
    )
    counts = verdict_counts(page)
    peak = max(counts.values()) if counts else 1
    bars = "".join(
        f'<div class="bar"><span class="blabel">{h(verdict_label(page, status))}</span>'
        f'<span class="btrack"><span class="bfill v-{h(status)}" style="width:{counts[status] / peak * 100:.1f}%"></span></span>'
        f'<span class="bval">{counts[status]}</span></div>'
        for status in VERDICT_ORDER if counts.get(status)
    )
    framing_block = (
        f'<p class="note">The report is framed on these findings:</p><ul class="framing">{framing}</ul>'
        if framing else ""
    )
    return f'''<section id="summary">
        <p class="kicker">Summary</p>
        <h2>What the investigation found</h2>
        {framing_block}
        <div class="panel"><h3>Fact-check verdicts</h3>{bars}<p class="src">Independent verdicts across all {len(page.records)} findings. A finding that is not verified is shown at low confidence.</p></div>
      </section>'''


def diagram_section(page: ReportPage) -> str:
    if not page.diagrams:
        return ""
    figures = []
    for diagram in page.diagrams:
        diagram_id = h(diagram["id"])
        title_id = f"diagram-{diagram_id}-title"
        description_id = f"diagram-{diagram_id}-description"
        links = finding_links(page, diagram["finding_ids"])
        if diagram["connections"]:
            detail_heading = "Connections shown"
            details = "".join(
                f"<li><strong>{h(one_line(source))}</strong> <span aria-hidden=\"true\">→</span> "
                f"{h(one_line(relationship, 96))} <span aria-hidden=\"true\">→</span> "
                f"<strong>{h(one_line(target))}</strong>"
                f"{f' <span class=basis-note>({h(BASIS_LEGEND[basis])})</span>' if basis in BASIS_LEGEND else ''}</li>"
                for source, target, relationship, basis in diagram["connection_rows"]
            )
        else:
            detail_heading = "Data shown"
            details = "".join(
                f"<li><strong>{h(label)}</strong> — {h(detail)}</li>"
                for label, detail in diagram.get("data_points", [])
            )
        legend = "".join(
            f'<span class="legend-item"><span class="legend-key key-{h(role)}" aria-hidden="true"></span>{h(label)}</span>'
            for role, label in diagram["legend"]
        )
        figures.append(f'''
        <figure class="report-diagram" id="diagram-{diagram_id}" aria-labelledby="{title_id}" aria-describedby="{description_id}">
          <p class="kicker">Diagram</p>
          <h3 id="{title_id}">{h(diagram["title"])}</h3>
          <figcaption id="{description_id}">{h(diagram["caption"])}</figcaption>
          <p class="note">Supporting findings: {links or "none recorded"} · drag to pan, ctrl or ⌘ and scroll to zoom.</p>
          <div class="mermaid-wrap">
            <div class="zoom-controls" role="group" aria-label="Diagram controls">
              <button type="button" onclick="spotlightDiagramZoom(this, 1.2)" aria-label="Zoom diagram in">+</button>
              <button type="button" onclick="spotlightDiagramZoom(this, 0.8)" aria-label="Zoom diagram out">−</button>
              <button type="button" onclick="spotlightDiagramFit(this)" aria-label="Fit diagram to frame">↻</button>
              <button type="button" onclick="spotlightDiagramOpen(this)" aria-label="Open diagram full size">⛶</button>
            </div>
            <pre class="mermaid" aria-label="{h(diagram["title"])}">{h(diagram["source"])}</pre>
          </div>
          <div class="legend-row" aria-label="Diagram legend">{legend}</div>
          <details class="diagram-connections"><summary>{detail_heading}</summary><ul>{details}</ul></details>
        </figure>''')
    return ('<section class="report-diagrams" id="diagrams" aria-label="Diagrams">'
            '<p class="kicker">Diagrams</p><h2>How the pieces connect</h2>'
            + "".join(figures) + "\n      </section>")


def finding_row(page: ReportPage, record: dict[str, Any], section_index: int,
                source_numbers: list[int], entries: list[dict[str, Any]]) -> str:
    fid, finding, treatment, verdict = record["id"], record["finding"], record["treatment"], record["verdict"]
    status = verdict["status"]
    full, items = evidence_reads(record)
    basis = (f'<span class="basis">{full} of {items} read in full</span>' if items
             else '<span class="basis">no supporting evidence recorded</span>')
    chips = "".join(
        f'<a class="chip" href="#source-{n}">{h(host(public_url(entries[n - 1]["source"])))}</a>'
        for n in source_numbers[:3]
    )
    if len(source_numbers) > 3:
        chips += f'<span class="chip more">+{len(source_numbers) - 3}</span>'
    if not chips:
        chips = '<span class="none">case file only</span>' if record["sources"] else '<span class="none">none</span>'
    lede = f'<span class="lede">{h(treatment.get("summary"))}</span>' if str(treatment.get("summary") or "").strip() else ""
    why = (f'<p class="why"><strong>Why it matters:</strong> {h(treatment.get("why_it_matters"))}</p>'
           if str(treatment.get("why_it_matters") or "").strip() else "")
    evidence = " ".join(str(item) for item in (finding.get("evidence") if isinstance(finding.get("evidence"), list)
                                               else [finding.get("evidence")]) if item)
    evidence_html = f"<p><strong>Recorded evidence:</strong> {h(evidence)}</p>" if evidence.strip() else ""
    assessment = verdict["assessment"] or "No fact-check narrative was recorded."
    quotations = "".join(
        '<blockquote class="source-expression">'
        f'<p>“{html.escape(expression.get("text", ""), quote=True)}”</p>'
        + (f'<footer>— {h(expression.get("attribution"))}</footer>' if str(expression.get("attribution") or "").strip() else "")
        + "</blockquote>"
        for expression in record["quotes"]
    )
    source_items = []
    for source in record["sources"]:
        access = ACCESS_LABEL.get(source.get("access_method", ""), "")
        suffix = f' <span class="access">· {h(access)}</span>' if access else ""
        source_items.append(f"<li>{html_source(source)}{suffix}</li>")
    sources_html = "".join(dict.fromkeys(source_items)) or "<li>No accessible source URL or case-local evidence file was recorded.</li>"
    return f'''
          <details class="finding" id="{h(page.anchors[fid])}" data-verdict="{h(status)}" data-section="{section_index}">
            <summary>
              <span class="c-id">{h(fid)}</span>
              <span class="c-finding"><strong>{h(treatment.get("headline"))}</strong>{lede}</span>
              <span class="c-conf">{pill_confidence(verdict["confidence"])}</span>
              <span class="c-verdict">{pill_verdict(page, status)}{basis}</span>
              <span class="c-src">{chips}</span>
            </summary>
            <div class="fdetail">
              <div class="fmain">
                {why}
                {evidence_html}
                <p><strong>Independent fact-check:</strong> {h(assessment)}</p>
                {quotations}
                <div class="path" aria-label="How we got here">
                  <span>Structured claim · <code>data/findings.json</code> · {h(fid)}</span>
                  <span>{len(record["sources"])} source record(s) kept</span>
                  <span>Fact-check · <code>data/fact-check.json</code> · {h(verdict_label(page, status))} · {h(verdict["confidence"].title())}</span>
                </div>
              </div>
              <div class="faside">
                <h4>Claim as fact-checked</h4>
                <p class="claim">{h(finding.get("claim"))}</p>
                <h4>Sources</h4>
                <ul class="slist">{sources_html}</ul>
              </div>
            </div>
          </details>'''


def findings_table(page: ReportPage, entries: list[dict[str, Any]], per_finding: dict[str, list[int]]) -> str:
    records = {record["id"]: record for record in page.records}
    counts = verdict_counts(page)
    groups = []
    for index, (label, ids) in enumerate(page.sections, 1):
        tally: dict[str, int] = {}
        for fid in ids:
            status = records[fid]["verdict"]["status"]
            tally[status] = tally.get(status, 0) + 1
        meta = " · ".join(f"{tally[s]} {verdict_label(page, s).lower()}" for s in VERDICT_ORDER if tally.get(s))
        rows = "".join(finding_row(page, records[fid], index, per_finding.get(fid, []), entries) for fid in ids)
        heading = (f'<div class="ghead"><h3>{h(label)}</h3><span>{h(meta)}</span></div>'
                   if len(page.sections) > 1 else "")
        groups.append(f'<div class="fgroup" data-section="{index}">{heading}{rows}\n        </div>')
    verdict_filters = "".join(
        f'<button type="button" class="fbtn" data-filter="verdict" data-value="{h(status)}" aria-pressed="false">'
        f'{h(verdict_label(page, status))} <span>{counts[status]}</span></button>'
        for status in VERDICT_ORDER if counts.get(status)
    )
    section_filters = "".join(
        f'<button type="button" class="fbtn" data-filter="section" data-value="{index}" aria-pressed="false">{h(label)}</button>'
        for index, (label, _ids) in enumerate(page.sections, 1)
    ) if len(page.sections) > 1 else ""
    section_group = (
        '<div class="filters" role="group" aria-label="Filter by theme">'
        '<button type="button" class="fbtn" data-filter="section" data-value="" aria-pressed="true">All themes</button>'
        f'{section_filters}</div>' if section_filters else ""
    )
    return f'''<section id="findings">
        <p class="kicker">Findings</p>
        <h2>All {len(page.records)} findings, with verdicts and sources</h2>
        <dl class="key">
          <div><dt>Verdict</dt><dd>A second AI agent re-sourced each claim. <b>Verified</b> means it found supporting evidence it could read; <b>unverified</b> means it could not confirm the claim; <b>disputed</b> means credible evidence points the other way.</dd></div>
          <div><dt>Confidence</dt><dd>The lowest of the investigator's and the fact-checker's ratings and any recorded cap. Anything not verified is shown at low confidence.</dd></div>
          <div><dt>Read in full</dt><dd>How many of the fact-checker's supporting sources it read in full rather than from a snippet or abstract.</dd></div>
        </dl>
        <p class="note">Open a row for the evidence, the exact claim checked and every source.</p>
        <div class="filters" role="group" aria-label="Filter by verdict"><button type="button" class="fbtn" data-filter="verdict" data-value="" aria-pressed="true">All verdicts</button>{verdict_filters}</div>
        {section_group}
        <div class="ftable">
          <div class="thead" aria-hidden="true"><span>ID</span><span>Finding</span><span>Confidence</span><span>Fact-check</span><span>Sources</span></div>
          {"".join(groups)}
        </div>
      </section>'''


def editorial_item(page: ReportPage, item: dict[str, Any]) -> str:
    refs = [str(ref) for ref in item.get("finding_ids", [])]
    return f'{h(item.get("text"))} <span class="refs">{finding_links(page, refs)}</span>'


def limitations(page: ReportPage) -> str:
    caveats = "".join(f"<li>{editorial_item(page, item)}</li>" for item in page.caveats)
    planned = "".join(f"<li>{h(item)}</li>" for item in page.limitations)
    caveat_list = caveats or "<li>No editorial caveat was recorded.</li>"
    planned_block = (f'<h3 class="kicker">Limitations set at planning</h3><ul class="plain">{planned}</ul>'
                     if planned else "")
    next_items = "".join(f"<li>{editorial_item(page, item)}</li>" for item in page.next_steps)
    next_block = (f'<h3 class="kicker">Next reporting steps</h3><ul class="plain">{next_items}</ul>'
                  if next_items else "")
    return f'''<section id="caveats">
        <p class="kicker">Limitations</p>
        <h2>What this record cannot show</h2>
        <ul class="plain">{caveat_list}</ul>
        {planned_block}
        <section id="next">{next_block}</section>
      </section>'''


def methodology(page: ReportPage) -> str:
    rows = []
    for index, direction in enumerate(page.directions, 1):
        questions = [str(q) for q in direction.get("questions", []) if str(q).strip()] if isinstance(direction.get("questions"), list) else []
        steps = [
            f'{h(step.get("action"))}' + (f' <code>{h(step.get("tool"))}</code>' if step.get("tool") else "")
            for step in direction.get("steps", []) if isinstance(step, dict) and str(step.get("action") or "").strip()
        ]
        detail = "".join(f"<li>{h(q)}</li>" for q in questions) + "".join(f"<li>{s}</li>" for s in steps)
        rows.append(
            f'<li><strong>{h(direction.get("direction") or f"Direction {index}")}</strong>'
            f'<ul>{detail or "<li>No structured steps recorded.</li>"}</ul></li>'
        )
    directions = "".join(rows) or "<li>No structured methodology steps were recorded.</li>"
    return f'''<section id="method">
        <p class="kicker">Methodology</p>
        <h2>How the investigation was carried out</h2>
        <p>Spotlight planned the investigation from the brief, ran research cycles, and sent every finding to a separate fact-checking agent before any verdict was recorded. Report confidence is computed from those verdicts and their caps; it is not written during rendering.</p>
        <ul class="method">{directions}</ul>
      </section>'''


def sources(entries: list[dict[str, Any]], page: ReportPage) -> str:
    rows = "".join(
        f'<tr id="source-{entry["n"]}"><td class="num">{entry["n"]}</td>'
        f'<td><a href="{h(public_url(entry["source"]))}" rel="noreferrer">{h(host(public_url(entry["source"])))}</a>'
        f'<span class="surl">{h(public_url(entry["source"]))}</span></td>'
        f'<td>{h(", ".join(sorted(ACCESS_LABEL.get(a, a) for a in entry["access"])) or "—")}</td>'
        f'<td class="refs">{finding_links(page, entry["findings"])}</td></tr>'
        for entry in entries
    )
    body = (f'<table class="sources-index"><thead><tr><th>#</th><th>Source</th><th>Access</th><th>Cited in</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>') if rows else "<p>No public source URL was recorded.</p>"
    return f'''<section id="sources">
        <p class="kicker">Source index</p>
        <h2>{len(entries)} public sources</h2>
        {body}
      </section>'''


def footer(page: ReportPage) -> str:
    badges = "".join(f"<span>{h(path)} · sha256:{h(digest[:12])}</span>" for path, digest in page.hashes.items())
    return f'''<footer><p><strong>Deliverables:</strong> <code>findings-report.md</code>, <code>report.html</code>, <code>evidence-map.json</code>.</p><p class="databases">{badges}</p></footer>'''


PAGE_SCRIPT = '''
<script>
  (function () {
    var state = { verdict: "", section: "" };
    function apply() {
      document.querySelectorAll("details.finding").forEach(function (row) {
        var show = (!state.verdict || row.dataset.verdict === state.verdict) &&
                   (!state.section || row.dataset.section === state.section);
        row.hidden = !show;
      });
      document.querySelectorAll(".fgroup").forEach(function (group) {
        group.hidden = !group.querySelector("details.finding:not([hidden])");
      });
    }
    document.querySelectorAll(".fbtn").forEach(function (button) {
      button.addEventListener("click", function () {
        state[button.dataset.filter] = button.dataset.value;
        button.parentElement.querySelectorAll(".fbtn").forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        apply();
      });
    });
    function openTarget() {
      var row = location.hash && document.getElementById(location.hash.slice(1));
      if (row && row.tagName === "DETAILS") { row.hidden = false; row.open = true; }
    }
    window.addEventListener("hashchange", openTarget);
    window.addEventListener("beforeprint", function () {
      document.querySelectorAll("details.finding").forEach(function (row) { row.open = true; });
    });
    openTarget();
  })();
</script>'''


def diagram_runtime(page: ReportPage) -> str:
    """Pinned Mermaid runtime, emitted only when the report has diagrams.

    Kept from report-drafting/references/interactive-diagrams.md: render after
    fonts load, natural-size SVG, CSS zoom rather than transform scale. Added:
    one render per diagram with its own id, label wrapping wide enough that
    measured and drawn labels agree, and a fit to the union of the SVG and its
    labels so nothing is clipped at the default view.
    """
    if not page.diagrams:
        return ""
    return f'''
<script type="module">
  import mermaid from "{page.mermaid_url}";
  import elkLayouts from "{page.elk_url}";

  mermaid.registerLayoutLoaders(elkLayouts);
  mermaid.initialize({{
    startOnLoad: false,
    theme: "base",
    layout: "elk",
    securityLevel: "strict",
    flowchart: {{ useMaxWidth: false, padding: 14, wrappingWidth: 420 }},
    themeVariables: {{
      fontSize: "15px",
      fontFamily: "Inter, Helvetica Neue, sans-serif",
      primaryColor: "#ffffff",
      primaryBorderColor: "#57503f",
      primaryTextColor: "#14110b",
      lineColor: "#6b6258",
      clusterBkg: "#fbf8f1",
      clusterBorder: "#d4cdc0",
      edgeLabelBackground: "#fbf8f1"
    }}
  }});

  try {{
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    // Render one source at a time with its own id: Mermaid's batch runner can
    // give two diagrams the same id and draw both into one canvas.
    for (const [index, node] of Array.from(document.querySelectorAll(".mermaid")).entries()) {{
      const result = await mermaid.render("spotlight-mermaid-" + index, node.textContent || "");
      node.innerHTML = result.svg;
      if (result.bindFunctions) result.bindFunctions(node);
    }}
    document.querySelectorAll(".mermaid-wrap").forEach(spotlightDiagramFitWrap);
    window.addEventListener("resize", () => document.querySelectorAll(".mermaid-wrap").forEach(spotlightDiagramFitWrap));
    if (location.hash) {{ const target = document.getElementById(location.hash.slice(1)); if (target) target.scrollIntoView(); }}
  }} catch (error) {{
    console.warn("Spotlight diagram could not render", error);
  }}
</script>
<script>
  function spotlightDiagramApply(diagram) {{
    diagram.style.zoom = diagram.dataset.zoom || "1";
    diagram.style.transform = "translate(" + (diagram.dataset.tx || 0) + "px," + (diagram.dataset.ty || 0) + "px)";
  }}
  // Fit the drawing to the frame width (and at most 75% of the window height),
  // measuring labels too: a label can overflow the box Mermaid measured for it.
  function spotlightDiagramFitWrap(wrap) {{
    const diagram = wrap.querySelector(".mermaid"), svg = diagram && diagram.querySelector("svg");
    if (!svg) return;
    diagram.style.zoom = "1"; diagram.style.transform = ""; wrap.style.height = "";
    const outer = svg.getBoundingClientRect();
    let left = outer.left, top = outer.top, right = outer.right, bottom = outer.bottom;
    svg.querySelectorAll(".nodeLabel, .edgeLabel, .cluster-label").forEach((label) => {{
      const box = label.getBoundingClientRect(); if (!box.width) return;
      left = Math.min(left, box.left); top = Math.min(top, box.top);
      right = Math.max(right, box.right); bottom = Math.max(bottom, box.bottom);
    }});
    const style = getComputedStyle(wrap);
    const padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const width = right - left, height = bottom - top;
    const zoom = Math.min(1.1, (wrap.clientWidth - padX) / width, (Math.min(window.innerHeight * 0.75, 760) - padY) / height);
    diagram.dataset.zoom = zoom.toString();
    diagram.dataset.tx = (((outer.left - left) - (right - outer.right)) / 2).toString();
    diagram.dataset.ty = "0";
    spotlightDiagramApply(diagram);
    wrap.style.height = Math.ceil(height * zoom + padY + 2) + "px";
    wrap.classList.add("is-rendered");
  }}
  function spotlightDiagramTarget(button) {{
    const wrap = button.closest(".mermaid-wrap");
    return {{ wrap: wrap, diagram: wrap && wrap.querySelector(".mermaid") }};
  }}
  function spotlightDiagramZoom(button, factor) {{
    const target = spotlightDiagramTarget(button); if (!target.diagram) return;
    const zoom = parseFloat(target.diagram.dataset.zoom || "1");
    target.diagram.dataset.zoom = Math.min(Math.max(zoom * factor, 0.25), 4).toString();
    spotlightDiagramApply(target.diagram);
  }}
  function spotlightDiagramFit(button) {{
    const target = spotlightDiagramTarget(button); if (target.wrap) spotlightDiagramFitWrap(target.wrap);
  }}
  function spotlightDiagramOpen(button) {{
    const target = spotlightDiagramTarget(button);
    const svg = target.diagram && target.diagram.querySelector("svg"); if (!svg) return;
    const copy = svg.cloneNode(true); copy.style.zoom = ""; copy.style.transform = "";
    const page = "<!doctype html><html><head><meta charset=\\"utf-8\\"><title>Diagram</title>" +
      "<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#ffffff;padding:32px;box-sizing:border-box}}svg{{max-width:100%;max-height:92vh}}</style>" +
      "</head><body>" + copy.outerHTML + "</body></html>";
    window.open(URL.createObjectURL(new Blob([page], {{ type: "text/html" }})), "_blank", "noopener");
  }}
  document.querySelectorAll(".mermaid-wrap").forEach((wrap) => {{
    let startX = 0, startY = 0, startTX = 0, startTY = 0;
    wrap.addEventListener("wheel", (event) => {{
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const diagram = wrap.querySelector(".mermaid"); if (!diagram) return;
      const zoom = parseFloat(diagram.dataset.zoom || "1") * (event.deltaY < 0 ? 1.1 : 0.9);
      diagram.dataset.zoom = Math.min(Math.max(zoom, 0.25), 4).toString();
      spotlightDiagramApply(diagram);
    }}, {{ passive: false }});
    wrap.addEventListener("pointerdown", (event) => {{
      if (!wrap.classList.contains("is-rendered") || event.target.closest(".zoom-controls")) return;
      const diagram = wrap.querySelector(".mermaid"); if (!diagram) return;
      startX = event.clientX; startY = event.clientY;
      startTX = parseFloat(diagram.dataset.tx || "0"); startTY = parseFloat(diagram.dataset.ty || "0");
      wrap.setPointerCapture(event.pointerId); wrap.classList.add("is-panning");
    }});
    wrap.addEventListener("pointermove", (event) => {{
      if (!wrap.classList.contains("is-panning")) return;
      const diagram = wrap.querySelector(".mermaid"); if (!diagram) return;
      const zoom = parseFloat(diagram.dataset.zoom || "1");
      diagram.dataset.tx = (startTX + (event.clientX - startX) / zoom).toString();
      diagram.dataset.ty = (startTY + (event.clientY - startY) / zoom).toString();
      spotlightDiagramApply(diagram);
    }});
    wrap.addEventListener("pointerup", () => wrap.classList.remove("is-panning"));
    wrap.addEventListener("pointercancel", () => wrap.classList.remove("is-panning"));
  }});
</script>'''


def render_page(page: ReportPage) -> str:
    entries, per_finding = source_index(page)
    # Prefixed here so a case without Arbiter data renders byte-for-byte the
    # same page as before that integration existed.
    arbiter_block = f"\n  {page.appendix}" if page.appendix else ""
    return f'''<!doctype html>
<html lang="{h(page.language or 'und')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(page.title)} — Spotlight</title>
<style>
{page.css}
</style>
</head>
<body>
{masthead(page)}
<div class="wrap">
  {hero(page, entries)}
  <div class="layout">
    {contents(page)}
    <main>
      {summary(page)}
      {diagram_section(page)}
      {findings_table(page, entries, per_finding)}{arbiter_block}
  {limitations(page)}
  {methodology(page)}
      {sources(entries, page)}
    </main>
  </div>
  {footer(page)}
</div>
{PAGE_SCRIPT}
{diagram_runtime(page)}
</body>
</html>
'''
