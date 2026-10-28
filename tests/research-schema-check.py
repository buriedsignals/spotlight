#!/usr/bin/env python3
"""The research-phase file formats refuse what the phase forbids.

The samples in tests/fixtures/research/ validate (tests/eval.sh checks that);
each case below breaks one rule of a sample and must be refused.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

try:
    from jsonschema import Draft7Validator
except ImportError:
    if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
        raise
    print("skip research schema checks (jsonschema unavailable; required in CI)")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "research"
PAIRS = {
    "lead": ("research-lead.sample.json", "research-lead.schema.json"),
    "lane": ("research-lane.sample.json", "research-lane.schema.json"),
    "leads": ("research-leads.sample.json", "research-leads.schema.json"),
    "clues": ("research-clues.sample.json", "research-clues.schema.json"),
    "methodology": ("methodology-research.sample.json", "methodology.schema.json"),
}


def load(name: str, base: Path) -> dict:
    return json.loads((base / name).read_text(encoding="utf-8"))


def valid(kind: str, document: dict) -> bool:
    schema = load(PAIRS[kind][1], ROOT / "schemas")
    return not list(Draft7Validator(schema).iter_errors(document))


def sample(kind: str) -> dict:
    return copy.deepcopy(load(PAIRS[kind][0], FIXTURES))


def broken(kind: str, change) -> dict:
    document = sample(kind)
    change(document)
    return document


def snippet_with_evidence(lead: dict) -> None:
    lead["stored"] = "snippet"


def snippet_without_evidence(lead: dict) -> None:
    lead["stored"] = "snippet"
    for key in ("content_ref", "content_sha256", "derivatives"):
        lead.pop(key)


def absence(lead: dict) -> None:
    for key in ("stored", "capture", "content_ref", "content_sha256", "derivatives", "provenance", "excerpt", "attribution"):
        lead.pop(key)
    lead["absence"] = True


def topic(plan: dict) -> dict:
    return plan["research_plan"]["topics"][0]


REFUSED = {
    "a snippet lead carrying an evidence record": ("lead", snippet_with_evidence),
    "a stored page without its content hash": ("lead", lambda d: d.pop("content_sha256")),
    "an absence lead without the queries tried": ("lead", absence),
    "an attribution stance other than asserts or disputes": ("lead", lambda d: d["attribution"].update(stance="neutral")),
    "a lead without provenance": ("lead", lambda d: d.pop("provenance")),
    "a failed lane without its failure": ("lane", lambda d: d.update(status="failed")),
    "a clue from an unknown source": ("clues", lambda d: d["clues"][0].update(source="K9")),
    "a K2 conflict pair without the attribute": (
        "clues", lambda d: d["clues"][0].update(source="K2", type="conflict", lead_ids=["lead-a", "lead-b"])
    ),
    "a K2 conflict pair with three leads": (
        "clues", lambda d: d["clues"][0].update(source="K2", type="conflict", lead_ids=["a", "b", "c"], attribute="owner")
    ),
    "a K2 conflict pair with one lead": (
        "clues", lambda d: d["clues"][0].update(source="K2", type="conflict", lead_ids=["lead-a"], attribute="owner")
    ),
    "a round budget above the case ceiling": ("methodology", lambda d: d["research_plan"].update(round_budget_usd=6)),
    "a case budget above 5": ("methodology", lambda d: d["research_plan"].update(case_budget_usd=5.5)),
    "a plan without a consent record": ("methodology", lambda d: d["research_plan"].pop("consent")),
    "a topic without questions": ("methodology", lambda d: topic(d).update(questions=[])),
    "a topic id that is not a folder-safe slug": ("methodology", lambda d: topic(d).update(topic_id="../ownership")),
    "an unversioned Anomaly method": ("methodology", lambda d: topic(d).update(anomaly_methods=["method:shell-signals"])),
    "a key value instead of its name": ("methodology", lambda d: d["research_plan"]["keys"][0].update(name="sk-or-v1-abc")),
}


def main() -> int:
    failures = [f"sample {name} does not validate" for name in PAIRS if not valid(name, sample(name))]
    for name, change in (("a snippet lead without an evidence record", snippet_without_evidence),):
        if not valid("lead", broken("lead", change)):
            failures.append(f"refused: {name}")
    for name, (kind, change) in REFUSED.items():
        if valid(kind, broken(kind, change)):
            failures.append(f"accepted: {name}")
    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        return 1
    print(f"research schemas: {len(REFUSED)} refusals hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
