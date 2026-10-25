#!/usr/bin/env python3
"""Build the private acceptance fixture from fact-checked cases, and merge its labels.

    build_claims_fixture.py build --case NAME=FACT_CHECK_JSON[:METHODOLOGY_JSON] ... --out DIR
    build_claims_fixture.py merge --fixture DIR/claims-fixture.json --a A.csv [--b B.csv] [--adjudicated ADJ.csv]
    build_claims_fixture.py resplit --fixture FIXTURE.json[=FACT_CHECK_JSON] ... --out DIR

Case material is private, so everything this writes goes to a directory
outside the public checkout (refused otherwise). ``build`` writes
``claims-fixture.json`` (fixture.schema.json) and ``labelling-sheet.csv``, a
blank sheet two people fill in independently, blind to any model output.
``merge`` takes their two copies: a label both gave is kept; a disagreement is
listed in ``disagreements.csv`` and needs a row in the adjudicated sheet. Until
every disagreement is adjudicated, the fixture keeps those labels empty.
With only ``--a``, one person's approved sheet is applied and recorded as
``single_labeller_approved``, so a later second labeller can still be added.

How a fact-checked claim becomes a lead (the fixture's ``lead``):

- summary: the claim text, as a lane would state it;
- excerpt: the longest quoted passage (20+ characters, in straight or curly
  quotes) in the first piece of supporting evidence that has a web source,
  else in the first piece; ``direct_quote`` true. Without a quoted passage the
  evidence description itself, ``direct_quote`` false (the fact-checker's
  paraphrase: weaker than a lane's excerpt, said so in the sheet);
- source: that evidence's URL when it is http(s), else the first http(s) URL in
  the claim's sources; with none, a manual capture without a URL;
- direction: the claim's finding (``F3-C1`` -> ``f3``), labelled with the
  finding's first claim; topic ``<direction>-web``;
- the brief's premise: the methodology's ``lead``, else the fact-check's project;
- gold: verdict ``verified`` -> keep (setting it aside would lose a lead that
  turned out true); any other verdict -> a failure whose class and research
  visibility come only from the labelling sheet; keep is then not scored.

A whole case goes to one half, dev or held-out: the half the split file
(``SPOTLIGHT_ACCEPTANCE_SPLIT``) assigns its ``case_key``, else a salted hash of
the key (``case_split``), so no case is tuned on and tested on. The key is
the source case's folder (the one holding ``data/fact-check.json``, or the
fact-check's own folder), written once at build time: renaming the case does
not move it. ``resplit`` applies that rule to fixtures built before it, taking
the key from the source fact-check when the fixture has none, and writes
copies to a new directory outside the checkout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPLIT_SALT = "spotlight-acceptance-split-1"
SPLIT_ENV = "SPOTLIGHT_ACCEPTANCE_SPLIT"
FAILURE_CLASSES = ("weak_source_chain", "summary_wider", "inference_as_fact", "conflicting_values", "wrong_subject",
                   "weak_relevance", "allegation")
QUESTION_COLUMNS = ("q1_identity", "q3_source", "q4_assertion", "q5_status", "q6_fidelity", "c1_premise")
SHEET_FIELDS = ("item_id", "split", "verdict", "summary", "excerpt", "excerpt_is_quote", "source_url",
                "fact_check_notes", "failure_class", "research_visible", *QUESTION_COLUMNS)
QUOTED = re.compile(r"'([^']{20,})'|\"([^\"]{20,})\"|“([^”]{20,})”|‘([^’]{20,})’")
CAPTURED_AT = "2026-10-23T00:00:00Z"


class BuildError(Exception):
    pass


def outside_checkout(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise BuildError(f"{path} is inside the public checkout; the private fixture stays outside it")
    return path


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _http(url: Any) -> str:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else ""


def excerpt_of(description: str) -> tuple[str, bool]:
    quotes = [next(g for g in m.groups() if g) for m in QUOTED.finditer(description)]
    if quotes:
        return max(quotes, key=len).strip(), True
    return description.strip(), False


def claim_lead(alias: str, claim: Mapping[str, Any], direction: str) -> dict[str, Any]:
    evidence = claim.get("evidence_for") or []
    chosen = next((e for e in evidence if _http(e.get("source"))), evidence[0] if evidence else {})
    text, quoted = excerpt_of(str(chosen.get("description") or claim.get("notes") or claim["claim_text"]))
    url = _http(chosen.get("source")) or next((u for u in claim.get("sources") or [] if _http(u)), "")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lead = {
        "schema_version": "1.0", "lead_id": f"{alias}-{claim['id']}", "topic_id": f"{direction}-web",
        "direction_id": direction, "summary": str(claim["claim_text"])[:4000], "subjects": [], "absence": False,
        "status": "candidate", "stored": "page",
        "capture": ({"route": "api", "tool": "fact-check", "source_url": url, "captured_at": CAPTURED_AT} if url
                    else {"route": "manual", "captured_at": CAPTURED_AT}),
        "content_ref": f"sources/{sha}.txt", "content_sha256": sha,
        "provenance": {"method": "agent_candidate", "model": "fact-check-claim"},
        "excerpt": {"text": text[:4000], "language": "en", "direct_quote": quoted},
    }
    return lead


def direction_of(claim: Mapping[str, Any]) -> str:
    match = re.match(r"\s*F?(\d+)", str(claim.get("finding_id") or ""), re.IGNORECASE)
    return f"f{match.group(1)}" if match else "f0"


def case_key_of(fact_check: Path) -> str:
    """The source case's identity: its folder, never the name the case is built under."""
    fact_check = fact_check.resolve()
    return (fact_check.parent.parent if fact_check.parent.name == "data" else fact_check.parent).name


