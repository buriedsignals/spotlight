#!/usr/bin/env python3
"""Validate Spotlight's Every-style plugin distribution payload."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "spotlight"


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - failure path prints detail
        fail(f"{path}: invalid JSON: {exc}")


def assert_file(path: Path) -> None:
    if not path.is_file():
        fail(f"missing file: {path.relative_to(ROOT)}")


def assert_dir(path: Path) -> None:
    if not path.is_dir():
        fail(f"missing directory: {path.relative_to(ROOT)}")


def compare_file(source: Path, copied: Path) -> None:
    assert_file(source)
    assert_file(copied)
    if source.read_bytes() != copied.read_bytes():
        fail(f"stale plugin payload copy: {copied.relative_to(ROOT)} differs from {source.relative_to(ROOT)}")


def compare_tree(source: Path, copied: Path, patterns: tuple[str, ...]) -> None:
    for source_file in sorted(path for pattern in patterns for path in source.rglob(pattern) if path.is_file()):
        rel = source_file.relative_to(source)
        if any(part in {"__pycache__", ".pytest_cache"} for part in rel.parts):
            continue
        compare_file(source_file, copied / rel)


def validate_marketplaces() -> None:
    claude_market = load_json(ROOT / ".claude-plugin" / "marketplace.json")
    agents_market = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude_plugin = load_json(PLUGIN / ".claude-plugin" / "plugin.json")
    codex_plugin = load_json(PLUGIN / ".codex-plugin" / "plugin.json")

    claude_entries = {entry["name"]: entry for entry in claude_market.get("plugins", [])}
    agents_entries = {entry["name"]: entry for entry in agents_market.get("plugins", [])}
    if "spotlight" not in claude_entries:
        fail(".claude-plugin marketplace missing spotlight entry")
    if "spotlight" not in agents_entries:
        fail(".agents marketplace missing spotlight entry")
    if claude_entries["spotlight"].get("source") != "./plugins/spotlight":
        fail(".claude-plugin marketplace spotlight source must be ./plugins/spotlight")

    source = agents_entries["spotlight"].get("source", {})
    if source.get("source") != "local" or source.get("path") != "./plugins/spotlight":
        fail(".agents marketplace spotlight source must be local ./plugins/spotlight")

    for key in ("name", "version", "description"):
        if claude_plugin.get(key) != codex_plugin.get(key):
            fail(f"plugin metadata drift for {key}")
    if codex_plugin.get("skills") != "./skills/":
        fail("Codex plugin metadata must declare skills: ./skills/")
    if not (PLUGIN / "skills").is_dir():
        fail("Codex plugin declares skills but plugins/spotlight/skills is missing")


def validate_payload_sync() -> None:
    # Skill bundles include executable helpers as well as instructions. Keeping
    # both in the payload comparison prevents a plugin release from advertising
    # a portable skill while omitting or drifting its bundled safety helper.
    compare_tree(ROOT / "skills", PLUGIN / "skills", ("*.md", "*.py"))
    compare_tree(ROOT / "agents", PLUGIN / "agents", ("*.md",))
    compare_tree(ROOT / "schemas", PLUGIN / "schemas", ("*.json",))
    compare_tree(ROOT / "scripts", PLUGIN / "scripts", ("*.py",))
    compare_tree(ROOT / "monitoring", PLUGIN / "monitoring", ("*.py", ".gitkeep"))
    compare_tree(ROOT / "third_party", PLUGIN / "third_party", ("*",))
    compare_tree(ROOT / "upstreams", PLUGIN / "upstreams", ("*.json", "*.md"))
    compare_file(ROOT / "AGENTS.md", PLUGIN / "AGENTS.md")
    compare_file(ROOT / "VALIDATED_DEPENDENCIES.md", PLUGIN / "VALIDATED_DEPENDENCIES.md")
    compare_file(ROOT / "skills-manifest.json", PLUGIN / "skills-manifest.json")
    compare_file(ROOT / "NOTICE.md", PLUGIN / "NOTICE.md")

    for copied_doc in sorted(path for path in (PLUGIN / "docs").iterdir() if path.is_file()):
        compare_file(ROOT / "docs" / copied_doc.name, copied_doc)

    # Full Python seam packages that back the sovereign verbs (U2b): the plugin
    # ships the whole `scraping`/`search` package so `python -m integrations.*`
    # runs (Crawl4AI/SearXNG). __pycache__ is excluded by the build.
    seam_packages = {"scraping", "search"}
    arbiter_runtime_modules = {"client.py", "credentials.py", "workflow.py"}
    for copied in sorted(path for path in (PLUGIN / "integrations").rglob("*") if path.is_file()):
        allowed_root = copied.parent == PLUGIN / "integrations" and copied.name in {
            "README.md",
            "_preflight_base.py",
            "_runner.py",
            "preflight.py",
        }
        allowed_integration = copied.parent.parent == PLUGIN / "integrations" and (
            copied.name in {"integration.md", "manifest.json"} or copied.name.startswith("run_") and copied.suffix == ".py"
        )
        allowed_seam = (
            copied.parent.parent == PLUGIN / "integrations"
            and copied.parent.name in seam_packages
            and copied.suffix == ".py"
        )
        allowed_arbiter_runtime = (
            copied.parent == PLUGIN / "integrations" / "arbiter"
            and copied.name in arbiter_runtime_modules
        )
        if not (allowed_root or allowed_integration or allowed_seam or allowed_arbiter_runtime):
            fail(f"plugin integration payload includes non-contract file: {copied.relative_to(ROOT)}")
        compare_file(ROOT / copied.relative_to(PLUGIN), copied)


def validate_boundaries() -> None:
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    if "does not install runtime packages" not in readme:
        fail("plugin README must state that plugin install does not install runtime packages")
    if "https://buriedsignals.com/join" not in readme or "VALIDATED_DEPENDENCIES.md" not in readme:
        fail("plugin README must point runtime installs at Indicator Labs join and dependency pins")
    if "lead-only and never verified or publishable evidence" not in readme:
        fail("plugin README must preserve RLM evidence boundary")
    pilot_path = PLUGIN / "docs" / "source-expression-pilot-results.json"
    pilot = load_json(pilot_path)
    if pilot.get("activation_recommendation") != "NOT APPROVED":
        fail("source-expression pilot must not approve activation without measured evidence")
    alternatives = {item.get("id"): item for item in pilot.get("alternatives", [])}
    expected_alternatives = {
        "separate_source_expressions_artifact",
        "embedded_in_findings",
        "richer_evidence_and_fact_check_records",
    }
    if set(alternatives) != expected_alternatives:
        fail("source-expression pilot must compare all three storage alternatives")
    for name, alternative in alternatives.items():
        metrics = alternative.get("metrics", {})
        required_metrics = {
            "reviewer_correction_yield",
            "reviewer_time",
            "duplication",
            "write_failures",
            "unresolved_links",
            "locator_stability",
            "migration_effort",
        }
        if set(metrics) != required_metrics:
            fail(f"source-expression pilot metrics incomplete for {name}")
    separate_metrics = alternatives["separate_source_expressions_artifact"]["metrics"]
    if separate_metrics["reviewer_correction_yield"].get("status") != "unmeasured":
        fail("source-expression pilot must not fabricate reviewer correction yield")
    if separate_metrics["reviewer_time"].get("status") != "unmeasured":
        fail("source-expression pilot must not fabricate reviewer time")

    spotlight_skill = (PLUGIN / "skills" / "spotlight" / "SKILL.md").read_text(encoding="utf-8")
    if "Do not enable\n`1.1` as the new-case default" not in spotlight_skill:
        fail("plugin orchestrator must keep new-case source-expression activation disabled")
    for relative in (
        "schemas/source-expressions.schema.json",
        "schemas/case-contract.schema.json",
        "schemas/source-expression-migration.schema.json",
        "scripts/migrate-source-expressions.py",
        "schemas/knowledge-batch.schema.json",
        "schemas/case-policy-receipt.schema.json",
        "schemas/knowledge-workspace-package.schema.json",
        "schemas/projection-job.schema.json",
        "schemas/projection-manifest.schema.json",
        "scripts/knowledge_destination.py",
        "scripts/knowledge_projection.py",
        "scripts/query_vault.py",
        "scripts/validate-install-config.py",
        "docs/knowledge-destination.md",
    ):
        assert_file(PLUGIN / relative)

    forbidden = [
        PLUGIN / "cases",
        PLUGIN / "container",
        PLUGIN / ".env",
        PLUGIN / ".venv",
        PLUGIN / ".firecrawl",
        PLUGIN / "docs" / "plans",
        PLUGIN / "tests",
        PLUGIN / "evals",
    ]
    for path in forbidden:
        if path.exists():
            fail(f"forbidden runtime payload path present: {path.relative_to(ROOT)}")

    generated = PLUGIN / "GENERATED_FROM_ROOT.txt"
    if not generated.is_file():
        fail("plugin payload missing GENERATED_FROM_ROOT.txt")


def main() -> int:
    assert_dir(PLUGIN)
    validate_marketplaces()
    validate_payload_sync()
    validate_boundaries()
    print("plugin distribution: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
