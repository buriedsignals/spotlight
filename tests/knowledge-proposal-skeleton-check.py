#!/usr/bin/env python3
"""scripts/knowledge-proposal-skeleton.py on an activated case.

The skeleton carries one claim per fact-check row with the references the
case fixes, leaves every judgment field as a placeholder the builder refuses,
and, once those are filled, passes build-knowledge-batch.py --check-proposal
unchanged otherwise.
"""

from __future__ import annotations

import importlib.util
import json
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
BUILDER = load("knowledge_batch_builder_check", ROOT / "tests" / "knowledge-batch-builder-check.py")
require, read, write = BUILDER.require, BUILDER.read, BUILDER.write


def skeleton(case: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "knowledge-proposal-skeleton.py"), str(case), *extra],
                          capture_output=True, text=True)


def check_proposal(case: Path, proposal: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "build-knowledge-batch.py"), str(case), "--check-proposal"],
                          input=json.dumps(proposal), capture_output=True, text=True)


def fill(proposal: dict, attributed: bool) -> dict:
    """What the analyst adds: only the placeholders."""
    filled = json.loads(json.dumps(proposal))
    filled["model"] = "analyst-model-1"
    for claim in filled["claims"]:
        claim["proposition"] = "Acme paid Doe."
        claim["statement"] = {"subject": {"text": "Acme"}, "predicate": "paid", "object": {"text": "Doe"}}
        if claim["attributed"] is None:
            claim["attributed"] = attributed
    return filled


def born_case(path: Path) -> Path:
    """An activated case not yet bound, so the test may change its files."""
    CASES.write_activated_case(path)
    contract = read(path, "case-contract.json")
    contract["activation_events"] = [{"event_id": "born-test", "kind": "born", "activated_contract_version": "1.1",
                                      "activated_at": "2026-10-22T12:00:00Z", "tool_version": "test"}]
    write(path, "case-contract.json", contract)
    return path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        case = born_case(Path(tmp) / "case")
        write(case, "source-positions.json", BUILDER.position_doc())

        made = skeleton(case)
        require(made.returncode == 0, made.stderr)
        proposal = json.loads(made.stdout)
        require(proposal["interface_version"] == "1" and proposal["model"] == "" and proposal["events"] == []
                and proposal["story_arcs"] == [], f"top level: {proposal}")
        require(proposal["claims"] == [{
            "key": "c1", "finding_id": "F1", "origin_expression_id": "SX1", "proposition": "", "language": "en",
            "statement": {"subject": {"text": ""}, "predicate": ""}, "attributed": None,
        }], f"claim skeleton: {proposal['claims']}")
        # Placeholders are refused, so an unfilled skeleton never builds.
        refused = check_proposal(case, proposal)
        require(refused.returncode == 2 and "does not match interface" in refused.stderr, refused.stderr)
        accepted = check_proposal(case, fill(proposal, attributed=True))
        require(accepted.returncode == 0, f"a filled skeleton was refused: {accepted.stderr}")

        # --model prefills the model; the prompt names what to fill and quotes the case.
        shown = skeleton(case, "--format", "prompt", "--model", "analyst-model-1")
        require(shown.returncode == 0, shown.stderr)
        require('"model": "analyst-model-1"' in shown.stdout and "passage SX1 [en]: Acme paid Doe." in shown.stdout
                and "position SP1: Registry asserts" in shown.stdout and "fill only" in shown.stdout, shown.stdout)

        # Without a source position, attributed can only be false.
        (case / "data" / "source-positions.json").unlink()
        proposal = json.loads(skeleton(case).stdout)
        require(proposal["claims"][0]["attributed"] is False, f"attributed not prefilled false: {proposal['claims'][0]}")
        accepted = check_proposal(case, fill(proposal, attributed=True))
        require(accepted.returncode == 0, accepted.stderr)

        # Two fact-check rows on one finding: one claim each, named by fact_check_id.
        fact_check = read(case, "fact-check.json")
        second = json.loads(json.dumps(fact_check["claims"][0]))
        fact_check["claims"][0]["id"], second["id"] = 1, 2
        fact_check["claims"].append(second)
        write(case, "fact-check.json", fact_check)
        proposal = json.loads(skeleton(case).stdout)
        require([(c["key"], c.get("fact_check_id")) for c in proposal["claims"]] == [("c1", 1), ("c2", 2)],
                f"rows not mapped to claims: {proposal['claims']}")
        accepted = check_proposal(case, fill(proposal, attributed=False))
        require(accepted.returncode == 0, accepted.stderr)
        # Rows the builder cannot tell apart get no claim, and the prompt says why.
        del fact_check["claims"][1]["id"]
        write(case, "fact-check.json", fact_check)
        require(json.loads(skeleton(case).stdout)["claims"] == [], "claims made from rows without ids")
        require("F1: it has 2 fact-check rows and not all have an id" in skeleton(case, "--format", "prompt").stdout,
                "the prompt does not say why F1 has no claim")

        # Several candidate passages: the origin is the analyst's choice.
        expressions = read(case, "source-expressions.json")
        other = json.loads(json.dumps(expressions["expressions"][0]))
        other.update(id="SX2", language="de")
        expressions["expressions"].append(other)
        write(case, "source-expressions.json", expressions)
        del fact_check["claims"][1]
        write(case, "fact-check.json", fact_check)
        claim = json.loads(skeleton(case).stdout)["claims"][0]
        require(claim["origin_expression_id"] == "" and claim["language"] == "", f"a passage was chosen: {claim}")

        # A finding without a fact-check row gets no claim.
        write(case, "fact-check.json", dict(fact_check, claims=[]))
        require(json.loads(skeleton(case).stdout)["claims"] == [], "a claim without a fact-check row")
        require("F1: no fact-check row" in skeleton(case, "--format", "prompt").stdout, "the prompt does not list F1")

        # No findings file: refused, nothing printed.
        (case / "data" / "findings.json").unlink()
        missing = skeleton(case)
        require(missing.returncode == 2 and not missing.stdout, f"a case without findings: {missing}")

    print("knowledge-proposal-skeleton-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
