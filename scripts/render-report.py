#!/usr/bin/env python3
"""Render Spotlight reports from validated evidence plus a model-authored editorial plan.

The model owns prioritization, framing, and prose in ``data/report-draft.json``.
This script owns safe, byte-deterministic file construction from that draft plus
``data/findings.json`` and ``data/fact-check.json``.

* ``findings-report.md`` — claim-by-claim editorial audit
* ``report.html`` — reader page laid out by ``report_html.py`` with the canonical template CSS
* ``evidence-map.json`` — machine-readable claim/evidence ledger

No source, quote, verdict, or confidence is invented here. All case-controlled text
is escaped before HTML insertion, links are limited to HTTP(S) or existing files
inside the case, and non-verified findings are capped at Low confidence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import decision_signals_lib as decision_signals
import report_html
from source_expression_contract import lifecycle_state
from spotlight_orchestration.case_writer import atomic_write_files
from spotlight_orchestration.contract import OrchestrationError
from spotlight_orchestration.review import review_file, set_aside_findings


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/report-drafting/references/report-template.html"
DRAFT_VALIDATOR = SCRIPT_DIR / "validate-report-draft.py"
CASE_VALIDATOR = SCRIPT_DIR / "validate-case.py"
ARBITER_APPENDIX = SCRIPT_DIR.parent / "integrations/arbiter/run_appendix.py"
ARBITER_APPENDIX_TIMEOUT = 30
AI_NOTICE = (
    "Spotlight is designed to help surface, organize, and cross-check information, "
    "but AI can make mistakes. You are responsible for verifying sources, confirming "
    "authenticity, assessing risks, and deciding what is publishable."
)
# Tom, 2026-11-06: the journalist verifies. Same words as review.py LOCAL_MODEL_WARNING.
LOCAL_MODEL_WARNING = (
    "Spotlight's local model can get facts wrong, even on findings marked verified. "
    "Check every finding against its source before you use it."
)
CONFIDENCE_VALUE = {"low": 1, "medium": 2, "high": 3}
VERDICTS_1_1 = ("verified", "unverified", "disputed", "false", "not_checkable")
VERDICT_LABEL = {
    "verified": "Verified",
    "partially_verified": "Partially verified",
    "unverified": "Unverified",
    "disputed": "Disputed",
    "false": "False",
    "mischaracterized": "Mischaracterized",
    "not_checkable": "Not checkable",
}
# How a findings.json connection is known (schemas/findings.schema.json). An
# inferred link is drawn undirected and dotted: it must never read as a step.
CONNECTION_BASIS = ("record", "reported", "inferred")
EDGE_BY_BASIS = {"record": "==>", "reported": "-->", "inferred": "-.-", "": "-->"}
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.esm.min.mjs"
ELK_LAYOUT_URL = "https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.2.2/dist/mermaid-layout-elk.esm.min.mjs"


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
            # ``notes`` are the fact-checker's internal working notes (pipeline
            # steps, file names, reuse of the investigator's evidence); the
            # public report shows only the assessment written for readers.
            "assessment": text(item.get("verification_evidence") or assessment),
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
    elif all(status == "not_checkable" for status in statuses):
        status = "not_checkable"
    elif "not_checkable" in statuses:
        # Part of the finding could not be checked, so the whole is not established.
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
            raw_sources.append({**source, "origin": "finding"})
        elif isinstance(source, str):
            raw_sources.append({"url": source, "origin": "finding"})
    for check in verdict["checks"]:
        for source in check["evidence_for"] + check["evidence_against"]:
            raw_sources.append({
                "url": source.get("source"),
                "local_file": source.get("local_file"),
                "archive_url": source.get("archive_url"),
                "type": source.get("source_type"),
                "description": source.get("description"),
                "access_method": source.get("access_method"),
                "origin": "fact_check",
            })
        raw_sources.extend({"url": source, "origin": "fact_check"} for source in check["sources"])

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
            "origin": source["origin"],
        }
        records.append({key: value for key, value in record.items() if value})
    return records


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


def input_hashes(paths: list[Path]) -> dict[str, str]:
    return {f"data/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def ordered_findings(findings_doc: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    findings = [item for item in findings_doc.get("findings", []) if isinstance(item, dict)]
    by_id = {text(item.get("id")): item for item in findings}
    order = [text(fid) for fid in draft.get("finding_order", [])]
    return [by_id[fid] for fid in order if fid in by_id]


def local_tier() -> bool:
    return (os.environ.get("SPOTLIGHT_MODEL_TIER", "").strip() or "12b") != "frontier"


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


def mermaid_text(value: Any, limit: int = 160) -> str:
    """Collapse model/case text into one inert Mermaid label line."""
    label = re.sub(r"\s+", " ", text(value)).strip()
    label = label.translate(str.maketrans({
        '"': "'", "`": "", "\\": "", "[": "(", "]": ")",
        "{": "(", "}": ")", "|": "/", "<": "‹", ">": "›", ";": ",", "%": "",
    }))
    return label if len(label) <= limit else label[:limit - 1].rstrip() + "…"


def mermaid_node(node_id: str, label: str, role: str) -> str:
    """Return one static Mermaid node declaration for a structural role."""
    quoted = f'"{mermaid_text(label)}"'
    if role in {"source", "leaf"}:
        return f"  {node_id}([{quoted}])"
    if role == "sink":
        return f"  {node_id}[[{quoted}]]"
    return f"  {node_id}[{quoted}]"


def diagram_role(
    diagram_type: str, node: str, incoming: dict[str, int], outgoing: dict[str, int]
) -> str:
    """Derive visual roles from topology without accepting model styling."""
    if diagram_type == "flow":
        if incoming.get(node, 0) == 0:
            return "source"
        if outgoing.get(node, 0) == 0:
            return "sink"
        return "intermediary"
    if diagram_type == "hierarchy":
        if incoming.get(node, 0) == 0:
            return "root"
        if outgoing.get(node, 0) == 0:
            return "leaf"
        return "branch"
    return "entity"


def ordered_loop_connections(
    connections: list[tuple[str, str, str]], focal_entity: str
) -> list[tuple[str, str, str]]:
    """Declare a validated simple loop from its chosen starting entity."""
    by_source = {source: (source, target, relationship) for source, target, relationship in connections}
    ordered: list[tuple[str, str, str]] = []
    node = focal_entity
    for _ in range(len(connections)):
        connection = by_source.get(node)
        if connection is None:
            raise RenderError("loop diagram cannot be ordered from focal entity")
        ordered.append(connection)
        node = connection[1]
    if node != focal_entity:
        raise RenderError("loop diagram does not return to focal entity")
    return ordered


    if diagram_type == "timeline":
        return "milestone"
    if diagram_type == "bar":
        return "measure"
    return "entity"


def chart_text(value: Any, limit: int = 160) -> str:
    """Collapse case text into one inert Mermaid chart label line."""
    # Timeline/xychart lexers treat ":" and "#" structurally; strip them too.
    return mermaid_text(value, limit).translate(str.maketrans({":": ",", "#": ""}))


def observation_period(indicator: dict[str, Any]) -> tuple[str, str]:
    """Return (sort_key, period_label) for an indicator's recorded observation span."""
    start = text(indicator.get("first_observed"))[:10]
    end = text(indicator.get("last_observed"))[:10]
    if start and end:
        return start, f"{start} \→ {end}"
    if start or end:
        return start or end, start or end
    return text(indicator.get("id")), text(indicator.get("id"))


