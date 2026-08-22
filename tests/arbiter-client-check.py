#!/usr/bin/env python3
"""Executable safety contract for the native Arbiter HTTP seam.

The test intentionally names the smallest stdlib-first seam required by the
native integration. It uses an injected opener and never contacts Arbiter.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError
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


def injected_dns_fixture(client):
    """Resolve fixture hosts without weakening production DNS validation."""
    return patch.object(
        client.socket,
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )




def check_base_validation(client) -> None:
    for value in (
        "http://arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org/api/v1?x=1",
        "https://arbiter.simppl.org/api/v1#fragment",
        "https://user:pass@arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org:8443/api/v1",
        "https://arbiter.simppl.org/not-api-v1",
        "https://arbiter.simppl.org/api/v1/extra",
        "https://127.1/api/v1",
        "https://0177.0.0.1/api/v1",
        "https://127.0.0.1.nip.io/api/v1",
        "https://metadata.google.internal/api/v1",
    ):
        expect_error(lambda value=value: client.validate_api_base(value), f"unsafe base accepted: {value}")

    fake_bases = (
        "https://arbiter.simppl.org/api/v1",
        "https://staging.example/api/v1",
        "https://staging.arbiter.example/api/v1",
    )
    with patch.object(client.socket, "getaddrinfo", side_effect=socket.gaierror("fixture DNS unavailable")):
        for value in fake_bases[1:]:
            expect_error(
                lambda value=value: client.validate_api_base(value),
                f"unresolved fixture base accepted: {value}",
            )
    with injected_dns_fixture(client):
        for value in fake_bases:
            assert client.validate_api_base(value) == value


def check_request_shape_and_secret_boundary(client) -> None:
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return FakeResponse({"items": [], "meta": {"request_id": "req_fixture"}})
    env = {
        "ARBITER_API_KEY": "member-secret",
        "ARBITER_API_BASE": "https://staging.example/api/v1",
    }

    with injected_dns_fixture(client):
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
    for hostile_path in (
        "/../../admin",
        "/%2e%2e/%2e%2e/admin",
        "/topics/%2F..%2Fadmin",
    ):
        expect_error(
            lambda hostile_path=hostile_path: arbiter.request_json("GET", hostile_path),
            f"API traversal path accepted: {hostile_path}",
        )
    assert len(seen) == 1, "rejected request paths must not reach the opener"



def check_sensitive_and_safe_paths(client) -> None:
    calls = []

    def opener(*_args):
        calls.append(True)
        return FakeResponse({})

    with injected_dns_fixture(client):
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
        external_case = Path(raw) / "external-case"
        (external_case / "research").mkdir(parents=True)
        linked_case = Path(raw) / "linked-case"
        linked_case.symlink_to(external_case, target_is_directory=True)
        expect_error(
            lambda: client.safe_research_path(linked_case, "inside.json"),
            "symlinked case root must not establish the research boundary",
        )


def check_http_error_contract(client) -> None:
    """Preserve Arbiter error envelopes and headers, and never retry blindly."""
    raw = b'{"error":{"code":"rate_limited","message":"slow down","request_id":"req-1"}}'
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        raise HTTPError(
            request.full_url, 429, "rate limited",
            {"Retry-After": "30", "X-Request-ID": "req-1"},
            io.BytesIO(raw),
        )

    with injected_dns_fixture(client):
        arbiter = client.ArbiterClient.from_env(
            {"ARBITER_API_KEY": "fixture-secret", "ARBITER_API_BASE": "https://staging.example/api/v1"},
            opener=opener,
        )
        try:
            arbiter.request_json("GET", "/topics", query={"limit": 1})
        except HTTPError as error:
            assert error.code == 429
            assert error.headers["Retry-After"] == "30"
            assert error.read() == raw
        else:
            raise AssertionError("HTTPError envelope was swallowed")
    assert len(calls) == 1, "rate-limited requests must not be retried automatically"


def check_redirect_origin_policy() -> None:
    """The authenticated opener must revalidate redirects before forwarding bearer auth."""
    source = CLIENT.read_text(encoding="utf-8")
    assert "HTTPRedirectHandler" in source, "redirect policy is not installed"
    assert "Authorization" in source and "same-origin" in source.lower(), (
        "redirect policy must strip bearer credentials across origins"
    )


def main() -> int:
    client = load_client()
    check_base_validation(client)
    check_request_shape_and_secret_boundary(client)
    check_http_error_contract(client)
    check_redirect_origin_policy()
    check_sensitive_and_safe_paths(client)
    print("arbiter client boundary: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
