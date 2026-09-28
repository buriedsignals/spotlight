#!/usr/bin/env python3
"""Report-finalizer stage: apply stored decision-model prose signals (offline).

Reads data/decision-signals.json (phase "report"), written earlier by
scripts/decision-signals.py. Makes no network request. Targets and their
fingerprints come from decision_signals_lib.report_items, the same function the
producer uses, so the sentence, its field and every cited finding's identity,
claim, verdict and displayed confidence are covered.

* No signals file, or no report phase: PASS (decision checks not configured).
* Signals file present but invalid: FAIL in every mode (never silently skipped).
* advisory / shadow: PASS, listing flagged, changed, unchecked and unavailable prose.
* enforce: FAIL when current prose is flagged and not overridden for its exact
  fingerprint, or when prose or its findings changed after the check, or when
  prose was never checked. Unavailable checks are reported but do not block.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_signals_lib as lib  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-report-fidelity.py CASE_DIR")
        return 2
    case = Path(sys.argv[1])
    try:
        signals = lib.load_signals(case)
    except lib.SignalsError as exc:
        print(f"FAIL  data/decision-signals.json is invalid: {exc}")
        return 1
    phase = ((signals or {}).get("phases") or {}).get("report")
    if not phase:
        print("PASS  decision checks not configured for report prose")
        return 0
    mode = phase.get("mode", "advisory")
    items = {item.get("target"): item for item in phase.get("items", [])}
    flagged, stale, unchecked, unavailable = [], [], [], []
    for current in lib.report_items(case, signals):
        target = current["target"]
        item = items.get(target)
        if item is None:
            unchecked.append(target)
        elif item.get("input_sha256") != current["input_sha256"]:
            stale.append(target)
        elif item.get("status") == "unavailable":
            unavailable.append(target)
        elif item.get("status") == "judged" and item.get("flags") and not lib.override_for(signals, target, current["input_sha256"]):
            reasons = ", ".join(lib.text(f.get("reason")) for f in item["flags"] if isinstance(f, dict))
            flagged.append(f"{target} ({item.get('overstatement_type', 'unspecified')}: {reasons})")
    blocking = [f"prose may overstate its findings: {entry}" for entry in flagged]
    blocking += [f"prose or its findings changed after the decision check: {target}" for target in stale]
    blocking += [f"prose has no decision check: {target}" for target in unchecked]
    notices = [f"decision check unavailable (not judged): {target}" for target in unavailable]
    if phase.get("status") in ("partial", "unavailable"):
        notices.append(f"report decision checks were {phase['status']}: {lib.text(phase.get('reason'))}")
    if mode == "enforce" and blocking:
        print("\n".join([f"FAIL  {line}" for line in blocking] + [f"WARN  {line}" for line in notices]))
        print("FAIL  revise only data/report-draft.json, rerun scripts/decision-signals.py --phase report, "
              "or record a reviewer override bound to the flagged item's input_sha256")
        return 1
    lines = [f"WARN  {line}" for line in blocking + notices]
    print("\n".join(lines + [f"PASS  report prose decision check ({mode}; {len(flagged)} flagged, "
                             f"{len(unavailable)} unavailable)"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