def compile_timeline_diagram(
    diagram: dict[str, Any], findings_doc: dict[str, Any]
) -> dict[str, Any]:
    """Compile validated indicator selections into a deterministic Mermaid timeline."""
    available: list[dict[str, Any]] = [
        item for item in (findings_doc.get("technical_indicators") or [])
        if isinstance(item, dict) and text(item.get("id"))
    ]
    requested = [text(value) for value in diagram.get("indicator_ids", [])]
    if not requested:
        raise RenderError("timeline diagram selects no technical indicators")
    if len(requested) > 12:
        raise RenderError(f"timeline diagram has {len(requested)} points; limit is 12")
    points: list[tuple[str, str, str, str]] = []
    for indicator_id in requested:
        matches = [item for item in available if text(item.get("id")) == indicator_id]
        if not matches:
            raise RenderError(
                f"timeline indicator does not resolve to technical_indicators: {indicator_id!r}"
            )
        if len(matches) > 1:
            raise RenderError(
                f"timeline indicator resolves ambiguously in technical_indicators: {indicator_id!r}"
            )
        indicator = matches[0]
        sort_key, period = observation_period(indicator)
        event = chart_text(f"{text(indicator.get('value'))} ({text(indicator.get('type'))})")
        points.append((sort_key, indicator_id, period, event))
    # A timeline's documented canonical order is chronological: first_observed,
    # then indicator id as the tiebreaker.
    points.sort()
    source = [
        "timeline",
        f"  accTitle: {chart_text(diagram.get('title'), 180)}",
        f"  accDescr: {chart_text(diagram.get('caption'), 500)}",
    ]
    for _sort_key, _indicator_id, period, event in points:
        source.append(f"  {chart_text(period, 96)} : {event}")
    return {
        "id": text(diagram.get("id")),
        "title": text(diagram.get("title")),
        "caption": text(diagram.get("caption")),
        "finding_ids": [text(finding_id) for finding_id in diagram.get("finding_ids", [])],
        "source": "\n".join(source),
        "legend": [],
        "connections": [],
        "connection_rows": [],
        "data_points": [[period, event] for _s, _i, period, event in points],
    }


