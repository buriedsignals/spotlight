#!/usr/bin/env python3
"""Check shared integration runner safety properties."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))
from _runner import (  # noqa: E402
    IntegrationError,
    dispatch_integration,
    integration_run_dir,
    resolve_within,
    run_subprocess,
    write_json_atomic,
)


def expect_error(fn, label: str) -> None:
    try:
        fn()
    except IntegrationError:
        print(f"ok {label}")
        return
    raise AssertionError(f"expected IntegrationError: {label}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        expect_error(lambda: resolve_within(base, "/etc/passwd"), "absolute path rejected")
        expect_error(lambda: resolve_within(base, "../escape"), "traversal rejected")
        outside = base.parent / "outside-target"
        outside.write_text("x", encoding="utf-8")
        link = base / "link"
        os.symlink(outside, link)
        expect_error(lambda: resolve_within(base, "link"), "symlink escape rejected")

        run_dir = integration_run_dir("case-one", "maigret", "run-001", root=base)
        write_json_atomic(run_dir / "run-manifest.json", {"ok": True})
        data = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
        assert data["ok"] is True
        print("ok atomic manifest write")

        workspace = base / "workspace-cases"
        old_cases_root = os.environ.get("SPOTLIGHT_CASES_ROOT")
        os.environ["SPOTLIGHT_CASES_ROOT"] = str(workspace)
        try:
            env_run_dir = integration_run_dir("case-two", "rlm", "run-002")
            assert env_run_dir == (workspace / "case-two" / "research" / "rlm" / "run-002").resolve()
            print("ok SPOTLIGHT_CASES_ROOT integration workspace")
        finally:
            if old_cases_root is None:
                os.environ.pop("SPOTLIGHT_CASES_ROOT", None)
            else:
                os.environ["SPOTLIGHT_CASES_ROOT"] = old_cases_root

        proc = run_subprocess(["python3", "-c", "print('ok')"])
        assert proc.returncode == 0 and proc.stdout.strip() == "ok"
        print("ok subprocess argument list")

        request = base / "maigret-request.json"
        request.write_text(json.dumps({
            "project": "case-three",
            "run_id": "run-003",
            "usernames": ["example_user"],
        }), encoding="utf-8")
        old_cases_root = os.environ.get("SPOTLIGHT_CASES_ROOT")
        os.environ["SPOTLIGHT_CASES_ROOT"] = str(base / "dispatcher-cases")
        try:
            assert dispatch_integration(["maigret", str(request), "--dry-run"]) == 0
        finally:
            if old_cases_root is None:
                os.environ.pop("SPOTLIGHT_CASES_ROOT", None)
            else:
                os.environ["SPOTLIGHT_CASES_ROOT"] = old_cases_root
        print("ok closed maigret dispatcher")

        isolated = subprocess.run(
            [sys.executable, "-I", "-S", str(ROOT / "integrations" / "_runner.py"), "maigret", str(request), "--dry-run"],
            env={**os.environ, "SPOTLIGHT_CASES_ROOT": str(base / "isolated-dispatcher-cases")},
            text=True,
            capture_output=True,
            check=False,
        )
        assert isolated.returncode == 0, isolated.stderr
        print("ok isolated Engine dispatcher")

        expect_error(lambda: dispatch_integration(["../../shell", str(request)]), "dispatcher id traversal rejected")
        expect_error(lambda: dispatch_integration(["unknown", str(request)]), "unknown dispatcher id rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
