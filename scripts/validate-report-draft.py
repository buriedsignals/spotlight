#!/usr/bin/env python3
"""Validate the language-neutral model-authored editorial plan.

This gate proves structural claims only: every prose block is attached to known,
fact-checked finding IDs; every finding is covered exactly once; and generated-file
fields stay bounded. Semantic support remains an independent editorial/fact-check
judgment. The renderer always places canonical verdicts beside model prose.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from source_expression_contract import lifecycle_state


MAX_TEXT = {
    "title": 180,
    "deck": 900,
    "headline": 240,
    "summary": 1400,
    "why_it_matters": 900,
    "list_item": 700,
    "diagram_title": 180,
    "diagram_caption": 500,
}
DIAGRAM_TYPES = {"flow", "hierarchy", "network", "loop"}
DIAGRAM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"missing required model-authored input: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_text(label: str, value: Any, limit: int, failures: list[str]) -> str:
    prose = clean(value)
    if not prose:
        failures.append(f"STRUCTURE: {label} must be a non-empty string")
    elif len(prose) > limit:
        failures.append(f"STRUCTURE: {label} is {len(prose)} characters; limit is {limit}")
    return prose


def validate_refs(
    label: str, value: Any, known_ids: set[str], failures: list[str], *, exact: bool = False
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        failures.append(f"STRUCTURE: {label} must be an array of finding IDs")
        return []
    refs: list[str] = []
    for index, item in enumerate(value):
        normalized = item.strip()
        if not normalized:
            failures.append(f"STRUCTURE: {label}[{index}] must be a non-empty finding ID")
            continue
        if item != normalized:
            failures.append(
                f"STRUCTURE: {label}[{index}] has surrounding whitespace; use {normalized!r}"
            )
        refs.append(normalized)
    if not refs:
        failures.append(f"STRUCTURE: {label} must reference at least one finding")
    if len(refs) != len(set(refs)):
        failures.append(f"STRUCTURE: {label} contains duplicate finding IDs")
    missing = sorted(set(refs) - known_ids)
    if missing:
        failures.append(f"STRUCTURE: {label} references unknown finding IDs: {missing}")
    if exact and set(refs) != known_ids:
        absent = sorted(known_ids - set(refs))
        extra = sorted(set(refs) - known_ids)
        failures.append(
            f"STRUCTURE: {label} must contain every finding exactly once; "
            f"missing={absent}, extra={extra}"
        )
    return refs


def validate_editorial_list(
    field: str, value: Any, known_ids: set[str], failures: list[str]
) -> None:
    if not isinstance(value, list):
        failures.append(f"STRUCTURE: {field} must be an array")
        return
    if len(value) > 12:
        failures.append(f"STRUCTURE: {field} has {len(value)} items; limit is 12")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            failures.append(f"STRUCTURE: {field}[{index}] must be an object")
            continue
        unknown = sorted(set(item) - {"text", "finding_ids"})
        if unknown:
            failures.append(f"STRUCTURE: {field}[{index}] has unknown field(s): {unknown}")
        validate_text(f"{field}[{index}].text", item.get("text"), MAX_TEXT["list_item"], failures)
        validate_refs(f"{field}[{index}].finding_ids", item.get("finding_ids"), known_ids, failures)


def active_expression_ids(expressions: dict[str, Any]) -> set[str]:
    active: set[str] = set()
    for expression in expressions.get("expressions", []):
        if not isinstance(expression, dict):
            continue
        if lifecycle_state(expression) == "activated" and clean(expression.get("id")):
            active.add(clean(expression["id"]))
    return active


def validate_quote_selections(
    treatment: dict[str, Any],
    index: int,
    finding_id: str,
    activated: bool,
    expressions: dict[str, Any] | None,
    failures: list[str],
) -> None:
    selections = treatment.get("quote_selections")
    if selections is None:
        return
    label = f"finding_treatments[{finding_id or index}].quote_selections"
    if not isinstance(selections, list):
        failures.append(f"STRUCTURE: {label} must be an array")
        return
    if not activated or expressions is None:
        failures.append(
            f"STRUCTURE: {label} requires an activated 1.1 case with source expressions"
        )
        return
    by_id = {
        clean(item.get("id")): item
        for item in expressions.get("expressions", [])
        if isinstance(item, dict) and clean(item.get("id"))
    }
    active = active_expression_ids(expressions)
    seen: set[str] = set()
    for selection_index, selection in enumerate(selections):
        item_label = f"{label}[{selection_index}]"
        if not isinstance(selection, dict):
            failures.append(f"STRUCTURE: {item_label} must be an object")
            continue
        unknown = sorted(set(selection) - {"expression_id"})
        if unknown:
            failures.append(
                f"STRUCTURE: {item_label} may contain expression_id only; unknown field(s): {unknown}"
            )
        expression_id = clean(selection.get("expression_id"))
        if not expression_id:
            failures.append(f"STRUCTURE: {item_label}.expression_id must be non-empty")
            continue
        if selection.get("expression_id") != expression_id:
            failures.append(
                f"STRUCTURE: {item_label}.expression_id has surrounding whitespace; "
                f"use {expression_id!r}"
            )
        if expression_id in seen:
            failures.append(f"STRUCTURE: {label} contains duplicate expression IDs")
        seen.add(expression_id)
        expression = by_id.get(expression_id)
        if expression is None:
            failures.append(
                f"STRUCTURE: {item_label} references unknown source expression {expression_id!r}"
            )
            continue
        if expression_id not in active:
            failures.append(
                f"STRUCTURE: {item_label} references inactive source expression {expression_id!r}"
            )
        if expression.get("direct_quote") is not True:
            failures.append(
                f"STRUCTURE: {item_label} requires a direct_quote source expression"
            )
        if expression.get("derivative_type") == "translation":
            failures.append(
                f"STRUCTURE: {item_label} cannot publish a translation as a direct quotation"
            )
        linked = any(
            isinstance(link, dict) and clean(link.get("finding_id")) == finding_id
            for link in expression.get("finding_links", [])
        )
        if not linked:
            failures.append(
                f"STRUCTURE: source expression {expression_id!r} is not linked to finding "
                f"{finding_id!r}"
            )


def has_directed_cycle(connections: list[tuple[str, str, str]]) -> bool:
    """Return whether a directed connection selection contains a cycle."""
    adjacency: dict[str, set[str]] = {}
    for source, target, _relationship in connections:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(target) for target in adjacency[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(adjacency))


def is_simple_loop(
    connections: list[tuple[str, str, str]], focal_entity: str
) -> bool:
    """Require one directed cycle that begins and ends at the declared focal node."""
    if not connections:
        return False
    outgoing: dict[str, str] = {}
    incoming: dict[str, str] = {}
    nodes: set[str] = set()
    for source, target, _relationship in connections:
        if source in outgoing or target in incoming:
            return False
        outgoing[source] = target
        incoming[target] = source
        nodes.update({source, target})
    if focal_entity not in nodes or set(outgoing) != nodes or set(incoming) != nodes:
        return False
    visited: set[str] = set()
    node = focal_entity
    for _ in range(len(nodes)):
        if node in visited:
            return False
        visited.add(node)
        node = outgoing.get(node, "")
    return node == focal_entity and visited == nodes


def validate_diagrams(
    value: Any,
    known_ids: set[str],
    findings_connections: Any,
    failures: list[str],
) -> None:
    """Validate bounded report diagrams against current canonical connections."""
    if value is None:
        return
    if not isinstance(value, list):
        failures.append("STRUCTURE: diagrams must be an array")
        return

    canonical: dict[tuple[str, str, str], int] = {}
    if isinstance(findings_connections, list):
        for connection in findings_connections:
            if not isinstance(connection, dict):
                continue
            triple = tuple(clean(connection.get(field)) for field in ("from", "to", "relationship"))
            if all(triple):
                canonical[triple] = canonical.get(triple, 0) + 1

    seen_ids: set[str] = set()
    for index, diagram in enumerate(value):
        label = f"diagrams[{index}]"
        if not isinstance(diagram, dict):
            failures.append(f"STRUCTURE: {label} must be an object")
            continue
        unknown = sorted(
            set(diagram) - {"id", "type", "title", "caption", "finding_ids", "connections", "focal_entities"}
        )
        if unknown:
            failures.append(f"STRUCTURE: {label} has unknown field(s): {unknown}")

        diagram_id = clean(diagram.get("id"))
        if not diagram_id:
            failures.append(f"STRUCTURE: {label}.id must be a non-empty string")
        elif diagram.get("id") != diagram_id:
            failures.append(f"STRUCTURE: {label}.id has surrounding whitespace; use {diagram_id!r}")
        elif not DIAGRAM_ID_RE.fullmatch(diagram_id):
            failures.append(f"STRUCTURE: {label}.id must use letters, digits, hyphens, or underscores")
        elif diagram_id in seen_ids:
            failures.append(f"STRUCTURE: diagrams contains duplicate id {diagram_id!r}")
        seen_ids.add(diagram_id)

        diagram_type = clean(diagram.get("type"))
        if diagram_type not in DIAGRAM_TYPES:
            failures.append(f"STRUCTURE: {label}.type must be one of {sorted(DIAGRAM_TYPES)}")
        validate_text(f"{label}.title", diagram.get("title"), MAX_TEXT["diagram_title"], failures)
        validate_text(f"{label}.caption", diagram.get("caption"), MAX_TEXT["diagram_caption"], failures)
        validate_refs(f"{label}.finding_ids", diagram.get("finding_ids"), known_ids, failures)

        selectors = diagram.get("connections")
        resolved: list[tuple[str, str, str]] = []
        if not isinstance(selectors, list):
            failures.append(f"STRUCTURE: {label}.connections must be an array")
        else:
            if not selectors:
                failures.append(f"STRUCTURE: {label}.connections must select at least one connection")
            if len(selectors) > 12:
                failures.append(f"STRUCTURE: {label}.connections has {len(selectors)} items; limit is 12")
            seen_selectors: set[tuple[str, str, str]] = set()
            for selector_index, selector in enumerate(selectors):
                selector_label = f"{label}.connections[{selector_index}]"
                if not isinstance(selector, dict):
                    failures.append(f"STRUCTURE: {selector_label} must be an object")
                    continue
                selector_unknown = sorted(set(selector) - {"from", "to", "relationship"})
                if selector_unknown:
                    failures.append(
                        f"STRUCTURE: {selector_label} has unknown field(s): {selector_unknown}"
                    )
                triple = tuple(clean(selector.get(field)) for field in ("from", "to", "relationship"))
                if not all(triple):
                    failures.append(
                        f"STRUCTURE: {selector_label} requires non-empty from, to, and relationship"
                    )
                    continue
                for field, normalized in zip(("from", "to", "relationship"), triple):
                    if selector.get(field) != normalized:
                        failures.append(
                            f"STRUCTURE: {selector_label}.{field} has surrounding whitespace; use {normalized!r}"
                        )
                if triple in seen_selectors:
                    failures.append(f"STRUCTURE: {label}.connections contains duplicate selector {triple!r}")
                seen_selectors.add(triple)
                matches = canonical.get(triple, 0)
                if matches != 1:
                    reason = "unknown" if matches == 0 else "ambiguous"
                    failures.append(f"STRUCTURE: {selector_label} is {reason} in findings.json.connections")
                    continue
                resolved.append(triple)

        nodes = {node for source, target, _relationship in resolved for node in (source, target)}
        if len(nodes) > 9:
            failures.append(f"STRUCTURE: {label} has {len(nodes)} nodes; limit is 9")
        focal = diagram.get("focal_entities", [])
        focal_entities: list[str] = []
        if not isinstance(focal, list):
            failures.append(f"STRUCTURE: {label}.focal_entities must be an array")
        else:
            if len(focal) > 2:
                failures.append(f"STRUCTURE: {label}.focal_entities has {len(focal)} items; limit is 2")
            for focal_index, raw_entity in enumerate(focal):
                entity = clean(raw_entity)
                focal_label = f"{label}.focal_entities[{focal_index}]"
                if not entity:
                    failures.append(f"STRUCTURE: {focal_label} must be a non-empty endpoint label")
                    continue
                if raw_entity != entity:
                    failures.append(f"STRUCTURE: {focal_label} has surrounding whitespace; use {entity!r}")
                if entity in focal_entities:
                    failures.append(f"STRUCTURE: {label}.focal_entities contains duplicate endpoint {entity!r}")
                focal_entities.append(entity)
                if entity not in nodes:
                    failures.append(f"STRUCTURE: {focal_label} is not an endpoint in the selected connections")

        if diagram_type == "hierarchy" and has_directed_cycle(resolved):
            failures.append(f"STRUCTURE: {label}.type hierarchy cannot contain a directed cycle")
        if diagram_type == "loop":
            if len(focal_entities) != 1:
                failures.append(f"STRUCTURE: {label}.type loop requires exactly one focal_entities entry")
            elif not is_simple_loop(resolved, focal_entities[0]):
                failures.append(
                    f"STRUCTURE: {label}.type loop requires one simple directed cycle containing its focal entity"
                )


def check(case: Path) -> list[str]:
    failures: list[str] = []
    try:
        findings_doc = load_object(case / "data" / "findings.json")
        fact_check = load_object(case / "data" / "fact-check.json")
        draft = load_object(case / "data" / "report-draft.json")
    except ValueError as exc:
        return [f"STRUCTURE: {exc}"]

    findings = [row for row in findings_doc.get("findings", []) if isinstance(row, dict)]
    known_ids = {clean(row.get("id")) for row in findings if clean(row.get("id"))}
    if not known_ids:
        return ["STRUCTURE: findings.json has no findings to present"]
    activated = findings_doc.get("schema_version") == "1.1" and (
        case / "data" / "case-contract.json"
    ).is_file()
    expressions: dict[str, Any] | None = None
    if activated:
        try:
            expressions = load_object(case / "data" / "source-expressions.json")
        except ValueError as exc:
            failures.append(f"STRUCTURE: {exc}")

    allowed_top = {
        "schema_version", "language", "title", "deck", "framing_finding_ids",
        "finding_order", "finding_treatments", "diagrams", "caveats", "next_steps",
    }
    if draft.get("schema_version") != "1.0":
        failures.append("STRUCTURE: schema_version must be '1.0'")
    unknown_top = sorted(set(draft) - allowed_top)
    if unknown_top:
        failures.append(f"STRUCTURE: unknown report-draft field(s): {unknown_top}")
    if "language" in draft and not (2 <= len(clean(draft.get("language"))) <= 35):
        failures.append("STRUCTURE: language must be a 2-35 character language tag")

    validate_text("title", draft.get("title"), MAX_TEXT["title"], failures)
    validate_text("deck", draft.get("deck"), MAX_TEXT["deck"], failures)
    validate_refs("framing_finding_ids", draft.get("framing_finding_ids"), known_ids, failures)
    order = validate_refs("finding_order", draft.get("finding_order"), known_ids, failures, exact=True)

    treatments = draft.get("finding_treatments")
    if not isinstance(treatments, list):
        failures.append("STRUCTURE: finding_treatments must be an array")
        treatments = []
    treatment_ids: list[str] = []
    for index, item in enumerate(treatments):
        if not isinstance(item, dict):
            failures.append(f"STRUCTURE: finding_treatments[{index}] must be an object")
            continue
        fid = clean(item.get("finding_id"))
        treatment_ids.append(fid)
        unknown = sorted(
            set(item)
            - {"finding_id", "headline", "summary", "why_it_matters", "quote_selections"}
        )
        if unknown:
            failures.append(f"STRUCTURE: finding_treatments[{index}] has unknown field(s): {unknown}")
        if fid not in known_ids:
            failures.append(f"STRUCTURE: finding_treatments[{index}] has unknown finding_id {fid!r}")
        if isinstance(item.get("finding_id"), str) and item["finding_id"] != fid:
            failures.append(
                f"STRUCTURE: finding_treatments[{index}].finding_id has surrounding whitespace; "
                f"use {fid!r}"
            )
        for field in ("headline", "summary", "why_it_matters"):
            validate_text(
                f"finding_treatments[{fid or index}].{field}",
                item.get(field),
                MAX_TEXT[field],
                failures,
            )
        validate_quote_selections(item, index, fid, activated, expressions, failures)
    if len(treatment_ids) != len(set(treatment_ids)):
        failures.append("STRUCTURE: finding_treatments contains duplicate finding IDs")
    if treatment_ids != order:
        failures.append("STRUCTURE: finding_treatments must follow finding_order exactly")

    validate_diagrams(draft.get("diagrams"), known_ids, findings_doc.get("connections"), failures)
    validate_editorial_list("caveats", draft.get("caveats"), known_ids, failures)
    validate_editorial_list("next_steps", draft.get("next_steps"), known_ids, failures)

    # The fact-check document is loaded here deliberately: the report plan may only
    # exist after the independent fact-check artifact exists. Evidence/verdict validity
    # itself is enforced by validate-fact-check.py before this stage.
    if not fact_check:
        failures.append("STRUCTURE: fact-check.json is empty")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    case = Path(args.case_dir)
    failures = [f"STRUCTURE: case dir not found: {case}"] if not case.is_dir() else check(case)
    if args.json:
        print(json.dumps({"passed": not failures, "failures": failures}, indent=2))
    else:
        for failure in failures:
            print(f"FAIL  {failure}")
        print("report draft: " + ("PASSED" if not failures else "FAILED"))
    return 3 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
