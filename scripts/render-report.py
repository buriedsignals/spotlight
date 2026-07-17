#!/usr/bin/env python3
"""Render Spotlight reports from validated evidence plus a model-authored editorial plan.

The model owns prioritization, framing, and prose in ``data/report-draft.json``.
This script owns safe, byte-deterministic file construction from that draft plus
``data/findings.json`` and ``data/fact-check.json``.

* ``findings-report.md`` — claim-by-claim editorial audit
* ``report.html`` — designed reader artifact (using the canonical template CSS)
* ``evidence-map.json`` — machine-readable claim/evidence ledger

No source, quote, verdict, or confidence is invented here. All case-controlled text
is escaped before HTML insertion, links are limited to HTTP(S) or existing files
inside the case, and non-verified findings are capped at Low confidence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from source_expression_contract import lifecycle_state


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/report-drafting/references/report-template.html"
DRAFT_VALIDATOR = SCRIPT_DIR / "validate-report-draft.py"
CASE_VALIDATOR = SCRIPT_DIR / "validate-case.py"
AI_NOTICE = (
    "Spotlight is designed to help surface, organize, and cross-check information, "
    "but AI can make mistakes. You are responsible for verifying sources, confirming "
    "authenticity, assessing risks, and deciding what is publishable."
)
CONFIDENCE_VALUE = {"low": 1, "medium": 2, "high": 3}
VERDICT_LABEL = {
    "verified": "Verified",
    "partially_verified": "Partially verified",
    "unverified": "Unverified",
    "disputed": "Disputed",
    "false": "False",
    "mischaracterized": "Mischaracterized",
}


class RenderError(ValueError):
    """The structured inputs cannot produce a report."""


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RenderError(f"missing required input: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RenderError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RenderError(f"expected a JSON object in {path}")
    return value


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def list_of_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (text(v) for v in value) if item]
    one = text(value)
    return [one] if one else []


def canonical_checks(fact_check: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize both shipped fact-check contracts to one report-facing shape."""
    raw = fact_check.get("fact_checks")
    if not isinstance(raw, list):
        raw = fact_check.get("claims")
    if not isinstance(raw, list):
        raw = fact_check.get("verdicts")
    if not isinstance(raw, list):
        return []

    checks: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        grounding = item.get("grounding_assessment")
        assessment = text(grounding.get("assessment")) if isinstance(grounding, dict) else ""
        evidence_for = item.get("evidence_for") if isinstance(item.get("evidence_for"), list) else []
        checks.append({
            "id": text(item.get("id")) or f"FC{index}",
            "finding_id": text(item.get("finding_id")),
            "claim": text(item.get("claim") or item.get("claim_text")),
            "status": text(item.get("status") or item.get("verdict")).lower() or "unverified",
            "confidence": text(item.get("confidence")).lower() or "low",
            "assessment": text(item.get("verification_evidence") or item.get("notes") or assessment),
            "grounding": grounding if isinstance(grounding, dict) else {},
            "evidence_for": [entry for entry in evidence_for if isinstance(entry, dict)],
            "evidence_against": [entry for entry in item.get("evidence_against", [])
                                 if isinstance(entry, dict)],
            "sources": list_of_text(item.get("sources")),
        })
    return checks


def cap_confidence(*values: Any) -> str:
    normalized = [text(v).lower() for v in values]
    levels = [CONFIDENCE_VALUE[v] for v in normalized if v in CONFIDENCE_VALUE]
    level = min(levels) if levels else 1
    return {1: "low", 2: "medium", 3: "high"}[level]