def compile_bar_diagram(
    diagram: dict[str, Any], findings_doc: dict[str, Any], checks: list[dict[str, Any]],
    verdict_for: Any = None,
) -> dict[str, Any]:
    """Compile a validated metric selection into a deterministic Mermaid xychart bar chart."""
    metric = text(diagram.get("metric"))
    if metric not in {"verdict_tally", "confidence_distribution", "source_types"}:
        raise RenderError(f"unsupported bar metric: {metric!r}")
    scope = text(diagram.get("scope")) or "all"
    if scope not in {"all", "finding_ids"}:
        raise RenderError(f"unsupported bar scope: {scope!r}")
    findings = [row for row in findings_doc.get("findings", []) if isinstance(row, dict)]
    if scope == "finding_ids":
        selected_ids = {text(fid) for fid in diagram.get("finding_ids", [])}
        findings = [row for row in findings if text(row.get("id")) in selected_ids]
    if not findings:
        raise RenderError("bar series is empty: no findings in scope")

    verdicts = [verdict_for(row) if verdict_for else aggregate_verdict(row, checks) for row in findings]
    if metric == "verdict_tally":
        counts = {status: 0 for status in VERDICT_LABEL}
        for verdict in verdicts:
            counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
        # Batch 1.1's five verdicts always show; a legacy verdict shows only
        # when an old case actually records it.
        categories = [(VERDICT_LABEL[status], counts[status]) for status in VERDICT_LABEL
                      if status in VERDICTS_1_1 or counts[status]]
        axis_title = "Findings"
    elif metric == "confidence_distribution":
        confidence_labels = {"low": "Low", "medium": "Medium", "high": "High"}
        counts = {level: 0 for level in confidence_labels}
        for verdict in verdicts:
            counts[verdict["confidence"]] = counts.get(verdict["confidence"], 0) + 1
        categories = [(confidence_labels[level], counts[level]) for level in confidence_labels]
        axis_title = "Findings"
    else:
        tally: dict[str, int] = {}
        for row in findings:
            sources = row.get("sources") if isinstance(row.get("sources"), list) else []
            for source in sources:
                if isinstance(source, dict) and text(source.get("type")):
                    source_type = text(source.get("type"))
                    tally[source_type] = tally.get(source_type, 0) + 1
        if not tally:
            raise RenderError("bar series is empty: no typed sources in scope")
        # Source types have no documented canonical order: sort lexicographically.
        categories = sorted(tally.items())
        axis_title = "Sources"

    peak = max(count for _label, count in categories)
    if peak <= 0:
        raise RenderError("bar series is empty: every category counts zero")
    source = [
        "xychart-beta",
        f"  accTitle: {chart_text(diagram.get('title'), 180)}",
        f"  accDescr: {chart_text(diagram.get('caption'), 500)}",
        "  x-axis [" + ", ".join(f'"{chart_text(label, 96)}"' for label, _count in categories) + "]",
        f'  y-axis "{axis_title}" 0 --> {peak}',
        "  bar [" + ", ".join(str(count) for _label, count in categories) + "]",
    ]
    return {
        "id": text(diagram.get("id")),
        "title": text(diagram.get("title")),
        "caption": text(diagram.get("caption")),
        "finding_ids": [text(finding_id) for finding_id in diagram.get("finding_ids", [])],
        "source": "\n".join(source),
        "legend": [],
        "connections": [],
        "connection_rows": [],
        "data_points": [[label, str(count)] for label, count in categories],
    }