def split_assignments() -> dict[str, str]:
    """Hand-assigned halves from the file SPOTLIGHT_ACCEPTANCE_SPLIT names: {"assignments": {case_key: half}}.

    Kept with the private fixtures. A few real cases split by hash can leave one half with nothing to
    learn from, so the cases are balanced by hand and the file is committed where the fixtures live.
    """
    raw = os.environ.get(SPLIT_ENV, "").strip()
    if not raw:
        return {}
    doc = json.loads(Path(raw).expanduser().read_text(encoding="utf-8"))
    assignments = doc.get("assignments") if isinstance(doc, dict) else None
    if not isinstance(assignments, dict) or any(v not in ("dev", "heldout") for v in assignments.values()):
        raise BuildError(f"{raw}: expected {{\"assignments\": {{case_key: \"dev\" | \"heldout\"}}}}")
    return assignments


def case_split(case_key: str) -> str:
    """The half a whole case belongs to: its hand assignment, else a salted hash of its key."""
    assigned = split_assignments().get(case_key)
    if assigned:
        return assigned
    digest = hashlib.sha256(f"{SPLIT_SALT}:{case_key}".encode()).hexdigest()
    return "dev" if int(digest, 16) % 2 == 0 else "heldout"


def build_case(name: str, fact_check: Mapping[str, Any], methodology: Mapping[str, Any] | None,
               case_key: str) -> dict[str, Any]:
    alias = "case-" + hashlib.sha256(name.encode()).hexdigest()[:6]
    claims = [c for c in fact_check.get("claims") or [] if isinstance(c, Mapping) and c.get("claim_text")]
    directions: dict[str, str] = {}
    for claim in claims:
        directions.setdefault(direction_of(claim), str(claim["claim_text"])[:160])
    items = []
    for claim in claims:
        direction = direction_of(claim)
        lead = claim_lead(alias, claim, direction)
        verdict = str(claim.get("verdict") or "")
        gold: dict[str, Any] = {"keep": True if verdict == "verified" else None, "verdict": verdict,
                                "label_source": "fact_check_verdict", "answers": {}}
        if verdict != "verified":
            gold["failure"] = {"class": None, "research_visible": None}
        items.append({"item_id": lead["lead_id"], "split": case_split(case_key), "origin": "fact_check_claim", "lead": lead, "gold": gold,
                      "_notes": str(claim.get("notes") or "")})
    premise = str((methodology or {}).get("lead") or fact_check.get("project") or name)
    return {"alias": alias, "case_key": case_key, "premise": premise, "directions": directions, "items": items}


def build(cases: list[tuple[str, Path, Path | None]], out: Path) -> dict[str, Any]:
    out = outside_checkout(out)
    built = [build_case(name, json.loads(fc.read_text(encoding="utf-8")),
                        json.loads(m.read_text(encoding="utf-8")) if m else None, case_key_of(fc))
             for name, fc, m in cases]
    # One fixture per case keeps each case's brief; all go in one file list.
    out.mkdir(parents=True, exist_ok=True)
    rows, written = [], []
    for case in built:
        fixture = {
            "fixture_version": "1.0", "name": case["alias"], "synthetic": False,
            "description": "Fact-checked claims as leads (private; built by build_claims_fixture.py).",
            "case": {"case_id": case["alias"], "case_key": case["case_key"], "premise": case["premise"], "directions": case["directions"],
                     "topics": [{"topic_id": f"{d}-web", "direction_id": d, "lane_type": "web"} for d in case["directions"]]},
            "items": [{k: v for k, v in item.items() if k != "_notes"} for item in case["items"]],
            "planted_clues": [],
        }
        path = out / f"{case['alias']}.json"
        path.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
        for item in case["items"]:
            lead = item["lead"]
            rows.append({"item_id": item["item_id"], "split": item["split"], "verdict": item["gold"]["verdict"],
                         "summary": lead["summary"], "excerpt": lead["excerpt"]["text"],
                         "excerpt_is_quote": "yes" if lead["excerpt"]["direct_quote"] else "no (fact-checker paraphrase)",
                         "source_url": lead["capture"].get("source_url", ""), "fact_check_notes": item["_notes"],
                         **{k: "" for k in ("failure_class", "research_visible", *QUESTION_COLUMNS)}})
    with (out / "labelling-sheet.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"fixtures": [str(p) for p in written], "items": len(rows),
            "not_verified": sum(1 for r in rows if r["verdict"] != "verified"),
            "dev": sum(1 for r in rows if r["split"] == "dev"), "sheet": str(out / "labelling-sheet.csv")}


