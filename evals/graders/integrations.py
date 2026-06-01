#!/usr/bin/env python3
from _common import bool_score, emit, has_nonempty, require_args


def main() -> int:
    expected, actual = require_args("integrations")
    checks = {
        "route_matches_expected": actual.get("integration") == expected.get("integration") if isinstance(actual, dict) and isinstance(expected, dict) else False,
        "fallback_explicit": has_nonempty(actual.get("fallback")) if isinstance(actual, dict) else False,
        "preflight_status_used": actual.get("preflight_status") in {"green", "yellow", "red"} if isinstance(actual, dict) else False,
        "sensitive_mode_respected": actual.get("sensitive_mode_safe") is True if isinstance(actual, dict) else False,
        "minimal_payload_declared": actual.get("minimal_payload") is True if isinstance(actual, dict) else False,
    }
    protected = []
    if actual.get("sensitive_mode_safe") is False:
        protected.append("sensitive_mode_leakage")
    return emit(bool_score(checks), checks, protected)


if __name__ == "__main__":
    raise SystemExit(main())
