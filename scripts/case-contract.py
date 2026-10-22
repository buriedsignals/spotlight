#!/usr/bin/env python3
"""Activate a new case, or bind an activated case's artifacts.

  python3 scripts/case-contract.py activate-new {CASE_DIR}
      Writes data/case-contract.json with a `born` event: the case starts at
      findings contract 1.1 with exact passages. Refused when the case has a
      contract already, or already has findings (an existing legacy case is
      migrated, never re-born).

  python3 scripts/case-contract.py bind {CASE_DIR}
      Appends a `binding` event with the current artifact hashes. The
      Investigation-gate approval does this itself (transitions.approve), so
      the hashes always cover exactly what was approved.

Both write under the case lock and publish atomically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from spotlight_orchestration.case_contract import ContractError, activate_new, bind  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("activate-new", "bind"))
    parser.add_argument("case_dir")
    args = parser.parse_args(argv)
    case = Path(args.case_dir)
    try:
        with transaction(case) as descriptor:
            doc = activate_new(case, descriptor) if args.command == "activate-new" else bind(case, descriptor)
    except (ContractError, OrchestrationError, OSError) as exc:
        print(f"case-contract: refused: {exc}", file=sys.stderr)
        return 2
    if doc is None:
        print(json.dumps({"ok": True, "activated": False}))
    else:
        print(json.dumps({"ok": True, "activated": True, "events": len(doc["activation_events"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
