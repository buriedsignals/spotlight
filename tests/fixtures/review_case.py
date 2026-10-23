#!/usr/bin/env python3
"""Build an offline case paused at one review gate, for the review-page checks.

Usage: review_case.py CASE_DIR {methodology,research,investigation_gate,report,ingest}
       [--excerpt TEXT] [--clean] [--activated] [--no-suggestions]

Earlier gates are passed on the command-line channel, the same path the
current skills use. ``--excerpt`` places hostile case text in the finding's
claim and evidence. Without ``--clean`` the Investigation gate shows the shared
fixture as it is: one unverified finding (an open disposition) and a log the
case validator rejects (a blocking failure). ``--clean`` marks the finding
verified and writes a valid log, so nothing blocks approval. ``--activated``
replaces the investigation artifacts with an activated (findings 1.1) case,
born and bound at the Investigation-gate approval like a new case;
``--flagged`` gives its fact-check one flag for the ingest page to decide.
``research`` builds a case with a research plan paused at the Research gate
(see ``build_research``); there ``--excerpt`` is the first lead's excerpt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
STAGES = ("methodology", "research", "investigation_gate", "report", "ingest")


def conformance_fixtures():
    spec = importlib.util.spec_from_file_location(
        "spotlight_conformance_fixtures", ROOT / "tests" / "orchestration-conformance-check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, check=False, cwd=ROOT
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_research(case: Path, excerpt: str | None, suggestions: bool) -> None:
    """A case paused at the Research gate, built by the real producers: lane
    folders as the runner writes them, the real merge (leads, clues K1-K6),
    triage in flag-only mode with a deterministic in-process model (one lead
    complicates the premise, one has orders that disagree, one is about none
    of the brief's subjects), a round ledger
    with a topic the budget left out and, unless ``suggestions`` is false,
    the investigator's suggested marks."""
    sys.path.insert(0, str(ROOT / "tests"))
    lanes = load_module("spotlight_research_lane_fixture", ROOT / "tests" / "research_lane_fixture.py")
    triage = load_module("spotlight_research_triage", ROOT / "scripts" / "research-triage.py")
    client = sys.modules["client"]
    topic = lanes.topic
    method = lanes.methodology(topic("ownership-registries", "ownership", "registries"),
                               topic("ownership-web", "ownership", "web"),
                               topic("contracts-news-courts", "contracts", "news-courts"),
                               topic("contracts-archives", "contracts", "archives"))
    lane_case = lanes.Case(case, method)
    (case / "brief-directions.txt").write_text("Who owns Northwind Holdings?\n", encoding="utf-8")
    run(str(SEAM), "approve", "methodology", "--approved-by", "journalist:fixture",
        "--approved-at", "2026-10-23T09:00:00Z", str(case))
    owner = excerpt or "Shareholder: Example Trust (100 ordinary shares)"
    lane_case.page_lead("ownership-registries", "reg-1", page_text=f"Annual return 2025. {owner}.", excerpt=owner,
                        summary="Northwind Holdings names Example Trust as its sole shareholder.",
                        subjects=["Northwind Holdings", "Example Trust"], url="https://registry.example.org/northwind")
    lane_case.page_lead("ownership-registries", "reg-2", page_text="Example Trust was sanctioned in 2024 by the regulator.",
                        excerpt="Example Trust was sanctioned in 2024", subjects=["Example Trust"],
                        url="https://sanctions.example.org/example-trust")
    lane_case.absence_lead("ownership-registries", "reg-gap", ["Northwind Holdings"], ["Northwind Holdings beneficial owner"])
    lane_case.finish("ownership-registries")
    lane_case.snippet_lead("ownership-web", "web-1", "Northwind Holdings is said to be owned by a trust.",
                           "https://blog.example.org/northwind")
    lane_case.page_lead("ownership-web", "web-2", page_text="Northwind Holdings opened an office.",
                        excerpt="Northwind Holdings is owned by its directors", url="https://news.example.org/office")
    lane_case.page_lead("ownership-web", "web-3", page_text="A Northwind Holdings shop sells bicycles.",
                        excerpt="A Northwind Holdings shop sells bicycles", url="https://shop.example.org/",
                        status="set_aside")
    lane_case.finish("ownership-web")
    lane_case.page_lead("contracts-news-courts", "court-1",
                        page_text="The harbour contract was awarded to Portside Ltd, not Northwind Holdings.",
                        excerpt="The harbour contract was awarded to Portside Ltd, not Northwind Holdings",
                        url="https://court.example.org/harbour")
    lane_case.page_lead("contracts-news-courts", "court-2", page_text="The harbour authority published the tender in 2024.",
                        excerpt="The harbour authority published the tender in 2024",
                        url="https://harbour.example.org/tender")
    lane_case.finish("contracts-news-courts")
    lane_case.finish("contracts-archives", status="not_started")
    run(str(ROOT / "scripts" / "research-merge.py"), str(case), "--round", "1")

    def answer(state, qid, question):
        options = list(question["criteria"])
        summary = state.get("item", {}).get("summary", "")
        base = qid.removesuffix("_rev")
        picks = {"q1_identity": "none_of_the_brief" if "harbour authority" in summary else "same_identifier",
                 "q3_source": "primary_record", "q4_assertion": "asserted",
                 "q5_status": "done", "q6_fidelity": "faithful",
                 "c1_premise": "complicates" if "Portside" in summary else "supports"}
        pick = picks.get(base, sorted(options)[0])  # the same option in both orders
        if qid == "q3_source_rev" and "sole shareholder" in summary:
            pick = "secondary"  # the two orders disagree: "model unsure"
        return {"type": "choice", "choice": pick, "confidence": 0.9,
                "probabilities": {o: (0.9 if o == pick else 0.1 / (len(options) - 1)) for o in options}}

    provider = client.FakeProvider(answer)
    leads_doc = json.loads((case / "data/leads.json").read_text(encoding="utf-8"))
    clues_doc = json.loads((case / "data/clues.json").read_text(encoding="utf-8"))
    document = triage.run_triage([e["lead"] for e in leads_doc["leads"]], triage.brief_from_methodology(method),
                                 clues_doc, {"jev": provider}.get, lambda _ji: ("jev", ""))
    document["generated_at"] = "2026-10-23T09:30:00Z"  # after the merge, as the triage script stamps it
    triage.write_output(case, document)
    write_json(case / "research/rounds.json", {"schema_version": "1.0", "rounds": [{
        "round": 1, "plan_sha256": "0" * 64, "mode": "openrouter_swarm", "budget_usd": 1, "spent_usd": 0.03,
        "started_at": "2026-10-23T09:10:00Z", "finished_at": "2026-10-23T09:20:00Z",
        "ran": ["ownership-registries", "ownership-web", "contracts-news-courts"],
        "left_out": [{"topic_id": "contracts-archives", "reason": "the round budget was spent"}], "refused": []}]})
    if suggestions:
        # The Research gate's schema: a mark for every clue and approved
        # direction, bound to the leads, clues and triage it was written for.
        clue_marks = {"absence": ("dismiss", "A registry gap is common here.")}
        inputs = {relative: hashlib.sha256((case / relative).read_bytes()).hexdigest()
                  for relative in ("data/leads.json", "data/clues.json", "data/research-triage.json")}
        write_json(case / "data/research-suggestions.json", {
            "schema_version": "1.0",
            "generated_at": "2026-10-23T09:40:00Z",
            "inputs": inputs,
            "clues": [{"clue_id": c["clue_id"],
                       "mark": clue_marks.get(c["type"], ("pursue", "The clue bears on the premise."))[0],
                       "reason": clue_marks.get(c["type"], ("pursue", "The clue bears on the premise."))[1]}
                      for c in clues_doc["clues"]],
            "directions": [
                {"direction_id": "ownership", "mark": "keep", "reason": "The register names an owner."},
                {"direction_id": "contracts", "mark": "drop", "reason": "The court record answers it."},
                {"direction_id": "sanctions", "mark": "add", "direction": "Sanctions on Example Trust",
                 "questions": ["Is Example Trust under sanctions?"],
                 "reason": "A watchlist clue names the shareholder."},
            ]})


def build(case: Path, stage: str, excerpt: str | None, clean: bool, activated: bool = False, flagged: bool = False,
          suggestions: bool = True) -> None:
    if stage == "research":
        build_research(case, excerpt, suggestions)
        return
    fixtures = conformance_fixtures()
    (case / "data").mkdir(parents=True)
    (case / "brief-directions.txt").write_text("Verify the offline fixture.\n", encoding="utf-8")
    write_json(case / "data/methodology.json", fixtures.methodology())
    if stage == "methodology":
        return
    approval = ("--approved-by", "journalist:fixture", "--approved-at", "2026-08-23T12:00:00Z")
    run(str(SEAM), "approve", "methodology", *approval, str(case))
    fixtures.investigation_outputs(case)
    if excerpt is not None or clean:
        findings = json.loads((case / "data/findings.json").read_text(encoding="utf-8"))
        check = json.loads((case / "data/fact-check.json").read_text(encoding="utf-8"))
        if excerpt is not None:
            findings["findings"][0]["evidence"] = excerpt
            findings["findings"][0]["sources"][0]["description"] = excerpt
        if clean:
            check["claims"][0]["verdict"] = "verified"
            check["claims"][0]["evidence_for"] = [{
                "description": "Primary record states the claim.",
                "source": "research/primary-record.txt",
                "access_method": "full_text",
                "local_file": "research/primary-record.txt",
            }]
            log = json.loads((case / "data/investigation-log.json").read_text(encoding="utf-8"))
            log["cycles"][0]["sources_consulted"] = [
                {"url": source["url"], "type": "file", "accessed": "2026-08-23T11:00:00Z", "useful": True}
                for source in findings["findings"][0]["sources"]
            ]
            write_json(case / "data/investigation-log.json", log)
        write_json(case / "data/findings.json", findings)
        write_json(case / "data/fact-check.json", check)
    if activated:
        cases = load_module("validate_case_check", ROOT / "tests" / "validate-case-check.py")
        findings, check, bundle, expressions = cases.activated_documents(case)
        project = json.loads((case / "data/methodology.json").read_text(encoding="utf-8")).get("project", findings["project"])
        for document in (findings, check, bundle, expressions):
            document["project"] = project
        # Anchor the fact-check evidence to the stored passage text.
        anchor = case / "research" / "filing.txt"
        evidence = check["claims"][0]["evidence_for"][0]
        evidence.update({"local_file": "research/filing.txt",
                         "source_ref": {"path": "research/filing.txt", "line_start": 2, "line_end": 2},
                         "sha256": hashlib.sha256(anchor.read_bytes()).hexdigest()})
        if flagged:
            check["claims"][0]["flags"] = [{"code": "partial_support", "reason": "Only part of the payment is documented."}]
        for name, document in (("findings.json", findings), ("fact-check.json", check),
                               ("evidence-bundle.json", bundle), ("source-expressions.json", expressions)):
            write_json(case / "data" / name, document)
        write_json(case / "data/case-contract.json", {
            "schema_version": "1.0", "project": project, "current_contract_version": "1.1",
            "activation_events": [{"event_id": "born-fixture", "kind": "born", "activated_contract_version": "1.1",
                                   "activated_at": "2026-08-23T11:00:00Z", "tool_version": "fixture"}]})
    if stage == "investigation_gate":
        return
    run(str(SEAM), "approve", "investigation_gate", *approval, str(case))
    run(str(ROOT / "scripts" / "build-provenance-manifest.py"), str(case))
    (case / "review.html").write_text("<!doctype html><title>Offline review</title>\n", encoding="utf-8")
    run(str(SEAM), "seal-investigation-gate", str(case))
    if stage == "report":
        return
    run(str(SEAM), "decide-report", "declined", str(case))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir")
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--excerpt")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--activated", action="store_true")
    parser.add_argument("--flagged", action="store_true", help="with --activated: the fact-check carries one flag")
    parser.add_argument("--no-suggestions", dest="suggestions", action="store_false",
                        help="research: no data/research-suggestions.json")
    args = parser.parse_args()
    build(Path(args.case_dir), args.stage, args.excerpt, args.clean, args.activated, args.flagged, args.suggestions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
