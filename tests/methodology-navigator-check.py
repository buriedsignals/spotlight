#!/usr/bin/env python3
"""Regression checks for the v2.1 methodology Navigator gate."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-methodology-navigator.py"
CTI_ACTIVE_SHA = json.loads(
    (ROOT / "upstreams" / "cti-expert" / "source.lock.json").read_text(encoding="utf-8")
)["active_sha"]
OLD_REVIEWED_SHA = "1" * 40


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(case_dir: Path, config_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(case_dir), "--config", str(config_path), *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def base_config(required: bool) -> dict:
    return {
        "model_tier": "frontier",
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
            },
        ],
        "tools_required": ["OpenCorporates"],
    }


def methodology_with_navigator() -> dict:
    response = "cases/sample/research/navigator-search-corporate-ownership-response.json"
    return {
        "schema_version": "1.0",
        "project": "sample",
        "skills_invoked": ["integrations", "osint", "investigation-methodology", "epistemic-grounding"],
        "navigator": {
            "required": True,
            "used": True,
            "status": "green",
            "queries": [
                {
                "direction": "corporate ownership trail",
					"mode": "tool_discovery",
					"interface": "cli",
					"command": "navigator tools find 'corporate ownership registry' --json",
					"catalog_id": "opencorporates",
					"retrieved_at": "2026-07-17T12:00:00Z",
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

        # Without --config the validator reads the install config the way
        # every Spotlight script does: SPOTLIGHT_CONFIG, else the checkout's.
        from_env = subprocess.run(
            [sys.executable, str(VALIDATOR), str(case_dir)],
            text=True,
            capture_output=True,
            check=False,
            cwd=case_dir,
            env={**os.environ, "SPOTLIGHT_CONFIG": str(config)},
        )
        if from_env.returncode != 0:
            raise AssertionError(f"SPOTLIGHT_CONFIG should name the config without --config\nSTDOUT:\n{from_env.stdout}\nSTDERR:\n{from_env.stderr}")

        legacy = methodology_with_navigator()
        legacy["skills_invoked"] = ["integrations", "osint", "investigate", "epistemic-grounding"]
        write_json(methodology_path, legacy)
        legacy_result = run(case_dir, config)
        if legacy_result.returncode != 0:
            raise AssertionError(
                "pre-rename skill id 'investigate' must still satisfy the planning-skill check"
                f"\nSTDOUT:\n{legacy_result.stdout}\nSTDERR:\n{legacy_result.stderr}"
            )

        # Data Navigator is retired. Cases planned before the retirement may
        # still carry navigator.data_sources (even incomplete results); the
        # gate must ignore them rather than reject the archived methodology.
        legacy_data = methodology_with_navigator()
        legacy_data["navigator"]["data_sources"] = [{
            "direction": "corporate ownership trail",
            "considered": ["company registry"],
            "selected": ["global/procurement"],
            "reason": "Planned before Data Navigator retirement.",
            "results": [{"source_id": "global/procurement", "interface": "cli"}],
        }]
        legacy_data["navigator"]["capabilities"] = ["osint_tools", "data_sources"]
        legacy_data["navigator"]["queries"][0]["mode"] = "data_source"
        legacy_data["investigation_plan"][0]["navigator_data_not_applicable_reason"] = "legacy"
        write_json(methodology_path, legacy_data)
        legacy_data_result = run(case_dir, config)
        if legacy_data_result.returncode != 0:
            raise AssertionError(
                "legacy navigator.data_sources must be ignored, not validated"
                f"\nSTDOUT:\n{legacy_data_result.stdout}\nSTDERR:\n{legacy_data_result.stderr}"
            )

        # Entitlement enables discovery; it does not force a lookup when the
        # task already has its sources, nor authorize egress forbidden by policy.
        for outcome, reason in [
            ("not-needed", "The brief supplies the two official release URLs; search/fetch suffice."),
            ("unavailable", "Navigator CLI is not installed; use the local catalogue if needed."),
            ("policy-skipped", "Sensitive case: external tool discovery is prohibited."),
        ]:
            skipped_on_demand = methodology_without_navigator()
            skipped_on_demand["navigator"] = {
                "required": False, "used": False, "outcome": outcome,
                "fallback_reason": reason, "queries": [],
            }
            write_json(methodology_path, skipped_on_demand)
            result = run(case_dir, config)
            if result.returncode:
                raise AssertionError(f"truthful {outcome} must pass on entitled install\n{result.stderr}")
            skipped_on_demand["navigator"]["fallback_reason"] = ""
            write_json(methodology_path, skipped_on_demand)
            result = run(case_dir, config)
            if result.returncode == 0 or "fallback_reason" not in result.stderr:
                raise AssertionError(f"{outcome} without reason must fail\n{result.stderr}")

        receipt_path = "research/navigator/12345678-1234-1234-1234-123456789abc-receipt.json"
        response_json = '{"tools":[{"id":"registry"}],"score":1e-7}'
        receipt = {
            "outcome": "used", "action": "find", "query": "corporate ownership registry",
            "argv": ["navigator", "tools", "find", "--json", "--", "corporate ownership registry"],
            "retrieved_at": "2026-11-01T12:00:00.000Z", "response_json": response_json,
            "response_sha256": hashlib.sha256(response_json.encode()).hexdigest(),
        }
        write_json(case_dir / receipt_path, receipt)
        recorded = methodology_without_navigator()
        recorded["navigator"] = {"required": False, "used": True, "outcome": "used", "queries": [{
            "interface": "cli", "receipt_path": receipt_path, "response_path": receipt_path,
            "command": json.dumps(receipt["argv"], separators=(",", ":")),
            "retrieved_at": receipt["retrieved_at"], "response_sha256": receipt["response_sha256"],
        }]}
        write_json(methodology_path, recorded)
        result = run(case_dir, config)
        if result.returncode:
            raise AssertionError(f"actual code-owned receipt should validate without invented catalogue/direction\n{result.stderr}")
        recorded["navigator"]["queries"][0]["retrieved_at"] = "invented"
        write_json(methodology_path, recorded)
        result = run(case_dir, config)
        if result.returncode == 0 or "provenance differs" not in result.stderr:
            raise AssertionError(f"invented receipt metadata must fail\n{result.stderr}")
        recorded["navigator"]["queries"][0]["retrieved_at"] = receipt["retrieved_at"]
        write_json(methodology_path, recorded)
        receipt["response_json"] = '{"tools":[]}'
        write_json(case_dir / receipt_path, receipt)
        result = run(case_dir, config)
        if result.returncode == 0 or "digest differs" not in result.stderr:
            raise AssertionError(f"tampered response bytes must fail\n{result.stderr}")

        fabricated_skip = methodology_with_navigator()
        fabricated_skip["navigator"].update(required=False, used=False, outcome="not-needed", fallback_reason="No lookup needed")
        write_json(methodology_path, fabricated_skip)
        result = run(case_dir, config)
        if result.returncode == 0 or "skipped" not in result.stderr:
            raise AssertionError(f"skipped lookup must not claim performed queries/steps\n{result.stderr}")

        malformed_used = methodology_with_navigator()
        malformed_used["navigator"]["queries"][0].pop("response_path")
        write_json(methodology_path, malformed_used)
        result = run(case_dir, config)
        if result.returncode == 0 or "response_path" not in result.stderr:
            raise AssertionError(f"performed discovery without response provenance must fail\n{result.stderr}")

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

        technical = methodology_without_navigator()
        technical["skills_invoked"] = ["technical-investigation"]
        write_json(methodology_path, technical)
        missing_provenance = run(case_dir, config)
        if missing_provenance.returncode == 0 or "requires non-empty method_provenance" not in missing_provenance.stderr:
            raise AssertionError("technical methodology without provenance should fail")
        technical["method_provenance"] = []
        write_json(methodology_path, technical)
        empty_provenance = run(case_dir, config)
        if empty_provenance.returncode == 0 or "requires non-empty method_provenance" not in empty_provenance.stderr:
            raise AssertionError("technical methodology with empty provenance should fail")

        # Base arm ia: provenance as an object keyed by skill, rewritten nine
        # times against two messages that read as a contradiction. It still
        # fails; the message says it must be a list and names the near-miss id.
        keyed = methodology_without_navigator()
        keyed["skills_invoked"] = ["osint", "technical_investigation"]
        keyed["method_provenance"] = {"technical_investigation": {"source": "fixture"}}
        write_json(methodology_path, keyed)
        keyed_result = run(case_dir, config)
        if keyed_result.returncode == 0:
            raise AssertionError("provenance keyed by skill should still fail")
        for expected in ("method_provenance must be a list of entries", "skills_invoked has 'technical_investigation'; the skill is named 'technical-investigation'"):
            if expected not in keyed_result.stderr:
                raise AssertionError(f"missing {expected!r}\nSTDERR:\n{keyed_result.stderr}")
        if "requires non-empty method_provenance" in keyed_result.stderr:
            raise AssertionError(f"the contradictory message is still emitted\nSTDERR:\n{keyed_result.stderr}")

        technical["method_provenance"] = [
            {
                "skill_id": "technical-investigation",
                "method": "infrastructure-history",
                "upstream": "7onez/cti-expert",
                "active_sha": CTI_ACTIVE_SHA,
                "reference": "infrastructure-history.md",
            }
        ]
        write_json(methodology_path, technical)
        valid_technical = run(case_dir, config)
        if valid_technical.returncode != 0:
            raise AssertionError(f"valid technical provenance should pass\n{valid_technical.stderr}")

        expanded = json.loads(json.dumps(technical))
        expanded["method_provenance"][0]["reference"] = "infrastructure-history-compact.md"
        write_json(methodology_path, expanded)
        too_compact = run(case_dir, config)
        if too_compact.returncode == 0 or "must be expanded for model_tier frontier: set reference to 'infrastructure-history.md'" not in too_compact.stderr:
            raise AssertionError(f"frontier compact reference should fail naming the expanded one\n{too_compact.stderr}")
        write_json(methodology_path, technical)

        local_config = base_config(required=False)
        local_config["model_tier"] = "12b"
        write_json(config, local_config)
        wrong_density = run(case_dir, config)
        if wrong_density.returncode == 0 or "must be compact for model_tier 12b" not in wrong_density.stderr:
            raise AssertionError("12b expanded reference should fail")

        if "set reference to 'infrastructure-history-compact.md', the bare file name for model_tier 12b" not in wrong_density.stderr:
            raise AssertionError(f"the tier error names the accepted reference\n{wrong_density.stderr}")
        # r5 norway on 704dcc78: a guessed path, then an unknown method, each
        # answered with the value that passes on this tier.
        for field, value, expected in (
            ("reference", "references/infrastructure-history.md", "does not match method 'infrastructure-history': set reference to 'infrastructure-history-compact.md'"),
            ("method", "infrastructure-triage", "method is not recognized; use one of 'indicator-triage', 'infrastructure-history'"),
        ):
            guessed = json.loads(json.dumps(technical))
            guessed["method_provenance"][0][field] = value
            write_json(methodology_path, guessed)
            refused = run(case_dir, config)
            if refused.returncode == 0 or expected not in refused.stderr:
                raise AssertionError(f"{field} {value!r} should be refused naming {expected!r}\n{refused.stderr}")

        technical["method_provenance"][0]["reference"] = "infrastructure-history-compact.md"
        write_json(methodology_path, technical)
        compact = run(case_dir, config)
        if compact.returncode != 0:
            raise AssertionError(f"12b compact reference should pass\n{compact.stderr}")

        technical["method_provenance"][0]["active_sha"] = "0" * 40
        write_json(methodology_path, technical)
        stale = run(case_dir, config)
        if stale.returncode == 0 or "not in the reviewed CTI revision catalog" not in stale.stderr:
            raise AssertionError("unreviewed CTI revision should fail")

        historical_catalog = tmp / "reviewed-revisions.json"
        catalog = json.loads(
            (ROOT / "upstreams" / "cti-expert" / "reviewed-revisions.json").read_text(encoding="utf-8")
        )
        catalog["revisions"].append({"sha": OLD_REVIEWED_SHA})
        write_json(historical_catalog, catalog)
        technical["method_provenance"][0]["active_sha"] = OLD_REVIEWED_SHA
        write_json(methodology_path, technical)
        historical = run(
            case_dir,
            config,
            "--cti-revisions",
            str(historical_catalog),
            "--allow-historical-cti",
        )
        if historical.returncode != 0:
            raise AssertionError(f"historical reviewed revision should remain valid\n{historical.stderr}")
        new_plan = run(
            case_dir,
            config,
            "--cti-revisions",
            str(historical_catalog),
        )
        if new_plan.returncode == 0 or "must match the current reviewed CTI revision" not in new_plan.stderr:
            raise AssertionError("new methodology should require the current reviewed revision")

    print("methodology navigator checks: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
