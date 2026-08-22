#!/usr/bin/env python3
"""Focused RED proof for Arbiter SSRF, redirect, and research-I/O races.

The checks use only stdlib fixtures, injected openers, monkeypatched resolver
and race hooks, and isolated temporary directories.  They never contact
Arbiter, read a keyring, or invoke a plugin runtime.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

client = importlib.import_module("integrations.arbiter.client")
workflow = importlib.import_module("integrations.arbiter.workflow")
run_create = importlib.import_module("integrations.arbiter.run_create")
runner = importlib.import_module("integrations._runner")


class RawClient:
    """Minimal response seam for workflow evidence writes."""

    def request_raw(self, method, path, *, query=None, body=None, timeout=None):
        return b'{"safe":true}'


def expect_rejected(call, message: str) -> None:
    try:
        call()
    except (OSError, ValueError, PermissionError):
        return
    raise AssertionError(message)


def check_base_rejects_alternate_numeric_and_dns_aliases() -> None:
    """No alternate numeric or private DNS result may reach authenticated setup."""
    for host in (
        "0x7f.0x0.0x0.0x1",
        "127.0.0.1%2e",
        "127.0.0.1.example",
    ):
        expect_rejected(
            lambda host=host: client.validate_api_base(f"https://{host}/api/v1"),
            f"alternate numeric host accepted: {host}",
        )

    addresses = (
        ("alias-loopback.example", "127.0.0.1"),
        ("alias-private.example", "10.0.0.8"),
        ("alias-link-local.example", "169.254.10.20"),
        ("alias-reserved.example", "240.0.0.1"),
        ("metadata-alias.example", "169.254.169.254"),
    )
    for host, address in addresses:
        with patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        ):
            expect_rejected(
                lambda host=host: client.validate_api_base(f"https://{host}/api/v1"),
                f"private DNS alias accepted: {host} -> {address}",
            )

def check_dns_rebinding_rejected_before_authenticated_request() -> None:
    """A DNS answer used during validation cannot rebind before the request."""
    answers = [
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    ]
    opened = False

    def opener(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise OSError("authenticated opener reached after DNS rebinding")

    with patch("socket.getaddrinfo", side_effect=answers) as resolver:
        arbiter = client.ArbiterClient(
            "https://rebind.example/api/v1",
            "fixture-secret",
            opener=opener,
        )
        expect_rejected(
            lambda: arbiter.request_raw("GET", "/topics"),
            "DNS rebinding accepted before authenticated request",
        )
        assert resolver.call_count == 2, "request did not revalidate its DNS answer"
    assert not opened, "authenticated opener reached after DNS rebinding"


def check_dns_failure_rejected_before_authenticated_request() -> None:
    """A resolver failure must fail closed before authenticated setup."""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS unavailable")):
        expect_rejected(
            lambda: client.validate_api_base("https://dns-failure.example/api/v1"),
            "DNS failure accepted as an allowed API base",
        )


def _redirect_request(target: str):
    source = Request("https://staging.example/api/v1/topics")
    source.add_header("Authorization", "Bearer fixture-secret")
    return client.SameOriginRedirectHandler().redirect_request(
        source, None, 302, "found", {}, target
    )


def check_redirect_path_scheme_host_and_bearer_policy() -> None:
    """Only same-origin API redirects may retain the bearer credential."""
    valid = _redirect_request("https://staging.example/api/v1/topics?cursor=next")
    assert valid is not None
    assert valid.get_header("Authorization") == "Bearer fixture-secret"

    for target in (
        "https://staging.example/admin",
        "https://staging.example/api/v10/topics",
        "https://staging.example/api/v1/../admin",
        "https://staging.example/api/v1%2f..%2fadmin",
        "http://staging.example/api/v1/topics",
        "https://evil.example/api/v1/topics",
    ):
        try:
            redirected = _redirect_request(target)
        except (OSError, ValueError, PermissionError):
            continue
        assert redirected is None or redirected.get_header("Authorization") is None, (
            f"unsafe redirect forwarded bearer: {target}"
        )


def _case_fixture(root: Path) -> tuple[Path, Path, Path]:
    case_dir = root / "case"
    research = case_dir / "research"
    outside = root / "outside"
    research.mkdir(parents=True)
    outside.mkdir()
    return case_dir, research, outside


def check_workflow_write_survives_research_swap() -> None:
    """Evidence writes must not follow a research-directory replacement."""
    with tempfile.TemporaryDirectory(prefix="arbiter-write-race-") as raw:
        case_dir, research, outside = _case_fixture(Path(raw))
        original = workflow.safe_research_path
        calls = 0

        def checked_then_swapped(case, filename):
            nonlocal calls
            candidate = original(case, filename)
            calls += 1
            if calls == 2:
                parked = Path(raw) / "research-original"
                research.rename(parked)
                research.symlink_to(outside, target_is_directory=True)
            return candidate

        with patch.object(workflow, "safe_research_path", side_effect=checked_then_swapped):
            try:
                workflow.browse(RawClient(), case_dir, timestamp="2026-08-22T10-00-00Z")
            except (OSError, ValueError, PermissionError):
                pass
        assert not any(outside.iterdir()), "research swap redirected workflow write outside case"


def check_run_create_read_survives_research_swap() -> None:
    """Validated input reads must not follow a replaced research directory."""
    with tempfile.TemporaryDirectory(prefix="arbiter-read-race-") as raw:
        case_dir, research, outside = _case_fixture(Path(raw))
        query = research / "query.txt"
        query.write_text("inside-fixture", encoding="utf-8")
        (outside / "query.txt").write_text("outside-secret", encoding="utf-8")
        original = run_create.contained_path
        swapped = False

        def checked_then_swapped(args, value, label):
            nonlocal swapped
            candidate = original(args, value, label)
            if not swapped:
                swapped = True
                parked = Path(raw) / "research-original"
                research.rename(parked)
                research.symlink_to(outside, target_is_directory=True)
            return candidate

        args = argparse.Namespace(
            case_dir=case_dir,
            query_file=query,
            platforms="reddit",
            from_date="2025-01-01T00:00:00Z",
            to_date="2025-01-15T00:00:00Z",
            title_file=None,
        )
        with patch.object(run_create, "contained_path", side_effect=checked_then_swapped):
            try:
                body = run_create.build_create_body(args)
            except (OSError, ValueError, PermissionError):
                return
        assert body["search_query"] != "outside-secret", "research swap redirected input read outside case"

def check_atomic_replacement_survives_parent_symlink_swap() -> None:
    """Atomic output must not report success after its parent is replaced."""
    with tempfile.TemporaryDirectory(prefix="arbiter-atomic-race-") as raw:
        case_dir, research, outside = _case_fixture(Path(raw))
        output = research / "output.json"
        original_rename = runner.os.rename
        swapped = False

        def swapped_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
            nonlocal swapped
            if not swapped:
                swapped = True
                parked = Path(raw) / "research-original"
                source_path = Path(source)
                parked_source = parked / source_path.name
                research.rename(parked)
                research.symlink_to(outside, target_is_directory=True)
                outside_source = outside / source_path.name
                outside_source.write_bytes(parked_source.read_bytes())
            return original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        with patch.object(runner.os, "rename", side_effect=swapped_rename):
            try:
                runner.write_json_atomic(output, {"safe": True})
            except (OSError, ValueError, PermissionError):
                pass
        assert swapped, "atomic race did not exercise the replacement seam"
        assert not (outside / output.name).exists(), "atomic replacement escaped through swapped parent"


def main() -> int:
    checks = (
        ("base validation", check_base_rejects_alternate_numeric_and_dns_aliases),
        ("DNS rebinding", check_dns_rebinding_rejected_before_authenticated_request),
        ("DNS failure", check_dns_failure_rejected_before_authenticated_request),
        ("redirect policy", check_redirect_path_scheme_host_and_bearer_policy),
        ("workflow write race", check_workflow_write_survives_research_swap),
        ("run_create read race", check_run_create_read_survives_research_swap),
        ("atomic replacement race", check_atomic_replacement_survives_parent_symlink_swap),
    )
    failures: list[str] = []
    for label, check in checks:
        try:
            check()
        except Exception as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("arbiter hardening: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
