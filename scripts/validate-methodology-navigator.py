#!/usr/bin/env python3
"""Validate the Navigator CLI-first methodology contract.

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
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CTI_LOCK = ROOT / "upstreams" / "cti-expert" / "source.lock.json"
DEFAULT_CTI_REVISIONS = ROOT / "upstreams" / "cti-expert" / "reviewed-revisions.json"
CTI_REFERENCES = {
    "indicator-triage": {"indicator-triage-compact.md", "indicator-triage.md"},
    "infrastructure-history": {"infrastructure-history-compact.md", "infrastructure-history.md"},
    "document-message-forensics": {
        "document-message-forensics-compact.md",
        "document-message-forensics.md",
    },
    "github-public": {"github-public-compact.md", "github-public.md"},
    "verified-indicator-export": {"verified-indicator-export.md"},
}


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
    missing_skills = REQUIRED_PLANNING_SKILLS - {skill for skill in skills if isinstance(skill, str)}
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
        interface = query.get("interface", "api_fallback" if query.get("endpoint") else "cli")
        if interface == "cli":
            if not nonempty_string(query.get("command")):
                errors.append(f"{prefix}.command is required for CLI use")
            for key in ("catalog_id", "retrieved_at", "response_path"):
                if not nonempty_string(query.get(key)):
                    errors.append(f"{prefix}.{key} is required for CLI use")
        elif interface == "api_fallback":
            if query.get("endpoint") not in {"/api/tools/search", "/api/query"}:
                errors.append(f"{prefix}.endpoint must identify the documented API fallback")
            for key in ("request_path", "response_path"):
                if not nonempty_string(query.get(key)):
                    errors.append(f"{prefix}.{key} is required for API fallback")
        else:
            errors.append(f"{prefix}.interface must be cli or api_fallback")
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

    data_sources = nav.get("data_sources", [])
    if data_sources is not None and not isinstance(data_sources, list):
        errors.append("methodology.json: navigator.data_sources must be a list")
        data_sources = []
    data_by_direction = {
        item.get("direction"): item
        for item in data_sources
        if isinstance(item, dict) and nonempty_string(item.get("direction"))
    }
    for index, direction in enumerate(plan):
        if not isinstance(direction, dict):
            continue
        name = direction.get("direction")
        if name in data_by_direction:
            item = data_by_direction[name]
            for key in ("considered", "selected", "reason"):
                if key not in item or (isinstance(item[key], str) and not item[key].strip()):
                    errors.append(f"methodology.json: navigator.data_sources[{name!r}].{key} is required")
            results = item.get("results", [])
            if results is not None and not isinstance(results, list):
                errors.append(f"methodology.json: navigator.data_sources[{name!r}].results must be a list")
                results = []
            for result_index, result in enumerate(results):
                result_prefix = f"methodology.json: navigator.data_sources[{name!r}].results[{result_index}]"
                if not isinstance(result, dict):
                    errors.append(result_prefix + " must be an object")
                    continue
                for key in ("source_id", "interface", "parameters", "executed_at", "source_urls", "warnings", "output_path", "output_sha256"):
                    if key not in result or (isinstance(result[key], str) and not result[key].strip()):
                        errors.append(f"{result_prefix}.{key} is required")
                if result.get("interface") == "cli" and not nonempty_string(result.get("command")):
                    errors.append(f"{result_prefix}.command is required for CLI output")
                if result.get("interface") == "api_fallback" and result.get("endpoint") != "/api/query":
                    errors.append(f"{result_prefix}.endpoint must identify the API fallback")
                if not isinstance(result.get("parameters"), dict):
                    errors.append(f"{result_prefix}.parameters must be an object")
                if not isinstance(result.get("source_urls"), list) or not all(nonempty_string(url) for url in result.get("source_urls", [])):
                    errors.append(f"{result_prefix}.source_urls must be a list of source URLs")
                if not isinstance(result.get("warnings"), list):
                    errors.append(f"{result_prefix}.warnings must be a list")
                digest = result.get("output_sha256")
                if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    errors.append(f"{result_prefix}.output_sha256 must be a lowercase SHA-256 digest")


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


def validate_method_provenance(
    methodology: dict[str, Any],
    model_tier: str,
    active_sha: str,
    reviewed_shas: set[str],
    require_current: bool,
) -> list[str]:
    errors: list[str] = []
    skills = methodology.get("skills_invoked", [])
    technical_invoked = isinstance(skills, list) and "technical-investigation" in skills
    provenance = methodology.get("method_provenance")

    if not technical_invoked and provenance is None:
        return []
    if not technical_invoked:
        errors.append("methodology.json: method_provenance requires technical-investigation in skills_invoked")
    if not isinstance(provenance, list) or not provenance:
        return errors + ["methodology.json: technical-investigation requires non-empty method_provenance"]

    for index, entry in enumerate(provenance):
        prefix = f"methodology.json: method_provenance[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if entry.get("skill_id") != "technical-investigation":
            errors.append(f"{prefix}.skill_id must be 'technical-investigation'")
        if entry.get("upstream") != "7onez/cti-expert":
            errors.append(f"{prefix}.upstream must be '7onez/cti-expert'")
        entry_sha = entry.get("active_sha")
        if entry_sha not in reviewed_shas:
            errors.append(f"{prefix}.active_sha is not in the reviewed CTI revision catalog")
        elif require_current and entry_sha != active_sha:
            errors.append(f"{prefix}.active_sha must match the current reviewed CTI revision")
        method = entry.get("method")
        reference = entry.get("reference")
        allowed_references = CTI_REFERENCES.get(method) if isinstance(method, str) else None
        if allowed_references is None:
            errors.append(f"{prefix}.method is not recognized")
            continue
        if reference not in allowed_references:
            errors.append(f"{prefix}.reference does not match method {method!r}")
            continue
        if method == "verified-indicator-export":
            continue
        compact = isinstance(reference, str) and reference.endswith("-compact.md")
        if model_tier == "12b" and not compact:
            errors.append(f"{prefix}.reference must be compact for model_tier 12b")
        if model_tier in {"26b", "31b", "frontier", "api", ""} and compact:
            tier_label = model_tier or "desktop/default"
            errors.append(f"{prefix}.reference must be expanded for model_tier {tier_label}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to {CASE_DIR}")
    parser.add_argument("--config", default=".spotlight-config.json", help="Spotlight config path")
    parser.add_argument("--cti-lock", type=Path, default=DEFAULT_CTI_LOCK)
    parser.add_argument("--cti-revisions", type=Path, default=DEFAULT_CTI_REVISIONS)
    parser.add_argument(
        "--allow-historical-cti",
        action="store_true",
        help="Allow archived cases to cite any catalogued CTI revision instead of the current revision",
    )
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
        cti_lock = load_json(args.cti_lock.expanduser().resolve())
        cti_revisions = load_json(args.cti_revisions.expanduser().resolve())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    required, status = navigator_status(config)
    errors = validate_required(methodology, status) if required else validate_not_required(methodology)
    active_sha = str(cti_lock.get("active_sha", ""))
    reviewed_shas = {
        item.get("sha")
        for item in cti_revisions.get("revisions", [])
        if isinstance(item, dict) and isinstance(item.get("sha"), str)
    }
    if cti_revisions.get("active_sha") != active_sha or active_sha not in reviewed_shas:
        errors.append("CTI source lock and reviewed-revisions catalog disagree")
    errors.extend(
        validate_method_provenance(
            methodology,
            str(config.get("model_tier", "")),
            active_sha,
            reviewed_shas,
            not args.allow_historical_cti,
        )
    )

    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1

    print("methodology contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
