#!/usr/bin/env python3
from _common import bool_score, emit, has_nonempty, list_has_nonempty, require_args


def main() -> int:
    _expected, actual = require_args("report-drafting")
    checks = {
        "claims_map_to_evidence": bool(actual.get("claims_map_to_evidence")) if isinstance(actual, dict) else False,
        "evidence_ledger_present": bool(actual.get("evidence_ledger")) if isinstance(actual, dict) else False,
        "replication_block_present": bool(actual.get("replication_blocks")) if isinstance(actual, dict) else False,
        "verification_state_explicit": has_nonempty(actual.get("verification_state")) if isinstance(actual, dict) else False,
        "caveats_preserved": list_has_nonempty(actual.get("caveats")) if isinstance(actual, dict) else False,
    }
    protected = []
    if not checks["claims_map_to_evidence"]:
        protected.append("unsupported_public_claim")
    if not checks["evidence_ledger_present"]:
        protected.append("missing_source_url")
    return emit(bool_score(checks), checks, protected)


if __name__ == "__main__":
    raise SystemExit(main())
