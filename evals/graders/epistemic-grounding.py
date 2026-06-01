#!/usr/bin/env python3
from _common import bool_score, emit, has_nonempty, list_has_nonempty, require_args


def main() -> int:
    _expected, actual = require_args("epistemic-grounding")
    grounding = actual.get("grounding", {}) if isinstance(actual, dict) else {}
    checks = {
        "evidence_refs_present": list_has_nonempty(actual.get("evidence_refs")) if isinstance(actual, dict) else False,
        "support_type_present": has_nonempty(grounding.get("support_type")),
        "confidence_cap_present": grounding.get("confidence_cap") in {"high", "medium", "low"},
        "contradictions_addressed": has_nonempty(grounding.get("grounding_rationale") or grounding.get("assessment")),
        "human_review_present": actual.get("human_review") in {"unreviewed", "approved", "rejected"} if isinstance(actual, dict) else False,
    }
    protected = []
    if actual.get("confidence") == "high" and grounding.get("confidence_cap") in {"medium", "low"}:
        protected.append("false_high_confidence")
    return emit(bool_score(checks), checks, protected)


if __name__ == "__main__":
    raise SystemExit(main())
