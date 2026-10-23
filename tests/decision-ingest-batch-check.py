#!/usr/bin/env python3
"""Ingest decision checks read a Batch 1.1 (knowledge model U7).

A 1.1 claim's proposition check puts its source position's attribution back
into the compared text ("According to X, ..."; "X disputes that ..."), so a
correctly normalized attributed claim is not judged changed; memberships resolve through claim_ref/event_ref and get a generated
event label. A 1.0 batch keeps its own shape (decision-signals-check).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import decision_signals_lib as lib  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def items_for(batch: dict) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp)
        (case / "data").mkdir()
        (case / "data" / "findings.json").write_text(json.dumps({"project": "p", "schema_version": "1.1", "findings": [
            {"id": "F1", "claim": "The operator says it never discharged at night, but its permit allows night discharge."}]}), encoding="utf-8")
        (case / "data" / "knowledge-batch.json").write_text(json.dumps(batch), encoding="utf-8")
        return lib.batch_items(case)


def main() -> int:
    batch = json.loads((ROOT / "tests" / "fixtures" / "knowledge-batch-1.1.sample.json").read_text(encoding="utf-8"))
    items = items_for(batch)
    props = [i for i in items if i["group"] == "propositions"]
    require(len(props) == 1 and not props[0]["unresolved"], f"proposition item: {props}")
    # The operator's denial cites only a context passage of this claim: it
    # disputes something else, so the claim is compared without it.
    require(props[0]["state"]["proposition"] == "River Works Ltd is permitted to discharge at outfall 3 at night.",
            f"a position on a context passage was attributed: {props[0]['state']}")
    # Positions on the claim's supporting passage are all composed, asserts
    # first, disputes kept.
    composed = json.loads(json.dumps(batch))
    permit = {"id": "passage:permit-night-discharge", "version": 1}
    template = composed["source_positions"][0]
    composed["source_positions"] = [
        {**template, "id": "position:authority", "stance": "asserts", "actor": {"label": "Regional water authority"}, "source_expression_refs": [permit]},
        {**template, "id": "position:operator", "stance": "disputes", "actor": {"label": "River Works Ltd"}, "source_expression_refs": [permit]},
    ]
    text = [i for i in items_for(composed) if i["group"] == "propositions"][0]["state"]["proposition"]
    require(text == "According to Regional water authority, River Works Ltd is permitted to discharge at outfall 3 at night. River Works Ltd disputes this.",
            f"composed attribution: {text}")
    members = [i for i in items if i["group"] == "memberships"]
    require(len(members) == 1 and not members[0]["unresolved"], f"membership did not resolve through claim_ref/event_ref: {members}")
    require(members[0]["state"]["event_label"].startswith("Regional water authority, River Works Ltd grant"),
            f"generated event label: {members[0]['state']['event_label']}")
    require("source_positions" not in props[0]["state"], "positions are passed as a field the model does not read")
    print("decision-ingest-batch-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
