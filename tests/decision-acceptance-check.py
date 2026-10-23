#!/usr/bin/env python3
"""Decision-model acceptance test: the flag-only switch's record, the harness
measures, the held-out guard and the private fixture builder.

No network: contenders are FakeProvider callbacks.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))

import client  # noqa: E402
import research_questions as rq  # noqa: E402
import research_rules as rr  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


acc = _load("decision_acceptance", ROOT / "evals" / "decision-acceptance" / "acceptance.py")
builder = _load("build_claims_fixture", ROOT / "evals" / "decision-acceptance" / "build_claims_fixture.py")
os.environ.pop(acc.FIXTURES_ENV, None)  # the private fixture never enters these tests


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def raises(fn, kind, message: str) -> None:
    try:
        fn()
    except kind:
        return
    raise AssertionError(message)


def fake(choose) -> client.FakeProvider:
    """choose(qid, options) -> option; every noul answers 0.1."""
    def answer(_state, qid, question):
        if question["type"] == "noul":
            return {"type": "noul", "noul": 0.1}
        option = choose(qid, list(question["criteria"]))
        return {"type": "choice", "choice": option, "probabilities": {option: 0.9}, "confidence": 0.9}
    return client.FakeProvider(answer)


def args(split: str, contenders: list[str], targets: Path = acc.TARGETS) -> argparse.Namespace:
    return argparse.Namespace(split=split, contender=contenders, fixture=[], targets=str(targets), private_detail=None)


def results(record: dict) -> dict:
    return {r["metrics"]["contender"]: r for r in record["results"]}


def test_acceptance_record_flips_flag_only_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "research-acceptance.json"
        require(rr.load_acceptance(path) is None, "no record must mean no acceptance")
        path.write_text("{not json", encoding="utf-8")
        require(rr.load_acceptance(path) is None, "an unreadable record must mean no acceptance")
        path.write_text(json.dumps({"passed": True, "bank_version": rq.RESEARCH_BANK_VERSION,
                                    "thresholds": {"c2_new_actor": 0.5}, "cut": ["c3_change"]}), encoding="utf-8")
        record = rr.load_acceptance(path)
        require(rr.triage_mode(record) == rr.FULL, "a passing record for this bank version must end flag-only mode")
        require(rr.on_trial_asked(rr.FULL, record) == ["c2_new_actor"], "a cut on-trial question must not be asked")
        path.write_text(json.dumps({"passed": True, "bank_version": "0" * 64}), encoding="utf-8")
        require(rr.triage_mode(rr.load_acceptance(path)) == rr.FLAG_ONLY, "a record for another bank version must change nothing")


def test_rules_find_rule_planted_clues_only() -> None:
    record = acc.run(args("dev", ["rules"]))
    metrics = results(record)["rules"]["metrics"]
    clues = metrics["clues"]
    require(clues["false_clues"] == 0, f"rules raised clues nobody planted: {clues}")
    missed = set(clues["missed"])
    require(missed == {"ca-d-c1", "ca-d-c2", "ca-d-c3a", "ca-d-c3b", "ca-d-c3c"},
            f"rules must find every K-rule clue and none that only a question can find: {missed}")
    require(metrics["lost_leads"]["lost"] == 0, "rules alone never set a lead aside")


def test_circumstantial_keeps_are_not_lost_leads() -> None:
    def factory(_contender):
        return fake(lambda qid, options: "different_subject_or_event" if qid.startswith("q1_identity") else options[0]), "fake.test"
    jev = results(acc.run(args("dev", ["jev"]), provider_factory=factory))["jev"]["metrics"]
    # The 21 triaged keep items of the dev half (the watchlist lead too) are
    # kept as circumstantial, none lost; the absence lead is never triaged.
    require(jev["lost_leads"]["lost"] == 0 and jev["lost_leads"]["circumstantial_kept"] == 21,
            f"a circumstantial keep was counted as lost: {jev['lost_leads']}")
    require(jev["cost"]["requests"] > 0 and jev["cost"]["egress_hosts"] == ["fake.test"], "requests are metered")


def test_host_labels_are_rule_catches_not_model_gains() -> None:
    # The X post (ca-d25), made a research-visible weak source chain. The two
    # orders disagree on every choice, so no answer counts and the social host
    # labels the source: the failure is caught, by a rule, not by Q3.
    fixture = acc.load_fixture(acc.COMPANY_A)
    entry = next(i for i in fixture["items"] if i["item_id"] == "ca-d25")
    entry["gold"]["failure"] = {"class": "weak_source_chain", "research_visible": True}
    provider = acc.Metered(fake(lambda qid, options: options[0]), "fake.test")
    run = acc.run_case(fixture, "dev", "jev", provider, acc.trial_acceptance(acc.load_targets()))
    item = next(e for e in run["items"] if e["item_id"] == "ca-d25")
    require(item["result"]["labels"].get("source") == "user_generated", f"the social prior did not label the source: {item['result']}")
    require(acc.failures_caught([run])["caught"] == 1, "a host-labelled weak source was not caught")
    require(acc.adds_over_rules([run])["q3_source"] == 0, "a label the host set counted as a Q3 gain")


def test_order_disagreement_has_no_effect() -> None:
    def factory(_contender):
        # The same position in each order: the reversed order picks a different option.
        return fake(lambda qid, options: options[0] if not qid.startswith("q4_assertion") else
                    ("interpretation" if qid.endswith("_rev") else "asserted")), "fake.test"
    jev = results(acc.run(args("dev", ["jev"]), provider_factory=factory))["jev"]["metrics"]
    require(jev["lost_leads"]["lost"] == 0, f"answers the two orders disagree on must set nothing aside: {jev['lost_leads']}")
    require(jev["questions"]["q4_assertion"]["both_order_agreement"] == 0.0, "disagreement must be measured")


def test_heldout_needs_approved_targets_for_this_bank() -> None:
    raises(lambda: acc.run(args("heldout", ["rules"])), acc.AcceptanceError, "proposed targets must not unlock the held-out half")
    targets = json.loads(acc.TARGETS.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "targets.json"
        path.write_text(json.dumps({**targets, "status": "approved", "approved_by": "maintainer", "bank_version": "0" * 64}))
        raises(lambda: acc.run(args("heldout", ["rules"], path)), acc.AcceptanceError,
               "targets approved for another bank version must not unlock the held-out half")
        path.write_text(json.dumps({**targets, "status": "approved", "approved_by": "maintainer",
                                    "bank_version": rq.RESEARCH_BANK_VERSION}))
        record = acc.run(args("heldout", ["rules"], path))
        require(record["chosen_backend"] is None, "rules only is never chosen as the backend")
        raises(lambda: acc.acceptance_record(record, "f" * 64), acc.AcceptanceError,
               "no acceptance record without a passing contender")


def test_fixture_refuses_planted_clue_across_halves() -> None:
    fixture = json.loads(acc.COMPANY_A.read_text(encoding="utf-8"))
    broken = copy.deepcopy(fixture)
    broken["planted_clues"][0]["lead_ids"].append("ca-h01")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "broken.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        raises(lambda: acc.load_fixture(path), acc.AcceptanceError, "a planted clue must stay inside one half")


def test_claim_becomes_lead() -> None:
    fact_check = {"project": "case", "claims": [
        {"id": 1, "finding_id": "F2-C1", "claim_text": "Firm X paid 3 million to Firm Y.", "verdict": "verified",
         "evidence_for": [{"description": "Local file", "source": "file:///x.pdf"},
                          {"description": "The filing says 'Firm X transferred 3 million to Firm Y in 2024' on page 4.",
                           "source": "https://registry.example.test/filing"}], "sources": []},
        {"id": 2, "finding_id": "F3", "claim_text": "Firm Y is owned by Person Z.", "verdict": "disputed",
         "evidence_for": [{"description": "A blog post says so without a source.", "source": "N/A"}],
         "sources": ["https://blog.example.test/post"]},
    ]}
    case = builder.build_case("case", fact_check, {"lead": "Who funds Firm Y?"})
    first, second = case["items"]
    lead = first["lead"]
    require(lead["excerpt"] == {"text": "Firm X transferred 3 million to Firm Y in 2024", "language": "en", "direct_quote": True},
            f"the quoted passage of the first web evidence becomes the excerpt: {lead['excerpt']}")
    require(lead["capture"]["source_url"] == "https://registry.example.test/filing", "the evidence URL is the source")
    require(lead["summary"] == "Firm X paid 3 million to Firm Y." and lead["direction_id"] == "f2", "summary and direction")
    require(first["gold"]["keep"] is True and "failure" not in first["gold"], "a verified claim must be kept")
    require(second["lead"]["excerpt"]["direct_quote"] is False, "a paraphrase is not marked as a quote")
    require(second["lead"]["capture"]["source_url"] == "https://blog.example.test/post", "falls back to the claim's sources")
    require(second["gold"]["keep"] is None and second["gold"]["failure"] == {"class": None, "research_visible": None},
            "a failed claim is left for the labellers")
    require(case["premise"] == "Who funds Firm Y?", "the methodology lead is the premise")
    raises(lambda: builder.build([], ROOT / "evals" / "private"), builder.BuildError,
           "the private fixture must never be written inside the checkout")


def test_labels_merge_only_on_agreement() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        fixture = {"items": [{"item_id": "c-1", "gold": {"keep": None, "label_source": "fact_check_verdict",
                                                          "failure": {"class": None, "research_visible": None}}},
                             {"item_id": "c-2", "gold": {"keep": None, "label_source": "fact_check_verdict",
                                                          "failure": {"class": None, "research_visible": None}}}]}
        path = folder / "case.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        header = "item_id,failure_class,research_visible\n"
        (folder / "a.csv").write_text(header + "c-1,allegation,yes\nc-2,summary_wider,yes\n", encoding="utf-8")
        (folder / "b.csv").write_text(header + "c-1,allegation,yes\nc-2,inference_as_fact,yes\n", encoding="utf-8")
        report = builder.merge_labels([path], folder / "a.csv", folder / "b.csv", None)
        merged = {i["item_id"]: i["gold"] for i in json.loads(path.read_text())["items"]}
        require(merged["c-1"]["failure"] == {"class": "allegation", "research_visible": True}, "agreed labels are kept")
        require(merged["c-2"]["failure"]["class"] is None and report["disagreements"] == 1,
                "a disagreement stays unlabelled until adjudicated")
        (folder / "adj.csv").write_text(header + "c-2,summary_wider,\n", encoding="utf-8")
        builder.merge_labels([path], folder / "a.csv", folder / "b.csv", folder / "adj.csv")
        merged = {i["item_id"]: i["gold"] for i in json.loads(path.read_text())["items"]}
        require(merged["c-2"]["failure"]["class"] == "summary_wider", "the adjudicated label is applied")



def test_single_labeller_merge_records_one_approver() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        fixture = {"items": [{"item_id": "c-1", "gold": {"keep": None, "label_source": "fact_check_verdict",
                                                          "failure": {"class": None, "research_visible": None}}}]}
        path = folder / "case.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        (folder / "a.csv").write_text("item_id,failure_class,research_visible\nc-1,allegation,yes\n", encoding="utf-8")
        report = builder.merge_labels([path], folder / "a.csv", None, None)
        gold = json.loads(path.read_text())["items"][0]["gold"]
        require(gold["failure"] == {"class": "allegation", "research_visible": True}, "the approved label is applied")
        require(gold["label_source"] == "single_labeller_approved" and report["disagreements"] == 0,
                "one approver is recorded as such, never as two labellers")

def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"decision-acceptance-check: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