def aggregate_verdict(finding: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    fid = text(finding.get("id"))
    linked = [check for check in checks if check["finding_id"] == fid]
    if not linked:
        return {"status": "unverified", "confidence": "low", "checks": [],
                "assessment": "No fact-check verdict was recorded for this finding."}

    statuses = [check["status"] for check in linked]
    if all(status == "verified" for status in statuses):
        status = "verified"
    elif "disputed" in statuses or ("verified" in statuses and "false" in statuses):
        status = "disputed"
    elif "false" in statuses:
        status = "false"
    elif "mischaracterized" in statuses:
        status = "mischaracterized"
    elif "unverified" in statuses:
        status = "unverified"
    else:
        status = "partially_verified"

    finding_grounding = finding.get("grounding")
    finding_cap = (finding_grounding.get("confidence_cap")
                   if isinstance(finding_grounding, dict) else None)
    check_caps = [check["grounding"].get("confidence_cap") for check in linked
                  if isinstance(check.get("grounding"), dict)]
    confidence = cap_confidence(finding.get("confidence"), finding_cap,
                                *(check["confidence"] for check in linked), *check_caps)
    if status == "partially_verified" and confidence == "high":
        confidence = "medium"
    if status != "verified" and status != "partially_verified":
        confidence = "low"
    assessments = [check["assessment"] for check in linked if check["assessment"]]
    return {
        "status": status,
        "confidence": confidence,
        "checks": linked,
        "assessment": " ".join(dict.fromkeys(assessments)),
    }


def case_local_file(case: Path, candidate: Any) -> str | None:
    """Return a safe case-relative path for an existing source, never an escape."""
    raw = text(candidate)
    if not raw:
        return None
    case_real = case.resolve()
    supplied = Path(raw).expanduser()
    candidates = [supplied if supplied.is_absolute() else case / supplied]
    # Old fixtures sometimes retain an absolute path to a prior case copy. A file
    # with the same basename in this case's evidence roots is the safe repair.
    candidates.extend((case / "research" / supplied.name, case / "evidence" / supplied.name))
    for path in candidates:
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(case_real)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return relative.as_posix()
    return None


def valid_web_url(value: Any) -> str | None:
    raw = text(value)
    if not raw or re.search(r"[\x00-\x20<>\"'`]", raw):
        return None
    parsed = urlparse(raw)
    return raw if parsed.scheme in {"http", "https"} and parsed.netloc else None


def source_records(case: Path, finding: dict[str, Any], verdict: dict[str, Any]) -> list[dict[str, str]]:
    raw_sources: list[dict[str, Any]] = []
    for source in finding.get("sources", []):
        if isinstance(source, dict):
            raw_sources.append(source)
        elif isinstance(source, str):
            raw_sources.append({"url": source})
    for check in verdict["checks"]:
        for source in check["evidence_for"] + check["evidence_against"]:
            raw_sources.append({
                "url": source.get("source"),
                "local_file": source.get("local_file"),
                "archive_url": source.get("archive_url"),
                "type": source.get("source_type"),
                "description": source.get("description"),
                "access_method": source.get("access_method"),
            })
        raw_sources.extend({"url": source} for source in check["sources"])

    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in raw_sources:
        url = valid_web_url(source.get("url") or source.get("source")) or ""
        archive_url = valid_web_url(source.get("archive_url")) or ""
        local_file = case_local_file(case, source.get("local_file") or source.get("raw_path")) or ""
        key = (url or archive_url, local_file)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        record = {
            "url": url,
            "archive_url": archive_url,
            "local_file": local_file,
            "type": text(source.get("type") or source.get("source_type")),
            "description": text(source.get("description")),
            "accessed": text(source.get("accessed")),
            "access_method": text(source.get("access_method")),
        }
        records.append({key: value for key, value in record.items() if value})
    return records


def source_label(source: dict[str, str]) -> str:
    if source.get("description"):
        return source["description"]
    if source.get("url"):
        return urlparse(source["url"]).netloc.removeprefix("www.") or source["url"]
    return source.get("local_file", "Source")


def md_cell(value: Any) -> str:
    return md_safe(value)


def md_safe(value: Any) -> str:
    """Render case-controlled prose as one inert CommonMark text span."""
    escaped = html.escape(text(value), quote=False)
    escaped = re.sub(r"\s*[\r\n]+\s*", " ", escaped)
    return re.sub(r"([\\`*_\[\](){}#+.!|>\-])", r"\\\1", escaped)


def md_code(value: Any) -> str:
    return "`" + text(value).replace("`", "ˋ") + "`"


def markdown_source(source: dict[str, str]) -> str:
    parts = []
    if source.get("url"):
        parts.append(f"<{source['url']}>")
    if source.get("archive_url"):
        parts.append(f"archive: <{source['archive_url']}>")
    if source.get("local_file"):
        parts.append(md_code(source["local_file"]))
    return " — ".join(parts)


def h(value: Any) -> str:
    return html.escape(text(value), quote=True)


def html_source(source: dict[str, str]) -> str:
    label = h(source_label(source))
    url = source.get("url")
    if url:
        rendered = f'<a href="{h(url)}" rel="noreferrer">{label}</a>'
    elif source.get("local_file"):
        rendered = f'<a href="{h(source["local_file"])}">{label}</a>'
    else:
        rendered = label
    if source.get("local_file"):
        rendered += f' <code>{h(source["local_file"])}</code>'
    return rendered


def input_hashes(paths: list[Path]) -> dict[str, str]:
    return {f"data/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def ordered_findings(findings_doc: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    findings = [item for item in findings_doc.get("findings", []) if isinstance(item, dict)]
    by_id = {text(item.get("id")): item for item in findings}
    order = [text(fid) for fid in draft.get("finding_order", [])]
    return [by_id[fid] for fid in order if fid in by_id]


def treatment_map(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(item.get("finding_id")): item
        for item in draft.get("finding_treatments", [])
        if isinstance(item, dict) and text(item.get("finding_id"))
    }


def expression_state(expression: dict[str, Any]) -> str:
    event = lifecycle_state(expression)
    if event is None:
        return "invalid"
    return "active" if event == "activated" else event


def expression_index(expressions_doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if expressions_doc is None:
        return {}
    return {
        text(item.get("id")): item
        for item in expressions_doc.get("expressions", [])
        if isinstance(item, dict) and text(item.get("id"))
    }


def expression_link(expression: dict[str, Any], finding_id: str) -> dict[str, Any] | None:
    return next(
        (
            link
            for link in expression.get("finding_links", [])
            if isinstance(link, dict) and text(link.get("finding_id")) == finding_id
        ),
        None,
    )


def selected_expressions(
    treatment: dict[str, Any],
    finding_id: str,
    expressions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for selection in treatment.get("quote_selections", []):
        expression_id = text(selection.get("expression_id")) if isinstance(selection, dict) else ""
        expression = expressions.get(expression_id)
        if expression is None:
            raise RenderError(f"quote selection {expression_id!r} does not resolve")
        if expression_state(expression) != "active":
            raise RenderError(f"quote selection {expression_id!r} is not active")
        if expression.get("direct_quote") is not True:
            raise RenderError(f"quote selection {expression_id!r} is not a direct quotation")
        if expression.get("derivative_type") == "translation":
            raise RenderError(f"quote selection {expression_id!r} is a translation")
        if expression_link(expression, finding_id) is None:
            raise RenderError(
                f"quote selection {expression_id!r} is not linked to finding {finding_id!r}"
            )
        selected.append(expression)
    return selected


def expressions_by_finding(
    expressions: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for expression in expressions.values():
        for link in expression.get("finding_links", []):
            if not isinstance(link, dict):
                continue
            finding_id = text(link.get("finding_id"))
            if finding_id:
                result.setdefault(finding_id, []).append(expression)
    return result


def md_exact_quote(value: Any) -> str:
    raw = value if isinstance(value, str) else str(value)
    escaped = html.escape(raw, quote=False).replace("\r\n", "\n").replace("\r", "\n")
    escaped = re.sub(r"([\\`*_\[\](){}#+.!|>\-])", r"\\\1", escaped)
    return escaped.replace("\n", "\n> ")


def editorial_items(draft: dict[str, Any], field: str) -> list[dict[str, Any]]:
    return [item for item in draft.get(field, []) if isinstance(item, dict)]


def render_markdown(case: Path, findings_doc: dict[str, Any], methodology: dict[str, Any],
                    draft: dict[str, Any], checks: list[dict[str, Any]],
                    expressions_doc: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    findings = ordered_findings(findings_doc, draft)
    treatments = treatment_map(draft)
    title = text(draft.get("title"))
    deck = text(draft.get("deck"))
    lead = text(findings_doc.get("lead") or methodology.get("lead"))
    rendered: list[dict[str, Any]] = []
    expressions = expression_index(expressions_doc)
    linked_by_finding = expressions_by_finding(expressions)
    lines = [
        f"# Investigation Report: {md_safe(title)}",
        "",
        "> **AI assistance notice:** " + AI_NOTICE,
        "",
        "The model authored the editorial framing and priority in `data/report-draft.json`; "
        "deterministic code rendered and escaped the files. Confidence remains capped by "
        "the recorded fact-check verdicts.",
        "",
        "## Editorial Summary",
        "",
        md_safe(deck),
    ]
    if lead:
        lines += ["", "## Scope", "", md_safe(lead)]
    lines += ["", "## Findings Summary", "",
              "| ID | Claim | Verdict | Confidence |",
              "|---|---|---|---|"]

    for index, finding in enumerate(findings, 1):
        fid = text(finding.get("id")) or f"F{index}"
        treatment = treatments[fid]
        verdict = aggregate_verdict(finding, checks)
        sources = source_records(case, finding, verdict)
        quotes = selected_expressions(treatment, fid, expressions)
        record = {"id": fid, "finding": finding, "treatment": treatment,
                  "verdict": verdict, "sources": sources, "quotes": quotes,
                  "source_expressions": linked_by_finding.get(fid, [])}
        rendered.append(record)
        lines.append(
            f"| {md_cell(fid)} | {md_cell(treatment.get('headline'))} | "
            f"{VERDICT_LABEL.get(verdict['status'], verdict['status'].title())} | "
            f"{verdict['confidence'].title()} |"
        )

    lines += ["", "## Detailed Findings"]
    for record in rendered:
        finding, treatment = record["finding"], record["treatment"]
        verdict, sources = record["verdict"], record["sources"]
        lines += [
            "",
            f"### {md_safe(record['id'])}: {md_safe(treatment.get('headline'))}",
            "",
            md_safe(treatment.get("summary")),
            "",
            f"- **Canonical fact-checked claim:** {md_safe(finding.get('claim'))}",
            f"- **Why it matters:** {md_safe(treatment.get('why_it_matters'))}",
            f"- **Verdict:** {VERDICT_LABEL.get(verdict['status'], verdict['status'].title())}",
            f"- **Report confidence:** {verdict['confidence'].title()}",
        ]
        evidence = list_of_text(finding.get("evidence"))
        if evidence:
            lines += ["- **Recorded evidence:** " + " ".join(md_safe(item) for item in evidence)]
        if verdict["assessment"]:
            lines += ["- **Fact-check assessment:** " + md_safe(verdict["assessment"])]
        rationale = text(finding.get("confidence_rationale"))
        if rationale:
            lines += ["- **Investigator rationale:** " + md_safe(rationale)]
        if record["quotes"]:
            lines += ["", "**Selected source quotations**", ""]
            for expression in record["quotes"]:
                attribution = text(expression.get("attribution"))
                lines += [f"> “{md_exact_quote(expression.get('text', ''))}”"]
                if attribution:
                    lines += [">", f"> — {md_safe(attribution)}"]
        lines += ["", "**Replication path**", "",
                  f"1. Read the structured claim in `data/findings.json` ({md_safe(record['id'])}).",
                  "2. Inspect the existing case-local source files listed below.",
                  "3. Compare the independent verdict in `data/fact-check.json`."]
        lines += ["", "**Sources**", ""]
        if sources:
            lines.extend(f"- {markdown_source(source)}" for source in sources)
        else:
            lines.append("- No accessible source URL or case-local evidence file was recorded.")

    lines += ["", "## Methodology", ""]
    directions = [item for item in methodology.get("investigation_plan", []) if isinstance(item, dict)]
    if directions:
        for index, direction in enumerate(directions, 1):
            heading = text(direction.get("direction")) or f"Direction {index}"
            lines += [f"### {index}. {md_safe(heading)}", ""]
            for question in list_of_text(direction.get("questions")):
                lines.append(f"- Question: {md_safe(question)}")
            for step in direction.get("steps", []):
                if not isinstance(step, dict):
                    continue
                action = text(step.get("action"))
                tool = text(step.get("tool"))
                if action:
                    lines.append(f"- {md_safe(action)}" + (f" (tool: {md_code(tool)})" if tool else ""))
            lines.append("")
    else:
        lines.append("No structured methodology steps were recorded.")

    verdict_by_id = {record["id"]: record["verdict"] for record in rendered}
    caveats = editorial_items(draft, "caveats")
    next_steps = editorial_items(draft, "next_steps")

    def editorial_line(item: dict[str, Any]) -> str:
        refs = [text(ref) for ref in item.get("finding_ids", [])]
        labels = [
            f"{ref} — {VERDICT_LABEL.get(verdict_by_id[ref]['status'], verdict_by_id[ref]['status'].title())}"
            for ref in refs if ref in verdict_by_id
        ]
        suffix = f" *({'; '.join(labels)})*" if labels else ""
        return md_safe(item.get("text")) + suffix

    lines += ["", "## Editorial Caveats", ""]
    lines.extend(f"- {editorial_line(item)}" for item in caveats)
    lines += ["", "## Open Questions and Next Steps", ""]
    lines.extend(f"- {editorial_line(item)}" for item in next_steps)
    lines += ["", "## Deliverables", "",
              "- `findings-report.md`", "- `report.html`", "- `evidence-map.json`", ""]
    return "\n".join(lines), rendered


def template_css() -> str:
    try:
        template = TEMPLATE.read_text()
    except OSError as exc:
        raise RenderError(f"cannot read canonical report template: {TEMPLATE}") from exc
    match = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    if not match:
        raise RenderError(f"canonical report template has no <style> block: {TEMPLATE}")
    return match.group(1).strip()


def render_html(case: Path, findings_doc: dict[str, Any], methodology: dict[str, Any],
                draft: dict[str, Any], rendered: list[dict[str, Any]], hashes: dict[str, str]) -> str:
    title = text(draft.get("title"))
    lead = text(draft.get("deck"))
    report_date = text(findings_doc.get("investigated_at") or methodology.get("planned_at"))
    verified_count = sum(record["verdict"]["status"] == "verified" for record in rendered)
    source_count = len({source.get("url") or source.get("local_file")
                        for record in rendered for source in record["sources"]})

    summary_items = []
    finding_sections = []
    for index, record in enumerate(rendered, 1):
        fid, finding, treatment = record["id"], record["finding"], record["treatment"]
        verdict, sources = record["verdict"], record["sources"]
        anchor = f"finding-{index}-{hashlib.sha256(fid.encode()).hexdigest()[:8]}"
        label = VERDICT_LABEL.get(verdict["status"], verdict["status"].title())
        pill = "high" if verdict["confidence"] == "high" else ("med" if verdict["confidence"] == "medium" else "low")
        summary_items.append(
            '<div class="tldr-item">'
            f'<div class="tldr-num">{h(fid)} <span class="pill pill-{pill}">{h(verdict["confidence"])}</span></div>'
            f'<div class="tldr-claim"><a href="#{h(anchor)}"><strong>{h(treatment.get("headline"))}</strong>'
            f'{h(label)}</a></div></div>'
        )
        source_html = '<span class="sep">·</span>'.join(html_source(source) for source in sources)
        if not source_html:
            source_html = "No accessible source URL or case-local evidence file was recorded."
        evidence = " ".join(list_of_text(finding.get("evidence")))
        assessment = verdict["assessment"] or "No fact-check narrative was recorded."
        quotations = "".join(
            '<blockquote class="source-expression">'
            f'<p>“{html.escape(expression.get("text", ""), quote=True)}”</p>'
            + (
                f'<footer>— {h(expression.get("attribution"))}</footer>'
                if text(expression.get("attribution")) else ""
            )
            + '</blockquote>'
            for expression in record["quotes"]
        )
        finding_sections.append(f'''
  <section class="finding" id="{h(anchor)}">
    <div class="finding-meta"><span class="pill pill-id">{h(fid)}</span><span class="pill pill-{pill}">{h(verdict['confidence'])}</span><span class="cat">{h(label)}</span></div>
    <h2>{h(treatment.get('headline'))}</h2>
    <p class="lede">{h(treatment.get('summary'))}</p>
    <p><strong>Canonical fact-checked claim:</strong> {h(finding.get('claim'))}</p>
    <p><strong>Why it matters:</strong> {h(treatment.get('why_it_matters'))}</p>
    <p><strong>Independent fact-check:</strong> {h(assessment)}</p>
    {quotations}
    <div class="path" aria-label="How we got here">
      <div class="step">Structured claim</div><div class="what"><code>data/findings.json</code> · {h(fid)}</div>
      <div class="step">Evidence</div><div class="what">{len(sources)} accessible source record(s) retained below</div>
      <div class="step">Fact-check</div><div class="what"><code>data/fact-check.json</code> · {h(label)} · {h(verdict['confidence'].title())}</div>
    </div>
    <div class="sources"><span class="label">Sources</span>{source_html}</div>
  </section>''')

    methodology_sections = []
    for index, direction in enumerate(methodology.get("investigation_plan", []), 1):
        if not isinstance(direction, dict):
            continue
        items = []
        for question in list_of_text(direction.get("questions")):
            items.append(f"<li><strong>Question:</strong> {h(question)}</li>")
        for step in direction.get("steps", []):
            if isinstance(step, dict) and text(step.get("action")):
                tool = text(step.get("tool"))
                suffix = f" — <code>{h(tool)}</code>" if tool else ""
                items.append(f"<li>{h(step.get('action'))}{suffix}</li>")
        methodology_sections.append(f'''
    <div class="phase">
      <div class="phase-head"><span class="phase-id">Phase 2 · direction {index}</span><h4 class="phase-title">{h(direction.get('direction') or f'Direction {index}')}</h4></div>
      <ul>{''.join(items) or '<li>No structured steps recorded.</li>'}</ul>
    </div>''')
    if not methodology_sections:
        methodology_sections.append('<div class="phase"><p>No structured methodology steps were recorded.</p></div>')

    verdict_by_id = {record["id"]: record["verdict"] for record in rendered}

    def html_editorial_item(item: dict[str, Any]) -> str:
        refs = [text(ref) for ref in item.get("finding_ids", [])]
        labels = [
            f"{ref} — {VERDICT_LABEL.get(verdict_by_id[ref]['status'], verdict_by_id[ref]['status'].title())}"
            for ref in refs if ref in verdict_by_id
        ]
        return f"{h(item.get('text'))}<br><small>{h('; '.join(labels))}</small>"

    caveat_items = "".join(
        f"<li>{html_editorial_item(item)}</li>" for item in editorial_items(draft, "caveats")
    ) or "<li>No editorial caveat was recorded.</li>"
    next_rows = "".join(
        f"<tr><td>{html_editorial_item(item)}</td><td>Model-authored · finding-linked</td></tr>"
        for item in editorial_items(draft, "next_steps")
    ) or "<tr><td>No next step was recorded</td><td>—</td></tr>"
    framing_labels = []
    for fid in draft.get("framing_finding_ids", []):
        if fid in verdict_by_id:
            verdict = verdict_by_id[fid]
            framing_labels.append(
                f"{fid} — {VERDICT_LABEL.get(verdict['status'], verdict['status'].title())}"
            )
    hash_badges = "".join(f"<span>{h(path)} · sha256:{h(digest[:12])}</span>" for path, digest in hashes.items())

    return f'''<!doctype html>
<html lang="{h(draft.get('language') or 'und')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(title)} — Spotlight</title>
<style>
{template_css()}
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead"><span class="pub">Spotlight</span><span class="meta">{h(case.name)}{f' · {h(report_date)}' if report_date else ''}</span></div>
  <h1>{h(title)}</h1>
  <p class="deck">{h(lead)}</p>
  <p class="byline"><strong>Model-authored editorial synthesis · deterministically rendered</strong><br>{verified_count} verified finding(s) · {source_count} accessible source record(s)</p>
  <section class="honesty" aria-label="AI assistance notice"><p><strong>AI assistance notice:</strong> {h(AI_NOTICE)}</p><p>The model chose localized framing and priority. Deterministic checks bind every prose block to finding IDs and place canonical verdicts beside it; they do not claim semantic entailment.</p><p><strong>Framing references:</strong> {h('; '.join(framing_labels))}</p></section>
  <section class="tldr" aria-label="findings summary">{''.join(summary_items)}</section>
  {''.join(finding_sections)}
  <section id="method">
    <div class="kicker">Methodology · structured case record</div>
    <h2>How the investigation was carried out.</h2>
    {''.join(methodology_sections)}
    <div class="phase"><div class="phase-head"><span class="phase-id">Phase 3 · fact-check</span><h4 class="phase-title">Independent verdict boundary</h4></div><p>Report confidence is computed from the recorded verdicts and confidence caps; it is not authored during rendering.</p></div>
    <div class="phase"><div class="phase-head"><span class="phase-id">Phase 5 · report</span><h4 class="phase-title">Editorial model + deterministic renderer</h4></div><p>The model authored <code>data/report-draft.json</code>; code validated its finding-reference coverage and output structure, then generated all three deliverables.</p></div>
  </section>
  <section id="caveats"><div class="kicker">Editorial caveats</div><h2>What readers should keep in view.</h2><ul>{caveat_items}</ul></section>
  <section id="next"><div class="kicker">Open questions</div><h2>What a later cycle should close.</h2><table><tr><th>Target</th><th>Provenance</th></tr>{next_rows}</table></section>
  <footer><p><strong>Deliverables:</strong> <code>findings-report.md</code>, <code>report.html</code>, <code>evidence-map.json</code>.</p><p class="databases">{hash_badges}</p></footer>
</div>
</body>
</html>
'''


def evidence_map(
    case: Path,
    draft: dict[str, Any],
    rendered: list[dict[str, Any]],
    hashes: dict[str, str],
    output_hashes: dict[str, str],
) -> dict[str, Any]:
    claims = []
    for record in rendered:
        finding, verdict = record["finding"], record["verdict"]
        claims.append({
            "id": record["id"],
            "claim": text(finding.get("claim")),
            "editorial": {
                "headline": text(record["treatment"].get("headline")),
                "summary": text(record["treatment"].get("summary")),
                "why_it_matters": text(record["treatment"].get("why_it_matters")),
            },
            "fact_check_status": verdict["status"],
            "report_confidence": verdict["confidence"],
            "fact_check_ids": [check["id"] for check in verdict["checks"]],
            "evidence_bundle_refs": list_of_text(finding.get("evidence_bundle_refs")),
            "sources": record["sources"],
        })
        if record["source_expressions"]:
            selected_ids = {text(item.get("id")) for item in record["quotes"]}
            claims[-1]["source_expression_refs"] = [
                {
                    "expression_id": text(expression.get("id")),
                    "relation": text(expression_link(expression, record["id"]).get("relation")),
                    "selected_quote": text(expression.get("id")) in selected_ids,
                }
                for expression in record["source_expressions"]
            ]
    result = {
        "schema_version": "1.0",
        "case_ref": case.name,
        "generator": "scripts/render-report.py",
        "input_sha256": hashes,
        "output_sha256": output_hashes,
        "editorial_plan": {
            "title": text(draft.get("title")),
            "deck": text(draft.get("deck")),
            "framing_finding_ids": draft.get("framing_finding_ids", []),
            "caveats": editorial_items(draft, "caveats"),
            "next_steps": editorial_items(draft, "next_steps"),
        },
        "claims": claims,
    }
    expressions_by_id = {
        text(expression.get("id")): expression
        for record in rendered
        for expression in record["source_expressions"]
    }
    if expressions_by_id:
        result["source_expressions"] = [
            {
                "id": expression_id,
                "text": expression.get("text", ""),
                "attribution": expression.get("attribution"),
                "language": expression.get("language"),
                "direct_quote": expression.get("direct_quote"),
                "anchor_ref": expression.get("anchor_ref"),
                "anchor_sha256": expression.get("anchor_sha256"),
                "original_evidence_bundle_id": expression.get("original_evidence_bundle_id"),
                "original_artifact_sha256": expression.get("original_artifact_sha256"),
                "expression_fingerprint": expression.get("expression_fingerprint"),
                "finding_links": expression.get("finding_links", []),
                "lifecycle": expression.get("lifecycle_events", []),
                "lifecycle_state": expression_state(expression),
            }
            for expression_id, expression in expressions_by_id.items()
        ]
    return result


def publish_outputs(case: Path, outputs: dict[str, str]) -> None:
    """Stage every artifact, then publish with rollback on replacement failure."""
    staged: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    try:
        for name, content in outputs.items():
            destination = case / name
            originals[destination] = destination.read_bytes() if destination.exists() else None
            handle, temp_name = tempfile.mkstemp(prefix=f".{name}.stage.", dir=case, text=True)
            with os.fdopen(handle, "w") as temp:
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
            staged[destination] = Path(temp_name)
        published: list[Path] = []
        try:
            for destination, staged_path in staged.items():
                staged_path.replace(destination)
                published.append(destination)
        except BaseException:
            for destination in reversed(published):
                original = originals[destination]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    handle, restore_name = tempfile.mkstemp(
                        prefix=f".{destination.name}.restore.", dir=case
                    )
                    with os.fdopen(handle, "wb") as restore:
                        restore.write(original)
                        restore.flush()
                        os.fsync(restore.fileno())
                    Path(restore_name).replace(destination)
            raise
    finally:
        for staged_path in staged.values():
            staged_path.unlink(missing_ok=True)


def render(case: Path) -> dict[str, Any]:
    case = case.resolve()
    findings_path = case / "data" / "findings.json"
    fact_check_path = case / "data" / "fact-check.json"
    methodology_path = case / "data" / "methodology.json"
    draft_path = case / "data" / "report-draft.json"
    expressions_path = case / "data" / "source-expressions.json"
    findings_doc = load_object(findings_path)
    fact_check = load_object(fact_check_path)
    methodology = load_object(methodology_path) if methodology_path.is_file() else {}
    draft = load_object(draft_path)
    activated = findings_doc.get("schema_version") == "1.1"
    expressions_doc = load_object(expressions_path) if activated else None
    findings = findings_doc.get("findings")
    if not isinstance(findings, list) or not findings:
        raise RenderError(f"no findings to render in {findings_path}")
    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise RenderError(f"findings.json[{index}] is not an object")
        fid = text(finding.get("id"))
        if not fid or not text(finding.get("claim")):
            raise RenderError(f"findings.json[{index}] needs non-empty id and claim")
        if fid in seen_ids:
            raise RenderError(f"duplicate finding id: {fid}")
        seen_ids.add(fid)

    checks = canonical_checks(fact_check)
    inputs = [findings_path, fact_check_path, draft_path] + ([methodology_path] if methodology_path.is_file() else [])
    if activated:
        inputs.append(expressions_path)
    hashes = input_hashes(inputs)
    markdown, rendered = render_markdown(
        case, findings_doc, methodology, draft, checks, expressions_doc
    )
    rendered_ids = [record["id"] for record in rendered]
    expected_order = [text(fid) for fid in draft.get("finding_order", [])]
    if rendered_ids != expected_order or set(rendered_ids) != seen_ids:
        raise RenderError(
            "rendered finding IDs do not exactly match finding_order and findings.json"
        )
    html_report = render_html(case, findings_doc, methodology, draft, rendered, hashes)
    output_hashes = {
        "findings-report.md": hashlib.sha256(
            (markdown + ("" if markdown.endswith("\n") else "\n")).encode()
        ).hexdigest(),
        "report.html": hashlib.sha256(html_report.encode()).hexdigest(),
    }
    ledger = json.dumps(
        evidence_map(case, draft, rendered, hashes, output_hashes),
        indent=2,
        ensure_ascii=False,
    ) + "\n"

    outputs = {
        "findings-report.md": markdown + ("" if markdown.endswith("\n") else "\n"),
        "report.html": html_report,
        "evidence-map.json": ledger,
    }
    publish_outputs(case, outputs)
    return {
        "case": str(case),
        "outputs": {name: hashlib.sha256(content.encode()).hexdigest()
                    for name, content in outputs.items()},
        "findings": len(rendered),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    case = Path(args.case_dir)
    if not case.is_dir():
        print(f"FAIL  case dir not found: {case}")
        return 3
    validation = subprocess.run(
        [sys.executable, str(DRAFT_VALIDATOR), str(case)], capture_output=True, text=True
    )
    if validation.returncode != 0:
        print(validation.stdout.strip() or validation.stderr.strip())
        return 3
    try:
        findings_doc = load_object(case / "data" / "findings.json")
    except RenderError as exc:
        print(f"FAIL  report render: {exc}")
        return 3
    if findings_doc.get("schema_version") == "1.1":
        case_validation = subprocess.run(
            [sys.executable, str(CASE_VALIDATOR), str(case), "--fact-check-only"],
            capture_output=True,
            text=True,
        )
        if case_validation.returncode != 0:
            print(case_validation.stderr.strip() or case_validation.stdout.strip())
            return 3
    try:
        result = render(case)
    except (OSError, RenderError) as exc:
        print(f"FAIL  report render: {exc}")
        return 3
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"report render: OK — {result['findings']} finding(s), 3 canonical artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
