#!/usr/bin/env python3
"""Contract checks for Arbiter's native member-owned API routing.

The transport assertions intentionally replace the former Navigator probe.  The
preflight checks use a mocked OpenAPI response; no network or member credential
is used.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations" / "arbiter" / "manifest.json"
INTEGRATION = ROOT / "integrations" / "arbiter" / "integration.md"
SKILL = ROOT / "skills" / "arbiter" / "SKILL.md"
ROUTER_SKILL = ROOT / "skills" / "integrations" / "SKILL.md"
API_DOCS = ROOT / "docs" / "arbiter-api.md"
DOCS_README = ROOT / "docs" / "README.md"
INTEGRATIONS_DOCS = ROOT / "docs" / "integrations.md"
CHANGELOG = ROOT / "CHANGELOG.md"
PLUGIN_INTEGRATION = ROOT / "plugins" / "spotlight" / "integrations" / "arbiter" / "integration.md"
PLUGIN_SKILL = ROOT / "plugins" / "spotlight" / "skills" / "arbiter" / "SKILL.md"
INTEGRATIONS_README = ROOT / "integrations" / "README.md"
PREFLIGHT = ROOT / "integrations" / "preflight.py"
SIGNUP_URL = (
    "https://arbiter.simppl.org/auth/register?"
    "eventSignup=5cce20c609334e538f07127322361862e3136e3d-"
    "324a-4c60-894a-5f42d2d57f8a"
)


class FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def load_preflight():
    spec = importlib.util.spec_from_file_location("spotlight_preflight", PREFLIGHT)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load integrations/preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_manifest(manifest: dict, errors: list[str]) -> None:
    expected = {
        "type": "api",
        "requires_key": True,
        "env_vars": ["ARBITER_API_KEY"],
        "smoke_url": "https://arbiter.simppl.org/api/v1/openapi.json",
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            errors.append(f"manifest.{field} must be {value!r}")
    if "ARBITER_API_BASE" not in (manifest.get("optional_env_vars") or []):
        errors.append("manifest.optional_env_vars must expose ARBITER_API_BASE")


def check_onboarding(docs: list[tuple[Path, str]], errors: list[str]) -> None:
    owned_markers = (
        "member-owned",
        "member's own api key",
        "provide their own arbiter key",
        "creates their own api key",
        "create your own api key",
    )
    for path, content in docs:
        normalized = " ".join(content.lower().split())
        relative = path.relative_to(ROOT)
        if SIGNUP_URL not in content:
            errors.append(f"{relative} must retain the attributed signup URL")
        if not any(marker in normalized for marker in owned_markers):
            errors.append(f"{relative} must describe a member-owned API key")
        if "ARBITER_API_KEY" not in content:
            errors.append(f"{relative} must name ARBITER_API_KEY")
        if not any(marker in normalized for marker in ("no shared", "no shared spotlight")):
            errors.append(f"{relative} must state that Spotlight has no shared key")
        if any(secret in content for secret in ("sk-", "Bearer test", "api_key=\"")):
            errors.append(f"{relative} must not contain key material")


def check_stale_claims(docs: list[tuple[Path, str]], errors: list[str]) -> None:
    current_copy = "\n".join(content for _path, content in docs)
    stale_claims = (
        "Navigator-hosted, operator-only",
        "requires Navigator administrator access",
        "hosted-source quota",
        "hosted-query quota",
        "Data Navigator's BYO-key source",
    )
    for claim in stale_claims:
        if claim in current_copy:
            errors.append(f"Arbiter activation copy is stale: found {claim!r}")


def check_wire_contract(text: str, skill_text: str, errors: list[str]) -> None:
    """Reject obsolete confirmation fields and unsafe shell transport recipes."""
    create_start = text.find("### 1. Create a pending study")
    plan_start = text.find("### 2. Generate the search plan", create_start)
    finalize_start = text.find("### 4. Finalize", plan_start)
    progress_start = text.find("### 5. Progress", finalize_start)
    create_section = text[create_start:plan_start]
    finalize_section = text[finalize_start:progress_start]
    if re.search(r"confirmed\s*:\s*true", create_section, re.IGNORECASE):
        errors.append("create wire instructions must not send confirmed")
    if re.search(r"confirmed\s*:\s*true", finalize_section, re.IGNORECASE):
        errors.append("finalize wire instructions must not send confirmed")
    if "human review" not in text.lower() or "explicit" not in text.lower():
        errors.append("local human confirmation gate must remain documented")
    for marker in ('${ARBITER_API_KEY}', '$ARBITER_API_KEY', "curl --"):
        if marker in text or marker in skill_text:
            errors.append(f"secret-bearing shell transport marker remains: {marker}")
    if "in-process" not in (text + "\n" + skill_text).lower():
        errors.append("native request seam must read the API key in-process")


def check_topics_query(text: str, errors: list[str]) -> None:
    """Require GET /topics limit to be encoded as query parameters."""
    if not re.search(r"GET\s+/topics[^\n]*\?[^`\n]*limit=100", text):
        errors.append("GET /topics must encode limit=100 in the query string")
    if re.search(r"Write\s+\{\s*[\"']limit[\"']\s*:\s*100\s*\}", text):
        errors.append("GET /topics must not describe a JSON request body")


def check_native_instructions(text: str, skill_text: str, errors: list[str]) -> None:
    forbidden = (
        "navigator query",
        "navigator data show",
        "navigator keys set arbiter",
        "Data Navigator",
        "global/arbiter/case-studies",
    )
    for marker in forbidden:
        if marker.lower() in (text + "\n" + skill_text).lower():
            errors.append(f"Arbiter instructions retain Navigator transport marker {marker!r}")
    required = (
        "ARBITER_API_KEY",
        "ARBITER_API_BASE",
        "openapi.json",
        "Authorization: Bearer",
        "file-backed",
        "input-file",
        "output",
        "untrusted",
        "shell",
        "never request or log",
        "raw",
        "case_study_id",
        "post_id",
        "search-plan",
        "finalize",
        "confirmed",
    )
    combined = text + "\n" + skill_text
    for marker in required:
        if marker not in combined:
            errors.append(f"native instructions are missing direct API marker {marker!r}")
    for path, marker in (
        (text, "GET /topics"),
        (text, "POST /case-studies"),
        (text, "/search-plan"),
        (text, "/finalize"),
        (text, "/progress"),
    ):
        if marker not in path:
            errors.append(f"native integration is missing workflow marker {marker!r}")


def check_preflight(manifest: dict, errors: list[str]) -> None:
    pf = load_preflight()
    expected_url = manifest.get("smoke_url", "https://arbiter.simppl.org/api/v1/openapi.json")
    with patch.object(pf.urllib.request, "urlopen", return_value=FakeResponse(200)) as urlopen:
        ok, err = pf.smoke_test(manifest)
    requested = urlopen.call_args.args[0].full_url if urlopen.call_args else None
    if not ok or err is not None:
        errors.append(f"OpenAPI smoke should pass with HTTP 200: ok={ok} err={err}")
    if requested != expected_url:
        errors.append(f"preflight must request OpenAPI URL, got {requested!r}")
    failure = pf.urllib.error.HTTPError(expected_url, 401, "unauthorized", None, None)
    with patch.object(pf.urllib.request, "urlopen", side_effect=failure):
        ok, err = pf.smoke_test(manifest)
    if ok or err != "HTTP 401":
        errors.append(f"OpenAPI auth failure should fail preflight: ok={ok} err={err}")

    override = {**manifest, "smoke_url": None, "smoke_url_env": "ARBITER_API_BASE"}
    override_base = "https://staging.arbiter.example/api/v1"
    arbiter_client = importlib.import_module("arbiter.client")
    fixture_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    with patch.object(arbiter_client.socket, "getaddrinfo", return_value=fixture_dns):
        with patch.dict(os.environ, {"ARBITER_API_BASE": override_base}, clear=False):
            with patch.object(pf.urllib.request, "urlopen", return_value=FakeResponse(200)) as probe:
                ok, err = pf.smoke_test(override)
    override_url = probe.call_args.args[0].full_url if probe.call_args else None
    if not ok or err is not None or override_url != override_base + "/openapi.json":
        errors.append(
            "preflight must probe configured ARBITER_API_BASE override: "
            f"ok={ok} err={err} url={override_url!r}"
        )

    for invalid in (
        "http://arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org/api/v1?query=1",
        "https://user:pass@arbiter.simppl.org/api/v1",
        "https://arbiter.simppl.org:8443/api/v1",
        "https://arbiter.simppl.org/not-api-v1",
    ):
        with patch.dict(os.environ, {"ARBITER_API_BASE": invalid}, clear=False):
            with patch.object(pf.urllib.request, "urlopen", return_value=FakeResponse(200)) as probe:
                ok, _err = pf.smoke_test(override)
        if ok or probe.called:
            errors.append(f"unsafe ARBITER_API_BASE must be rejected before network: {invalid}")


def main() -> int:
    errors: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = INTEGRATION.read_text(encoding="utf-8")
    skill_text = SKILL.read_text(encoding="utf-8")
    docs = [
        (SKILL, skill_text),
        (INTEGRATION, text),
        (API_DOCS, API_DOCS.read_text(encoding="utf-8")),
        (INTEGRATIONS_DOCS, INTEGRATIONS_DOCS.read_text(encoding="utf-8")),
        (INTEGRATIONS_README, INTEGRATIONS_README.read_text(encoding="utf-8")),
        (PLUGIN_INTEGRATION, PLUGIN_INTEGRATION.read_text(encoding="utf-8")),
        (PLUGIN_SKILL, PLUGIN_SKILL.read_text(encoding="utf-8")),
    ]
    claim_docs = docs + [(CHANGELOG, CHANGELOG.read_text(encoding="utf-8"))]
    check_manifest(manifest, errors)
    check_onboarding(docs, errors)
    check_stale_claims(claim_docs, errors)
    check_native_instructions(text, skill_text, errors)
    check_wire_contract(text, skill_text, errors)
    check_topics_query(text, errors)
    check_preflight(manifest, errors)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("arbiter native API routing contract: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