def compile_diagrams(
    draft: dict[str, Any], findings_doc: dict[str, Any], checks: list[dict[str, Any]],
    verdict_for: Any = None,
) -> list[dict[str, Any]]:
    """Compile validated connection selections into safe, deterministic Mermaid."""
    available: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    basis_of: dict[tuple[str, str, str], str] = {}
    for connection in findings_doc.get("connections", []):
        if not isinstance(connection, dict):
            continue
        triple = tuple(text(connection.get(field)) for field in ("from", "to", "relationship"))
        if all(triple):
            available.setdefault(triple, []).append(connection)
            basis = text(connection.get("basis"))
            if basis in CONNECTION_BASIS:
                basis_of[triple] = basis

    compiled: list[dict[str, Any]] = []
    for diagram in draft.get("diagrams", []):
        if not isinstance(diagram, dict):
            continue
        diagram_type = text(diagram.get("type"))
        if diagram_type == "timeline":
            compiled.append(compile_timeline_diagram(diagram, findings_doc))
            continue
        if diagram_type == "bar":
            compiled.append(compile_bar_diagram(diagram, findings_doc, checks, verdict_for))
            continue
        selected: list[tuple[str, str, str]] = []
        for selector in diagram.get("connections", []):
            if not isinstance(selector, dict):
                raise RenderError("diagram connection selector is not an object")
            triple = tuple(text(selector.get(field)) for field in ("from", "to", "relationship"))
            if len(available.get(triple, [])) != 1:
                raise RenderError(f"diagram selector does not resolve exactly once: {triple!r}")
            selected.append(triple)

        focal_entities = [text(entity) for entity in diagram.get("focal_entities", [])]
        ordered_connections = (
            ordered_loop_connections(selected, focal_entities[0])
            if diagram_type == "loop" and focal_entities else sorted(selected)
        )
        if diagram_type == "loop":
            nodes = [ordered_connections[0][0]] + [connection[1] for connection in ordered_connections]
            nodes = list(dict.fromkeys(nodes))
        else:
            nodes = sorted({node for source, target, _relationship in selected for node in (source, target)})
        node_ids = {node: f"n{index}" for index, node in enumerate(nodes, 1)}
        incoming = {node: 0 for node in nodes}
        outgoing = {node: 0 for node in nodes}
        for source, target, _relationship in selected:
            outgoing[source] += 1
            incoming[target] += 1

        groups = [
            (text(group.get("label")), [text(entity) for entity in group.get("entities", [])])
            for group in diagram.get("groups", []) if isinstance(group, dict)
        ]
        grouped = {entity for _label, entities in groups for entity in entities}
        # Grouped diagrams read top-down so their groups sit side by side as columns.
        direction = "TB" if diagram_type == "hierarchy" or groups else "LR"
        # ELK flattens clusters into one long row; dagre keeps a group together.
        layout = "dagre" if diagram_type == "loop" or groups else "elk"
        title = mermaid_text(diagram.get("title"), 180)
        caption = mermaid_text(diagram.get("caption"), 500)
        source = [
            "---",
            "config:",
            f"  layout: {layout}",
            "---",
            f"flowchart {direction}",
            f"  accTitle: {title}",
            f"  accDescr: {caption}",
        ]
        roles = {node: diagram_role(diagram_type, node, incoming, outgoing) for node in nodes}
        for group_index, (group_label, entities) in enumerate(groups, 1):
            source.append(f'  subgraph g{group_index}["{mermaid_text(group_label, 60)}"]')
            source.extend("  " + mermaid_node(node_ids[node], node, roles[node])
                          for node in nodes if node in entities)
            source.append("  end")
        source.extend(mermaid_node(node_ids[node], node, roles[node]) for node in nodes if node not in grouped)
        for source_node, target_node, relationship in ordered_connections:
            arrow = EDGE_BY_BASIS[basis_of.get((source_node, target_node, relationship), "")]
            source.append(
                f"  {node_ids[source_node]} {arrow}|\"{mermaid_text(relationship, 96).replace('_', ' ')}\"| {node_ids[target_node]}"
            )
        for node in nodes:
            classes = [roles[node]]
            if node in focal_entities:
                classes.append("focal")
            source.append(f"  class {node_ids[node]} {','.join(classes)};")
        if diagram_type == "loop":
            source.append(
                f"  linkStyle {len(ordered_connections) - 1} stroke:#6b6258,stroke-width:2px,stroke-dasharray:5 4;"
            )

        legend_labels = {
            "source": "source", "intermediary": "intermediary", "sink": "recipient",
            "root": "root", "branch": "branch", "leaf": "leaf", "entity": "entity",
            "focal": "focus",
        }
        legend_roles = list(dict.fromkeys([roles[node] for node in nodes] + (["focal"] if focal_entities else [])))
        bases = [basis for basis in CONNECTION_BASIS
                 if any(basis_of.get(connection) == basis for connection in ordered_connections)]
        compiled.append({
            "id": text(diagram.get("id")),
            "title": text(diagram.get("title")),
            "caption": text(diagram.get("caption")),
            "finding_ids": [text(finding_id) for finding_id in diagram.get("finding_ids", [])],
            "connections": ordered_connections,
            "connection_rows": [(*connection, basis_of.get(connection, "")) for connection in ordered_connections],
            "source": "\n".join(source),
            "legend": [(role, legend_labels[role]) for role in legend_roles]
                      + [(f"basis-{basis}", report_html.BASIS_LEGEND[basis]) for basis in bases],
        })
    return compiled


