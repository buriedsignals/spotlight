#!/usr/bin/env python3
"""Check integrations/preflight.py smoke_test() logic paths.

Network-free: API probes use an unroutable localhost port so connection
failures are immediate; CLI probes use binaries guaranteed present (sh)
or absent.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "integrations" / "preflight.py"

spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)
import _preflight_base as preflight_base  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"ok   {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}{' — ' + detail if detail else ''}")


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def run_temporary_preflight(
    manifest: dict,
    *args: str,
    env_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI against an isolated one-manifest integrations tree."""
    with tempfile.TemporaryDirectory(prefix="spotlight-preflight-") as temp_dir:
        integrations_dir = Path(temp_dir) / "integrations"
        manifest_dir = integrations_dir / manifest["id"]
        manifest_dir.mkdir(parents=True)
        shutil.copy2(SCRIPT, integrations_dir / "preflight.py")
        shutil.copy2(ROOT / "integrations" / "_preflight_base.py", integrations_dir / "_preflight_base.py")
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        env = os.environ.copy()
        for name in manifest.get("activation_env_vars", []):
            env.pop(name, None)
        env.update(env_updates or {})
        return subprocess.run(
            [sys.executable, str(integrations_dir / "preflight.py"), *args],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )


def main() -> int:
    # api: no URL configured → trivially ok
    ok, err = pf.smoke_test({"type": "api"})
    check("api: no homepage/docs URL passes", ok and err is None)

    # api: unreachable endpoint → failed with error detail
    ok, err = pf.smoke_test({"type": "api", "homepage": "http://127.0.0.1:1/"})
    check("api: connection failure reported", not ok and err is not None, f"ok={ok} err={err}")

    original_smoke_url = os.environ.get("TEST_SIGNER_URL")
    os.environ["TEST_SIGNER_URL"] = "http://signer.test.local/provenance/sign"
    try:
        with patch.object(pf.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
            ok, err = pf.smoke_test({
                "type": "api",
                "smoke_url_env": "TEST_SIGNER_URL",
                "homepage": "https://c2pa.org/",
            })
        requested_url = urlopen.call_args.args[0].full_url
        check(
            "api: configured smoke endpoint takes precedence over homepage",
            ok and err is None and requested_url == os.environ["TEST_SIGNER_URL"],
            f"ok={ok} err={err} url={requested_url}",
        )
    finally:
        if original_smoke_url is None:
            os.environ.pop("TEST_SIGNER_URL", None)
        else:
            os.environ["TEST_SIGNER_URL"] = original_smoke_url

    for status in (401, 403, 404):
        error = pf.urllib.error.HTTPError(
            "http://signer.test.local/provenance/sign",
            status,
            "probe failed",
            None,
            None,
        )
        with patch.object(pf.urllib.request, "urlopen", side_effect=error):
            ok, err = pf.smoke_test({
                "type": "api",
                "smoke_url": "http://signer.test.local/provenance/sign",
                "homepage": "https://c2pa.org/",
            })
        check(
            f"api: configured signer HTTP {status} fails",
            not ok and err == f"HTTP {status}",
            f"ok={ok} err={err}",
        )

    method_error = pf.urllib.error.HTTPError(
        "http://signer.test.local/provenance/sign",
        405,
        "method not allowed",
        None,
        None,
    )
    with patch.object(pf.urllib.request, "urlopen", side_effect=method_error):
        ok, err = pf.smoke_test({
            "type": "api",
            "smoke_url": "http://signer.test.local/provenance/sign",
        })
    check(
        "api: configured signer HTTP 405 proves reachability",
        ok and err == "HTTP 405",
        f"ok={ok} err={err}",
    )

    legacy_error = pf.urllib.error.HTTPError(
        "https://legacy.example.test/",
        404,
        "not found",
        None,
        None,
    )
    with patch.object(pf.urllib.request, "urlopen", side_effect=legacy_error):
        ok, err = pf.smoke_test({
            "type": "api",
            "homepage": "https://legacy.example.test/",
        })
    check(
        "api: legacy homepage HTTP 404 remains reachable",
        ok and err == "HTTP 404",
        f"ok={ok} err={err}",
    )

    # library: unknown integration id → no module mapping, passes
    ok, err = pf.smoke_test({"type": "library", "id": "unknown-lib"})
    check("library: unmapped id passes", ok and err is None)

    # library: mapped id with module that is not installed under this name
    ok, err = pf.smoke_test({"type": "library", "id": "browser-use"})
    check("library: mapped id returns boolean with error on miss",
          isinstance(ok, bool) and (ok or "import" in (err or "")))

    # cli: binary present, no version args
    ok, err = pf.smoke_test({"type": "cli", "id": "sh"})
    check("cli: present binary passes", ok and err is None)

    # cli: binary missing
    ok, err = pf.smoke_test({"type": "cli", "id": "definitely-not-a-binary-xyz"})
    check("cli: missing binary fails with PATH error", not ok and "not on PATH" in (err or ""))

    # cli: version check exits non-zero
    ok, err = pf.smoke_test({"type": "cli", "id": "sh", "version_args": ["-c", "exit 7"]})
    check("cli: failing version check fails with exit code", not ok and "exited 7" in (err or ""))

    # cli: version check succeeds
    ok, err = pf.smoke_test({"type": "cli", "id": "sh", "version_args": ["-c", "exit 0"]})
    check("cli: passing version check passes", ok and err is None)

    # cli: local_binary override respected
    ok, err = pf.smoke_test({"type": "cli", "id": "anything", "local_binary": "sh"})
    check("cli: local_binary override resolves", ok and err is None)

    # unknown type → assumed ok
    ok, err = pf.smoke_test({"type": "mcp", "id": "x"})
    check("mcp/unknown type assumed ok", ok and err is None)

    noosphere = {
        "id": "noosphere-c2pa",
        "name": "Noosphere C2PA",
        "requires_key": False,
        "env_vars": [],
        "activation_env_vars": ["NOOSPHERE_C2PA_URL"],
    }
    original_url = os.environ.pop("NOOSPHERE_C2PA_URL", None)
    try:
        report = preflight_base.build_report(noosphere)
        check(
            "activation env: absent optional signer is unconfigured",
            report["status"] == "unconfigured"
            and report["activation_env_vars_missing"] == ["NOOSPHERE_C2PA_URL"],
            str(report),
        )

        os.environ["NOOSPHERE_C2PA_URL"] = "http://localhost:5002/api/spotlight/provenance/sign"
        report = preflight_base.build_report(noosphere)
        check(
            "activation env: configured signer is green",
            report["status"] == "green"
            and report["activation_env_vars_set"] == ["NOOSPHERE_C2PA_URL"],
            str(report),
        )

        summary = preflight_base.summarize([
            report,
            {**report, "status": "unconfigured"},
        ])
        check(
            "summary: unconfigured integrations are counted separately",
            summary["green"] == 1 and summary["unconfigured"] == 1,
            str(summary),
        )

        cli_manifest = {**noosphere, "type": "api", "smoke_url_env": "NOOSPHERE_C2PA_URL"}
        result = run_temporary_preflight(cli_manifest, "--json")
        output = json.loads(result.stdout)
        check(
            "cli json: wholly unconfigured integrations exit zero",
            result.returncode == 0
            and output["integrations"][0]["status"] == "unconfigured"
            and output["summary"]["unconfigured"] == 1,
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )

        result = run_temporary_preflight(cli_manifest, "--text")
        check(
            "cli text: unconfigured activation variable is shown",
            result.returncode == 0
            and "unconfigured" in result.stdout
            and "NOOSPHERE_C2PA_URL" in result.stdout,
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )

        result = run_temporary_preflight(
            cli_manifest,
            "--json",
            env_updates={"NOOSPHERE_C2PA_URL": "http://127.0.0.1:1/sign"},
        )
        output = json.loads(result.stdout)
        check(
            "cli json: configured integration is green without smoke test",
            result.returncode == 0 and output["integrations"][0]["status"] == "green",
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )

        result = run_temporary_preflight(
            cli_manifest,
            "--json",
            "--smoke-test",
            env_updates={"NOOSPHERE_C2PA_URL": "http://127.0.0.1:1/provenance/sign"},
        )
        output = json.loads(result.stdout)
        check(
            "cli smoke: unreachable configured signer is yellow",
            result.returncode == 1
            and output["integrations"][0]["status"] == "yellow"
            and output["integrations"][0]["smoke_error"],
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    finally:
        if original_url is None:
            os.environ.pop("NOOSPHERE_C2PA_URL", None)
        else:
            os.environ["NOOSPHERE_C2PA_URL"] = original_url

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
