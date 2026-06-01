#!/usr/bin/env python3
from _common import bool_score, emit, has_nonempty, list_has_nonempty, require_args


def main() -> int:
    _expected, actual = require_args("review")
    checks = {
        "unsupported_claims_flagged": bool(actual.get("unsupported_claims")) if isinstance(actual, dict) else False,
        "weak_evidence_separated": bool(actual.get("weak_evidence")) if isinstance(actual, dict) else False,
        "source_refs_present": list_has_nonempty(actual.get("source_refs")) if isinstance(actual, dict) else False,
        "next_actions_present": list_has_nonempty(actual.get("next_actions")) if isinstance(actual, dict) else False,
        "independence_note_present": has_nonempty(actual.get("fact_checker_independence")) if isinstance(actual, dict) else False,
    }
    protected = []
    if not checks["source_refs_present"]:
        protected.append("missing_source_url")
    if not checks["independence_note_present"]:
        protected.append("fact_checker_independence_break")
    return emit(bool_score(checks), checks, protected)


if __name__ == "__main__":
    raise SystemExit(main())