def diagram_markdown(diagrams: list[dict[str, Any]]) -> list[str]:
    """Render deterministic Mermaid figures into the Markdown report."""
    if not diagrams:
        return []
    lines = ["", "## Diagrams"]
    for diagram in diagrams:
        lines += ["", f"### {md_safe(diagram['title'])}", "", md_safe(diagram["caption"]), ""]
        if diagram["finding_ids"]:
            lines += ["Supporting findings: " + ", ".join(md_code(fid) for fid in diagram["finding_ids"]), ""]
        lines += ["```mermaid", diagram["source"], "```"]
        if diagram["connections"]:
            lines += ["", "Connections shown:"]
            for source, target, relationship in diagram["connections"]:
                lines.append(
                    f"- {md_safe(source)} → {md_safe(relationship)} → {md_safe(target)}"
                )
        elif diagram.get("data_points"):
            lines += ["", "Data shown:"]
            for label, detail in diagram["data_points"]:
                lines.append(f"- {md_safe(label)} — {md_safe(detail)}")
    return lines


def render_markdown(case: Path, findings_doc: dict[str, Any], methodology: dict[str, Any],
                    draft: dict[str, Any], checks: list[dict[str, Any]],
                    expressions_doc: dict[str, Any] | None = None,
                    diagrams: list[dict[str, Any]] | None = None,
                    effective: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
                    ) -> tuple[str, list[dict[str, Any]]]:
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
        *([f"> **{LOCAL_MODEL_WARNING}**", ""] if local_tier() else []),
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
        verdict, signal = (effective or {}).get(fid) or (aggregate_verdict(finding, checks), None)
        sources = source_records(case, finding, verdict)
        quotes = selected_expressions(treatment, fid, expressions)
        record = {"id": fid, "finding": finding, "treatment": treatment,
                  "verdict": verdict, "sources": sources, "quotes": quotes,
                  "source_expressions": linked_by_finding.get(fid, []),
                  "decision_signal": signal}
        rendered.append(record)
        lines.append(
            f"| {md_cell(fid)} | {md_cell(treatment.get('headline'))} | "
            f"{VERDICT_LABEL.get(verdict['status'], verdict['status'].title())} | "
            f"{verdict['confidence'].title()} |"
        )

    lines += diagram_markdown(diagrams or [])
    lines += ["", "## Detailed Findings"]
    for record in rendered:
        finding, treatment = record["finding"], record["treatment"]
        verdict, sources = record["verdict"], record["sources"]
        lines += [
            "",
            f"### {md_safe(record['id'])}: {md_safe(treatment.get('headline'))}",
            "",
            *([md_safe(treatment.get("summary")), ""] if text(treatment.get("summary")) else []),
            f"- **Canonical fact-checked claim:** {md_safe(finding.get('claim'))}",
            *([f"- **Why it matters:** {md_safe(treatment.get('why_it_matters'))}"] if text(treatment.get("why_it_matters")) else []),
            f"- **Verdict:** {VERDICT_LABEL.get(verdict['status'], verdict['status'].title())}",
            f"- **Report confidence:** {verdict['confidence'].title()}",
        ]
        if record["decision_signal"]:
            lines += ["- **Decision check:** " + md_safe(decision_check_line(record["decision_signal"], verdict))]
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


