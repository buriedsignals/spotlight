#!/usr/bin/env python3
"""Knowledge Batch 1.1 core: schema boundary and cross-record rules.

- The core fixture validates against the core schema, and the core schema
  refuses the newsroom edition's records and fields (R3).
- When SPOTLIGHT_BATCH_FULL_SCHEMA names the full Batch 1.1 schema (not in
  this repository), the fixture also validates against it, after its hash is
  checked against the one the core schema records (R1).
- scripts/validate-knowledge-batch.py passes the fixture and fails each rule
  for the reason that rule names.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "knowledge-batch-1.1.sample.json").read_text(encoding="utf-8"))
CORE = json.loads((ROOT / "schemas" / "knowledge-batch-1.1.schema.json").read_text(encoding="utf-8"))
_SPEC = importlib.util.spec_from_file_location("validate_knowledge_batch", ROOT / "scripts" / "validate-knowledge-batch.py")
RULES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(RULES)


def schema_errors(schema: dict, document: dict) -> list[str]:
    from jsonschema import Draft7Validator, FormatChecker

    Draft7Validator.check_schema(schema)
    return [e.message for e in Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(document)]


def check_schemas() -> None:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
            raise AssertionError("jsonschema is required for this contract test")
        print("skip schema checks (jsonschema unavailable)")
        return
    assert not schema_errors(CORE, FIXTURE), schema_errors(CORE, FIXTURE)
    newsroom = []
    doc = copy.deepcopy(FIXTURE)
    doc["journalist_inputs"] = [{"id": "input:x", "version": 1}]
    newsroom.append(("journalist_inputs", doc))
    doc = copy.deepcopy(FIXTURE)
    doc["source_expressions"][1]["translation_of_ref"] = {"id": "passage:permit-night-discharge", "version": 1}
    newsroom.append(("translation_of_ref", doc))
    doc = copy.deepcopy(FIXTURE)
    doc["claims"][0]["statement"]["subject"]["entity_ids"] = ["org:1"]
    newsroom.append(("entity_ids", doc))
    doc = copy.deepcopy(FIXTURE)
    doc["claims"][0]["valid_from"] = "2025-01-01"
    newsroom.append(("valid_from", doc))
    doc = copy.deepcopy(FIXTURE)
    doc["source_positions"][0]["source_count"] = 2
    newsroom.append(("source_count", doc))
    doc = copy.deepcopy(FIXTURE)
    doc["assessments"][0]["basis_refs"].append({"type": "article_link", "id": "link:x", "version": 1})
    newsroom.append(("article_link basis", doc))
    for name, document in newsroom:
        assert schema_errors(CORE, document), f"core schema accepted newsroom-only {name}"

    full_path = os.environ.get("SPOTLIGHT_BATCH_FULL_SCHEMA")
    if not full_path:
        print("skip full-schema check (SPOTLIGHT_BATCH_FULL_SCHEMA unset)")
        return
    raw = Path(full_path).read_bytes()
    pinned = re.search(r"sha256 ([0-9a-f]{64})", CORE["$comment"]).group(1)
    assert hashlib.sha256(raw).hexdigest() == pinned, "full schema differs from the one the core was derived from"
    errors = schema_errors(json.loads(raw), FIXTURE)
    assert not errors, errors


def mutated(change) -> dict:
    doc = copy.deepcopy(FIXTURE)
    change(doc)
    return doc


def setv(path: list, value):
    def change(doc):
        node = doc
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
    return change


def check_rules() -> None:
    assert RULES.validate(FIXTURE, []) == [], RULES.validate(FIXTURE, [])
    v3_claim = dict(FIXTURE["claims"][0], version=3)
    cases = [
        ("C1", mutated(lambda d: d["claims"].append(copy.deepcopy(d["claims"][0]))), []),
        ("C1", FIXTURE, [FIXTURE]),
        ("C2", mutated(lambda d: d["claims"].append(v3_claim)), []),
        ("C3", mutated(setv(["claim_source_expressions", 0, "source_expression_ref", "version"], 2)), []),
        ("C3", mutated(setv(["assessments", 0, "basis_refs", 0, "type"], "source_position")), []),
        ("C4", mutated(setv(["source_expressions", 0, "origin_ref", "derivative_id"], "ocr")), []),
        ("C5", mutated(setv(["source_expressions", 1, "translation_of_ref"], {"id": "passage:x", "version": 1})), []),
        ("C6", {"schema_version": "1.1", "batch_id": "batch:b2", "case_id": "case:river-monitoring",
                "evidence": [dict(FIXTURE["evidence"][0], version=2, content_sha256="0" * 64)]}, [FIXTURE]),
        ("C7", mutated(lambda d: d.pop("claim_event_memberships")), []),
        ("C8", mutated(setv(["assessments", 0, "flags", 0, "code"], "looks_wrong")), []),
        ("C9", mutated(lambda d: d["source_positions"][0].__setitem__("claim_ref", {"id": "claim:other", "version": 1})
                       or d["claims"].append(dict(d["claims"][0], id="claim:other"))), []),
        ("C10", {"schema_version": "1.1", "batch_id": "batch:b2", "case_id": "case:river-monitoring",
                 "claim_source_expressions": [dict(FIXTURE["claim_source_expressions"][0], version=2,
                                                   source_expression_ref={"id": "passage:operator-denial", "version": 1})]},
         [FIXTURE]),
        ("C10", mutated(lambda d: d["claim_source_expressions"].append(dict(d["claim_source_expressions"][0], id="grounding:copy"))), []),
        ("C13", mutated(setv(["events", 0, "time"], {"kind": "point", "value": "2025-02-30"})), []),
        ("C13", mutated(setv(["events", 0, "time"], {"kind": "interval", "start": "2025-03", "end": "2025-02-28"})), []),
        ("C13", mutated(setv(["evidence", 0, "capture", "captured_at"], "2026-13-01T00:00:00Z")), []),
    ]
    for rule, batch, admitted in cases:
        errors = RULES.validate(batch, admitted)
        assert errors and all(e.startswith(rule + ":") for e in errors), (rule, errors)
    # Mixed precision: a month that ends after the day it starts on is fine.
    ok = mutated(setv(["events", 0, "time"], {"kind": "interval", "start": "2025-03-31", "end": "2025-03"}))
    assert RULES.validate(ok, []) == [], RULES.validate(ok, [])
    # A correct next version against the store passes: lineage kept, hash kept.
    follow_up = {"schema_version": "1.1", "batch_id": "batch:b2", "case_id": "case:river-monitoring",
                 "evidence": [dict(FIXTURE["evidence"][0], version=2)],
                 "claim_source_expressions": [dict(FIXTURE["claim_source_expressions"][0], version=2, role="context")]}
    assert RULES.validate(follow_up, [FIXTURE]) == [], RULES.validate(follow_up, [FIXTURE])


def main() -> int:
    check_schemas()
    check_rules()
    print("knowledge-batch-1.1-check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
