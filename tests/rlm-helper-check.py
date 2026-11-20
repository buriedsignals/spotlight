#!/usr/bin/env python3
"""Check RLM helper modes without requiring Ollama."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "rlm"))
from run_rlm import MODEL, get_case_dir, run, validate_analysis  # noqa: E402


def check_cases_root_from_install_config() -> None:
    """Without SPOTLIGHT_CASES_ROOT (a session not started by the launcher) the
    RLM writes under the install config's root, not the checkout's cases/."""
    saved = {name: os.environ.get(name) for name in ("SPOTLIGHT_CASES_ROOT", "SPOTLIGHT_CONFIG")}
    with tempfile.TemporaryDirectory() as raw:
        configured = Path(raw) / "configured-cases"
        config = Path(raw) / "spotlight-config.json"
        config.write_text(json.dumps({"case_workspace_root": str(configured)}), encoding="utf-8")
        os.environ.pop("SPOTLIGHT_CASES_ROOT", None)
        os.environ["SPOTLIGHT_CONFIG"] = str(config)
        try:
            assert get_case_dir("case-one") == (configured / "case-one").resolve(), get_case_dir("case-one")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    print("ok install-config cases root")


def main() -> int:
    check_cases_root_from_install_config()
    # The checks below write under the checkout's cases/; pin it so a local
    # install config cannot move them.
    os.environ["SPOTLIGHT_CASES_ROOT"] = str(ROOT / "cases")
    with tempfile.TemporaryDirectory(dir=ROOT / "cases") as tmp_dir:
        project = Path(tmp_dir).name
        research = Path(tmp_dir) / "research"
        research.mkdir()
        source = research / "source.md"
        source.write_text(
            "Alice used alice@example.com and @alice on 2026-06-04.\n"
            "See https://example.com/profile for context.\n"
            "The portal URL is https://example.org/northstar/harborfront.\n",
            encoding="utf-8",
        )
        request = Path(tmp_dir) / "request.json"
        request.write_text(json.dumps({"project": project, "run_id": "run-1", "mode": "off", "corpus_paths": []}), encoding="utf-8")
        off = run(request)
        assert off["status"] == "off" and off["analysis_path"] is None
        print("ok off mode")

        request.write_text(
            json.dumps({"project": project, "run_id": "run-2", "mode": "lite", "corpus_paths": ["research/source.md"]}),
            encoding="utf-8",
        )
        lite = run(request)
        analysis = json.loads(Path(lite["analysis_path"]).read_text(encoding="utf-8"))
        assert analysis["provider"] == "deterministic" and analysis["model"] is None
        assert not validate_analysis(analysis)
        assert all(a["verification_status"] == "needs_verification" for a in analysis["artifacts"])
        assert any(a["text"] == "alice@example.com" for a in analysis["artifacts"])
        assert any(a["text"] == "https://example.org/northstar/harborfront" for a in analysis["artifacts"])
        assert not any(a["text"] == "https://example.org/northstar/harborfront." for a in analysis["artifacts"])
        assert validate_analysis(
            {
                "schema_version": "1.0",
                "project": project,
                "run_id": "bad-shape",
                "mode": "local_gemma4_e4b",
                "provider": "ollama",
                "created_at": "2026-06-04T00:00:00+00:00",
                "artifacts": [
                    {
                        "lead": "Loose model output should not validate.",
                        "source_refs": [{"path": "research/source.md", "line": 1}],
                        "verification_status": "needs_verification",
                    }
                ],
            }
        )
        print("ok lite mode")

        os.environ["SPOTLIGHT_RLM_FAKE_LOCAL"] = "1"
        request.write_text(
            json.dumps({"project": project, "run_id": "run-3", "mode": "local_gemma4_e4b", "corpus_paths": ["research/source.md"]}),
            encoding="utf-8",
        )
        local = run(request)
        analysis = json.loads(Path(local["analysis_path"]).read_text(encoding="utf-8"))
        assert analysis["provider"] == "ollama" and analysis["model"] == MODEL
        assert not validate_analysis(analysis)
        print("ok local gemma4:e4b path")

        data_dir = Path(tmp_dir) / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "findings.json").write_text(
            json.dumps(
                {
                    "project": project,
                    "findings": [
                        {
                            "id": "finding-1",
                            "claim": "Fixture claim.",
                            "evidence": "Fixture evidence.",
                            "sources": [{"url": "https://example.com/profile"}],
                            "confidence": "low",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["python3", "scripts/validate-case.py", str(Path(tmp_dir)), "--include-rlm"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        print("ok validate-case --include-rlm")

    with tempfile.TemporaryDirectory() as workspace_dir:
        old_cases_root = os.environ.get("SPOTLIGHT_CASES_ROOT")
        os.environ["SPOTLIGHT_CASES_ROOT"] = workspace_dir
        try:
            project = "case-workspace"
            case_dir = Path(workspace_dir) / project
            research = case_dir / "research"
            research.mkdir(parents=True)
            (research / "source.md").write_text("Contact workspace@example.com.\n", encoding="utf-8")
            request = case_dir / "request.json"
            request.write_text(
                json.dumps({"project": project, "run_id": "run-env", "mode": "lite", "corpus_paths": ["research/source.md"]}),
                encoding="utf-8",
            )
            result = run(request)
            assert Path(result["analysis_path"]).resolve() == (case_dir / "data" / "rlm-analysis.json").resolve()
            analysis = json.loads(Path(result["analysis_path"]).read_text(encoding="utf-8"))
            assert any(a["text"] == "workspace@example.com" for a in analysis["artifacts"])
            print("ok SPOTLIGHT_CASES_ROOT rlm workspace")
        finally:
            if old_cases_root is None:
                os.environ.pop("SPOTLIGHT_CASES_ROOT", None)
            else:
                os.environ["SPOTLIGHT_CASES_ROOT"] = old_cases_root
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