def decision_check_line(signal: dict[str, Any], verdict: dict[str, Any]) -> str:
    """One editor-facing line describing a decision-model signal (never evidence)."""
    status = signal.get("status")
    mode = signal.get("mode", "advisory")
    if status == "stale":
        return "stale: the finding changed after the check, so it was not applied."
    if status == "unavailable":
        return "unavailable: no signal was recorded."
    if status == "routed":
        return (f"routed to the existing fact-check process ({text(signal.get('route_reason'))}); "
                "the decision model did not judge this finding.")
    flags = [text(flag.get("reason")) for flag in signal.get("flags", []) if isinstance(flag, dict)]
    head = f"{mode}; support {text(signal.get('support')) or 'n/a'}"
    if not flags:
        return head + "; no flags. Classification signal, not verification."
    applied = f"; confidence capped at {verdict['decision_cap'].title()}" if verdict.get("decision_cap") else ""
    return head + "; flags: " + ", ".join(flags) + applied + ". Classification signal, not verification."


def template_css() -> str:
    try:
        template = TEMPLATE.read_text()
    except OSError as exc:
        raise RenderError(f"cannot read canonical report template: {TEMPLATE}") from exc
    match = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    if not match:
        raise RenderError(f"canonical report template has no <style> block: {TEMPLATE}")
    return match.group(1).strip()


