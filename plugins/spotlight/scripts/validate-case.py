#!/usr/bin/env python3
"""Validate a Spotlight case directory against the published schemas.

Run after the investigator and fact-checker write their JSON to catch
data bugs before they enter the case archive — empty required strings,
missing top-level keys, wrong-shape documents, dangling fact-check
references, and confidence-vs-cap mismatches.

Intentionally avoids third-party JSON Schema dependencies. Performs
both schema-shape checks (re-implementing the subset that matters at
write-time) and cross-file checks that JSON Schema can't express.

Usage:
    python3 scripts/validate-case.py {CASE_DIR}
    python3 scripts/validate-case.py {CASE_DIR} --strict
    python3 scripts/validate-case.py {CASE_DIR} --include-rlm

Exit code:
    0 — valid (warnings may still print to stderr)
    1 — invalid (at least one error in case JSON contracts)
    2 — case directory not found or unreadable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_anchors import (
    case_evidence_path,
    normalized,
    rlm_lead_failure,
    selected_source_text,
    sha256_file,
)
from source_expression_contract import (
    POSITIVE_VERDICTS,
    canonical_fingerprint,
    passage_core,
)


VERDICTS = {"verified", "partially_verified", "unverified", "mischaracterized", "disputed", "false"}
CONFIDENCES = {"high", "medium", "low", "disputed"}
FACT_CHECK_CONFIDENCES = {"high", "medium", "low"}
CAPS = {"high", "medium", "low"}
SUPPORT_TYPES = {"direct", "indirect", "inferred", "contradicted", "insufficient"}
SOURCE_ROLES = {"primary", "secondary", "contextual"}
EVIDENCE_SOURCE_TYPES = {"primary", "secondary"}
ACCESS_METHODS = {"full_text", "open_access", "archive_copy", "abstract_only", "inaccessible"}
ACQUISITION_METHODS = {"firecrawl", "dev_browser", "browse", "browser_harness", "browser_use", "manual", "api", "other"}
MISSING_SOURCE_CONFIDENCE_EFFECTS = {"none", "cap_medium", "cap_low", "human_verification_required"}
HUMAN_REVIEW = {"unreviewed", "approved", "rejected"}
RLM_MODES = {"lite", "local_gemma4_e4b"}
RLM_PROVIDERS = {"deterministic", "ollama"}
RLM_STATUSES = {"needs_verification"}
RLM_KINDS = {"entity", "timeline_event", "contradiction", "lead", "discarded"}
TECHNICAL_INDICATOR_TYPES = {
    "ipv4",
    "ipv6",
    "domain",
    "url",
    "md5",
    "sha1",
    "sha256",
    "bitcoin",
    "ethereum",
}


def load_json(path: Path) -> tuple[Any, list[str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), []
    except FileNotFoundError:
        return None, [f"{path.name}: file not found"]
    except json.JSONDecodeError as exc:
        return None, [f"{path.name}: malformed JSON at line {exc.lineno}, col {exc.colno}: {exc.msg}"]
    except OSError as exc:
        return None, [f"{path.name}: read error: {exc}"]


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def parse_datetime(value: Any) -> Optional[datetime]:
    if not nonempty_string(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def validate_case_contract(data: Any) -> list[str]:
    prefix = "case-contract.json"
    if not isinstance(data, dict):
        return [f"{prefix}: top-level must be an object"]
    errors: list[str] = []
    allowed_top = {"schema_version", "project", "current_contract_version", "activation_events"}
    if set(data) - allowed_top:
        errors.append(f"{prefix}: unknown fields {sorted(set(data) - allowed_top)}")
    if data.get("schema_version") != "1.0":
        errors.append(f"{prefix}: schema_version must be 1.0")
    if not nonempty_string(data.get("project")):
        errors.append(f"{prefix}: project must be a non-empty string")
    if data.get("current_contract_version") != "1.1":
        errors.append(f"{prefix}: current_contract_version must be 1.1")
    events = data.get("activation_events")
    if not isinstance(events, list) or not events:
        return errors + [f"{prefix}: activation_events must be a non-empty list"]
    event_ids: set[str] = set()
    for index, event in enumerate(events):
        item = f"{prefix}.activation_events[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{item}: must be an object")
            continue
        allowed_event = {
            "event_id", "previous_contract_version", "activated_contract_version",
            "activated_at", "tool_version", "prior_input_hashes",
            "activated_artifact_hashes",
        }
        if set(event) - allowed_event:
            errors.append(f"{item}: unknown fields {sorted(set(event) - allowed_event)}")
        event_id = event.get("event_id")
        if not nonempty_string(event_id):
            errors.append(f"{item}.event_id: must be a non-empty string")
        elif event_id in event_ids:
            errors.append(f"{item}.event_id: duplicate {event_id!r}")
        else:
            event_ids.add(event_id)
        if event.get("previous_contract_version") != "1.0" or event.get("activated_contract_version") != "1.1":
            errors.append(f"{item}: must record the 1.0 to 1.1 transition")
        if parse_datetime(event.get("activated_at")) is None:
            errors.append(f"{item}.activated_at: must be a timezone-aware ISO timestamp")
        if not nonempty_string(event.get("tool_version")):
            errors.append(f"{item}.tool_version: must be a non-empty string")
        for key, require_expressions in (("prior_input_hashes", False), ("activated_artifact_hashes", True)):
            hashes = event.get(key)
            required = {"findings_sha256", "fact_check_sha256", "evidence_bundle_sha256"}
            if require_expressions:
                required.add("source_expressions_sha256")
            if not isinstance(hashes, dict):
                errors.append(f"{item}.{key}: must be an object")
                continue
            allowed_hashes = required | ({"source_expressions_sha256"} if not require_expressions else set())
            if set(hashes) - allowed_hashes:
                errors.append(f"{item}.{key}: unknown fields {sorted(set(hashes) - allowed_hashes)}")
            for field in required:
                value = hashes.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                    errors.append(f"{item}.{key}.{field}: must be lowercase SHA-256")
    return errors


def _graph_cycles(edges: dict[str, str], label: str) -> list[str]:
    errors: list[str] = []
    for start in edges:
        seen: set[str] = set()
        current = start
        while current in edges:
            if current in seen:
                errors.append(f"source-expressions.json: {label} cycle includes expression {current}")
                break
            seen.add(current)
            current = edges[current]
    return sorted(set(errors))


def expression_refs(evidence_rows: list[Any]) -> list[dict[str, Any]]:
    return [
        ref
        for evidence in evidence_rows
        if isinstance(evidence, dict)
        for ref in (
            evidence.get("source_expression_refs", [])
            if isinstance(evidence.get("source_expression_refs", []), list)
            else []
        )
        if isinstance(ref, dict)
    ]


def validate_source_expressions(
    case_dir: Path,
    data: Any,
    findings_data: dict[str, Any],
    factcheck_data: dict[str, Any] | None,
    evidence_data: dict[str, Any] | None,
    enforce_positive_verdicts: bool = True,
) -> list[str]:
    """Validate the full activated expression chain; never infer semantic truth."""
    if not isinstance(data, dict):
        return ["source-expressions.json: top-level must be an object"]
    errors: list[str] = []
    if data.get("schema_version") != "1.0":
        errors.append("source-expressions.json: schema_version must be 1.0")
    if data.get("project") != findings_data.get("project"):
        errors.append("source-expressions.json: project must match findings.json")
    raw_findings = findings_data.get("findings", [])
    findings = {
        item.get("id"): item for item in raw_findings
        if isinstance(item, dict) and nonempty_string(item.get("id"))
    }
    raw_bundles = (evidence_data or {}).get("items", [])
    if not isinstance(raw_bundles, list):
        raw_bundles = []
    bundles = {
        item.get("id"): item for item in raw_bundles
        if isinstance(item, dict) and nonempty_string(item.get("id"))
    }
    expressions_raw = data.get("expressions")
    if not isinstance(expressions_raw, list):
        return errors + ["source-expressions.json: expressions must be a list"]
    expressions: dict[str, dict[str, Any]] = {}
    derivative_edges: dict[str, str] = {}
    successor_edges: dict[str, str] = {}
    active: set[str] = set()
    blocked_support: set[str] = set()
    support_links: set[tuple[str, str]] = set()
    finding_ids = [
        item.get("id") for item in raw_findings
        if isinstance(item, dict) and nonempty_string(item.get("id"))
    ]
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("findings.json: activated cases require unique finding IDs")
    for index, expression in enumerate(expressions_raw):
        expression_id = expression.get("id") if isinstance(expression, dict) else "?"
        prefix = f"source-expressions.json expression {expression_id!r}"
        if not isinstance(expression, dict):
            errors.append(f"source-expressions.json[{index}]: must be an object")
            continue
        if not nonempty_string(expression_id):
            errors.append(f"{prefix}: id must be a non-empty string")
            continue
        if expression_id in expressions:
            errors.append(f"{prefix}: duplicate expression ID")
            continue
        expressions[expression_id] = expression
        required_fields = {
            "id", "text", "anchor_ref", "anchor_sha256",
            "original_evidence_bundle_id", "original_artifact_sha256",
            "expression_fingerprint", "finding_links", "lifecycle_events",
            "created_by", "cycle",
        }
        missing = sorted(required_fields - set(expression))
        if missing:
            errors.append(f"{prefix}: missing required fields {missing}")
        if not nonempty_string(expression.get("text")):
            errors.append(f"{prefix}: text must be a non-empty string")
        if expression.get("created_by") not in {"investigator", "fact-checker", "human"}:
            errors.append(f"{prefix}: created_by is invalid")
        if not isinstance(expression.get("cycle"), int) or expression.get("cycle") < 1:
            errors.append(f"{prefix}: cycle must be an integer >= 1")
        core = passage_core(expression)
        if canonical_fingerprint(core) != expression.get("expression_fingerprint"):
            errors.append(f"{prefix}: expression_fingerprint mismatch")
        anchor_ref = expression.get("anchor_ref")
        anchor_path = case_evidence_path(case_dir, anchor_ref.get("path") if isinstance(anchor_ref, dict) else None)
        if anchor_path is None:
            errors.append(f"{prefix}: anchor_ref does not resolve to a case-local file")
        else:
            selected, selection_error = selected_source_text(anchor_path, anchor_ref)
            if selection_error:
                errors.append(f"{prefix}: {selection_error}")
            elif selected != expression.get("text"):
                errors.append(f"{prefix}: text does not exactly match the selected anchor")
            if sha256_file(anchor_path) != expression.get("anchor_sha256"):
                errors.append(f"{prefix}: anchor_sha256 does not match anchor artifact")
            lead_failure = rlm_lead_failure(anchor_path)
            if lead_failure:
                errors.append(f"{prefix}: {lead_failure}")
                blocked_support.add(expression_id)
        bundle = bundles.get(expression.get("original_evidence_bundle_id"))
        if bundle is None:
            errors.append(f"{prefix}: original_evidence_bundle_id does not resolve")
        else:
            if normalized(bundle.get("sha256")).lower() != expression.get("original_artifact_sha256"):
                errors.append(f"{prefix}: original_artifact_sha256 does not match evidence bundle")
            original_paths = [bundle.get(key) for key in ("raw_path", "downloaded_document_path", "screenshot_path", "path") if bundle.get(key)]
            if not original_paths or not any(
                (path := case_evidence_path(case_dir, value)) is not None
                and sha256_file(path) == expression.get("original_artifact_sha256")
                for value in original_paths
            ):
                errors.append(f"{prefix}: original evidence artifact path/hash is not intact")
            derivatives = bundle.get("text_derivatives", [])
            if bundle.get("human_verification_required") is True:
                blocked_support.add(expression_id)
            gate = bundle.get("missing_source_gate")
            if isinstance(gate, dict) and gate.get("fallback_required") is not False:
                blocked_support.add(expression_id)
            if anchor_path is not None and original_paths:
                known_paths = {
                    path for value in original_paths
                    if (path := case_evidence_path(case_dir, value)) is not None
                }
                known_paths.update(
                    path for item in derivatives if isinstance(item, dict)
                    if (path := case_evidence_path(case_dir, item.get("path"))) is not None
                )
                if anchor_path not in known_paths:
                    errors.append(f"{prefix}: anchor_ref is not owned by its evidence bundle")
                if any(
                    isinstance(item, dict)
                    and case_evidence_path(case_dir, item.get("path")) == anchor_path
                    and item.get("human_verification_required") is True
                    for item in derivatives
                ):
                    blocked_support.add(expression_id)
        parent = expression.get("derived_from_expression_id")
        if nonempty_string(parent):
            derivative_edges[expression_id] = parent
        lifecycle = expression.get("lifecycle_events")
        state = ""
        if not isinstance(lifecycle, list) or not lifecycle:
            errors.append(f"{prefix}: lifecycle_events must be non-empty")
        else:
            timestamps: list[datetime] = []
            for event_index, event in enumerate(lifecycle):
                event_name = event.get("event") if isinstance(event, dict) else None
                timestamp = parse_datetime(event.get("timestamp")) if isinstance(event, dict) else None
                if not isinstance(event, dict):
                    errors.append(f"{prefix}: lifecycle event {event_index} must be an object")
                    continue
                unknown = set(event) - {
                    "event", "timestamp", "actor", "reason", "successor_expression_id"
                }
                if unknown:
                    errors.append(
                        f"{prefix}: lifecycle event {event_index} has unknown fields {sorted(unknown)}"
                    )
                if not nonempty_string(event.get("actor")):
                    errors.append(f"{prefix}: lifecycle event {event_index} needs actor")
                if not nonempty_string(event.get("reason")):
                    errors.append(f"{prefix}: lifecycle event {event_index} needs reason")
                if timestamp is None:
                    errors.append(f"{prefix}: lifecycle event {event_index} has invalid timestamp")
                else:
                    timestamps.append(timestamp)
                if event_index == 0 and event_name != "activated":
                    errors.append(f"{prefix}: lifecycle must begin with activated")
                elif event_index > 0 and event_name not in {"superseded", "withdrawn"}:
                    errors.append(f"{prefix}: illegal lifecycle transition to {event_name!r}")
                if state in {"superseded", "withdrawn"}:
                    errors.append(f"{prefix}: lifecycle continues after terminal state {state}")
                state = event_name or state
                if event_name == "superseded":
                    successor = event.get("successor_expression_id")
                    if not nonempty_string(successor):
                        errors.append(f"{prefix}: superseded event needs successor_expression_id")
                    else:
                        successor_edges[expression_id] = successor
                elif "successor_expression_id" in event:
                    errors.append(
                        f"{prefix}: lifecycle event {event_index} cannot name a successor"
                    )
            if timestamps != sorted(timestamps):
                errors.append(f"{prefix}: lifecycle timestamps are not monotonic")
        if state == "activated":
            active.add(expression_id)
        links = expression.get("finding_links")
        if not isinstance(links, list) or not links:
            errors.append(f"{prefix}: finding_links must be a non-empty list")
            links = []
        linked_findings: set[str] = set()
        for link_index, link in enumerate(links):
            if not isinstance(link, dict):
                errors.append(f"{prefix}: finding_links[{link_index}] must be an object")
                continue
            finding_id = link.get("finding_id")
            if nonempty_string(finding_id) and finding_id in linked_findings:
                errors.append(f"{prefix}: duplicate finding link for {finding_id!r}")
            elif nonempty_string(finding_id):
                linked_findings.add(finding_id)
            if link.get("relation") not in {"supports", "contradicts", "context"}:
                errors.append(f"{prefix}: finding link {finding_id!r} has invalid relation")
            finding = findings.get(finding_id)
            if finding is None:
                errors.append(f"{prefix}: finding link {finding_id!r} does not resolve")
                continue
            finding_fp = canonical_fingerprint({"claim": finding.get("claim")})
            if finding.get("finding_fingerprint") != finding_fp or link.get("finding_fingerprint") != finding_fp:
                errors.append(f"{prefix}: finding_fingerprint mismatch for {finding_id}")
            link_payload = {
                "expression_fingerprint": expression.get("expression_fingerprint"),
                "finding_fingerprint": link.get("finding_fingerprint"),
                "finding_id": finding_id,
                "relation": link.get("relation"),
            }
            if canonical_fingerprint(link_payload) != link.get("link_fingerprint"):
                errors.append(f"{prefix}: link_fingerprint mismatch for {finding_id}")
            if link.get("relation") == "supports":
                support_links.add((expression_id, finding_id))
    for expression_id, parent in derivative_edges.items():
        if parent not in expressions:
            errors.append(f"source-expressions.json expression {expression_id!r}: derivative parent does not resolve")
    for expression_id, successor in successor_edges.items():
        target = expressions.get(successor)
        if target is None:
            errors.append(f"source-expressions.json expression {expression_id!r}: successor does not resolve")
        elif target.get("supersedes_expression_id") != expression_id:
            errors.append(f"source-expressions.json expression {expression_id!r}: successor back-reference mismatch")
    for expression_id, expression in expressions.items():
        predecessor = expression.get("supersedes_expression_id")
        if nonempty_string(predecessor) and successor_edges.get(predecessor) != expression_id:
            errors.append(f"source-expressions.json expression {expression_id!r}: supersedes back-reference has no matching lifecycle event")
    errors.extend(_graph_cycles(derivative_edges, "derivative"))
    errors.extend(_graph_cycles(successor_edges, "lifecycle"))

    container, rows = fact_check_rows(factcheck_data or {})
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        _, verdict = fact_check_values(container, row)
        finding_id = row.get("finding_id")
        evidence_for = row.get("evidence_for")
        evidence_against = row.get("evidence_against")
        if not isinstance(evidence_for, list):
            evidence_for = []
        if not isinstance(evidence_against, list):
            evidence_against = []
        refs = expression_refs(evidence_for)
        all_refs = refs + expression_refs(evidence_against)
        eligible = False
        valid_ref_ids: set[str] = set()
        for ref in all_refs:
            expression_id = ref.get("expression_id")
            expression = expressions.get(expression_id)
            link = next((item for item in (expression or {}).get("finding_links", []) if isinstance(item, dict) and item.get("finding_id") == finding_id), None)
            if expression is None or link is None:
                errors.append(f"fact-check.json.{container}[{index}]: expression ref {expression_id!r} does not resolve for finding {finding_id}")
                continue
            if any(ref.get(key) != expected for key, expected in (
                ("expression_fingerprint", expression.get("expression_fingerprint")),
                ("finding_fingerprint", link.get("finding_fingerprint")),
                ("link_fingerprint", link.get("link_fingerprint")),
            )):
                errors.append(f"fact-check.json.{container}[{index}]: stale expression ref {expression_id!r}")
                continue
            valid_ref_ids.add(expression_id)
        if not enforce_positive_verdicts or verdict not in POSITIVE_VERDICTS:
            continue
        for ref in refs:
            expression_id = ref.get("expression_id")
            if (
                expression_id in valid_ref_ids
                and expression_id in active
                and expression_id not in blocked_support
                and (expression_id, finding_id) in support_links
            ):
                eligible = True
            elif expression_id in blocked_support:
                errors.append(f"fact-check.json.{container}[{index}]: source expression {expression_id!r} is pending human verification or not source evidence")
        if not eligible:
            errors.append(f"fact-check.json.{container}[{index}]: positive verdict for {finding_id!r} has no active supporting source expression")
    return errors


def validate_findings(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if not isinstance(data, dict):
        return [f"findings.json: top-level must be an object, got {type(data).__name__}"]

    # Required top-level fields
    if not nonempty_string(data.get("project")):
        errors.append("findings.json: missing or empty top-level 'project'")
    if "findings" not in data:
        errors.append("findings.json: missing top-level 'findings' — this is a finding archive, not a wrong-shape doc (paz-valais-communes-style). If the case produces commune_checks / claim_checks / other shapes, write them to a separate file (e.g. data/commune-checks.json), not findings.json.")
        return errors  # cannot continue without findings
    if not isinstance(data["findings"], list):
        errors.append("findings.json: 'findings' must be a list")
        return errors

    # Per-finding checks
    for i, finding in enumerate(data["findings"]):
        prefix = f"findings.json[{i}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        if not nonempty_string(finding.get("id")):
            errors.append(f"{prefix}: missing or empty 'id'")
        if not nonempty_string(finding.get("claim")):
            errors.append(f"{prefix}: missing or empty 'claim' — every finding must have a claim text. Skip the finding entirely if you can't articulate one.")
        if "evidence" in finding:
            ev = finding["evidence"]
            if not (nonempty_string(ev) or (isinstance(ev, list) and all(nonempty_string(x) for x in ev) and ev)):
                errors.append(f"{prefix}: 'evidence' must be a non-empty string or non-empty list of non-empty strings")
        elif "evidence" not in finding:
            errors.append(f"{prefix}: missing 'evidence'")
        if "sources" not in finding or not isinstance(finding["sources"], list):
            errors.append(f"{prefix}: missing or non-list 'sources'")
        elif len(finding["sources"]) == 0:
            errors.append(f"{prefix}: 'sources' is empty — every finding must cite at least one source")
        else:
            for j, src in enumerate(finding["sources"]):
                if not isinstance(src, dict):
                    errors.append(f"{prefix}.sources[{j}]: must be an object")
                # sources are optional-fielded now (schema reality alignment); only check basic shape

        conf = finding.get("confidence")
        if conf not in CONFIDENCES:
            errors.append(f"{prefix}: 'confidence' must be one of {sorted(CONFIDENCES)} (got {conf!r})")

        # Grounding object (optional in schema, but if present must be valid)
        grounding = finding.get("grounding")
        if grounding is not None:
            errors.extend(validate_grounding(grounding, prefix))

    indicators = data.get("technical_indicators")
    if indicators is not None:
        if not isinstance(indicators, list):
            errors.append("findings.json: 'technical_indicators' must be a list")
        else:
            finding_ids = {
                finding.get("id")
                for finding in data["findings"]
                if isinstance(finding, dict) and nonempty_string(finding.get("id"))
            }
            indicator_ids: set[str] = set()
            for index, indicator in enumerate(indicators):
                prefix = f"findings.json.technical_indicators[{index}]"
                if not isinstance(indicator, dict):
                    errors.append(f"{prefix}: must be an object")
                    continue
                indicator_id = indicator.get("id")
                if not nonempty_string(indicator_id):
                    errors.append(f"{prefix}.id: must be a non-empty string")
                elif indicator_id in indicator_ids:
                    errors.append(f"{prefix}.id: duplicate technical indicator id {indicator_id!r}")
                else:
                    indicator_ids.add(indicator_id)
                finding_id = indicator.get("finding_id")
                if not nonempty_string(finding_id):
                    errors.append(f"{prefix}.finding_id: must be a non-empty string")
                elif finding_id not in finding_ids:
                    errors.append(f"{prefix}.finding_id: must reference an existing finding")
                indicator_type = indicator.get("type")
                if not isinstance(indicator_type, str) or indicator_type not in TECHNICAL_INDICATOR_TYPES:
                    errors.append(f"{prefix}.type: must be one of {sorted(TECHNICAL_INDICATOR_TYPES)}")
                for key in ("value", "context"):
                    if not nonempty_string(indicator.get(key)):
                        errors.append(f"{prefix}.{key}: must be a non-empty string")
                sources = indicator.get("sources")
                if not isinstance(sources, list) or not sources or not all(nonempty_string(source) for source in sources):
                    errors.append(f"{prefix}.sources: must be a non-empty list of non-empty strings")
                observed: dict[str, datetime] = {}
                for key in ("first_observed", "last_observed"):
                    if key in indicator:
                        parsed = parse_datetime(indicator.get(key))
                        if parsed is None:
                            errors.append(f"{prefix}.{key}: must be an ISO 8601 timestamp with timezone")
                        else:
                            observed[key] = parsed
                if (
                    "first_observed" in observed
                    and "last_observed" in observed
                    and observed["last_observed"] < observed["first_observed"]
                ):
                    errors.append(f"{prefix}: last_observed must not precede first_observed")

    return errors


def validate_grounding(grounding: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(grounding, dict):
        return [f"{prefix}.grounding: must be an object"]
    if grounding.get("support_type") not in SUPPORT_TYPES:
        errors.append(f"{prefix}.grounding.support_type: must be one of {sorted(SUPPORT_TYPES)}")
    if grounding.get("source_role") not in SOURCE_ROLES:
        errors.append(f"{prefix}.grounding.source_role: must be one of {sorted(SOURCE_ROLES)}")
    if not isinstance(grounding.get("claim_elements_supported"), list):
        errors.append(f"{prefix}.grounding.claim_elements_supported: must be a list")
    if not isinstance(grounding.get("missing_assumptions"), list):
        errors.append(f"{prefix}.grounding.missing_assumptions: must be a list")
    if grounding.get("confidence_cap") not in CAPS:
        errors.append(f"{prefix}.grounding.confidence_cap: must be one of {sorted(CAPS)}")
    if not nonempty_string(grounding.get("misgrounding_risk")):
        errors.append(f"{prefix}.grounding.misgrounding_risk: missing or empty (write at minimum 'low; the claim restates source fields')")
    if not nonempty_string(grounding.get("grounding_rationale")):
        errors.append(f"{prefix}.grounding.grounding_rationale: missing or empty — explain why this evidence does or does not ground the claim, and include the contradiction-search outcome")
    return errors


def fact_check_rows(data: dict[str, Any]) -> tuple[str, Any]:
    for key in ("claims", "fact_checks", "verdicts"):
        if key in data:
            return key, data[key]
    return "claims", None


def fact_check_values(container: str, row: dict[str, Any]) -> tuple[Any, Any]:
    if container == "claims":
        return row.get("claim_text"), row.get("verdict")
    if container == "fact_checks":
        return row.get("claim"), row.get("status")
    current = "claim_text" in row or "verdict" in row
    legacy = "claim" in row or "status" in row
    if current and not legacy:
        return row.get("claim_text"), row.get("verdict")
    if legacy and not current:
        return row.get("claim"), row.get("status")
    return None, None


def validate_fact_check(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if not isinstance(data, dict):
        return [f"fact-check.json: top-level must be an object, got {type(data).__name__}"]

    if not nonempty_string(data.get("project")):
        errors.append("fact-check.json: missing or empty top-level 'project'")

    containers = [key for key in ("claims", "fact_checks", "verdicts") if key in data]
    if len(containers) > 1:
        errors.append(
            "fact-check.json: use exactly one verdict container; found "
            + ", ".join(containers)
        )
    container, rows = fact_check_rows(data)
    if rows is not None:
        if not isinstance(rows, list):
            errors.append(f"fact-check.json: '{container}' must be a list")
        else:
            for i, claim in enumerate(rows):
                prefix = f"fact-check.json.{container}[{i}]"
                if not isinstance(claim, dict):
                    errors.append(f"{prefix}: must be an object")
                    continue
                if container == "claims" and ("claim" in claim or "status" in claim):
                    errors.append(f"{prefix}: legacy claim/status aliases are not allowed in claims[]")
                if container == "fact_checks" and ("claim_text" in claim or "verdict" in claim):
                    errors.append(f"{prefix}: current claim_text/verdict aliases are not allowed in fact_checks[]")
                claim_text, verdict = fact_check_values(container, claim)
                if not nonempty_string(claim_text):
                    errors.append(f"{prefix}: missing or empty claim text")
                if verdict not in VERDICTS:
                    errors.append(
                        f"{prefix}: verdict/status must be one of {sorted(VERDICTS)} "
                        f"(got {verdict!r})"
                    )
                if "confidence" in claim and claim.get("confidence") not in FACT_CHECK_CONFIDENCES:
                    errors.append(f"{prefix}: 'confidence' must be one of {sorted(FACT_CHECK_CONFIDENCES)}")
                if "finding_id" in claim and not nonempty_string(claim.get("finding_id")):
                    errors.append(f"{prefix}: 'finding_id' must be a non-empty string when present")
                if "technical_indicator_ids" in claim:
                    indicator_ids = claim.get("technical_indicator_ids")
                    if not string_list(indicator_ids) or not all(nonempty_string(item) for item in indicator_ids):
                        errors.append(f"{prefix}: 'technical_indicator_ids' must be a list of non-empty strings")
                    elif len(indicator_ids) != len(set(indicator_ids)):
                        errors.append(f"{prefix}: 'technical_indicator_ids' must not contain duplicates")
                if "sources" in claim and not string_list(claim.get("sources")):
                    errors.append(f"{prefix}: 'sources' must be a list of strings")
                if "notes" in claim and not isinstance(claim.get("notes"), str):
                    errors.append(f"{prefix}: 'notes' must be a string")
                if "grounding_assessment" in claim:
                    errors.extend(validate_fact_grounding_assessment(claim["grounding_assessment"], f"{prefix}.grounding_assessment"))
                for key in ("evidence_for", "evidence_against"):
                    if key not in claim:
                        continue
                    if not isinstance(claim[key], list):
                        errors.append(f"{prefix}: '{key}' must be a list")
                        continue
                    for j, item in enumerate(claim[key]):
                        errors.extend(validate_fact_evidence_item(item, f"{prefix}.{key}[{j}]"))
                if verdict in {"verified", "partially_verified"}:
                    if not nonempty_string(claim.get("finding_id")):
                        errors.append(f"{prefix}: positive verdict needs a finding_id")
                    evidence_for = claim.get("evidence_for")
                    if not isinstance(evidence_for, list) or not evidence_for:
                        errors.append(f"{prefix}: positive verdict needs evidence_for")
                    else:
                        allowed_access = {"full_text", "open_access", "archive_copy"}
                        if verdict == "partially_verified":
                            allowed_access.add("abstract_only")
                        has_anchor = any(
                            isinstance(item, dict) and (
                                nonempty_string(item.get("evidence_bundle_id"))
                                or (
                                    item.get("access_method") in allowed_access
                                    and (
                                        nonempty_string(item.get("local_file"))
                                        or isinstance(item.get("source_ref"), dict)
                                    )
                                )
                            )
                            for item in evidence_for
                        )
                    if isinstance(evidence_for, list) and evidence_for and not has_anchor:
                        errors.append(
                            f"{prefix}: positive verdict needs an accessible case-local evidence anchor"
                        )

    if "summary" in data:
        summary = data["summary"]
        if isinstance(summary, dict):
            for key in ("total_claims", "verified", "unverified", "disputed", "false"):
                if key in summary and not isinstance(summary[key], int):
                    errors.append(f"fact-check.json.summary.{key}: must be an integer")
        elif not isinstance(summary, str):
            errors.append("fact-check.json: 'summary' must be an object or string")
    if "cycle" in data and (not isinstance(data["cycle"], int) or data["cycle"] < 1):
        errors.append("fact-check.json: 'cycle' must be an integer >= 1")
    if "gaps_for_next_cycle" in data and not string_list(data["gaps_for_next_cycle"]):
        errors.append("fact-check.json: 'gaps_for_next_cycle' must be a list of strings")

    return errors


def validate_fact_grounding_assessment(data: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{prefix}: must be an object"]
    required = ["support_type", "claim_elements_checked", "missing_assumptions", "confidence_cap", "assessment"]
    for key in required:
        if key not in data:
            errors.append(f"{prefix}: missing '{key}'")
    if data.get("support_type") not in SUPPORT_TYPES:
        errors.append(f"{prefix}.support_type: must be one of {sorted(SUPPORT_TYPES)}")
    if not string_list(data.get("claim_elements_checked")):
        errors.append(f"{prefix}.claim_elements_checked: must be a list of strings")
    if not string_list(data.get("missing_assumptions")):
        errors.append(f"{prefix}.missing_assumptions: must be a list of strings")
    if data.get("confidence_cap") not in CAPS:
        errors.append(f"{prefix}.confidence_cap: must be one of {sorted(CAPS)}")
    if not nonempty_string(data.get("assessment")):
        errors.append(f"{prefix}.assessment: missing or empty")
    return errors


def validate_fact_evidence_item(data: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{prefix}: must be an object"]
    for key in ("description", "source"):
        if not nonempty_string(data.get(key)):
            errors.append(f"{prefix}: missing or empty '{key}'")
    if "source_type" in data and data.get("source_type") not in EVIDENCE_SOURCE_TYPES:
        errors.append(f"{prefix}.source_type: must be one of {sorted(EVIDENCE_SOURCE_TYPES)}")
    if "archive_url" in data and not optional_string(data.get("archive_url")):
        errors.append(f"{prefix}.archive_url: must be a string or null")
    if "access_method" in data and data.get("access_method") not in ACCESS_METHODS:
        errors.append(f"{prefix}.access_method: must be one of {sorted(ACCESS_METHODS)}")
    if "local_file" in data and not optional_string(data.get("local_file")):
        errors.append(f"{prefix}.local_file: must be a string or null")
    if "evidence_bundle_id" in data and not nonempty_string(data.get("evidence_bundle_id")):
        errors.append(f"{prefix}.evidence_bundle_id: must be a non-empty string")
    if "quote" in data and not nonempty_string(data.get("quote")):
        errors.append(f"{prefix}.quote: must be a non-empty string")
    if "sha256" in data and (
        not isinstance(data.get("sha256"), str)
        or not re.fullmatch(r"[A-Fa-f0-9]{64}", data["sha256"])
    ):
        errors.append(f"{prefix}.sha256: must be 64 hexadecimal characters")
    if "source_ref" in data:
        source_ref = data.get("source_ref")
        if not isinstance(source_ref, dict):
            errors.append(f"{prefix}.source_ref: must be an object")
        else:
            if not nonempty_string(source_ref.get("path")):
                errors.append(f"{prefix}.source_ref.path: must be a non-empty string")
            has_pointer = "json_pointer" in source_ref
            has_lines = "line_start" in source_ref or "line_end" in source_ref
            if has_pointer == has_lines:
                errors.append(
                    f"{prefix}.source_ref: use either json_pointer or line_start/line_end"
                )
            if has_pointer and (
                not isinstance(source_ref.get("json_pointer"), str)
                or (source_ref["json_pointer"] and not source_ref["json_pointer"].startswith("/"))
            ):
                errors.append(f"{prefix}.source_ref.json_pointer: must be empty or start with '/'")
            if has_lines:
                start = source_ref.get("line_start")
                end = source_ref.get("line_end")
                if not isinstance(start, int) or start < 1:
                    errors.append(f"{prefix}.source_ref.line_start: must be an integer >= 1")
                if not isinstance(end, int) or not isinstance(start, int) or end < start:
                    errors.append(
                        f"{prefix}.source_ref.line_end: must be an integer >= line_start"
                    )
    return errors


def cross_reference(findings_data: dict[str, Any] | None, factcheck_data: dict[str, Any] | None) -> list[str]:
    """Cross-file: fact-check finding and explicit-indicator links must resolve."""
    errors: list[str] = []
    if findings_data is None or factcheck_data is None:
        return errors
    finding_ids = {
        finding.get("id")
        for finding in findings_data.get("findings", [])
        if isinstance(finding, dict) and nonempty_string(finding.get("id"))
    }
    indicators = {
        indicator.get("id"): indicator
        for indicator in findings_data.get("technical_indicators", [])
        if isinstance(indicator, dict) and nonempty_string(indicator.get("id"))
    }
    container, rows = fact_check_rows(factcheck_data)
    for i, claim in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(claim, dict):
            continue
        fid = claim.get("finding_id")
        if nonempty_string(fid) and fid not in finding_ids:
            errors.append(f"fact-check.json.{container}[{i}]: 'finding_id' {fid!r} does not match any id in findings.json")
        technical_ids = claim.get("technical_indicator_ids", [])
        if not isinstance(technical_ids, list):
            continue
        for technical_id in technical_ids:
            if not nonempty_string(technical_id):
                continue
            indicator = indicators.get(technical_id)
            if indicator is None:
                errors.append(
                    f"fact-check.json.{container}[{i}]: technical_indicator_id {technical_id!r} "
                    "does not match findings.json"
                )
                continue
            if fid != indicator.get("finding_id"):
                errors.append(
                    f"fact-check.json.{container}[{i}]: technical indicator {technical_id!r} "
                    "belongs to a different finding_id"
                )
            value = indicator.get("value")
            claim_text, _ = fact_check_values(container, claim)
            if nonempty_string(value) and nonempty_string(claim_text) and value not in claim_text:
                errors.append(
                    f"fact-check.json.{container}[{i}]: claim_text must contain the exact value "
                    f"for technical indicator {technical_id!r}"
                )
    return errors


def validate_evidence_bundle(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"evidence-bundle.json: top-level must be an object, got {type(data).__name__}"]
    for key in ("schema_version", "project", "run_id", "created_at", "items"):
        if key not in data:
            errors.append(f"evidence-bundle.json: missing top-level '{key}'")
    if data.get("schema_version") != "1.0":
        errors.append("evidence-bundle.json: schema_version must be 1.0")
    for key in ("project", "run_id", "created_at"):
        if not nonempty_string(data.get(key)):
            errors.append(f"evidence-bundle.json: missing or empty top-level '{key}'")
    items = data.get("items")
    if not isinstance(items, list):
        errors.append("evidence-bundle.json: 'items' must be a list")
        return errors
    for i, item in enumerate(items):
        prefix = f"evidence-bundle.json.items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for key in ("id", "query_or_task", "source_url", "accessed"):
            if not nonempty_string(item.get(key)):
                errors.append(f"{prefix}: missing or empty '{key}'")
        if item.get("acquisition_method") not in ACQUISITION_METHODS:
            errors.append(f"{prefix}.acquisition_method: must be one of {sorted(ACQUISITION_METHODS)}")
        if item.get("extraction_confidence") not in FACT_CHECK_CONFIDENCES:
            errors.append(f"{prefix}.extraction_confidence: must be one of {sorted(FACT_CHECK_CONFIDENCES)}")
        if not isinstance(item.get("human_verification_required"), bool):
            errors.append(f"{prefix}.human_verification_required: must be boolean")
        if "sha256" in item and (not isinstance(item["sha256"], str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", item["sha256"])):
            errors.append(f"{prefix}.sha256: must be 64 hex characters")
        if "claim_links" in item:
            if not isinstance(item["claim_links"], list):
                errors.append(f"{prefix}.claim_links: must be a list")
            else:
                for j, link in enumerate(item["claim_links"]):
                    link_prefix = f"{prefix}.claim_links[{j}]"
                    if not isinstance(link, dict):
                        errors.append(f"{link_prefix}: must be an object")
                        continue
                    for key in ("finding_id", "claim_text"):
                        if not nonempty_string(link.get(key)):
                            errors.append(f"{link_prefix}: missing or empty '{key}'")
                    if link.get("support_type") not in SUPPORT_TYPES:
                        errors.append(f"{link_prefix}.support_type: must be one of {sorted(SUPPORT_TYPES)}")
        if "missing_source_gate" in item:
            errors.extend(validate_missing_source_gate(item["missing_source_gate"], f"{prefix}.missing_source_gate"))
    return errors


def validate_missing_source_gate(data: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{prefix}: must be an object"]
    for key in ("requested_source", "returned_artifact", "missing"):
        if not nonempty_string(data.get(key)):
            errors.append(f"{prefix}: missing or empty '{key}'")
    if not isinstance(data.get("fallback_required"), bool):
        errors.append(f"{prefix}.fallback_required: must be boolean")
    if "confidence_effect" in data and data.get("confidence_effect") not in MISSING_SOURCE_CONFIDENCE_EFFECTS:
        errors.append(f"{prefix}.confidence_effect: must be one of {sorted(MISSING_SOURCE_CONFIDENCE_EFFECTS)}")
    return errors


def validate_investigation_log(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"investigation-log.json: top-level must be an object, got {type(data).__name__}"]
    for key in ("schema_version", "project", "cycles"):
        if key not in data:
            errors.append(f"investigation-log.json: missing top-level '{key}'")
    if data.get("schema_version") != "1.0":
        errors.append("investigation-log.json: schema_version must be 1.0")
    if not nonempty_string(data.get("project")):
        errors.append("investigation-log.json: missing or empty top-level 'project'")
    cycles = data.get("cycles")
    if not isinstance(cycles, list):
        errors.append("investigation-log.json: 'cycles' must be a list")
        return errors
    for i, cycle in enumerate(cycles):
        prefix = f"investigation-log.json.cycles[{i}]"
        if not isinstance(cycle, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        if not isinstance(cycle.get("cycle"), int) or cycle.get("cycle") < 1:
            errors.append(f"{prefix}.cycle: must be an integer >= 1")
        for key in ("timestamp", "focus"):
            if not nonempty_string(cycle.get(key)):
                errors.append(f"{prefix}: missing or empty '{key}'")
        methodology = cycle.get("methodology")
        if not isinstance(methodology, dict):
            errors.append(f"{prefix}.methodology: must be an object")
        else:
            for key in ("techniques_used", "tools_used"):
                if not string_list(methodology.get(key)):
                    errors.append(f"{prefix}.methodology.{key}: must be a list of strings")
            for key in ("search_queries", "failed_approaches"):
                if key in methodology and not string_list(methodology.get(key)):
                    errors.append(f"{prefix}.methodology.{key}: must be a list of strings")
        if not isinstance(cycle.get("findings_added"), int):
            errors.append(f"{prefix}.findings_added: must be an integer")
        if "findings_upgraded" in cycle and not isinstance(cycle.get("findings_upgraded"), int):
            errors.append(f"{prefix}.findings_upgraded: must be an integer")
        if not string_list(cycle.get("gaps_remaining")):
            errors.append(f"{prefix}.gaps_remaining: must be a list of strings")
        if "gaps_resolved" in cycle and not string_list(cycle.get("gaps_resolved")):
            errors.append(f"{prefix}.gaps_resolved: must be a list of strings")
        if "sources_consulted" in cycle:
            if not isinstance(cycle["sources_consulted"], list):
                errors.append(f"{prefix}.sources_consulted: must be a list")
            else:
                for j, source in enumerate(cycle["sources_consulted"]):
                    source_prefix = f"{prefix}.sources_consulted[{j}]"
                    if not isinstance(source, dict):
                        errors.append(f"{source_prefix}: must be an object")
                        continue
                    for key in ("url", "type", "accessed"):
                        if not nonempty_string(source.get(key)):
                            errors.append(f"{source_prefix}: missing or empty '{key}'")
                    if not isinstance(source.get("useful"), bool):
                        errors.append(f"{source_prefix}.useful: must be boolean")
    return errors


def validate_rlm_analysis(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"rlm-analysis.json: top-level must be an object, got {type(data).__name__}"]
    for key in ("schema_version", "project", "run_id", "mode", "provider", "created_at", "artifacts"):
        if key not in data:
            errors.append(f"rlm-analysis.json: missing top-level '{key}'")
    if data.get("schema_version") != "1.0":
        errors.append("rlm-analysis.json: schema_version must be 1.0")
    if data.get("mode") not in RLM_MODES:
        errors.append(f"rlm-analysis.json: mode must be one of {sorted(RLM_MODES)}")
    if data.get("provider") not in RLM_PROVIDERS:
        errors.append(f"rlm-analysis.json: provider must be one of {sorted(RLM_PROVIDERS)}")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("rlm-analysis.json: artifacts must be a list")
        return errors
    for i, item in enumerate(artifacts):
        prefix = f"rlm-analysis.json.artifacts[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        if not nonempty_string(item.get("id")):
            errors.append(f"{prefix}: missing or empty id")
        if item.get("kind") not in RLM_KINDS:
            errors.append(f"{prefix}: kind must be one of {sorted(RLM_KINDS)}, got {item.get('kind')!r}")
        if not nonempty_string(item.get("text")):
            errors.append(f"{prefix}: missing or empty text")
        status = item.get("verification_status")
        if status not in RLM_STATUSES:
            errors.append(f"{prefix}: verification_status must be needs_verification, got {status!r}")
        if status in {"verified", "confirmed", "publishable"}:
            errors.append(f"{prefix}: forbidden verified-style status")
        source_refs = item.get("source_refs")
        if item.get("kind") != "discarded" and not source_refs:
            errors.append(f"{prefix}: non-discarded artifact must have source_refs")
        if source_refs is not None:
            if not isinstance(source_refs, list):
                errors.append(f"{prefix}: source_refs must be a list")
            else:
                for j, ref in enumerate(source_refs):
                    ref_prefix = f"{prefix}.source_refs[{j}]"
                    if not isinstance(ref, dict):
                        errors.append(f"{ref_prefix}: must be an object")
                        continue
                    if not nonempty_string(ref.get("path")):
                        errors.append(f"{ref_prefix}: missing or empty path")
                    for key in ("line_start", "line_end"):
                        if not isinstance(ref.get(key), int) or ref.get(key) < 1:
                            errors.append(f"{ref_prefix}: {key} must be a positive integer")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Spotlight case directory.")
    parser.add_argument("case_dir", help="Path to {CASE_DIR}/")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument("--include-rlm", action="store_true", help="Also validate optional data/rlm-analysis.json if present")
    parser.add_argument(
        "--fact-check-only",
        action="store_true",
        help="validate findings, fact-check, and their cross-references only",
    )
    args = parser.parse_args()

    case_dir = Path(args.case_dir).expanduser().resolve()
    if not case_dir.is_dir():
        print(f"error: not a directory: {case_dir}", file=sys.stderr)
        return 2

    findings_path = case_dir / "data" / "findings.json"
    if not findings_path.exists():
        findings_path = case_dir / "findings.json"
    factcheck_path = case_dir / "data" / "fact-check.json"
    if not factcheck_path.exists():
        factcheck_path = case_dir / "fact-check.json"

    all_errors: list[str] = []

    findings_data = None
    if findings_path.exists():
        findings_data, errs = load_json(findings_path)
        all_errors.extend(errs)
        if findings_data is not None:
            all_errors.extend(validate_findings(findings_data))
    else:
        all_errors.append(f"findings.json: not found in {case_dir}/data/ or {case_dir}/")

    factcheck_data = None
    if factcheck_path.exists():
        factcheck_data, errs = load_json(factcheck_path)
        all_errors.extend(errs)
        if factcheck_data is not None:
            all_errors.extend(validate_fact_check(factcheck_data))

    all_errors.extend(cross_reference(findings_data, factcheck_data))

    evidence_bundle_data = None
    evidence_bundle_path = case_dir / "data" / "evidence-bundle.json"
    if evidence_bundle_path.exists():
        evidence_bundle_data, errs = load_json(evidence_bundle_path)
        if errs:
            all_errors.extend(errs)
        elif not args.fact_check_only or (
            isinstance(evidence_bundle_data, dict)
            and isinstance(evidence_bundle_data.get("items"), list)
        ):
            if evidence_bundle_data is not None:
                all_errors.extend(validate_evidence_bundle(evidence_bundle_data))

    contract_path = case_dir / "data" / "case-contract.json"
    contract_data = None
    contract_errors: list[str] = []
    if contract_path.exists():
        contract_data, contract_errors = load_json(contract_path)
        all_errors.extend(contract_errors)
        if contract_data is not None:
            contract_errors = validate_case_contract(contract_data)
            all_errors.extend(contract_errors)
    findings_version = findings_data.get("schema_version", "1.0") if isinstance(findings_data, dict) else "1.0"
    activated = contract_data is not None and not contract_errors and findings_version == "1.1"
    if contract_data is not None and not contract_errors and findings_version != "1.1":
        all_errors.append("case-contract.json: valid 1.1 activation requires findings.json schema_version 1.1")
    if findings_version == "1.1" and not activated:
        all_errors.append("findings.json: schema_version 1.1 requires a valid data/case-contract.json")

    if activated:
        if contract_data.get("project") != findings_data.get("project"):
            all_errors.append("case-contract.json: project must match findings.json")
        expressions_path = case_dir / "data" / "source-expressions.json"
        expressions_data, errs = load_json(expressions_path)
        all_errors.extend(errs)
        if expressions_data is not None:
            all_errors.extend(validate_source_expressions(
                case_dir, expressions_data, findings_data, factcheck_data, evidence_bundle_data
            ))
        latest = contract_data["activation_events"][-1].get("activated_artifact_hashes", {})
        artifact_paths = {
            "findings_sha256": findings_path,
            "fact_check_sha256": factcheck_path,
            "evidence_bundle_sha256": evidence_bundle_path,
            "source_expressions_sha256": expressions_path,
        }
        for key, path in artifact_paths.items():
            if not path.is_file():
                all_errors.append(f"case-contract.json: activated artifact {path.name} is missing")
            elif latest.get(key) != sha256_file(path):
                all_errors.append(f"case-contract.json: {key} does not match the activated artifact")
    elif findings_version == "1.0":
        expressions_path = case_dir / "data" / "source-expressions.json"
        if expressions_path.exists():
            expressions_data, errs = load_json(expressions_path)
            all_errors.extend(errs)
            if expressions_data is not None:
                all_errors.extend(validate_source_expressions(
                    case_dir,
                    expressions_data,
                    findings_data,
                    factcheck_data,
                    evidence_bundle_data,
                    enforce_positive_verdicts=False,
                ))

    if not args.fact_check_only:
        investigation_log_path = case_dir / "data" / "investigation-log.json"
        if investigation_log_path.exists():
            investigation_log_data, errs = load_json(investigation_log_path)
            all_errors.extend(errs)
            if investigation_log_data is not None:
                all_errors.extend(validate_investigation_log(investigation_log_data))

    if args.include_rlm and not args.fact_check_only:
        rlm_path = case_dir / "data" / "rlm-analysis.json"
        if rlm_path.exists():
            rlm_data, errs = load_json(rlm_path)
            all_errors.extend(errs)
            if rlm_data is not None:
                all_errors.extend(validate_rlm_analysis(rlm_data))

    if all_errors:
        print(f"\n{len(all_errors)} validation error(s) in {case_dir.name}:\n", file=sys.stderr)
        for err in all_errors:
            print(f"  • {err}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    suffix = " (fact-check only)" if args.fact_check_only else (" + RLM" if args.include_rlm else "")
    print(f"✓ {case_dir.name} validates against case schemas{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
