#!/usr/bin/env python3
"""Knowledge Batch 1.1 core: schema boundary and cross-record rules.

- The core fixture validates against the core schema, and the core schema
  refuses the records and fields only the full schema carries.
- When SPOTLIGHT_BATCH_FULL_SCHEMA names the full Batch 1.1 schema (not in
  this repository), the fixture also validates against it, after its hash is
  checked against the one the core schema records: a core batch must be a
  valid full batch.
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
import subprocess
import sys
import tempfile
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


def follow_up(**arrays) -> dict:
    """A second batch, checked against the fixture as the admitted store."""
    return {"schema_version": "1.1", "batch_id": "batch:b2", "case_id": "case:river-monitoring", **arrays}


def check_rules() -> None:
    assert RULES.validate(FIXTURE, []) == [], RULES.validate(FIXTURE, [])
    grounding = FIXTURE["claim_source_expressions"][0]
    new_passage = dict(FIXTURE["source_expressions"][1], id="passage:new")
    # (expected error prefix, batch, admitted store); every error must carry
    # the prefix, so a case cannot pass on an adjacent rule.
    cases = [
        ("shape:", mutated(setv(["claims"], [None])), []),
        ("shape:", mutated(setv(["claims"], {"id": "claim:x"})), []),
        ("shape:", mutated(setv(["claims", 0, "version"], "1")), []),
        ("shape:", mutated(setv(["assessments", 0, "basis_refs"], ["grounding:night-discharge-permit"])), []),
        ("shape:", FIXTURE, [{"claims": "not a list"}]),
        ("C1: claim 'claim:night-discharge' v1 appears twice", mutated(lambda d: d["claims"].append(copy.deepcopy(d["claims"][0]))), []),
        ("C1: evidence 'evidence:permit-register' v1 is already admitted", FIXTURE, [FIXTURE]),
        ("C2: claim 'claim:night-discharge' v3 has no version 2", mutated(lambda d: d["claims"].append(dict(d["claims"][0], version=3))), []),
        ("C4:", mutated(setv(["source_expressions", 0, "origin_ref", "derivative_id"], "ocr")), []),
        ("C5:", mutated(setv(["source_expressions", 1, "translation_of_ref"], {"id": "passage:x", "version": 1})), []),
        ("C6:", follow_up(evidence=[dict(FIXTURE["evidence"][0], version=2, content_sha256="0" * 64)]), [FIXTURE]),
        ("C7: event 'event:permit-granted' v1 has no claim membership", mutated(lambda d: d.pop("claim_event_memberships")), []),
        ("C8:", mutated(setv(["assessments", 0, "flags", 0, "code"], "looks_wrong")), []),
        ("C9:", mutated(lambda d: d["source_positions"][0].__setitem__("claim_ref", {"id": "claim:other", "version": 1})
                        or d["claims"].append(dict(d["claims"][0], id="claim:other"))), []),
        ("C10: claim_source_expression 'grounding:night-discharge-permit' v2 changes its lineage",
         follow_up(source_expressions=[new_passage],
                   claim_source_expressions=[dict(grounding, version=2, source_expression_ref={"id": "passage:new", "version": 1})]),
         [FIXTURE]),
        # Two new ids on one pair: whichever sorts second is reported.
        ("C10:",
         mutated(lambda d: d["claim_source_expressions"].append(dict(grounding, id="grounding:copy"))), []),
        # A submitted id that sorts before the admitted owner is still the second id.
        ("C10: claim_source_expression 'grounding:aaa' v1 links the same pair",
         follow_up(claim_source_expressions=[dict(grounding, id="grounding:aaa")]), [FIXTURE]),
        ("C13:", mutated(setv(["events", 0, "time"], {"kind": "point", "value": "2025-02-30"})), []),
        ("C13:", mutated(setv(["events", 0, "time"], {"kind": "point", "value": "2025-W53"})), []),
        ("C13: event 'event:permit-granted' v1 interval ends before it starts",
         mutated(setv(["events", 0, "time"], {"kind": "interval", "start": "2025-03", "end": "2025-02-28"})), []),
        ("C13:", mutated(setv(["evidence", 0, "capture", "captured_at"], "2026-13-01T00:00:00Z")), []),
        ("C13:", mutated(setv(["evidence", 0, "capture", "captured_at"], "2026-08-17")), []),
    ]
    # C3: one dangling reference per exact-reference field and typed list.
    dangling = [
        ["claims", 0, "origin_source_expression_ref"],
        ["claim_source_expressions", 0, "claim_ref"],
        ["claim_source_expressions", 0, "source_expression_ref"],
        ["source_positions", 0, "claim_ref"],
        ["assessments", 0, "claim_ref"],
        ["claim_event_memberships", 0, "claim_ref"],
        ["claim_event_memberships", 0, "event_ref"],
        ["event_story_arc_memberships", 0, "event_ref"],
        ["event_story_arc_memberships", 0, "story_arc_ref"],
        ["source_positions", 0, "source_expression_refs", 0],
        ["source_expressions", 0, "origin_ref"],
        ["assessments", 0, "basis_refs", 0],
        ["assessments", 0, "flags", 0, "related_refs", 0],
    ]
    for path in dangling:
        def change(doc, path=path):
            node = doc
            for step in path:
                node = node[step]
            node["version"] = 9
        cases.append(("C3:", mutated(change), []))
    cases.append(("C3:", mutated(setv(["assessments", 0, "basis_refs", 0, "type"], "source_position")), []))
    for expected, batch, admitted in cases:
        errors = RULES.validate(batch, admitted)
        rule = expected.split(":")[0] + ":"
        # Retargeting a reference can also orphan what pointed through it;
        # only C3 cases tolerate C9/C10 echoes of the same dangling ref.
        relevant = [e for e in errors if e.startswith(rule)]
        assert relevant and any(e.startswith(expected) for e in relevant), (expected, errors)
        if rule != "C3:":
            assert relevant == errors, (expected, errors)
    # Flags may cite another claim's records; only the basis is C9's concern.
    other = mutated(lambda d: d["claims"].append(dict(d["claims"][0], id="claim:other"))
                    or d["source_positions"].append(dict(d["source_positions"][0], id="position:other",
                                                         claim_ref={"id": "claim:other", "version": 1}))
                    or d["assessments"][0]["flags"][0]["related_refs"].append(
                        {"type": "source_position", "id": "position:other", "version": 1}))
    assert RULES.validate(other, []) == [], RULES.validate(other, [])
    # Mixed precision: a month that ends after the day it starts on is fine.
    ok = mutated(setv(["events", 0, "time"], {"kind": "interval", "start": "2025-03-31", "end": "2025-03"}))
    assert RULES.validate(ok, []) == [], RULES.validate(ok, [])
    # A correct next version against the store passes: lineage kept, hash kept.
    nxt = follow_up(evidence=[dict(FIXTURE["evidence"][0], version=2)],
                    claim_source_expressions=[dict(grounding, version=2, role="context")])
    assert RULES.validate(nxt, [FIXTURE]) == [], RULES.validate(nxt, [FIXTURE])


def check_cli() -> None:
    script = ROOT / "scripts" / "validate-knowledge-batch.py"
    fixture = ROOT / "tests" / "fixtures" / "knowledge-batch-1.1.sample.json"
    with tempfile.TemporaryDirectory() as raw:
        legacy = Path(raw) / "legacy.json"
        legacy.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
        runs = [
            ([str(fixture)], 0),
            ([str(fixture), "--admitted", str(fixture)], 3),
            ([str(Path(raw) / "missing.json")], 2),
            ([str(legacy)], 2),
        ]
        for args, code in runs:
            done = subprocess.run([sys.executable, str(script), *args, "--json"], capture_output=True, text=True)
            assert done.returncode == code, (args, done.returncode, done.stdout, done.stderr)
            if code in (0, 3):
                assert json.loads(done.stdout)["ok"] is (code == 0), done.stdout


def main() -> int:
    check_schemas()
    check_rules()
    check_cli()
    print("knowledge-batch-1.1-check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
