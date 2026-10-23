#!/usr/bin/env python3
"""Ingest decision checks read a Batch 1.1 (knowledge model U7).

A 1.1 claim's proposition check carries its source positions in the state,
so attribution held in a position is visible to the question that judges
drift; memberships resolve through claim_ref/event_ref and get a generated
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


def main() -> int:
    batch = json.loads((ROOT / "tests" / "fixtures" / "knowledge-batch-1.1.sample.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp)
        (case / "data").mkdir()
        (case / "data" / "findings.json").write_text(json.dumps({"project": "p", "schema_version": "1.1", "findings": [
            {"id": "F1", "claim": "The operator says it never discharged at night, but its permit allows night discharge."}]}), encoding="utf-8")
        (case / "data" / "knowledge-batch.json").write_text(json.dumps(batch), encoding="utf-8")
        items = lib.batch_items(case)
    props = [i for i in items if i["group"] == "propositions"]
    require(len(props) == 1 and not props[0]["unresolved"], f"proposition item: {props}")
    require(props[0]["state"]["source_positions"] == [{"actor": "River Works Ltd", "stance": "disputes",
                                                        "attribution_verbatim": "We have never discharged at night."}],
            f"positions missing from the proposition state: {props[0]['state']}")
    members = [i for i in items if i["group"] == "memberships"]
    require(len(members) == 1 and not members[0]["unresolved"], f"membership did not resolve through claim_ref/event_ref: {members}")
    require(members[0]["state"]["event_label"].startswith("Regional water authority, River Works Ltd grant"),
            f"generated event label: {members[0]['state']['event_label']}")
    bare = {k: v for k, v in props[0]["state"].items() if k != "source_positions"}
    require(props[0]["input_sha256"] != lib.canonical_sha256({"state": bare, "rule_inputs": props[0]["rule_inputs"]}),
            "the positions are not part of the proposition check's fingerprint")
    print("decision-ingest-batch-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