def arbiter_appendix(case: Path) -> str:
    """Optional analytics section for a case that used the Arbiter integration.

    The check is deliberately minimal: a saved ``research/arbiter-report-*.json``
    response is the signal that Arbiter was used, and the markup, styling, and
    chart geometry for it all live in the integration rather than here.

    Args:
        case: The resolved case directory.

    Returns:
        The HTML fragment printed by ``integrations/arbiter/run_appendix.py``, or
        the empty string when the case carries no saved Arbiter report or the
        integration cannot produce a fragment. Every failure degrades to the
        empty string with a warning on stderr, so an absent, broken, or slow
        integration can never fail a report render.
    """
    if not any((case / "research").glob("arbiter-report-*.json")):
        return ""
    if not ARBITER_APPENDIX.is_file():
        print(f"WARN  arbiter appendix skipped: no {ARBITER_APPENDIX}", file=sys.stderr)
        return ""
    try:
        appendix = subprocess.run(
            [sys.executable, str(ARBITER_APPENDIX), "--case-dir", str(case)],
            capture_output=True, text=True, timeout=ARBITER_APPENDIX_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARN  arbiter appendix skipped: {exc}", file=sys.stderr)
        return ""
    if appendix.returncode != 0:
        detail = (appendix.stderr or appendix.stdout).strip().splitlines()
        reason = f" — {detail[-1]}" if detail else ""
        print(f"WARN  arbiter appendix skipped: exit {appendix.returncode}{reason}",
              file=sys.stderr)
        return ""
    return appendix.stdout.strip()


def report_sections(draft: dict[str, Any], rendered: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Group the findings table by the draft's sections, or show one group.

    validate-report-draft.py proves the sections partition finding_order;
    within a section, findings keep finding_order.
    """
    order = [record["id"] for record in rendered]
    sections = [item for item in draft.get("sections", []) if isinstance(item, dict)]
    if not sections:
        return [("Findings", order)]
    position = {fid: index for index, fid in enumerate(order)}
    return [
        (text(item.get("label")), sorted((text(fid) for fid in item.get("finding_ids", [])
                                          if text(fid) in position), key=position.__getitem__))
        for item in sections
    ]


def render_html(case: Path, findings_doc: dict[str, Any], methodology: dict[str, Any],
                draft: dict[str, Any], rendered: list[dict[str, Any]], hashes: dict[str, str],
                diagrams: list[dict[str, Any]] | None = None) -> str:
    """Resolve the page's inputs; ``report_html`` lays them out."""
    anchors = {record["id"]: report_html.finding_anchor(index, record["id"])
               for index, record in enumerate(rendered, 1)}
    cycle = findings_doc.get("cycle")
    page = report_html.ReportPage(
        case_name=case.name,
        language=text(draft.get("language")),
        title=text(draft.get("title")),
        deck=text(draft.get("deck")),
        report_date=text(findings_doc.get("investigated_at") or methodology.get("planned_at"))[:10],
        cycle=str(cycle) if isinstance(cycle, int) and not isinstance(cycle, bool) else "",
        records=rendered,
        verdict_labels=VERDICT_LABEL,
        sections=report_sections(draft, rendered),
        framing_ids=[text(fid) for fid in draft.get("framing_finding_ids", [])],
        diagrams=diagrams or [],
        caveats=editorial_items(draft, "caveats"),
        next_steps=editorial_items(draft, "next_steps"),
        directions=[item for item in methodology.get("investigation_plan", []) if isinstance(item, dict)],
        limitations=list_of_text(methodology.get("limitations")),
        ai_notice=AI_NOTICE,
        local_warning=LOCAL_MODEL_WARNING if local_tier() else "",
        hashes=hashes,
        css=template_css(),
        appendix=arbiter_appendix(case),
        mermaid_url=MERMAID_URL,
        elk_url=ELK_LAYOUT_URL,
        anchors=anchors,
    )
    return report_html.render_page(page)


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
        signal = record.get("decision_signal")
        if signal:
            claims[-1]["decision_signal"] = {
                "status": signal.get("status"),
                "mode": signal.get("mode"),
                "support": signal.get("support"),
                "cap": signal.get("cap"),
                "applied_cap": verdict.get("decision_cap"),
                "flags": signal.get("flags", []),
                "facets": signal.get("facets", {}),
                # Grounding inputs (claim, evidence, located source excerpts) at render time;
                # validate-report recomputes it so a later source edit cannot hide behind the ledger.
                "dependency_sha256": signal.get("current_input_sha256"),
            }
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
    rendered_ids = {record["id"] for record in rendered}
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
                # Only links to reported findings: a set-aside finding's id stays out.
                "finding_links": [
                    link for link in expression.get("finding_links", [])
                    if isinstance(link, dict) and text(link.get("finding_id")) in rendered_ids
                ],
                "lifecycle": expression.get("lifecycle_events", []),
                "lifecycle_state": expression_state(expression),
            }
            for expression_id, expression in expressions_by_id.items()
        ]
    return result


