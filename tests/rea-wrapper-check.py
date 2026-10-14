#!/usr/bin/env python3
"""Check the bounded REA wrapper with a stand-in `rea` binary."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "rea"))
from run_rea import build_command, run, validate_request  # noqa: E402

FAKE_REA = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("FAKE_REA_VERSION", "6.4.0")); sys.exit(0)
with open(os.environ["FAKE_REA_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_REA_GARBAGE"):
    print("not json"); sys.exit(1)
print("{\\"rea_progress\\": {\\"terminal\\": true}}", file=sys.stderr)
print(json.dumps({"evidence_id": "ev_test", "subject": {"name": os.path.basename(args[1]), "digest": {"sha256": "abc"}}}))
"""


def rejects(payload: dict, needle: str) -> None:
    try:
        validate_request(payload)
    except Exception as exc:  # the wrapper raises IntegrationError (a ValueError)
        assert needle in str(exc), f"{needle!r} not in {exc}"
        return
    raise AssertionError(f"expected rejection containing {needle!r}: {payload}")


def main() -> int:
    with tempfile.TemporaryDirectory() as cases, tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as bin_dir:
        os.environ["SPOTLIGHT_CASES_ROOT"] = cases
        project = "case-one"
        evidence = Path(cases, project, "evidence")
        evidence.mkdir(parents=True)
        (evidence / "app.asar").write_bytes(b"asar")
        (evidence / "capture.har").write_text("{}")
        Path(outside, "secret.har").write_text("{}")
        (evidence / "link.har").symlink_to(Path(outside, "secret.har"))
        base = {"project": project, "run_id": "rea-1", "operation": "javascript-application", "input": "evidence/app.asar"}

        assert build_command(validate_request(base))[:2] == ["rea", "analyze-javascript-application"]
        har = validate_request({**base, "operation": "network-capture", "input": "evidence/capture.har", "capture_format": "har"})
        assert build_command(har)[1:] == ["inspect-web-network-capture", har["input_path"], "har", "--format", "json"]
        for blocked in ("capture-process", "capture-browser-scenario", "decompile", "setup", "update", "install-adb-package"):
            rejects({**base, "operation": blocked}, "operation must be one of")
        rejects({**base, "input": "../other-case/evidence/app.asar"}, "unsafe segment")
        rejects({**base, "input": str(evidence / "app.asar")}, "absolute paths")
        rejects({**base, "input": "evidence/link.har"}, "escapes")
        rejects({**base, "input": "evidence/missing.apk"}, "does not exist")
        rejects({**base, "operation": "network-capture", "input": "evidence/capture.har"}, "capture_format")
        rejects({**base, "capture_format": "har"}, "only to network-capture")
        print("ok request validation: closed operation set, case-contained inputs")

        fake = Path(bin_dir, "rea")
        fake.write_text(FAKE_REA)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        log = Path(bin_dir, "calls.jsonl")
        os.environ["FAKE_REA_LOG"] = str(log)
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

        request_path = Path(bin_dir, "request.json")
        request_path.write_text(json.dumps(base))
        result = run(request_path)
        assert result["status"] == "ok", result
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert calls == [["analyze-javascript-application", str((evidence / "app.asar").resolve()), "--format", "json"]], calls
        run_dir = Path(result["run_dir"])
        assert run_dir == Path(cases, project, "research", "rea", "rea-1").resolve()
        manifest = json.loads(Path(result["manifest"]).read_text())
        assert manifest["tool_version"] == "6.4.0" and manifest["warnings"] == []
        hashed = {item["path"]: item["sha256"] for item in manifest["outputs"]}
        assert set(hashed) == {"request.json", "evidence.json", "summary.json", "stderr.txt"}, hashed
        for name, digest in hashed.items():
            assert hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == digest, name
        summary = json.loads((run_dir / "summary.json").read_text())
        assert summary["evidence_id"] == "ev_test" and summary["verification_status"] == "unverified"
        print("ok run: one REA call, evidence saved and hashed")

        os.environ["FAKE_REA_VERSION"] = "7.0.0"
        os.environ["FAKE_REA_GARBAGE"] = "1"
        request_path.write_text(json.dumps({**base, "run_id": "rea-2"}))
        result = run(request_path)
        manifest = json.loads(Path(result["manifest"]).read_text())
        assert result["status"] == "error" and "did not return JSON" in manifest["errors"][0], manifest
        assert any("not the reviewed release" in warning for warning in manifest["warnings"]), manifest["warnings"]
        assert not (Path(result["run_dir"]) / "evidence.json").exists()
        print("ok non-JSON output is an error and version drift is a warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
