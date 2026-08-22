"""Native Arbiter workflow callers and case-local raw response evidence."""

from __future__ import annotations

import importlib.util
import json
import os
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
_MAX_SEARCH_PHRASES = 50
_MAX_ENTITIES = 200
_MAX_PHRASE_LENGTH = 200
_MAX_ENTITY_LENGTH = 500
_SEARCH_PLAN_TIMEOUT = 900.0


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


def _response_study_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("create response must be an object")
    value = payload.get("case_study_id")
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError("create response has an invalid case-study id")
    return value


def _case_dir(case_dir: Path | str) -> Path:
    case_path = Path(case_dir)
    safe_research_path(case_path, "arbiter-case-check.json")
    return case_path


def _exclusive_write(path: Path, content: bytes) -> Path:
    """Write once without replacing an existing artifact, including on races."""
    stem, suffix = path.stem, path.suffix
    for index in range(1000):
        candidate = path if index == 0 else path.with_name(f"{stem}-{index}{suffix}")
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        return candidate
    raise OSError("could not allocate a unique research evidence filename")


def _write_bytes(case_dir: Path, filename: str, content: bytes) -> Path:
    path = safe_research_path(case_dir, filename)
    return _exclusive_write(path, content)


def _write_json(case_dir: Path, filename: str, payload: Any) -> Path:
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _write_bytes(case_dir, filename, content)


def _request(
    client: ArbiterClient,
    method: str,
    path: str,
    *,
    query: Mapping[str, Any] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout: float | None = None,
) -> tuple[Any, bytes]:
    raw_request = getattr(client, "request_raw", None)
    if callable(raw_request):
        raw = raw_request(method, path, query=query, body=body, timeout=timeout)
        if not isinstance(raw, bytes):
            raise TypeError("Arbiter raw response must be bytes")
        return json.loads(raw.decode("utf-8")), raw
    payload = client.request_json(method, path, query=query, body=body, timeout=timeout)
    raw = (json.dumps(payload, separators=(",", ":"))).encode("utf-8")
    return payload, raw


def _save_response(case_dir: Path, operation: str, timestamp: str, raw: bytes) -> Path:
    return _write_bytes(case_dir, f"arbiter-{operation}-{timestamp}.json", raw)


def browse(
    client: ArbiterClient, case_dir: Path | str, *, timestamp: str | None = None
) -> Path:
    """Fetch the free study menu and preserve the complete response."""
    case_path = _case_dir(case_dir)
    token = _timestamp_token(timestamp)
    _payload, raw = _request(client, "GET", "/topics", query={"limit": 100})
    return _save_response(case_path, "topics-menu", token, raw)


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
    _payload, raw = _request(client, "GET", f"/topics/{study_id}/posts", query=query)
    return _save_response(case_path, f"posts-{study_id}", token, raw)


def report(
    client: ArbiterClient, case_dir: Path | str, case_study_id: str, *, timestamp: str | None = None
) -> Path:
    """Fetch the free study report and preserve all upstream fields."""
    case_path = _case_dir(case_dir)
    study_id = _topic_id(case_study_id)
    token = _timestamp_token(timestamp)
    _payload, raw = _request(client, "GET", f"/topics/{study_id}/report")
    return _save_response(case_path, f"report-{study_id}", token, raw)


def progress(
    client: ArbiterClient, case_dir: Path | str, case_study_id: str, *, timestamp: str | None = None
) -> Path:
    """Poll free case-study progress once; callers control poll cadence."""
    case_path = _case_dir(case_dir)
    study_id = _topic_id(case_study_id)
    token = _timestamp_token(timestamp)
    _payload, raw = _request(client, "GET", f"/case-studies/{study_id}/progress")
    return _save_response(case_path, f"progress-{study_id}", token, raw)


def _create_body(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("create body must be an object")
    allowed = {"search_query", "platforms", "date_range", "title"}
    if set(body) - allowed or "search_query" not in body or "platforms" not in body or "date_range" not in body:
        raise ValueError("create body has an invalid request shape")
    return dict(body)


def _reviewed_arrays(search_phrases: list[str], final_entities: list[str]) -> dict[str, list[str]]:
    if (
        not isinstance(search_phrases, list)
        or not search_phrases
        or len(search_phrases) > _MAX_SEARCH_PHRASES
        or not all(isinstance(item, str) and 1 <= len(item) <= _MAX_PHRASE_LENGTH for item in search_phrases)
    ):
        raise ValueError("search_phrases must contain 1..50 strings of at most 200 characters")
    if (
        not isinstance(final_entities, list)
        or len(final_entities) > _MAX_ENTITIES
        or not all(isinstance(item, str) and 1 <= len(item) <= _MAX_ENTITY_LENGTH for item in final_entities)
    ):
        raise ValueError("final_entities must contain strings of at most 500 characters")
    if len({item.casefold() for item in search_phrases}) != len(search_phrases):
        raise ValueError("search_phrases must be unique")
    if len({item.casefold() for item in final_entities}) != len(final_entities):
        raise ValueError("final_entities must be unique")
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
    """Run the explicitly confirmed create → plan → review → finalize lifecycle."""
    if confirmed is not True:
        raise PermissionError("local confirmation is required before creating a study")
    case_path = _case_dir(case_dir)
    token = _timestamp_token(timestamp)
    create_body = _create_body(body)
    finalize_body = _reviewed_arrays(search_phrases, final_entities)

    create_input = _write_json(case_path, f"arbiter-create-{token}.input.json", create_body)
    create_payload, create_raw = _request(client, "POST", "/case-studies", body=create_body)
    create_output = _save_response(case_path, "create", token, create_raw)
    study_id = _response_study_id(create_payload)

    plan_input = _write_json(case_path, f"arbiter-search-plan-{token}.input.json", {})
    plan_payload, plan_raw = _request(
        client,
        "POST",
        f"/case-studies/{study_id}/search-plan",
        body={},
        timeout=_SEARCH_PLAN_TIMEOUT,
    )
    plan_output = _save_response(case_path, "search-plan", token, plan_raw)

    finalize_input = _write_json(case_path, f"arbiter-finalize-{token}.input.json", finalize_body)
    _finalize_payload, finalize_raw = _request(
        client,
        "POST",
        f"/case-studies/{study_id}/finalize",
        body=finalize_body,
    )
    finalize_output = _save_response(case_path, "finalize", token, finalize_raw)
    return {
        "create_input": create_input,
        "create": create_output,
        "search_plan_input": plan_input,
        "search_plan": plan_output,
        "finalize_input": finalize_input,
        "finalize": finalize_output,
    }
