#!/usr/bin/env python3
"""Validate performed Navigator discovery or an explicit truthful skip.

Installation entitlement enables discovery; it does not make every investigation
need a new tool lookup. Evidence and provenance remain strict when it is used.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations._credentials import config_path as install_config_path  # noqa: E402
from integrations.anomaly import adapter as anomaly  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import research_checks  # noqa: E402
import schema_check  # noqa: E402

METHODOLOGY_SCHEMA = ROOT / "schemas" / "methodology.schema.json"
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


def validate_required(methodology: dict[str, Any], status: str, case_dir: Path) -> list[str]:
    errors: list[str] = []

    nav = methodology.get("navigator")
    if not isinstance(nav, dict):
        return errors + ["methodology.json: navigator block is required when OSINT Navigator is green"]

    if nav.get("used") is not True:
        errors.append("methodology.json: navigator.used must be true")
    if nav.get("outcome") is None and nav.get("status") != status:
        errors.append(f"methodology.json: navigator.status must be {status!r}")

    queries = nav.get("queries", [])
    if not isinstance(queries, list) or not queries:
        errors.append("methodology.json: navigator.queries must be a non-empty list")
        queries = []

    selected_tools: set[str] = set()
    for index, query in enumerate(queries):
        prefix = f"methodology.json: navigator.queries[{index}]"
        if not isinstance(query, dict):
            errors.append(f"{prefix} must be an object")
            continue
        receipt = query.get("receipt_path")
        if receipt is not None:
            errors.extend(validate_receipt(query, case_dir, prefix))
        if not nonempty_string(query.get("direction")) and receipt is None:
            errors.append(f"{prefix}.direction is required")
        interface = query.get("interface", "api_fallback" if query.get("endpoint") else "cli")
        if interface == "cli":
            if not nonempty_string(query.get("command")):
                errors.append(f"{prefix}.command is required for CLI use")
            for key in ("retrieved_at", "response_path"):
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
        steps = direction.get("steps", [])
        if isinstance(steps, list):
            for step_index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                if step.get("tool_source") == "navigator":
                    if not nonempty_string(step.get("navigator_response_path")):
                        errors.append(f"{prefix}.steps[{step_index}].navigator_response_path is required when tool_source is navigator")
                    elif step["navigator_response_path"] not in {query.get("response_path") for query in queries if isinstance(query, dict)}:
                        errors.append(f"{prefix}.steps[{step_index}]: Navigator response must reference a recorded query")

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
    errors: list[str] = []
    if nav.get("used") is not False or nav.get("required") is not False:
        errors.append("methodology.json: skipped Navigator requires required=false and used=false")
    if not nonempty_string(nav.get("fallback_reason")):
        errors.append("methodology.json: navigator fallback_reason is required when Navigator is skipped")
    if nav.get("queries", []) != []:
        errors.append("methodology.json: skipped Navigator must not claim performed queries")
    plan = methodology.get("investigation_plan", [])
    if not isinstance(plan, list):
        return errors + ["methodology.json: investigation_plan must be a list"]
    for direction in plan:
        steps = direction.get("steps", []) if isinstance(direction, dict) else []
        if isinstance(steps, list) and any(isinstance(step, dict) and step.get("tool_source") == "navigator" for step in steps):
            errors.append("methodology.json: skipped Navigator must not claim Navigator-sourced steps")
    return errors


def validate_receipt(query: dict[str, Any], case_dir: Path, prefix: str) -> list[str]:
    path = query.get("receipt_path")
    if not isinstance(path, str) or not re.fullmatch(r"research/navigator/[0-9a-f-]{36}-receipt\.json", path):
        return [f"{prefix}.receipt_path must name a code-owned Navigator receipt"]
    target = case_dir / path
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(case_dir.resolve()):
            return [f"{prefix}.receipt_path must stay inside the case without symlinks"]
        receipt = load_json(target)
        encoded = receipt["response_json"]
        json.loads(encoded)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if receipt.get("outcome") != "used" or receipt.get("response_sha256") != digest:
            return [f"{prefix}: Navigator receipt has no successful response or its digest differs"]
        expected = {"command": json.dumps(receipt["argv"], ensure_ascii=False, separators=(",", ":")),
                    "retrieved_at": receipt["retrieved_at"], "response_path": path,
                    "response_sha256": digest}
        if any(query.get(key) != value for key, value in expected.items()):
            return [f"{prefix}: Navigator provenance differs from its code-owned receipt"]
    except (OSError, ValueError, KeyError, TypeError):
        return [f"{prefix}: unreadable or malformed Navigator receipt"]
    return []


def validate_navigator(methodology: dict[str, Any], required: bool, status: str, case_dir: Path) -> list[str]:
    nav = methodology.get("navigator")
    if not isinstance(nav, dict):
        return validate_required(methodology, status, case_dir) if required else validate_not_required(methodology)
    outcome = nav.get("outcome")
    if outcome is not None and outcome not in {"used", "not-needed", "unavailable", "policy-skipped"}:
        return ["methodology.json: navigator.outcome must be used, not-needed, unavailable or policy-skipped"]
    if outcome in {"not-needed", "unavailable", "policy-skipped"}:
        return validate_not_required(methodology)
    if nav.get("used") is True or outcome == "used" or required:
        return validate_required(methodology, status, case_dir)
    return validate_not_required(methodology)


# The one reference a method takes on a tier, as the error names it. r5 norway
# on 704dcc78: "reference does not match method" named no value, and the
# investigator guessed paths ("references/indicator-triage.md", ...) through 45
# corrections; the 12B base arm repeated the same error 15 times.
def expected_reference(method: str, model_tier: str) -> str:
    if method == "verified-indicator-export":
        return "verified-indicator-export.md"
    return f"{method}-compact.md" if model_tier == "12b" else f"{method}.md"


def validate_method_provenance(
    methodology: dict[str, Any],
    model_tier: str,
    active_sha: str,
    reviewed_shas: set[str],
    require_current: bool,
) -> list[str]:
    errors: list[str] = []
    skills = methodology.get("skills_invoked", [])
    skills = [skill for skill in skills if isinstance(skill, str)] if isinstance(skills, list) else []
    technical_invoked = "technical-investigation" in skills
    # The rule is unchanged; the messages say what to fix (base arm ia rewrote
    # the plan nine times against the old two, which read as a contradiction).
    near_miss = [skill for skill in skills if skill != "technical-investigation" and skill.replace("_", "-") == "technical-investigation"]
    provenance = methodology.get("method_provenance")

    if not technical_invoked and provenance is None:
        return []
    if near_miss:
        errors.append(f"methodology.json: skills_invoked has {near_miss[0]!r}; the skill is named 'technical-investigation'")
    if not technical_invoked:
        errors.append("methodology.json: method_provenance is only for the technical-investigation skill, which skills_invoked does not list: add it to skills_invoked, or remove method_provenance")
    # Base arm ia rewrote method_provenance as an object keyed by skill nine
    # times: the old message never said it is a list.
    if isinstance(provenance, dict):
        return errors + [
            "methodology.json: method_provenance must be a list of entries, one per technical method used, "
            "each an object with skill_id 'technical-investigation', upstream, active_sha, method and reference; not an object keyed by skill"
        ]
    if not isinstance(provenance, list) or not provenance:
        if technical_invoked:
            errors.append("methodology.json: technical-investigation requires non-empty method_provenance (a list of entries)")
        return errors

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
        tier_label = model_tier or "desktop/default"
        if allowed_references is None:
            errors.append(f"{prefix}.method is not recognized; use one of {', '.join(repr(name) for name in CTI_REFERENCES)}")
            continue
        expected = f"set reference to {expected_reference(method, model_tier)!r}, the bare file name for model_tier {tier_label}"
        if reference not in allowed_references:
            errors.append(f"{prefix}.reference does not match method {method!r}: {expected}")
            continue
        if method == "verified-indicator-export":
            continue
        compact = isinstance(reference, str) and reference.endswith("-compact.md")
        if model_tier == "12b" and not compact:
            errors.append(f"{prefix}.reference must be compact for model_tier 12b: {expected}")
        if model_tier in {"26b", "31b", "frontier", "api", ""} and compact:
            errors.append(f"{prefix}.reference must be expanded for model_tier {tier_label}: {expected}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", help="Path to {CASE_DIR}")
    parser.add_argument("--config", default=None, help="Spotlight config path (default: $SPOTLIGHT_CONFIG, else the checkout root's)")
    parser.add_argument("--cti-lock", type=Path, default=DEFAULT_CTI_LOCK)
    parser.add_argument("--cti-revisions", type=Path, default=DEFAULT_CTI_REVISIONS)
    parser.add_argument(
        "--allow-historical-cti",
        action="store_true",
        help="Allow archived cases to cite any catalogued CTI revision instead of the current revision",
    )
    args = parser.parse_args()

    case_dir = Path(args.case_dir).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve() if args.config else install_config_path()
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
    errors = validate_navigator(methodology, required, status, case_dir)
    # e2e r1 (31B): this gate passed navigator.status "not-needed", which the
    # methodology schema refused, and research-triage.py stopped after the
    # member approved. The gate applies the same schema, so the two cannot drift.
    errors.extend(f"methodology.json: {error}" for error in schema_check.load(METHODOLOGY_SCHEMA).errors(methodology))
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
    # D1: a research plan must be runnable as approved (budgets, topic ids,
    # lane types, consent, egress, sensitive, newsroom and journalist-input rules).
    errors.extend(f"methodology.json: {problem}" for problem in research_checks.d1_from_config(methodology, config))
    # Every Anomaly domain and source the plan names must be one the installed catalogue lists.
    errors.extend(f"methodology.json: {problem}" for problem in anomaly.plan_problems(methodology))

    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        return 1

    print("methodology contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
