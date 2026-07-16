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
import sys
from pathlib import Path
from typing import Any


MAX_TEXT = {
    "title": 180,
    "deck": 900,
    "headline": 240,
    "summary": 1400,
    "why_it_matters": 900,
    "list_item": 700,
}


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
        lifecycle = expression.get("lifecycle_events")
        if (
            isinstance(lifecycle, list)
            and lifecycle
            and isinstance(lifecycle[-1], dict)
            and lifecycle[-1].get("event") == "activated"
            and clean(expression.get("id"))
        ):
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
        linked = any(
            isinstance(link, dict) and clean(link.get("finding_id")) == finding_id
            for link in expression.get("finding_links", [])
        )
        if not linked:
            failures.append(
                f"STRUCTURE: source expression {expression_id!r} is not linked to finding "
                f"{finding_id!r}"
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
        "finding_order", "finding_treatments", "caveats", "next_steps",
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