def publish_outputs(case: Path, outputs: dict[str, str]) -> None:
    atomic_write_files(
        case,
        {name: content.encode("utf-8") for name, content in outputs.items()},
    )


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
    # Findings left out at the Investigation gate stay in the case and never
    # reach the report, its charts or its evidence map.
    set_aside = set_aside_findings(case) & seen_ids
    if set_aside:
        findings = [finding for finding in findings if text(finding.get("id")) not in set_aside]
        # Indicators recorded for a set-aside finding are left out with it.
        indicators = [
            item for item in (findings_doc.get("technical_indicators") or [])
            if not (isinstance(item, dict) and text(item.get("finding_id")) in set_aside)
        ]
        findings_doc = {**findings_doc, "findings": findings, "technical_indicators": indicators}
        seen_ids -= set_aside
    gate_review_path = case / "data" / review_file("investigation_gate")

    checks = canonical_checks(fact_check)
    inputs = [findings_path, fact_check_path, draft_path] + ([methodology_path] if methodology_path.is_file() else [])
    if gate_review_path.is_file():
        inputs.append(gate_review_path)
    if activated:
        inputs.append(expressions_path)
    signals_path = case / decision_signals.SIGNALS_PATH
    try:
        signals = decision_signals.load_signals(case)
    except decision_signals.SignalsError as exc:
        raise RenderError(f"data/decision-signals.json is invalid: {exc}") from exc
    if signals is not None:
        inputs.append(signals_path)
    hashes = input_hashes(inputs)
    # One effective verdict per finding (fact-check verdict plus any enforced
    # decision cap), shared by charts, finding cards, Markdown and the ledger.
    this_module = sys.modules[__name__]
    effective = {
        text(finding.get("id")): decision_signals.effective_verdict(this_module, case, finding, checks, signals)
        for finding in findings
    }
    diagrams = compile_diagrams(draft, findings_doc, checks, lambda row: effective[text(row.get("id"))][0])
    markdown, rendered = render_markdown(
        case, findings_doc, methodology, draft, checks, expressions_doc, diagrams, effective
    )
    rendered_ids = [record["id"] for record in rendered]
    expected_order = [text(fid) for fid in draft.get("finding_order", [])]
    if rendered_ids != expected_order or set(rendered_ids) != seen_ids:
        raise RenderError(
            "rendered finding IDs do not exactly match finding_order and findings.json"
        )
    html_report = render_html(case, findings_doc, methodology, draft, rendered, hashes, diagrams)
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
    except (OSError, OrchestrationError, RenderError) as exc:
        print(f"FAIL  report render: {exc}")
        return 3
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"report render: OK — {result['findings']} finding(s), 3 canonical artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
