#!/usr/bin/env python3
"""Check the RLM methodology opt-in and benchmark audit contract."""

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

    methodology = (ROOT / "skills" / "phase-methodology" / "SKILL.md").read_text(encoding="utf-8")
    execution = (ROOT / "skills" / "phase-execution" / "SKILL.md").read_text(encoding="utf-8")
    for phrase, body in [
        ("methodology-phase option", methodology),
        ("Use RLM for\n   > this methodology?", methodology),
        ("integrations/rlm/run_rlm.py", methodology),
        ("Treat every RLM artifact as `needs_verification`", execution),
    ]:
        if phrase not in body:
            fail(f"spotlight phase skill missing RLM methodology instruction: {phrase}")

    configure = (ROOT / "install" / "configure.html").read_text(encoding="utf-8")
    if "docs/rlm-benchmark-audit.md" not in configure:
        fail("configurator page does not reference RLM benchmark audit")
    if "removed decoy hits from 4 to 0" not in configure:
        fail("configurator page missing concrete RLM benchmark improvement")

    audit = (ROOT / "docs" / "rlm-benchmark-audit.md").read_text(encoding="utf-8")
    for phrase in [
        "RLM remains off by default",
        "Without RLM | 0.75",
        "Hybrid prefiltered Gemma RLM | 1.0",
        "needs_verification",
    ]:
        if phrase not in audit:
            fail(f"RLM audit missing expected benchmark/boundary phrase: {phrase}")

    print("rlm methodology contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
