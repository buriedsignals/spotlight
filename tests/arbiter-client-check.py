#!/usr/bin/env python3
"""Executable safety contract for the native Arbiter HTTP seam.

The test intentionally names the smallest stdlib-first seam required by the
native integration. It uses an injected opener and never contacts Arbiter.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "integrations" / "arbiter" / "client.py"


class FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, object]):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def load_client():
    if not CLIENT.is_file():
        raise AssertionError("integrations/arbiter/client.py must provide the native request seam")
    spec = importlib.util.spec_from_file_location("spotlight_arbiter_client", CLIENT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load integrations/arbiter/client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_error(fn, message: str) -> None:
    try:
        fn()
    except Exception:
        return
    raise AssertionError(message)


def check_base_validation(client) -> None:
    for value in (
        "http://arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org/api/v1?x=1",
        "https://arbiter.simppl.org/api/v1#fragment",
        "https://user:pass@arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org:8443/api/v1",
        "https://arbiter.simppl.org/not-api-v1",
        "https://arbiter.simppl.org/api/v1/extra",
    ):
        expect_error(lambda value=value: client.validate_api_base(value), f"unsafe base accepted: {value}")
    assert client.validate_api_base("https://arbiter.simppl.org/api/v1") == (
        "https://arbiter.simppl.org/api/v1"
    )
    assert client.validate_api_base("https://staging.example/api/v1") == (
        "https://staging.example/api/v1"
    )


def check_request_shape_and_secret_boundary(client) -> None:
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return FakeResponse({"items": [], "meta": {"request_id": "req_fixture"}})

    env = {
        "ARBITER_API_KEY": "member-secret",
        "ARBITER_API_BASE": "https://staging.example/api/v1",
    }
    arbiter = client.ArbiterClient.from_env(env, opener=opener)
    with patch.object(subprocess, "run", side_effect=AssertionError("shell transport used")):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("shell transport used")):
            payload = arbiter.request_json("GET", "/topics", query={"limit": 100})
    assert payload["items"] == []
    assert payload["meta"]["request_id"] == "req_fixture"
    request, timeout = seen[0]
    parsed = urlsplit(request.full_url)
    assert parsed.path == "/api/v1/topics", request.full_url
    assert parse_qs(parsed.query) == {"limit": ["100"]}, request.full_url
    assert request.data is None, "GET /topics must not carry a JSON body"
    assert request.get_header("Authorization") == "Bearer member-secret"
    assert timeout > 0


def check_sensitive_and_safe_paths(client) -> None:
    calls = []

    def opener(*_args):
        calls.append(True)
        return FakeResponse({})

    sensitive = client.ArbiterClient.from_env(
        {"ARBITER_API_KEY": "member-secret", "ARBITER_API_BASE": "https://staging.example/api/v1"},
        sensitive=True,
        opener=opener,
    )
    expect_error(
        lambda: sensitive.request_json("GET", "/topics", query={"limit": 1}),
        "sensitive mode must block before the opener",
    )
    assert not calls, "sensitive mode reached the live request opener"

    with tempfile.TemporaryDirectory() as raw:
        case_dir = Path(raw) / "case"
        research = case_dir / "research"
        research.mkdir(parents=True)
        safe = client.safe_research_path(case_dir, "arbiter-report-topic-2026.json")
        assert safe == research / "arbiter-report-topic-2026.json"
        for hostile in ("../outside.json", "nested/report.json", "/tmp/outside.json", "-bad.json"):
            expect_error(lambda hostile=hostile: client.safe_research_path(case_dir, hostile), hostile)
        outside = Path(raw) / "outside"
        outside.mkdir()
        (research / "linked").symlink_to(outside, target_is_directory=True)
        expect_error(
            lambda: client.safe_research_path(case_dir, "linked/escaped.json"),
            "symlink-parent escape accepted",
        )


def main() -> int:
    client = load_client()
    check_base_validation(client)
    check_request_shape_and_secret_boundary(client)
    check_sensitive_and_safe_paths(client)
    print("arbiter client boundary: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
