#!/usr/bin/env python3
"""Validate the v2.1 OSINT Navigator methodology contract.

The gate is intentionally narrow: when Phase 0 preflight marks Navigator green
and required for Phase 2, methodology.json must prove Navigator was used before
the plan is shown for user approval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_PLANNING_SKILLS = {"integrations", "osint", "investigate", "epistemic-grounding"}


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def navigator_status(config: dict[str, Any]) -> tuple[bool, str]:
    integrations = config.get("integrations", {})
    nav = integrations.get("osint_navigator")
    if isinstance(nav, bool):
        return nav, "green" if nav else "red"
    if isinstance(nav, dict):
        return bool(nav.get("required_in_phase_2")), str(nav.get("status", "unknown"))
    return False, "missing"


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_required(methodology: dict[str, Any], status: str) -> list[str]:
    errors: list[str] = []

    skills = methodology.get("skills_invoked", [])
    if not isinstance(skills, list):
        errors.append("methodology.json: skills_invoked must be a list")
        skills = []
    missing_skills = REQUIRED_PLANNING_SKILLS - set(skills)
    if missing_skills:
        errors.append(f"methodology.json: missing required planning skills {sorted(missing_skills)}")

    nav = methodology.get("navigator")
    if not isinstance(nav, dict):
        return errors + ["methodology.json: navigator block is required when OSINT Navigator is green"]

    if nav.get("required") is not True:
        errors.append("methodology.json: navigator.required must be true")
    if nav.get("used") is not True:
        errors.append("methodology.json: navigator.used must be true")
    if nav.get("status") != status:
        errors.append(f"methodology.json: navigator.status must be {status!r}")

    queries = nav.get("queries", [])
    if not isinstance(queries, list) or not queries:
        errors.append("methodology.json: navigator.queries must be a non-empty list")
        queries = []

    query_directions: set[str] = set()
    selected_tools: set[str] = set()
    for index, query in enumerate(queries):
        prefix = f"methodology.json: navigator.queries[{index}]"
        if not isinstance(query, dict):
            errors.append(f"{prefix} must be an object")
            continue
        direction = query.get("direction")
        if nonempty_string(direction):
            query_directions.add(direction)
        else:
            errors.append(f"{prefix}.direction is required")
        if query.get("endpoint") != "/api/tools/search":
            errors.append(f"{prefix}.endpoint must be /api/tools/search")
        for key in ("request_path", "response_path"):
            if not nonempty_string(query.get(key)):
                errors.append(f"{prefix}.{key} is required")
        tools = query.get("selected_tools", [])
        if not isinstance(tools, list):
            errors.append(f"{prefix}.selected_tools must be a list")
        else:
            selected_tools.update(tool for tool in tools if nonempty_string(tool))

    plan = methodology.get("investigation_plan", [])
    if not isinstance(plan, list) or not plan:
        errors.append("methodology.json: investigation_plan must be a non-empty list")
        plan = []

    for index, direction in enumerate(plan):
        prefix = f"methodology.json: investigation_plan[{index}]"
        if not isinstance(direction, dict):
            errors.append(f"{prefix} must be an object")
            continue
        direction_name = direction.get("direction", "")
        not_applicable = nonempty_string(direction.get("navigator_not_applicable_reason"))
        steps = direction.get("steps", [])
        step_has_nav = False
        if isinstance(steps, list):
            for step_index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                if step.get("tool_source") == "navigator":
                    if nonempty_string(step.get("navigator_response_path")):
                        step_has_nav = True
                    else:
                        errors.append(f"{prefix}.steps[{step_index}].navigator_response_path is required when tool_source is navigator")
        if not not_applicable and direction_name not in query_directions and not step_has_nav:
            errors.append(f"{prefix}: cite a Navigator response or set navigator_not_applicable_reason")

    tools_required = methodology.get("tools_required", [])
    if not isinstance(tools_required, list):
        errors.append("methodology.json: tools_required must be a list")
        tools_required = []
    missing_tools = {
        tool
        for tool in selected_tools
        if not any(tool == required or tool in required for required in tools_required)
    }
    if missing_tools:
        errors.append(f"methodology.json: tools_required missing Navigator-selected tools {sorted(missing_tools)}")

    return errors


def validate_not_required(methodology: dict[str, Any]) -> list[str]:
    nav = methodology.get("navigator")
    if nav is None:
        return []
    if not isinstance(nav, dict):
        return ["methodology.json: navigator must be an object when present"]
    if nav.get("required") is True and nav.get("used") is not True:
        return ["methodology.json: navigator.required=true requires navigator.used=true"]
    if nav.get("required") is False and nav.get("used") is False and not nonempty_string(nav.get("fallback_reason")):
        return ["methodology.json: navigator fallback_reason is required when Navigator is skipped"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to cases/{project}")
    parser.add_argument("--config", default=".spotlight-config.json", help="Spotlight config path")
    args = parser.parse_args()

    case_dir = Path(args.case_dir).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    methodology_path = case_dir / "data" / "methodology.json"

    errors: list[str] = []
    if not case_dir.is_dir():
        errors.append(f"case directory not found: {case_dir}")
    if not config_path.exists():
        errors.append(f"config not found: {config_path}")
    if not methodology_path.exists():
        errors.append(f"methodology not found: {methodology_path}")
    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1

    try:
        config = load_json(config_path)
        methodology = load_json(methodology_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    required, status = navigator_status(config)
    errors = validate_required(methodology, status) if required else validate_not_required(methodology)

    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1

    print("methodology navigator contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
