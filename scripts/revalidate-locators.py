#!/usr/bin/env python3
"""Re-validate every activated case's passage locators (pilot blocker 2).

  python3 scripts/revalidate-locators.py {CASE_ROOT} [--json]

For each case under the root that has data/case-contract.json, every active
passage is re-checked against its stored artifact: the locator must still
reproduce the exact text and the anchor hash must still match. The report
gives the revalidation rate to compare with the stop threshold in
docs/source-expression-pilot-results.json (at least 0.95). Read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("validate_case", SCRIPT_DIR / "validate-case.py")
VALIDATOR = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(VALIDATOR)

LOCATOR_MARKERS = ("anchor", "locator", "line range", "json pointer", "does not reproduce", "text does not match")


def revalidate(root: Path) -> dict:
    cases, expressions, failures, skipped = 0, 0, [], []
    for contract in sorted(root.glob("*/data/case-contract.json")):
        case = contract.parent.parent
        try:
            doc = json.loads((case / "data" / "source-expressions.json").read_text(encoding="utf-8"))
            findings = json.loads((case / "data" / "findings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            skipped.append({"case": case.name, "reason": str(exc)[:200]})
            continue
        cases += 1
        active = [e for e in doc.get("expressions", []) if isinstance(e, dict)
                  and (e.get("lifecycle_events") or [{}])[-1].get("event") == "activated"]
        expressions += len(active)
        bundle_path = case / "data" / "evidence-bundle.json"
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.is_file() else None
        except (OSError, ValueError):
            bundle = None
        errors = VALIDATOR.validate_source_expressions(case, doc, findings, None, bundle, enforce_positive_verdicts=False)
        broken = {e.get("id") for e in active
                  if any(f"{e.get('id')!r}" in err and any(m in err.lower() for m in LOCATOR_MARKERS) for err in errors)}
        failures += [{"case": case.name, "expression_id": i} for i in sorted(broken)]
    # An unreadable case cannot count as stable: the rate is withheld until
    # every activated case can be read.
    rate = None if expressions == 0 or skipped else round(1 - len(failures) / expressions, 4)
    # Skipped cases are listed, not silently dropped: an unreadable case is
    # not a stable one.
    return {"cases": cases, "active_expressions": expressions, "locator_failures": failures,
            "skipped_cases": skipped, "revalidation_rate": rate}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = revalidate(Path(args.case_root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['cases']} activated cases, {report['active_expressions']} active passages, "
              f"revalidation rate {report['revalidation_rate']}")
        for failure in report["locator_failures"]:
            print(f"  {failure['case']}: {failure['expression_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
