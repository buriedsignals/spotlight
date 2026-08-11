#!/usr/bin/env python3
"""Contract checks for Arbiter's member-owned Navigator routing."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations" / "arbiter" / "manifest.json"
INTEGRATION = ROOT / "integrations" / "arbiter" / "integration.md"
SKILL = ROOT / "skills" / "arbiter" / "SKILL.md"
ROUTER_SKILL = ROOT / "skills" / "integrations" / "SKILL.md"
API_DOCS = ROOT / "docs" / "arbiter-api.md"
DOCS_README = ROOT / "docs" / "README.md"
INTEGRATIONS_DOCS = ROOT / "docs" / "integrations.md"
INTEGRATIONS_README = ROOT / "integrations" / "README.md"
SOURCE_ID = "global/arbiter/case-studies"
SIGNUP_URL = (
    "https://arbiter.simppl.org/auth/register?"
    "eventSignup=5cce20c609334e538f07127322361862e3136e3d-"
    "324a-4c60-894a-5f42d2d57f8a"
)


def main() -> int:
    errors: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = INTEGRATION.read_text(encoding="utf-8")
    skill_text = SKILL.read_text(encoding="utf-8")
    router_skill_text = ROUTER_SKILL.read_text(encoding="utf-8")
    api_docs_text = API_DOCS.read_text(encoding="utf-8")
    docs_readme_text = DOCS_README.read_text(encoding="utf-8")
    integrations_docs_text = INTEGRATIONS_DOCS.read_text(encoding="utf-8")
    integrations_readme_text = INTEGRATIONS_README.read_text(encoding="utf-8")

    expected_manifest = {
        "type": "cli",
        "local_binary": "navigator",
        "requires_key": False,
        "env_vars": [],
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            errors.append(f"manifest.{field} must be {expected!r}")
    probes = manifest.get("probes") or []
    if not any(SOURCE_ID in probe.get("args", []) for probe in probes):
        errors.append("manifest must probe the deployed Navigator Arbiter source")
    if not any(
        probe.get("output_contains") == '"queryable": true'
        for probe in probes
    ):
        errors.append("manifest must require Arbiter to be queryable, not merely listed")
    if not any(
        probe.get("env", {}).get("DATANAV_CACHE_FALLBACK") == "off"
        for probe in probes
    ):
        errors.append("manifest must reject a stale cached Arbiter availability result")
    if "name: arbiter" not in skill_text or "Browse existing studies" not in skill_text:
        errors.append("skills/arbiter must provide the direct /arbiter browse/create entry point")
    if "navigator keys set arbiter" not in skill_text:
        errors.append("skills/arbiter must explain how to configure the BYO key")
    for path, content in (
        (SKILL, skill_text),
        (INTEGRATION, text),
        (API_DOCS, api_docs_text),
        (INTEGRATIONS_DOCS, integrations_docs_text),
        (INTEGRATIONS_README, integrations_readme_text),
    ):
        if SIGNUP_URL not in content:
            errors.append(f"{path.relative_to(ROOT)} must use the attributed Arbiter signup URL")

    onboarding_docs = (
        (SKILL, skill_text),
        (INTEGRATION, text),
        (API_DOCS, api_docs_text),
        (INTEGRATIONS_DOCS, integrations_docs_text),
        (INTEGRATIONS_README, integrations_readme_text),
    )
    owned_key_markers = (
        "member-owned",
        "member's own api key",
        "provide their own arbiter key",
        "stores their own key",
        "creates their own api key",
        "create your own api key",
    )
    no_shared_key_markers = (
        "no shared",
        "never receives or supplies a shared",
    )
    for path, content in onboarding_docs:
        normalized = " ".join(content.lower().split())
        relative_path = path.relative_to(ROOT)
        if not any(marker in normalized for marker in owned_key_markers):
            errors.append(f"{relative_path} must describe a member- or user-owned Arbiter key")
        if "navigator keys set arbiter" not in content:
            errors.append(f"{relative_path} must configure the Arbiter key locally with Navigator")
        if not any(marker in normalized for marker in no_shared_key_markers):
            errors.append(f"{relative_path} must state that Spotlight does not provide a shared key")

    stale_claims = (
        "discount code pending",
        "pending the member discount-code flow",
        "Navigator-hosted, operator-only",
        "requires Navigator administrator access",
        "Live access is currently blocked",
        "The source is currently blocked",
        "Arbiter is first-party",
        "first-party Arbiter API reference",
        "hosted-source quota",
        "hosted-query quota",
    )
    current_copy = "\n".join(
        (
            skill_text,
            router_skill_text,
            text,
            api_docs_text,
            docs_readme_text,
            integrations_docs_text,
            integrations_readme_text,
        )
    )
    for claim in stale_claims:
        if claim in current_copy:
            errors.append(f"Arbiter activation copy is stale: found {claim!r}")

    forbidden = ("ARBITER_API_KEY", "Authorization: Bearer", "curl ")
    for value in forbidden:
        if value in text:
            errors.append(f"integration must not call Arbiter directly: found {value!r}")

    required = (
        f"navigator data show {SOURCE_ID}",
        f"navigator query {SOURCE_ID}",
        '"operation":"topics"',
        '"operation":"posts"',
        '"operation":"entities"',
        '"operation":"themes"',
        '"operation":"report"',
        '"operation":"post"',
        '"operation":"agent_questions"',
        '"operation":"agent"',
        '"operation":"usage"',
        '"operation":"create"',
        '"operation":"search_plan"',
        '"operation":"finalize"',
        '"operation":"status"',
        '"operation":"progress"',
        "arbiter-report-",
        "confirmed",
    )
    for value in required:
        if value not in text:
            errors.append(f"integration is missing Navigator contract text {value!r}")

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("arbiter navigator routing contract: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
