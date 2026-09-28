#!/usr/bin/env python3
"""Deterministic Phase 6 claim eligibility (offline).

    python3 scripts/ingest-eligibility.py CASE_DIR [--entities FILE] [--existing-claims FILE]

Implements the ingest eligibility gate from skills/ingest/references/entity-model.md
in code, then layers stored decision-model signals on top:

1. Verdict is verified or partially_verified.
2. Grounding confidence_cap is not low. A finding without any recorded cap stays
   eligible (the legacy policy of scripts/ingest-source-expressions.py) but
   carries a visible warning.
3. At least one source reference is present.
4. The finding is not RLM-derived.

Gate 1 decision signals (data/decision-signals.json) can only make the result
more conservative: in enforce mode a low decision cap excludes the finding and a
medium cap moves it to the lead layer with needs_verification. In advisory and
shadow modes the same outcome is only a recommendation. A reviewer override
counts only for the exact reviewed fingerprint. Claim facets (claim type,
temporal status, source assertion) are attached from fresh judged signals.

Ingest decision signals (data/decision-signals-ingest.json) are reported as
`ingest_checks`: every current knowledge-batch target, plus entity and matching
targets when the same input files are passed, with status judged, unavailable,
stale (inputs changed since the check) or unchecked (never checked). In enforce
mode, stale or unchecked targets must be checked before staging.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import decision_signals_lib as lib  # noqa: E402

POSITIVE = {"verified", "partially_verified"}
RANK = {"low": 1, "medium": 2, "high": 3}


def rlm_derived(finding: dict[str, Any]) -> bool:
    if finding.get("rlm_assisted") is True:
        return True
    blob = json.dumps(finding.get("sources", []), ensure_ascii=False)
    return "rlm-analysis.json" in blob or "rlm-artifact" in blob


def grounding_cap(finding: dict[str, Any], checks: list[dict[str, Any]]) -> str | None:
    caps = []
    grounding = finding.get("grounding")
    if isinstance(grounding, dict) and grounding.get("confidence_cap") in RANK:
        caps.append(grounding["confidence_cap"])
    for check in checks:
        cap = (check.get("grounding") or {}).get("confidence_cap")
        if cap in RANK:
            caps.append(cap)
    return min(caps, key=RANK.get) if caps else None


def evaluate(case: Path, entities: list[Any] | None = None, existing: list[Any] | None = None) -> dict[str, Any]:
    render = lib.render_module()
    findings = [f for f in json.loads((case / "data" / "findings.json").read_text(encoding="utf-8")).get("findings") or [] if isinstance(f, dict)]
    checks = render.canonical_checks(json.loads((case / "data" / "fact-check.json").read_text(encoding="utf-8")))
    signals = lib.load_signals(case)
    out = []
    for finding in findings:
        fid = lib.text(finding.get("id"))
        linked = [c for c in checks if c["finding_id"] == fid]
        verdict = render.aggregate_verdict(finding, checks)
        cap = grounding_cap(finding, linked)
        reasons, warnings = [], []
        if verdict["status"] not in POSITIVE:
            reasons.append(f"verdict {verdict['status']}")
        if cap == "low":
            reasons.append("grounding capped low")
        elif cap is None:
            warnings.append("no grounding confidence_cap recorded (legacy finding); eligibility does not rest on a cap")
        if not finding.get("sources"):
            reasons.append("no sources")
        if rlm_derived(finding):
            reasons.append("RLM-derived")
        layer = "durable" if verdict["status"] == "verified" else "lead"
        record: dict[str, Any] = {
            "finding_id": fid, "verdict": verdict["status"], "confidence": verdict["confidence"],
            "confidence_cap": cap, "eligible": not reasons, "exclusion_reasons": reasons,
            "warnings": warnings, "layer": layer, "needs_verification": layer == "lead",
        }
        signal = lib.finding_signal(signals, case, finding)
        if signal:
            decision: dict[str, Any] = {"status": signal.get("status"), "mode": signal.get("mode"),
                                        "flags": signal.get("flags", []), "applied": False}
            if signal.get("status") == "judged":
                record["facets"] = signal.get("facets", {})
                decision_cap = signal.get("cap")
                if decision_cap in ("low", "medium"):
                    recommendation = {"exclude": decision_cap == "low", "layer": "lead", "confidence_cap": decision_cap}
                    decision["recommendation"] = recommendation
                    if lib.enforced_cap(signal, signals):
                        decision["applied"] = True
                        if recommendation["exclude"] and record["eligible"]:
                            record["eligible"] = False
                            record["exclusion_reasons"].append("decision check: " + ", ".join(
                                lib.text(f.get("reason")) for f in signal.get("flags", []) if isinstance(f, dict)))
                        record["layer"] = "lead"
                        record["needs_verification"] = True
                        if RANK.get(record["confidence"], 1) > RANK[decision_cap]:
                            record["confidence"] = decision_cap
            record["decision_check"] = decision
        out.append(record)
    return {"findings": out, "ingest_checks": lib.ingest_status(case, entities, existing)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Phase 6 claim eligibility (offline).")
    parser.add_argument("case_dir")
    parser.add_argument("--entities", type=Path, help="the same entity list passed to decision-signals.py --entities")
    parser.add_argument("--existing-claims", type=Path, help="the same list passed to decision-signals.py --existing-claims")
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8")) if path else None  # noqa: E731
    try:
        report = evaluate(Path(args.case_dir), load(args.entities), load(args.existing_claims))
    except lib.SignalsError as exc:
        print(json.dumps({"error": f"decision signals are invalid: {exc}"}))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
