#!/usr/bin/env python3
"""Activate a new case.

  python3 scripts/case-contract.py activate-new {CASE_DIR}

Writes data/case-contract.json with a `born` event (and an empty
data/source-expressions.json first): the case starts at findings contract 1.1
with exact passages. Refused when the case already has a contract, findings,
a fact-check, an investigation log or passages: an existing legacy case is
migrated, never re-born.

There is deliberately no command to bind artifacts. Only the
Investigation-gate approval binds them (transitions.approve), so a binding
always means a person approved exactly those artifacts.

Writes under the case lock and publishes atomically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from spotlight_orchestration.case_contract import activate_new  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("activate-new",))
    parser.add_argument("case_dir")
    args = parser.parse_args(argv)
    case = Path(args.case_dir)
    try:
        with transaction(case) as descriptor:
            doc = activate_new(case, descriptor)
    except (OrchestrationError, OSError) as exc:
        print(f"case-contract: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "activated": True, "events": len(doc["activation_events"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
