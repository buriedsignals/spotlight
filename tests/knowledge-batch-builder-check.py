#!/usr/bin/env python3
"""scripts/build-knowledge-batch.py on an activated case.

The builder turns the analyst's proposal and the case files into a Batch 1.1
(core) that passes the schema and the cross-record rules; it keeps ids stable
across rebuilds, refuses an attributed claim without a source position, holds
flags until a person disposes of them, honours the reviewer's selection, and
gives a changed admitted record its next version.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CASES = load("validate_case_check", ROOT / "tests" / "validate-case-check.py")
RULES = load("validate_knowledge_batch", SCRIPTS / "validate-knowledge-batch.py")
MODELS = ["--model", "investigator=inv-model-1", "--model", "fact-checker=fc-model-1"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build(case: Path, *extra: str, models: list[str] = MODELS) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "build-knowledge-batch.py"), str(case), *models, *extra],
                          capture_output=True, text=True)


def read(case: Path, name: str) -> dict:
    return json.loads((case / "data" / name).read_text(encoding="utf-8"))


def write(case: Path, name: str, doc: dict) -> None:
    (case / "data" / name).write_text(json.dumps(doc), encoding="utf-8")


def schema_errors(batch: dict) -> list[str] | None:
    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError:
        if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
            raise AssertionError("jsonschema is required for this contract test")
        return None
    schema = json.loads((ROOT / "schemas" / "knowledge-batch-1.1.schema.json").read_text(encoding="utf-8"))
    return [e.message for e in Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(batch)]


PROPOSAL = {
    "interface_version": "1",
    "model": "analyst-model-1",
    "claims": [{
        "key": "c1", "finding_id": "F1", "origin_expression_id": "SX1",
        "proposition": "Acme paid Doe.", "language": "en", "attributed": True,
        "statement": {"subject": {"text": "Acme"}, "predicate": "paid", "object": {"text": "Doe"}},
    }],
    "events": [{"key": "e1", "language": "en", "actors": [{"name": "Acme", "role": "agent"}],
                "action": "pay", "object": {"text": "Doe"}, "time": {"kind": "point", "value": "2026-06"},
                "claim_keys": ["c1"]}],
    "story_arcs": [{"key": "a1", "language": "en", "title": "Acme payments",
                    "description": "Payments Acme made.", "event_keys": ["e1"]}],
}


def position_doc() -> dict:
    return {"schema_version": "1.0", "project": "test-case", "created_at": "2026-07-16T12:00:00Z",
            "positions": [{"id": "SP1", "finding_id": "F1", "actor": {"label": "Registry"}, "stance": "asserts",
                           "source_expression_ids": ["SX1"], "attribution_verbatim": "Acme paid Doe.",
                           "created_by": "investigator", "cycle": 1}]}


def check_schema_checker() -> None:
    """scripts/schema_check.py accepts the fixture and refuses every invalid
    variant, never laxer than jsonschema."""
    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError:
        if os.environ.get("SPOTLIGHT_REQUIRE_JSONSCHEMA") == "1":
            raise AssertionError("jsonschema is required for this contract test")
        return
    sys.path.insert(0, str(SCRIPTS))
    import schema_check
    schema = json.loads((ROOT / "schemas" / "knowledge-batch-1.1.schema.json").read_text(encoding="utf-8"))
    ours = schema_check.Checker(schema)
    theirs = Draft7Validator(schema, format_checker=FormatChecker())
    sample = json.loads((ROOT / "tests" / "fixtures" / "knowledge-batch-1.1.sample.json").read_text(encoding="utf-8"))
    variants = [sample]
    for path, value in (
        (["claims", 0, "statement", "subject"], "text"), (["claims", 0, "proposition"], ""),
        (["events", 0, "time"], {"kind": "point", "start": "2025"}), (["evidence", 0, "capture", "captured_at"], "2026-13-01T00:00:00Z"),
        (["evidence", 0, "capture", "source_url"], "not a url"), (["assessments", 0, "verdict"], "partially_verified"),
        (["assessments", 0, "verification_methods"], []), (["claims", 0, "id"], "Claim 1"), (["claims", 0, "version"], 0),
        (["source_positions", 0, "source_expression_refs"], []), (["claims", 0, "extra"], 1),
    ):
        doc = json.loads(json.dumps(sample))
        node = doc
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value
        variants.append(doc)
    require(not ours.errors(sample) and not list(theirs.iter_errors(sample)), "the fixture is not valid")
    # Never laxer than jsonschema; stricter on formats jsonschema checks only
    # with optional packages (date-time, uri), so every mutation is refused.
    for doc in variants[1:]:
        require(bool(ours.errors(doc)), f"schema_check accepted an invalid batch: {doc}")
        if list(theirs.iter_errors(doc)):
            require(bool(ours.errors(doc)), "schema_check is laxer than jsonschema")


def main() -> int:
    check_schema_checker()
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case"
        CASES.write_activated_case(case)
        # Born and not yet bound, so the test can change the fact-check freely.
        contract = read(case, "case-contract.json")
        contract["activation_events"] = [{"event_id": "born-test", "kind": "born", "activated_contract_version": "1.1",
                                          "activated_at": "2026-10-22T12:00:00Z", "tool_version": "test"}]
        write(case, "case-contract.json", contract)
        write(case, "source-positions.json", position_doc())
        write(case, "knowledge-proposal.json", PROPOSAL)

        made = build(case)
        require(made.returncode == 0, made.stderr)
        batch = read(case, "knowledge-batch.json")
        for array in RULES.ARRAYS:
            require(len(batch.get(array, [])) >= 1, f"the batch has no {array}")
        require(RULES.validate(batch, []) == [], RULES.validate(batch, []))
        errors = schema_errors(batch)
        require(not errors, f"the batch fails the core schema: {errors}")
        assessment = batch["assessments"][0]
        require(assessment["verdict"] == "verified" and assessment["verification_methods"] == ["re_fetched"]
                and {r["type"] for r in assessment["basis_refs"]} == {"claim_source_expression", "source_position"},
                f"assessment not built from the fact-check: {assessment}")
        require(batch["source_expressions"][0]["origin_ref"].get("derivative_id") == "TD1",
                "a passage anchored to a derivative does not name it")
        require(batch["claims"][0]["provenance"] == {"method": "agent_candidate", "model": "analyst-model-1"}, "claim provenance")
        first_ids = {r["id"] for array in RULES.ARRAYS for r in batch.get(array, [])}

        # A rebuild after review edits reuses every id.
        require(build(case).returncode == 0, "rebuild failed")
        again = read(case, "knowledge-batch.json")
        require({r["id"] for array in RULES.ARRAYS for r in again.get(array, [])} == first_ids, "ids changed on rebuild")

        # Producer models given once are remembered for page rebuilds.
        require(build(case, models=[]).returncode == 0, "a rebuild without --model failed")

        # Nothing is written on a refusal, and a first build needs the models.
        before = (case / "data" / "knowledge-batch.json").read_bytes()
        ids_before = (case / "data" / "knowledge-ids.json").read_bytes()
        (case / "data" / "knowledge-ids.json").unlink()
        refused = build(case, models=["--model", "investigator=inv-model-1"])
        require(refused.returncode == 2 and "--model fact-checker" in refused.stderr, "a missing producer model was accepted")
        require((case / "data" / "knowledge-batch.json").read_bytes() == before and not (case / "data" / "knowledge-ids.json").exists(),
                "a refused build wrote a file")
        (case / "data" / "knowledge-ids.json").write_bytes(ids_before)

        # An attributed claim needs a source position (R5).
        (case / "data" / "source-positions.json").unlink()
        refused = build(case)
        require(refused.returncode == 2 and "no source position" in refused.stderr, "an attributed claim without a position was built")
        write(case, "source-positions.json", position_doc())

        # A fact-check flag waits for a person's disposition.
        fact_check = read(case, "fact-check.json")
        fact_check["claims"][0]["flags"] = [{"code": "partial_support", "reason": "Only part of the payment is documented."}]
        write(case, "fact-check.json", fact_check)
        refused = build(case)
        require(refused.returncode == 2 and "await a disposition" in refused.stderr, "a flag without a disposition was built")
        drafted = build(case, "--draft")
        require(drafted.returncode == 0, drafted.stderr)
        require(not (case / "data" / "knowledge-batch.json").exists(), "a draft left an earlier final batch to ingest")
        draft = read(case, "knowledge-batch-draft.json")
        flag_key = draft["pending_flags"][0]["key"] if draft["pending_flags"] else ""
        require(draft["flags"] == [dict(draft["pending_flags"][0], decided=False)], f"the draft does not list every flag: {draft}")
        require(len(draft["pending_flags"]) == 1 and flag_key.startswith("flag:c1:partial_support:")
                and not draft["batch"]["assessments"][0].get("flags"),
                f"the draft does not hold the pending flag apart: {draft['pending_flags']}")
        review = {"schema_version": "spotlight-review/v1", "gate": "ingest", "reviewer": "J", "items": {
            flag_key: {"kind": "disposition", "final": "accept_with_reason", "note": "The documented part is what the claim says."},
            "record:a1": {"kind": "selection", "final": False}}}
        write(case, "ingest-review.json", review)
        made = build(case)
        require(made.returncode == 0, made.stderr)
        batch = read(case, "knowledge-batch.json")
        flag = batch["assessments"][0]["flags"][0]
        require(flag["disposition"] == {"action": "accept_with_reason", "reason": "The documented part is what the claim says."},
                f"the disposition was not applied: {flag}")
        require(not batch.get("story_arcs") and not batch.get("event_story_arc_memberships"), "a deselected arc was built")
        require(RULES.validate(batch, []) == [] and not schema_errors(batch), "the reviewed batch is invalid")

        # A disposition decides only the flag it was given for.
        changed_flag = read(case, "fact-check.json")
        changed_flag["claims"][0]["flags"][0]["reason"] = "A different concern."
        write(case, "fact-check.json", changed_flag)
        refused = build(case)
        require(refused.returncode == 2 and "await a disposition" in refused.stderr, "an old disposition decided a changed flag")
        write(case, "fact-check.json", fact_check)
        require(build(case).returncode == 0, "rebuild with the original flag failed")
        ids_doc = read(case, "knowledge-ids.json")
        require(not any("pending" in e for n, e in ids_doc["records"].items() if n.startswith(("story_arc:", "event_story_arc_membership:"))),
                "a deselected record stayed pending admission")

        # Admitted records are not resubmitted; a changed one gets its next version.
        ids = read(case, "knowledge-ids.json")
        for entry in ids["records"].values():
            if "pending" in entry:
                entry["admitted"] = entry.pop("pending")
        write(case, "knowledge-ids.json", ids)
        refused = build(case)
        require(refused.returncode == 2 and "nothing new" in refused.stderr, "an unchanged admitted batch was resubmitted")
        # The review page still gets its pending flags when nothing else is new.
        extra = read(case, "fact-check.json")
        extra["claims"][0]["flags"].append({"code": "date_mismatch", "reason": "The month is not in the filing."})
        write(case, "fact-check.json", extra)
        drafted = build(case, "--draft")
        require(drafted.returncode == 0 and len(read(case, "knowledge-batch-draft.json")["pending_flags"]) == 1, drafted.stderr)
        write(case, "fact-check.json", fact_check)
        proposal = json.loads(json.dumps(PROPOSAL))
        proposal["claims"][0]["proposition"] = "Acme paid Doe in June 2026."
        write(case, "knowledge-proposal.json", proposal)
        made = build(case)
        require(made.returncode == 0, made.stderr)
        batch = read(case, "knowledge-batch.json")
        require([c["version"] for c in batch["claims"]] == [2], f"the changed claim is not version 2: {batch.get('claims')}")
        admitted = {}
        for entry in read(case, "knowledge-ids.json")["records"].values():
            if "admitted" in entry:
                array = next(a for a, t in RULES.ARRAYS.items() if t == entry["type"])
                admitted.setdefault(array, []).append(entry["admitted"]["record"])
        require(RULES.validate(batch, [admitted]) == [], RULES.validate(batch, [admitted]))

        # The proposal is model output: anything outside interface 1 is refused.
        for bad, reason in (
            (lambda p: p["claims"][0].__setitem__("attributed", "yes"), "does not match interface"),
            (lambda p: p["claims"][0]["statement"].__setitem__("subject", "Acme"), "does not match interface"),
            (lambda p: p["claims"][0].__setitem__("proposition", ""), "does not match interface"),
            (lambda p: p["events"][0].__setitem__("time", {"kind": "point", "start": "2026-06"}), "does not match interface"),
            (lambda p: p["events"][0].__setitem__("claim_keys", ["c9"]), "unknown claims"),
            (lambda p: p["claims"][0].__setitem__("id", "claim:forged"), "does not match interface"),
        ):
            mutated = json.loads(json.dumps(PROPOSAL))
            bad(mutated)
            write(case, "knowledge-proposal.json", mutated)
            refused = build(case)
            require(refused.returncode == 2 and reason in refused.stderr, f"a malformed proposal was built: {refused.stderr}")
        write(case, "knowledge-proposal.json", proposal)

        # Artifacts changed after the gate approval bound them: no batch.
        approved = Path(tmp) / "approved"
        CASES.write_activated_case(approved)  # migrated: its activation hashes are enforced
        write(approved, "knowledge-proposal.json", PROPOSAL)
        write(approved, "source-positions.json", position_doc())
        changed = read(approved, "findings.json")
        changed["findings"][0]["claim"] = "Acme paid Doe twice."
        write(approved, "findings.json", changed)
        refused = build(approved)
        require(refused.returncode == 2 and "does not validate" in refused.stderr, "a batch was built from unbound artifacts")

        # Only an activated case builds a Batch 1.1.
        legacy = Path(tmp) / "legacy"
        (legacy / "data").mkdir(parents=True)
        write(legacy, "findings.json", CASES.valid_findings())
        refused = build(legacy)
        require(refused.returncode == 2 and "activated case" in refused.stderr, "a legacy case was built")

    print("knowledge-batch-builder-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
