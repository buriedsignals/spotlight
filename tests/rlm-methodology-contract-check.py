#!/usr/bin/env python3
"""Check the RLM methodology opt-in and evidence-boundary contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    schema = json.loads((ROOT / "schemas" / "methodology.schema.json").read_text(encoding="utf-8"))
    rlm = schema.get("properties", {}).get("rlm")
    if not isinstance(rlm, dict):
        fail("methodology schema missing rlm block")

    required = set(rlm.get("required", []))
    for key in {"available", "proposed", "approved", "mode", "evidence_boundary"}:
        if key not in required:
            fail(f"methodology schema rlm block missing required key: {key}")

    boundary = rlm.get("properties", {}).get("evidence_boundary", {}).get("const")
    if boundary != "lead-only; never verified or publishable":
        fail("methodology schema does not enforce RLM evidence boundary")

    # Agent-facing instructions: the script path the methodology phase runs,
    # and the execution phase's evidence boundary.
    methodology = (ROOT / "skills" / "phase-methodology" / "SKILL.md").read_text(encoding="utf-8")
    execution = (ROOT / "skills" / "phase-execution" / "SKILL.md").read_text(encoding="utf-8")
    for phrase, body in [
        ("integrations/rlm/run_rlm.py", methodology),
        ("Treat every RLM artifact as `needs_verification`", execution),
    ]:
        if phrase not in body:
            fail(f"spotlight phase skill missing RLM methodology instruction: {phrase}")

    print("rlm methodology contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
