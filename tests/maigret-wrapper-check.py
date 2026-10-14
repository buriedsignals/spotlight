#!/usr/bin/env python3
"""Check bounded Maigret wrapper behavior without invoking Maigret."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "maigret"))
from run_maigret import build_command, run, validate_request  # noqa: E402


def write_request(tmp: Path, payload: dict) -> Path:
    path = tmp / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def assert_reject(payload: dict, label: str) -> None:
    try:
        validate_request(payload)
    except Exception:
        print(f"ok {label}")
        return
    raise AssertionError(f"expected reject: {label}")


def main() -> int:
    valid = validate_request(
        {
            "project": "case-one",
            "run_id": "20260604-username-scan",
            "usernames": ["example_user", "exampleuser"],
            "site_tags": ["social", "news"],
            "formats": ["json", "csv", "txt"],
        }
    )
    assert valid["scan_all_sites"] is False
    command = build_command(valid, Path("/tmp/out"))
    assert command[:3] == ["maigret", "example_user", "exampleuser"]
    assert "--ai" not in command and "-a" not in command
    assert "--json" in command and "--csv" in command and "--txt" in command
    print("ok command args")

    assert_reject({**valid, "scan_all_sites": True}, "full scan rejected")
    assert_reject({**valid, "use_ai": True}, "--ai rejected")
    assert_reject({**valid, "usernames": ["bad;name"]}, "invalid username rejected")
    assert_reject({**valid, "site_tags": ["bad tag"]}, "invalid tag rejected")

    with tempfile.TemporaryDirectory(dir=ROOT / "cases") as tmp_dir:
        tmp = Path(tmp_dir)
        request_path = write_request(tmp, {**valid, "project": Path(tmp_dir).name})
        result = run(request_path, dry_run=True)
        assert result["status"] == "dry_run"
        leads = json.loads(Path(result["normalized_leads"]).read_text(encoding="utf-8"))["leads"]
        assert leads == []
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        assert manifest["command"][0] == "maigret"
        assert_outputs_hashed(manifest, Path(result["run_dir"]), {"request.json", "normalized-leads.json"})
        print("ok dry-run artifacts")

    with tempfile.TemporaryDirectory(dir=ROOT / "cases") as tmp_dir, tempfile.TemporaryDirectory() as bin_dir:
        install_fake_maigret(Path(bin_dir))
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        request_path = write_request(Path(tmp_dir), {**valid, "project": Path(tmp_dir).name, "formats": ["json"]})
        result = run(request_path)
        assert result["status"] == "ok", result
        assert result["lead_count"] == 1
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        assert manifest["tool_version"] == "Maigret v0.4.4", manifest["tool_version"]
        assert_outputs_hashed(
            manifest,
            Path(result["run_dir"]),
            {"request.json", "normalized-leads.json", "maigret.json", "stdout.txt", "stderr.txt"},
        )
        print("ok live run records tool version and output hashes")
    return 0


def assert_outputs_hashed(manifest: dict, run_dir: Path, expected: set[str]) -> None:
    outputs = {item["path"]: item for item in manifest["outputs"]}
    assert set(outputs) == expected, sorted(outputs)
    for name, item in outputs.items():
        data = (run_dir / name).read_bytes()
        assert item["sha256"] == hashlib.sha256(data).hexdigest(), name
        assert item["bytes"] == len(data), name


def install_fake_maigret(bin_dir: Path) -> None:
    """A stand-in Maigret: prints a version, or writes one found account to --json."""
    script = bin_dir / "maigret"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        "    print('Maigret v0.4.4'); sys.exit(0)\n"
        "out = args[args.index('--json') + 1]\n"
        "json.dump({args[0]: {'Example': {'url_user': 'https://example.com/' + args[0], 'status': 'Claimed'}}}, open(out, 'w'))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


if __name__ == "__main__":
    raise SystemExit(main())
