#!/usr/bin/env python3
"""Contract checks for Arbiter's Navigator-hosted Spotlight routing."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations" / "arbiter" / "manifest.json"
INTEGRATION = ROOT / "integrations" / "arbiter" / "integration.md"
SKILL = ROOT / "skills" / "arbiter" / "SKILL.md"
SOURCE_ID = "global/arbiter/case-studies"


def main() -> int:
    errors: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = INTEGRATION.read_text(encoding="utf-8")
    skill_text = SKILL.read_text(encoding="utf-8")

    expected_manifest = {
        "type": "cli",
        "local_binary": "navigator",
        "requires_key": False,
        "env_vars": [],
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            errors.append(f"manifest.{field} must be {expected!r}")
    probes = manifest.get("probes") or []
    if not any(SOURCE_ID in probe.get("args", []) for probe in probes):
        errors.append("manifest must probe the deployed Navigator Arbiter source")
    if not any(
        probe.get("output_contains") == '"queryable": true'
        for probe in probes
    ):
        errors.append("manifest must require Arbiter to be queryable, not merely listed")
    if not any(
        probe.get("env", {}).get("DATANAV_CACHE_FALLBACK") == "off"
        for probe in probes
    ):
        errors.append("manifest must reject a stale cached Arbiter availability result")
    if "name: arbiter" not in skill_text or "Browse existing studies" not in skill_text:
        errors.append("skills/arbiter must provide the direct /arbiter browse/create entry point")
    if "temporarily unavailable" not in skill_text:
        errors.append("skills/arbiter must explain the temporary access pause")

    forbidden = ("ARBITER_API_KEY", "Authorization: Bearer", "curl ")
    for value in forbidden:
        if value in text:
            errors.append(f"integration must not call Arbiter directly: found {value!r}")

    required = (
        f"navigator data show {SOURCE_ID}",
        f"navigator query {SOURCE_ID}",
        '"operation":"topics"',
        '"operation":"posts"',
        '"operation":"entities"',
        '"operation":"themes"',
        '"operation":"report"',
        '"operation":"post"',
        '"operation":"agent_questions"',
        '"operation":"agent"',
        '"operation":"usage"',
        '"operation":"create"',
        '"operation":"search_plan"',
        '"operation":"finalize"',
        '"operation":"status"',
        '"operation":"progress"',
        "arbiter-report-",
        "confirmed",
    )
    for value in required:
        if value not in text:
            errors.append(f"integration is missing Navigator contract text {value!r}")

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("arbiter navigator routing contract: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
