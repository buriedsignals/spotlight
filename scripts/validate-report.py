#!/usr/bin/env python3
"""Report-gate validator — completion claims, enforced deterministically.

A small local orchestrator will sometimes NARRATE completion that never happened
(grounded 2026-07-10, gold-inv-ef-0: the final gate declared `report.html`
"publication-ready" while it was byte-identical to the empty template, listed a
`data/investigation-log.json` deliverable that did not exist, and presented
validator-failing fact-check verdicts as "100% fact-checked"). This validator
makes those claims structurally impossible to pass:

  ARTIFACTS   the three mandatory report artifacts exist and are non-trivial:
              findings-report.md, report.html, evidence-map.json.
  TEMPLATE    report.html is not the unmodified skill template, and it carries
              case-specific content (a finding entity appears in the HTML).
  PHANTOM     every case/… or data/… path referenced in findings-report.md
              exists on disk — no phantom deliverables.
  CHAIN       data/fact-check.json passes validate-fact-check.py — a report can
              never be "ready" on top of a failing evidence trail.
  CONFIDENCE  a finding presented at High confidence in the report's findings
              table must map to a fact-check verdict with status "verified".

Usage:
  python3 scripts/validate-report.py <CASE_DIR> [--json]

Exit codes: 0 = report gate passes; 3 = at least one failure (report on stdout,
one line per failure). Same contract as validate-fact-check.py: run at the
report/final gate — the orchestrator fixes the listed issues (or discloses them
verbatim) BEFORE presenting artifacts as ready.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/report-drafting/references/report-template.html"
FACT_CHECK_VALIDATOR = SCRIPT_DIR / "validate-fact-check.py"
MIN_REPORT_BYTES = 500


def find_artifact(case: Path, name: str) -> Path | None:
    """Artifacts may sit at the case root or under data/ — accept either."""
    for cand in (case / name, case / "data" / name):
        if cand.is_file():
            return cand
    return None


def check(case: Path) -> list[str]:
    fails: list[str] = []

    # ARTIFACTS
    report_md = find_artifact(case, "findings-report.md")
    report_html = find_artifact(case, "report.html")
    evidence_map = find_artifact(case, "evidence-map.json")
    for name, p in (("findings-report.md", report_md),
                    ("report.html", report_html),
                    ("evidence-map.json", evidence_map)):
        if p is None:
            fails.append(f"ARTIFACTS: mandatory artifact {name} is missing")
    if report_md and report_md.stat().st_size < MIN_REPORT_BYTES:
        fails.append(f"ARTIFACTS: findings-report.md is trivial "
                     f"({report_md.stat().st_size} bytes < {MIN_REPORT_BYTES}) — not a drafted report")
    if evidence_map:
        try:
            json.loads(evidence_map.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            fails.append(f"ARTIFACTS: evidence-map.json is not valid JSON ({e})")

    # TEMPLATE — an unmodified copy of the skill template is not a report.
    if report_html and TEMPLATE.is_file():
        if report_html.read_bytes() == TEMPLATE.read_bytes():
            fails.append("TEMPLATE: report.html is byte-identical to the skill template — "
                         "it was copied but never populated with case content")
        else:
            # Case-specific content: at least one finding claim's leading entity
            # must appear in the HTML (weak but cheap; catches near-empty edits).
            findings = _load_findings(case)
            if findings:
                html_text = report_html.read_text(errors="replace").lower()
                entities = [e.lower() for e in _leading_entities(findings)]
                if entities and not any(e in html_text for e in entities):
                    fails.append("TEMPLATE: report.html contains none of the findings' entities "
                                 f"({', '.join(sorted(set(entities))[:4])}) — populated with wrong/no content")

    # PHANTOM — every case/data path the report references must exist.
    if report_md:
        text = report_md.read_text(errors="replace")
        for ref in sorted(set(re.findall(r"\b(?:case|data)/[\w./-]+\.\w+", text))):
            rel = ref[len("case/"):] if ref.startswith("case/") else ref
            if not ((case / rel).is_file() or (case / "data" / Path(rel).name).is_file()
                    or (case / Path(rel).name).is_file()):
                fails.append(f"PHANTOM: findings-report.md references {ref} but no such file exists in the case")

    # CHAIN — the evidence trail underneath the report must itself validate.
    fc = case / "data" / "fact-check.json"
    if not fc.is_file():
        fails.append("CHAIN: data/fact-check.json is missing — a report cannot precede its fact-check")
    elif FACT_CHECK_VALIDATOR.is_file():
        res = subprocess.run([sys.executable, str(FACT_CHECK_VALIDATOR), str(case)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            detail = " | ".join(l for l in res.stdout.splitlines() if l.startswith("FAIL"))[:400]
            fails.append(f"CHAIN: validate-fact-check.py FAILS on this case — fix the evidence "
                         f"trail before presenting the report ({detail})")

    # CONFIDENCE — High-confidence table rows need a verified fact-check verdict.
    if report_md and fc.is_file():
        try:
            checks = {c.get("finding_id"): c.get("status")
                      for c in json.loads(fc.read_text()).get("fact_checks", [])}
        except (json.JSONDecodeError, UnicodeDecodeError):
            checks = {}
        for fid, conf in re.findall(r"^\|\s*(F\d+)\s*\|.*\|\s*(High|Medium|Low)\s*\|\s*$",
                                    report_md.read_text(errors="replace"), re.MULTILINE):
            if conf == "High" and checks and checks.get(fid) != "verified":
                fails.append(f"CONFIDENCE: {fid} is presented at High confidence but its "
                             f"fact-check status is {checks.get(fid) or 'MISSING'} — downgrade "
                             f"the confidence or fix the fact-check")

    return fails


def _load_findings(case: Path) -> list[dict]:
    p = case / "data" / "findings.json"
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text()).get("findings", [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []


def _leading_entities(findings: list[dict]) -> list[str]:
    ents = []
    for f in findings:
        m = re.search(r"\b[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)+\b", f.get("claim", ""))
        if m:
            # Strip sentence-initial articles ("The Ethereum Foundation" → "Ethereum
            # Foundation") so capitalization noise can't false-fail the content check.
            ent = re.sub(r"^(?:The|A|An)\s+", "", m.group(0)).rstrip("'s").strip()
            if len(ent.split()) >= 2:
                ents.append(ent)
    return ents


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    case = Path(args.case_dir)
    if not case.is_dir():
        print(f"FAIL  case dir not found: {case}")
        return 3
    fails = check(case)
    if args.json:
        print(json.dumps({"passed": not fails, "failures": fails}, indent=2))
    else:
        for f in fails:
            print(f"FAIL  {f}")
        print("report gate: " + ("FAILED — fix or disclose the failures above before "
                                 "presenting the final gate" if fails else "PASSED"))
    return 3 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
