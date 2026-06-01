#!/usr/bin/env python3
"""Validate Spotlight's skills-manifest maintenance contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALID_ROLES = {"parent", "core-child", "conditional-child", "reference"}
REQUIRED_PHASES = {
    "phase-0-preflight",
    "phase-2-methodology",
    "phase-3-execution",
    "phase-3-fact-check",
    "phase-5-report",
    "ingest",
}


def skill_dirs() -> set[str]:
    return {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}


def agents_registry_skills() -> set[str]:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    return set(re.findall(r"\| `([^`]+)` \| `skills/[^`]+/SKILL\.md` \|", text))


def main() -> int:
    manifest = json.loads((ROOT / "skills-manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []

    if manifest.get("schema_version") != "1.0":
        errors.append("skills-manifest.json: schema_version must be 1.0")

    skills = manifest.get("skills", [])
    if not isinstance(skills, list) or not skills:
        errors.append("skills-manifest.json: skills must be a non-empty list")
        skills = []

    ids = [skill.get("id") for skill in skills if isinstance(skill, dict)]
    manifest_ids = set(ids)
    if len(ids) != len(manifest_ids):
        errors.append("skills-manifest.json: duplicate skill id")

    dirs = skill_dirs()
    registry = agents_registry_skills()
    if manifest_ids != dirs:
        errors.append(f"manifest skills differ from skills/*/SKILL.md: manifest={sorted(manifest_ids)} dirs={sorted(dirs)}")
    if manifest_ids != registry:
        errors.append(f"manifest skills differ from AGENTS.md registry: manifest={sorted(manifest_ids)} registry={sorted(registry)}")

    for skill in skills:
        if not isinstance(skill, dict):
            errors.append("skills-manifest.json: each skill must be an object")
            continue
        sid = skill.get("id", "<missing>")
        if skill.get("role") not in VALID_ROLES:
            errors.append(f"{sid}: invalid role {skill.get('role')!r}")
        for key in ("phases",):
            if not isinstance(skill.get(key), list) or not skill[key]:
                errors.append(f"{sid}: {key} must be a non-empty list")
        for key in ("may_make_external_calls", "disabled_in_sensitive_mode"):
            if not isinstance(skill.get(key), bool):
                errors.append(f"{sid}: {key} must be boolean")

    phase_requirements = manifest.get("phase_requirements", {})
    if not isinstance(phase_requirements, dict):
        errors.append("skills-manifest.json: phase_requirements must be an object")
        phase_requirements = {}
    missing_phases = REQUIRED_PHASES - set(phase_requirements)
    if missing_phases:
        errors.append(f"skills-manifest.json: missing phase requirements {sorted(missing_phases)}")
    for phase, contract in phase_requirements.items():
        for key in ("required", "conditional"):
            values = contract.get(key, [])
            if not isinstance(values, list):
                errors.append(f"{phase}: {key} must be a list")
                continue
            unknown = set(values) - manifest_ids
            if unknown:
                errors.append(f"{phase}: {key} references unknown skills {sorted(unknown)}")

    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1

    print("skills manifest contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
