#!/usr/bin/env python3
"""Preflight checker for external tool integrations.

Scans every integration manifest under integrations/, checks each
integration's required env vars, reports per-integration status:
green (ready), yellow (configured but smoke test failed), red (missing
required credentials), or unconfigured (an optional integration has not
been activated).

Shared machinery lives in integrations/_preflight_base.py.

Usage:
    python3 integrations/preflight.py [--smoke-test] [--json|--text]

Exit code:
    0 — at least one integration green, or all integrations unconfigured
    1 — all integrations red (nothing queryable)
"""

from __future__ import annotations

import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Import the shared helpers from integrations/ — local single source of truth
_BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(_BASE_DIR))
from _preflight_base import run_preflight  # noqa: E402

def _arbiter_components():
    """Load native Arbiter seams only when an Arbiter operation is requested."""
    from arbiter.client import ArbiterClient
    from arbiter.credentials import resolve_spotlight_arbiter_key
    from arbiter.workflow import browse, progress, read, report, reviewed_create

    return ArbiterClient, resolve_spotlight_arbiter_key, browse, progress, read, report, reviewed_create


def build_arbiter_client(
    env: dict[str, str] | None = None,
    *,
    read_spotlight_key=None,
    sensitive: bool = False,
    opener=None,
) -> "ArbiterClient":
    """Construct the native client through Spotlight's credential boundary."""
    (
        ArbiterClient,
        resolve_spotlight_arbiter_key,
        _browse,
        _progress,
        _read,
        _report,
        _reviewed_create,
    ) = _arbiter_components()
    values = os.environ if env is None else env
    if values.get("ARBITER_API_KEY"):
        provider = None
    elif read_spotlight_key is None:
        provider = lambda: (_ for _ in ()).throw(
            ValueError("Spotlight Arbiter key is not configured")
        )
    else:
        provider = lambda: resolve_spotlight_arbiter_key(read_spotlight_key)
    return ArbiterClient.from_env(
        env,
        sensitive=sensitive,
        opener=opener,
        credential_provider=provider,
    )


def run_arbiter_workflow(client, case_dir, *, case_study_id=None, create=None, confirmed=False):
    """Run native online workflow operations while offline renderers consume saved files."""
    (
        _ArbiterClient,
        _resolve_spotlight_arbiter_key,
        browse,
        progress,
        read,
        report,
        reviewed_create,
    ) = _arbiter_components()
    files = {"browse": browse(client, case_dir)}
    if case_study_id is not None:
        files.update(
            {
                "read": read(client, case_dir, case_study_id),
                "report": report(client, case_dir, case_study_id),
                "progress": progress(client, case_dir, case_study_id),
            }
        )
    if create is not None:
        files["reviewed_create"] = reviewed_create(
            client,
            case_dir,
            create["body"],
            search_phrases=create["search_phrases"],
            final_entities=create["final_entities"],
            confirmed=confirmed,
        )
    return files



INTEGRATIONS_DIR = Path(__file__).parent


def smoke_test(manifest: dict, *, sensitive: bool = False) -> tuple[bool, str | None]:
    """Probe one integration without egress when sensitive mode is active.

    API probes are network requests, so sensitive mode blocks them before
    configured URLs, imports, or openers are touched.
    """
    if sensitive and manifest.get("type") == "api":
        return False, "network smoke tests are unavailable in sensitive mode"
    kind = manifest.get("type", "api")

    if kind == "api":
        smoke_url_env = manifest.get("smoke_url_env")
        environment_url = os.environ.get(smoke_url_env) if smoke_url_env else None
        if smoke_url_env == "ARBITER_API_BASE" and environment_url:
            # Arbiter exposes OpenAPI below its validated deployment base.
            from arbiter.client import validate_api_base

            try:
                configured_smoke_url = validate_api_base(environment_url) + "/openapi.json"
            except ValueError as exc:
                return False, str(exc)
        else:
            configured_smoke_url = manifest.get("smoke_url") or environment_url
        url = configured_smoke_url or manifest.get("homepage") or manifest.get("docs")
        if not url:
            return True, None
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Spotlight-Preflight/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return (200 <= resp.status < 400), None
        except urllib.error.HTTPError as e:
            # An explicit service endpoint must be usable. A 405 still proves
            # the endpoint exists when it intentionally rejects HEAD. Preserve
            # the legacy shallow reachability semantics for homepage/docs URLs.
            if configured_smoke_url:
                return e.code == 405, f"HTTP {e.code}"
            return (400 <= e.code < 500), f"HTTP {e.code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    if kind == "library":
        # Extend as library integrations are added.
        mod = {"browser-use": "browser_use"}.get(manifest["id"])
        if not mod:
            return True, None
        import importlib.util
        found = importlib.util.find_spec(mod) is not None
        return found, None if found else f"python import '{mod}' failed"

    if kind == "cli":
        binary = manifest.get("local_binary") or manifest["id"]
        resolved = shutil.which(binary)
        if resolved is None:
            return False, f"{binary} not on PATH"
        import subprocess

        version_args = manifest.get("version_args")
        if isinstance(version_args, list) and version_args:
            try:
                proc = subprocess.run([binary, *version_args], text=True, capture_output=True, timeout=10, check=False)
            except Exception as e:
                return False, f"{binary} version check failed: {type(e).__name__}: {e}"
            if proc.returncode != 0:
                return False, f"{binary} version check exited {proc.returncode}"
            required_output = manifest.get("version_output_contains")
            if required_output and required_output not in f"{proc.stdout}\n{proc.stderr}":
                return False, f"{binary} output is missing required capability: {required_output}"
        probes = manifest.get("probes") or []
        if not isinstance(probes, list):
            return False, f"{binary} probes must be a list"
        for index, probe in enumerate(probes, start=1):
            if not isinstance(probe, dict) or not isinstance(probe.get("args"), list):
                return False, f"{binary} probe {index} must declare an args list"
            probe_env = probe.get("env") or {}
            if not (
                isinstance(probe_env, dict)
                and all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in probe_env.items()
                )
            ):
                return False, f"{binary} probe {index} env must map strings to strings"
            try:
                proc = subprocess.run(
                    [binary, *probe["args"]],
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                    env={**os.environ, **probe_env},
                )
            except Exception as e:
                return False, f"{binary} probe {index} failed: {type(e).__name__}: {e}"
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip()[:300]
                suffix = f": {detail}" if detail else ""
                return False, f"{binary} probe {index} exited {proc.returncode}{suffix}"
            required_output = probe.get("output_contains")
            if required_output and required_output not in f"{proc.stdout}\n{proc.stderr}":
                return False, f"{binary} probe {index} output is missing: {required_output}"
        return True, None

    return True, None


def main():
    run_preflight(
        INTEGRATIONS_DIR,
        result_key="integrations",
        smoke_fn=smoke_test,
        report_extra_fields=lambda m: {"type": m.get("type", "api")},
        text_columns=[("id", "ID", 20), ("type", "Type", 10), ("status", "Status", 8)],
        description="Preflight check for Spotlight external tool integrations",
        dismiss_when_constrained=True,
    )


if __name__ == "__main__":
    main()