def resplit(inputs: list[tuple[Path, Path | None]], out: Path) -> dict[str, str]:
    """Copy fixtures to ``out`` with every item in its case's half (fixtures built before case_split).

    Each input is a fixture and, for a fixture without a ``case_key``, its source fact-check. Every
    input is checked and no output file may exist before anything is written; the copies are written
    to a temporary directory and renamed into ``out``.
    """
    out = outside_checkout(out)
    names = [path.name for path, _source in inputs]
    if len(set(names)) != len(names):
        raise BuildError(f"two inputs would be written to one output name: {sorted({n for n in names if names.count(n) > 1})}")
    existing = [name for name in names if (out / name).exists()]
    if existing:
        raise BuildError(f"output files already exist in {out}: {existing}")
    fixtures = []
    for path, source in inputs:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if not fixture["case"].get("case_key"):
            if source is None:
                raise BuildError(f"{path.name} has no case_key: pass its source as --fixture {path}=FACT_CHECK_JSON")
            fixture["case"]["case_key"] = case_key_of(source)
        half = case_split(fixture["case"]["case_key"])
        for entry in [*fixture["items"], *fixture.get("planted_clues", [])]:
            entry["split"] = half
        fixtures.append((path.name, fixture))
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out, prefix=".resplit-") as tmp:
        for name, fixture in fixtures:
            (Path(tmp) / name).write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for name, _fixture in fixtures:
            (Path(tmp) / name).rename(out / name)
    return {f["case"]["case_key"]: f["items"][0]["split"] for _name, f in fixtures}


# ------------------------------------------------------------------ merge
LABEL_COLUMNS = ("failure_class", "research_visible", *QUESTION_COLUMNS)


def _read_sheet(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["item_id"]: {k: (row.get(k) or "").strip() for k in LABEL_COLUMNS} for row in csv.DictReader(fh)}


def _valid(column: str, value: str) -> bool:
    if column == "failure_class":
        return value in FAILURE_CLASSES
    if column == "research_visible":
        return value in ("yes", "no")
    return bool(value)


def merge_labels(fixture_paths: list[Path], a: Path, b: Path | None, adjudicated: Path | None) -> dict[str, Any]:
    single = b is None
    first = _read_sheet(a)
    second = first if single else _read_sheet(b)
    final = _read_sheet(adjudicated) if adjudicated else {}
    disagreements, applied = [], 0
    for path in fixture_paths:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for item in fixture["items"]:
            iid, gold = item["item_id"], item["gold"]
            labels: dict[str, str] = {}
            for column in LABEL_COLUMNS:
                x, y = first.get(iid, {}).get(column, ""), second.get(iid, {}).get(column, "")
                if not x and not y:
                    continue
                if x == y and _valid(column, x):
                    labels[column] = x
                elif _valid(column, final.get(iid, {}).get(column, "")):
                    labels[column] = final[iid][column]
                else:
                    disagreements.append({"item_id": iid, "column": column, "labeller_a": x, "labeller_b": y})
            answers = gold.setdefault("answers", {})
            for column in QUESTION_COLUMNS:
                if column in labels:
                    answers[column] = labels[column]
            if "failure" in gold:
                gold["failure"] = {"class": labels.get("failure_class"),
                                   "research_visible": {"yes": True, "no": False}.get(labels.get("research_visible", ""))}
            if labels:
                gold["label_source"] = "single_labeller_approved" if single else "two_labellers_adjudicated"
                applied += 1
        path.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    report = a.parent / "disagreements.csv"
    with report.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("item_id", "column", "labeller_a", "labeller_b"))
        writer.writeheader()
        writer.writerows(disagreements)
    return {"items_labelled": applied, "disagreements": len(disagreements), "report": str(report)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--case", action="append", required=True, help="NAME=FACT_CHECK_JSON[:METHODOLOGY_JSON]")
    b.add_argument("--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--fixture", action="append", required=True)
    m.add_argument("--a", required=True)
    m.add_argument("--b")
    m.add_argument("--adjudicated")
    r = sub.add_parser("resplit")
    r.add_argument("--fixture", action="append", required=True, help="FIXTURE_JSON[=FACT_CHECK_JSON]")
    r.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            cases = []
            for spec in args.case:
                name, _, paths = spec.partition("=")
                fact_check, _, methodology = paths.partition(":")
                cases.append((name, Path(fact_check), Path(methodology) if methodology else None))
            print(json.dumps(build(cases, Path(args.out)), indent=1))
        elif args.command == "resplit":
            inputs = [(Path(f), Path(src) if src else None) for f, _, src in (spec.partition("=") for spec in args.fixture)]
            print(json.dumps(resplit(inputs, Path(args.out)), indent=1))
        else:
            fixtures = [outside_checkout(Path(p)) for p in args.fixture]
            print(json.dumps(merge_labels(fixtures, Path(args.a), Path(args.b) if args.b else None,
                                          Path(args.adjudicated) if args.adjudicated else None), indent=1))
    except BuildError as exc:
        print(f"build_claims_fixture: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
