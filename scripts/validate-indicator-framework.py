#!/usr/bin/env python3
"""Check data/indicator-framework.json, the Indicator Framework's case file.

The Indicator Framework (a Navigator-hosted skill for Lab members) rates the
digital indicators behind findings and lists open items. It informs and never
grades, so this check proves only that the file holds together:

* it is a JSON object whose indicator, branch, conclusion and item ids are unique;
* every cross-reference resolves (an indicator's branch and parent element, a
  branch's indicators and strongest ones, a conclusion's branches, an item's
  indicators and conclusions), and every ``finding_ids`` entry is a finding in
  data/findings.json;
* quadrants, pairing statuses and item kinds use the framework's values, when
  the framework data sits beside this script (``../assets/framework.json``, as
  in the Lab-gated skill bundle); the public checkout carries none of it;
* no field carries a score, likelihood, probability or percentage, and no item
  arrives already assigned: the member assigns items on the Investigation gate
  page.

It reads only; it never touches findings' confidence or grounding.

Exit codes: 0 = the file holds together, or there is none; 3 = at least one failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FILE = "data/indicator-framework.json"
FRAMEWORK = Path(__file__).resolve().parent.parent / "assets" / "framework.json"
SCORE_KEY = re.compile(r"score|likelihood|probabilit|percent", re.IGNORECASE)
# "80%" or "12.5 %", but not a URL escape such as "%20".
PERCENTAGE = re.compile(r"\b\d+(?:\.\d+)?\s*%(?![0-9A-Fa-f]{2})")


def framework_values() -> dict[str, set[str]] | None:
    """The quadrant names (the matrix's cells), pairing statuses and item kinds."""
    try:
        framework = json.loads(FRAMEWORK.read_text(encoding="utf-8"))
        return {
            "quadrant": {cell["quadrant"] for cell in framework["matrix"]},
            "pairing_status": set(framework["pairing_statuses"]),
            "kind": {kind["kind"] for kind in framework["item_kinds"]},
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def entries(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key, [])
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def ids(values: object) -> list[object]:
    return values if isinstance(values, list) else []


def score_fields(value: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}/{key}"
            if SCORE_KEY.search(str(key)):
                found.append(here)
            found += score_fields(child, here)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found += score_fields(child, f"{path}/{index}")
    elif isinstance(value, str) and PERCENTAGE.search(value):
        found.append(f"{path} (a percentage)")
    return found


def finding_ids(case: Path) -> set[str]:
    try:
        findings = json.loads((case / "data/findings.json").read_text(encoding="utf-8")).get("findings", [])
    except (OSError, ValueError, AttributeError):
        return set()
    return {f["id"] for f in findings if isinstance(f, dict) and isinstance(f.get("id"), str)} if isinstance(findings, list) else set()


def validate(case: Path) -> list[str]:
    path = case / FILE
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [f"{FILE} is not valid JSON: {exc}"]
    if not isinstance(document, dict):
        return [f"{FILE} must be a JSON object"]
    failures: list[str] = []

    def unique(key: str, id_field: str) -> set[object]:
        seen: set[object] = set()
        for entry in entries(document, key):
            value = entry.get(id_field)
            if not isinstance(value, str) or not value:
                failures.append(f"{key}: an entry has no {id_field}")
            elif value in seen:
                failures.append(f"{key}: {id_field} {value} appears twice")
            seen.add(value)
        return seen

    indicators = unique("indicators", "id")
    branches = unique("branches", "branch_id")
    conclusions = unique("conclusions", "conclusion_id")
    unique("indicator_gaps", "item_id")
    findings = finding_ids(case)
    values = framework_values()

    def allowed(owner: str, field: str, value: object) -> None:
        if values is not None and value not in values[field]:
            failures.append(f"{owner}: {field} {value!r} is not one of {', '.join(sorted(values[field]))}")

    def resolve(owner: str, field: str, values: list[object], known: set[object], what: str) -> None:
        for value in values:
            if value not in known:
                failures.append(f"{owner} {field}: {value} is not {what}")

    for indicator in entries(document, "indicators"):
        owner = f"indicator {indicator.get('id')}"
        if indicator.get("branch_id") is not None:
            resolve(owner, "branch_id", [indicator["branch_id"]], branches, "a branch")
        if indicator.get("element_of") is not None:
            resolve(owner, "element_of", [indicator["element_of"]], indicators, "an indicator")
        resolve(owner, "finding_ids", ids(indicator.get("finding_ids")), findings, "a finding in data/findings.json")
        for rating in ("starting_rating", "current_rating"):
            value = indicator.get(rating)
            if isinstance(value, dict) and "quadrant" in value:
                allowed(f"{owner} {rating}", "quadrant", value["quadrant"])
        if "pairing_status" in indicator:
            allowed(owner, "pairing_status", indicator["pairing_status"])
    for branch in entries(document, "branches"):
        owner = f"branch {branch.get('branch_id')}"
        members = ids(branch.get("indicator_ids"))
        resolve(owner, "indicator_ids", members, indicators, "an indicator")
        resolve(owner, "strongest", ids(branch.get("strongest")), set(members), "one of its indicators")
    for conclusion in entries(document, "conclusions"):
        owner = f"conclusion {conclusion.get('conclusion_id')}"
        resolve(owner, "branches_present", ids(conclusion.get("branches_present")), branches, "a branch")
        resolve(owner, "finding_ids", ids(conclusion.get("finding_ids")), findings, "a finding in data/findings.json")
    for item in entries(document, "indicator_gaps"):
        owner = f"item {item.get('item_id')}"
        allowed(owner, "kind", item.get("kind"))
        resolve(owner, "indicator_ids", ids(item.get("indicator_ids")), indicators, "an indicator")
        resolve(owner, "conclusion_ids", ids(item.get("conclusion_ids")), conclusions, "a conclusion")
        if item.get("assign_to") is not None:
            failures.append(f"{owner}: assign_to must stay null; the member assigns items on the Investigation gate page")
    for field in score_fields(document):
        failures.append(f"{field}: the Indicator Framework carries no scores, likelihoods or percentages")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_dir")
    args = parser.parse_args()
    failures = validate(Path(args.case_dir))
    for failure in failures:
        print(f"FAIL  {failure}")
    print(f"indicator framework: {'OK' if not failures else 'FAILED'}")
    return 3 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
