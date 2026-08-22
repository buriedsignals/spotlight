"""Native Arbiter workflow callers and case-local raw response evidence."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from .client import ArbiterClient, safe_research_path
except ImportError:  # Loaded directly by focused checks and standalone tooling.
    client_module = sys.modules.get("spotlight_arbiter_client")
    if client_module is None:
        client_path = Path(__file__).with_name("client.py")
        spec = importlib.util.spec_from_file_location("spotlight_arbiter_client", client_path)
        if spec is None or spec.loader is None:
            raise ImportError("could not load the Arbiter client")
        client_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = client_module
        spec.loader.exec_module(client_module)
    ArbiterClient = client_module.ArbiterClient
    safe_research_path = client_module.safe_research_path


_ID_RE = re.compile(r"^[a-z0-9]{32}$")
_SAFE_TIMESTAMP_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _timestamp_token(timestamp: str | None) -> str:
    value = timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    token = _SAFE_TIMESTAMP_RE.sub("-", value).strip("-.")
    if not token:
        raise ValueError("timestamp must produce a safe research filename")
    return token


def _topic_id(value: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError("case-study id must contain 32 lowercase alphanumeric characters")
    return value

_RESPONSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


def _response_study_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("create response must be an object")
    value = payload.get("case_study_id")
    if not isinstance(value, str) or not _RESPONSE_ID_RE.fullmatch(value):
        raise ValueError("create response has an invalid case-study id")
    return value



def _case_dir(case_dir: Path | str) -> Path:
    case_path = Path(case_dir)
    # This also rejects symlinked roots/research directories before any write.
    safe_research_path(case_path, "arbiter-case-check.json")
    return case_path


def _write_json(case_dir: Path, filename: str, payload: Any) -> Path:
    path = safe_research_path(case_dir, filename)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _save_response(case_dir: Path, operation: str, timestamp: str, payload: Any) -> Path:
    return _write_json(case_dir, f"arbiter-{operation}-{timestamp}.json", payload)


def browse(
    client: ArbiterClient, case_dir: Path | str, *, timestamp: str | None = None
) -> Path:
    """Fetch the free study menu and preserve the complete response."""
    case_path = _case_dir(case_dir)
    token = _timestamp_token(timestamp)
    payload = client.request_json("GET", "/topics", query={"limit": 100})
    return _save_response(case_path, "topics-menu", token, payload)


def read(
    client: ArbiterClient,
    case_dir: Path | str,
    case_study_id: str,
    *,
    cursor: str | None = None,
    timestamp: str | None = None,
) -> Path:
    """Fetch one bounded page of archived posts for a selected study."""
    case_path = _case_dir(case_dir)
    study_id = _topic_id(case_study_id)
    token = _timestamp_token(timestamp)
    query: dict[str, Any] = {"limit": 100}
    if cursor is not None:
        query["cursor"] = cursor
    payload = client.request_json("GET", f"/topics/{study_id}/posts", query=query)
    return _save_response(case_path, f"posts-{study_id}", token, payload)


def report(
    client: ArbiterClient, case_dir: Path | str, case_study_id: str, *, timestamp: str | None = None
) -> Path:
    """Fetch the free study report and preserve all upstream fields."""
    case_path = _case_dir(case_dir)
    study_id = _topic_id(case_study_id)
    token = _timestamp_token(timestamp)
    payload = client.request_json("GET", f"/topics/{study_id}/report")
    return _save_response(case_path, f"report-{study_id}", token, payload)


def progress(
    client: ArbiterClient, case_dir: Path | str, case_study_id: str, *, timestamp: str | None = None
) -> Path:
    """Poll free case-study progress once; callers control poll cadence."""
    case_path = _case_dir(case_dir)
    study_id = _topic_id(case_study_id)
    token = _timestamp_token(timestamp)
    payload = client.request_json("GET", f"/case-studies/{study_id}/progress")
    return _save_response(case_path, f"progress-{study_id}", token, payload)


def _create_body(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("create body must be an object")
    allowed = {"search_query", "platforms", "date_range", "title"}
    if set(body) - allowed or "search_query" not in body or "platforms" not in body or "date_range" not in body:
        raise ValueError("create body has an invalid request shape")
    return dict(body)


def _reviewed_arrays(search_phrases: list[str], final_entities: list[str]) -> dict[str, list[str]]:
    if not isinstance(search_phrases, list) or not all(isinstance(item, str) for item in search_phrases):
        raise ValueError("search_phrases must be an array of strings")
    if not isinstance(final_entities, list) or not all(isinstance(item, str) for item in final_entities):
        raise ValueError("final_entities must be an array of strings")
    return {"search_phrases": list(search_phrases), "final_entities": list(final_entities)}


def reviewed_create(
    client: ArbiterClient,
    case_dir: Path | str,
    body: Mapping[str, Any],
    *,
    search_phrases: list[str],
    final_entities: list[str],
    confirmed: bool = False,
    timestamp: str | None = None,
) -> dict[str, Path]:
    """Run the explicitly confirmed create → plan → review → finalize lifecycle.

    There are no retries: create is non-idempotent and accepted metered calls
    must never be repeated after an ambiguous client-side failure.
    """
    if confirmed is not True:
        raise PermissionError("local confirmation is required before creating a study")
    case_path = _case_dir(case_dir)
    token = _timestamp_token(timestamp)
    create_body = _create_body(body)
    finalize_body = _reviewed_arrays(search_phrases, final_entities)

    create_input = _write_json(case_path, f"arbiter-create-{token}.input.json", create_body)
    create_payload = client.request_json("POST", "/case-studies", body=create_body)
    create_output = _save_response(case_path, "create", token, create_payload)
    study_id = _response_study_id(create_payload)

    plan_input = _write_json(case_path, f"arbiter-search-plan-{token}.input.json", {})
    plan_payload = client.request_json("POST", f"/case-studies/{study_id}/search-plan", body={})
    plan_output = _save_response(case_path, "search-plan", token, plan_payload)

    finalize_input = _write_json(case_path, f"arbiter-finalize-{token}.input.json", finalize_body)
    # The finalize response is requested exactly once; a client timeout must
    # never trigger an accidental retry of this metered operation.
    finalize_payload = client.request_json("POST", f"/case-studies/{study_id}/finalize", body=finalize_body)
    finalize_output = _save_response(case_path, "finalize", token, finalize_payload)
    return {
        "create_input": create_input,
        "create": create_output,
        "search_plan_input": plan_input,
        "search_plan": plan_output,
        "finalize_input": finalize_input,
        "finalize": finalize_output,
    }
