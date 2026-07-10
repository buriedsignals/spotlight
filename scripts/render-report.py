#!/usr/bin/env python3
"""Render Spotlight's three report artifacts from validated structured case data.

The report phase is a build step, not a prose/file-editing task for the model. Given
``data/findings.json`` and ``data/fact-check.json``, this script deterministically
writes:

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
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/report-drafting/references/report-template.html"
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
    return md_safe(value).replace("|", "\\|").replace("\n", " ")


def md_safe(value: Any) -> str:
    """Render case-controlled text as inert Markdown text (no raw HTML)."""
    return html.escape(text(value), quote=False)


def md_code(value: Any) -> str:
    return "`" + text(value).replace("`", "ˋ") + "`"


def markdown_source(source: dict[str, str]) -> str:
    parts = []
    if source.get("url"):
        parts.append(f"<{md_safe(source['url'])}>")
    if source.get("archive_url"):
        parts.append(f"archive: <{md_safe(source['archive_url'])}>")
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


def render_markdown(case: Path, findings_doc: dict[str, Any], methodology: dict[str, Any],
                    checks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    findings = [item for item in findings_doc.get("findings", []) if isinstance(item, dict)]
    title = text(findings_doc.get("project")) or case.name
    lead = text(findings_doc.get("lead") or methodology.get("lead"))
    rendered: list[dict[str, Any]] = []
    lines = [
        f"# Investigation Report: {md_safe(title)}",
        "",
        "> **AI assistance notice:** " + AI_NOTICE,
        "",
        "This report was rendered deterministically from `data/findings.json` and "
        "`data/fact-check.json`. Confidence is capped by the recorded fact-check verdicts.",
    ]
    if lead:
        lines += ["", "## Scope", "", md_safe(lead)]
    lines += ["", "## Findings Summary", "",
              "| ID | Claim | Verdict | Confidence |",
              "|---|---|---|---|"]

    for index, finding in enumerate(findings, 1):
        fid = text(finding.get("id")) or f"F{index}"
        verdict = aggregate_verdict(finding, checks)
        sources = source_records(case, finding, verdict)
        record = {"id": fid, "finding": finding, "verdict": verdict, "sources": sources}
        rendered.append(record)
        lines.append(
            f"| {md_cell(fid)} | {md_cell(finding.get('claim'))} | "
            f"{VERDICT_LABEL.get(verdict['status'], verdict['status'].title())} | "
            f"{verdict['confidence'].title()} |"
        )

    lines += ["", "## Detailed Findings"]
    for record in rendered:
        finding, verdict, sources = record["finding"], record["verdict"], record["sources"]
        lines += [
            "",
            f"### {md_safe(record['id'])}: {md_safe(finding.get('claim'))}",
            "",
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

    gaps = list_of_text(findings_doc.get("gaps")) + list_of_text(findings_doc.get("next_steps"))
    lines += ["", "## Open Questions and Next Steps", ""]
    lines.extend(f"- {md_safe(item)}" for item in dict.fromkeys(gaps))
    if not gaps:
        lines.append("- No open question was recorded in the structured findings.")
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
                rendered: list[dict[str, Any]], hashes: dict[str, str]) -> str:
    title = text(findings_doc.get("project")) or case.name
    lead = text(findings_doc.get("lead") or methodology.get("lead")) or "Structured investigation findings"
    report_date = text(findings_doc.get("investigated_at") or methodology.get("planned_at"))
    verified_count = sum(record["verdict"]["status"] == "verified" for record in rendered)
    source_count = len({source.get("url") or source.get("local_file")
                        for record in rendered for source in record["sources"]})

    summary_items = []
    finding_sections = []
    for record in rendered:
        fid, finding, verdict, sources = record["id"], record["finding"], record["verdict"], record["sources"]
        anchor = re.sub(r"[^a-z0-9_-]+", "-", fid.lower()).strip("-") or "finding"
        label = VERDICT_LABEL.get(verdict["status"], verdict["status"].title())
        pill = "high" if verdict["confidence"] == "high" else ("med" if verdict["confidence"] == "medium" else "low")
        summary_items.append(
            '<div class="tldr-item">'
            f'<div class="tldr-num">{h(fid)} <span class="pill pill-{pill}">{h(verdict["confidence"])}</span></div>'
            f'<div class="tldr-claim"><a href="#{h(anchor)}"><strong>{h(finding.get("claim"))}</strong>'
            f'{h(label)}</a></div></div>'
        )
        source_html = '<span class="sep">·</span>'.join(html_source(source) for source in sources)
        if not source_html:
            source_html = "No accessible source URL or case-local evidence file was recorded."
        evidence = " ".join(list_of_text(finding.get("evidence")))
        assessment = verdict["assessment"] or "No fact-check narrative was recorded."
        finding_sections.append(f'''
  <section class="finding" id="{h(anchor)}">
    <div class="finding-meta"><span class="pill pill-id">{h(fid)}</span><span class="pill pill-{pill}">{h(verdict['confidence'])}</span><span class="cat">{h(label)}</span></div>
    <h2>{h(finding.get('claim'))}</h2>
    <p class="lede">{h(evidence or assessment)}</p>
    <p><strong>Independent fact-check:</strong> {h(assessment)}</p>
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

    gaps = list(dict.fromkeys(list_of_text(findings_doc.get("gaps")) + list_of_text(findings_doc.get("next_steps"))))
    gap_rows = "".join(f"<tr><td>{h(item)}</td><td>Recorded in data/findings.json</td></tr>" for item in gaps)
    if not gap_rows:
        gap_rows = "<tr><td>No open question recorded</td><td>—</td></tr>"
    hash_badges = "".join(f"<span>{h(path)} · sha256:{h(digest[:12])}</span>" for path, digest in hashes.items())

    return f'''<!doctype html>
<html lang="en">
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
  <p class="byline"><strong>Deterministic case report</strong><br>{verified_count} verified finding(s) · {source_count} accessible source record(s)</p>
  <section class="honesty" aria-label="AI assistance notice"><p><strong>AI assistance notice:</strong> {h(AI_NOTICE)}</p><p>This artifact was rendered from validated structured case data. Non-verified findings are capped at Low confidence.</p></section>
  <section class="tldr" aria-label="findings summary">{''.join(summary_items)}</section>
  {''.join(finding_sections)}
  <section id="method">
    <div class="kicker">Methodology · structured case record</div>
    <h2>How the investigation was carried out.</h2>
    {''.join(methodology_sections)}
    <div class="phase"><div class="phase-head"><span class="phase-id">Phase 3 · fact-check</span><h4 class="phase-title">Independent verdict boundary</h4></div><p>Report confidence is computed from the recorded verdicts and confidence caps; it is not authored during rendering.</p></div>
    <div class="phase"><div class="phase-head"><span class="phase-id">Phase 5 · report</span><h4 class="phase-title">Deterministic renderer</h4></div><p><code>findings-report.md</code>, <code>report.html</code>, and <code>evidence-map.json</code> were generated from the same validated inputs.</p></div>
  </section>
  <section id="next"><div class="kicker">Open questions</div><h2>What a later cycle should close.</h2><table><tr><th>Target</th><th>Provenance</th></tr>{gap_rows}</table></section>
  <footer><p><strong>Deliverables:</strong> <code>findings-report.md</code>, <code>report.html</code>, <code>evidence-map.json</code>.</p><p class="databases">{hash_badges}</p></footer>
</div>
</body>
</html>
'''


def evidence_map(case: Path, rendered: list[dict[str, Any]], hashes: dict[str, str]) -> dict[str, Any]:
    claims = []
    for record in rendered:
        finding, verdict = record["finding"], record["verdict"]
        claims.append({
            "id": record["id"],
            "claim": text(finding.get("claim")),
            "fact_check_status": verdict["status"],
            "report_confidence": verdict["confidence"],
            "fact_check_ids": [check["id"] for check in verdict["checks"]],
            "evidence_bundle_refs": list_of_text(finding.get("evidence_bundle_refs")),
            "sources": record["sources"],
        })
    return {
        "schema_version": "1.0",
        "case_ref": case.name,
        "generator": "scripts/render-report.py",
        "input_sha256": hashes,
        "claims": claims,
    }


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
    findings_doc = load_object(findings_path)
    fact_check = load_object(fact_check_path)
    methodology = load_object(methodology_path) if methodology_path.is_file() else {}
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
    inputs = [findings_path, fact_check_path] + ([methodology_path] if methodology_path.is_file() else [])
    hashes = input_hashes(inputs)
    markdown, rendered = render_markdown(case, findings_doc, methodology, checks)
    html_report = render_html(case, findings_doc, methodology, rendered, hashes)
    ledger = json.dumps(evidence_map(case, rendered, hashes), indent=2, ensure_ascii=False) + "\n"

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
