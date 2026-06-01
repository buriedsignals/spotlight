#!/usr/bin/env python3
"""Regression checks for the v2.1 methodology Navigator gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-methodology-navigator.py"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(case_dir: Path, config_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(case_dir), "--config", str(config_path)],
        text=True,
        capture_output=True,
        check=False,
    )


def base_config(required: bool) -> dict:
    return {
        "search_library": "firecrawl",
        "vault_path": "./vault",
        "vault_type": "directory",
        "cases_root": "cases/",
        "integrations": {
            "osint_navigator": {
                "status": "green" if required else "red",
                "checked_at": "2026-06-01T12:00:00Z",
                "source": "integrations/preflight.py --json",
                "required_in_phase_2": required,
            }
        },
    }


def methodology_without_navigator() -> dict:
    return {
        "schema_version": "1.0",
        "project": "sample",
        "investigation_plan": [
            {
                "direction": "corporate ownership trail",
                "questions": ["Who owns Example Corp?"],
                "steps": [{"order": 1, "action": "Search registries", "tool": "search"}],
            }
        ],
        "tools_required": ["OpenCorporates"],
    }


def methodology_with_navigator() -> dict:
    response = "cases/sample/research/navigator-search-corporate-ownership-response.json"
    return {
        "schema_version": "1.0",
        "project": "sample",
        "skills_invoked": ["integrations", "osint", "investigate", "epistemic-grounding"],
        "navigator": {
            "required": True,
            "used": True,
            "status": "green",
            "queries": [
                {
                    "direction": "corporate ownership trail",
                    "endpoint": "/api/tools/search",
                    "request_path": "cases/sample/research/navigator-search-corporate-ownership.json",
                    "response_path": response,
                    "selected_tools": ["OpenCorporates", "OCCRP Aleph"],
                    "rejected_tools": [{"tool": "Example Tool", "reason": "not relevant"}],
                }
            ],
            "fallback_used": False,
            "fallback_reason": "",
        },
        "investigation_plan": [
            {
                "direction": "corporate ownership trail",
                "questions": ["Who owns Example Corp?"],
                "steps": [
                    {
                        "order": 1,
                        "action": "Search corporate registries recommended by Navigator",
                        "tool": "execute-shell",
                        "tool_source": "navigator",
                        "navigator_response_path": response,
                    }
                ],
            }
        ],
        "tools_required": ["Navigator: OpenCorporates", "OCCRP Aleph"],
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        config = tmp / ".spotlight-config.json"
        case_dir = tmp / "cases" / "sample"
        methodology_path = case_dir / "data" / "methodology.json"

        write_json(config, base_config(required=True))
        write_json(methodology_path, methodology_without_navigator())
        missing = run(case_dir, config)
        if missing.returncode == 0 or "navigator block is required" not in missing.stderr:
            raise AssertionError(f"missing navigator fixture should fail\nSTDOUT:\n{missing.stdout}\nSTDERR:\n{missing.stderr}")

        write_json(methodology_path, methodology_with_navigator())
        valid = run(case_dir, config)
        if valid.returncode != 0:
            raise AssertionError(f"valid navigator fixture should pass\nSTDOUT:\n{valid.stdout}\nSTDERR:\n{valid.stderr}")

        write_json(config, base_config(required=False))
        skipped = methodology_without_navigator()
        skipped["navigator"] = {
            "required": False,
            "used": False,
            "fallback_used": True,
            "fallback_reason": "preflight status red",
        }
        write_json(methodology_path, skipped)
        fallback = run(case_dir, config)
        if fallback.returncode != 0:
            raise AssertionError(f"navigator-not-required fixture should pass\nSTDOUT:\n{fallback.stdout}\nSTDERR:\n{fallback.stderr}")

    print("methodology navigator checks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
