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
  TEMPLATE    report.html is not the unmodified skill template, contains no
              unresolved {{…}} placeholders, and carries case-specific content
              (a finding entity appears in the HTML).
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
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE = SCRIPT_DIR.parent / "skills/report-drafting/references/report-template.html"
FACT_CHECK_VALIDATOR = SCRIPT_DIR / "validate-fact-check.py"
REPORT_DRAFT_VALIDATOR = SCRIPT_DIR / "validate-report-draft.py"
MIN_REPORT_BYTES = 500
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")


def find_artifact(case: Path, name: str) -> Path | None:
    """Artifacts may sit at the case root or under data/ — accept either."""
    for cand in (case / name, case / "data" / name):
        if cand.is_file():
            return cand
    return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(case: Path) -> list[str]:
    fails: list[str] = []
    evidence_map_doc: dict | None = None

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
            loaded = json.loads(evidence_map.read_text())
            if isinstance(loaded, dict):
                evidence_map_doc = loaded
            else:
                fails.append("ARTIFACTS: evidence-map.json must contain a JSON object")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            fails.append(f"ARTIFACTS: evidence-map.json is not valid JSON ({e})")

    # TEMPLATE — a cosmetic edit to an otherwise unpopulated template is not a
    # report either. The original check only compared bytes, so changing the
    # <title> made 61 remaining {{…}} placeholders invisible to the gate.
    if report_html and TEMPLATE.is_file():
        html_bytes = report_html.read_bytes()
        html_text = html_bytes.decode(errors="replace")
        if html_bytes == TEMPLATE.read_bytes():
            fails.append("TEMPLATE: report.html is byte-identical to the skill template — "
                         "it was copied but never populated with case content")
        else:
            unresolved = TEMPLATE_PLACEHOLDER_RE.findall(html_text)
            if unresolved:
                sample = ", ".join(unresolved[:3])
                fails.append(
                    f"TEMPLATE: report.html still contains {len(unresolved)} unresolved "
                    f"template placeholder(s) (first: {sample}) — populate them or remove "
                    "unused template blocks"
                )

    # GENERATED — exact finding coverage and hashes replace language-specific prose
    # heuristics. This works identically for every writing system.
    if evidence_map_doc is not None:
        findings = _load_findings(case)
        expected_ids = [str(item.get("id", "")).strip() for item in findings
                        if isinstance(item, dict) and str(item.get("id", "")).strip()]
        claims = evidence_map_doc.get("claims")
        actual_ids = ([str(item.get("id", "")).strip() for item in claims
                       if isinstance(item, dict)] if isinstance(claims, list) else [])
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
            fails.append(
                "GENERATED: evidence-map finding IDs do not exactly match data/findings.json"
            )

        expected_inputs: dict[str, str] = {}
        for name in ("findings.json", "fact-check.json", "report-draft.json", "methodology.json"):
            path = case / "data" / name
            if path.is_file():
                expected_inputs[f"data/{name}"] = sha256(path)
        if evidence_map_doc.get("input_sha256") != expected_inputs:
            fails.append("GENERATED: evidence-map input hashes are stale or incomplete")

        expected_outputs = {
            name: sha256(path)
            for name, path in (("findings-report.md", report_md), ("report.html", report_html))
            if path is not None
        }
        if evidence_map_doc.get("output_sha256") != expected_outputs:
            fails.append("GENERATED: report artifact hashes do not match evidence-map.json")

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

    # EDITORIAL — model framing is required and must remain bound to the
    # fact-checked finding set. This keeps synthesis in the model without giving
    # it ownership of unsafe HTML/Markdown file construction.
    draft = case / "data" / "report-draft.json"
    if not draft.is_file():
        fails.append("EDITORIAL: data/report-draft.json is missing — the model must author framing and priority")
    elif REPORT_DRAFT_VALIDATOR.is_file():
        res = subprocess.run([sys.executable, str(REPORT_DRAFT_VALIDATOR), str(case)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            detail = " | ".join(line for line in res.stdout.splitlines() if line.startswith("FAIL"))[:400]
            fails.append(f"EDITORIAL: validate-report-draft.py FAILS ({detail})")

    # CONFIDENCE — inspect the language-neutral ledger rather than parsing English
    # display labels from Markdown.
    if evidence_map_doc is not None and isinstance(evidence_map_doc.get("claims"), list):
        for claim in evidence_map_doc["claims"]:
            if not isinstance(claim, dict):
                continue
            if (str(claim.get("report_confidence", "")).lower() == "high"
                    and str(claim.get("fact_check_status", "")).lower() != "verified"):
                fails.append(
                    f"CONFIDENCE: {claim.get('id') or 'finding'} is High confidence but "
                    f"its fact-check status is {claim.get('fact_check_status') or 'MISSING'}"
                )

    return fails


def _load_findings(case: Path) -> list[dict]:
    p = case / "data" / "findings.json"
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text()).get("findings", [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
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
