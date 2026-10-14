#!/usr/bin/env python3
"""Run Web-Check checks against one URL through Spotlight's bounded integration contract.

    python3 integrations/webcheck/run_webcheck.py request.json [--dry-run]

Each check is one ``GET {WEB_CHECK_URL}/api/<check>?url=<target>`` call. The
aggregate ``/api`` endpoint is never used, because it runs every check the
instance has, including port scans and traceroute.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _runner import (  # noqa: E402
    IntegrationError,
    RunManifest,
    forbid_verified_statuses,
    integration_run_dir,
    load_request,
    reject_shell_metacharacters,
    validate_run_id,
    validate_slug,
    write_json_atomic,
)
from integrations._credentials import credential  # noqa: E402


# Checks answered from DNS, registries and public datasets. None of them sends
# a request to the target's web server (DNS lookups still reach its nameservers).
LOOKUP_CHECKS = (
    "get-ip",
    "dns",
    "txt-records",
    "caa-records",
    "dnssec",
    "dns-server",
    "whois",
    "archives",
    "subdomains",
    "rank",
    "block-lists",
    "location",
    "social-presence",
)
# Checks that make ordinary HTTP/TLS requests to the target, as a visit would.
# tech-stack and trackers load the page in Chromium on the instance.
PAGE_CHECKS = (
    "status",
    "headers",
    "redirects",
    "ssl",
    "tls-connection",
    "hsts",
    "http-security",
    "firewall",
    "robots-txt",
    "sitemap",
    "security-txt",
    "social-tags",
    "linked-pages",
    "mixed-content",
    "mail-config",
    "cookies",
    "tech-stack",
    "trackers",
)
# Never called: active scanning, or handing the target to a third-party scanner.
EXCLUDED_CHECKS = (
    "ports",
    "trace-route",
    "shodan",
    "threats",
    "tls-labs",
    "quality",
    "carbon",
    "breaches",
    "screenshot",
)
TIER = {**{name: "lookup" for name in LOOKUP_CHECKS}, **{name: "page" for name in PAGE_CHECKS}}
MAX_BODY_BYTES = 5 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    """The instance answers API calls directly; a redirect means it is misconfigured."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HTTPError(req.full_url, code, f"refused redirect to {newurl}", headers, fp)


urlopen = build_opener(_NoRedirect).open


def validate_target(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise IntegrationError("url must be a non-empty string")
    reject_shell_metacharacters(value, "url")
    if any(ch.isspace() for ch in value):
        raise IntegrationError("url must not contain whitespace")
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if parts.scheme not in {"http", "https"}:
        raise IntegrationError("url scheme must be http or https")
    if parts.username or parts.password:
        raise IntegrationError("url must not carry credentials")
    host = parts.hostname
    if not host:
        raise IntegrationError("url has no host")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise IntegrationError("local hosts are not investigation targets")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise IntegrationError("private, loopback and reserved addresses are not investigation targets")
    return value


def validate_instance(value: str | None) -> str:
    if not value:
        raise IntegrationError("WEB_CHECK_URL is not configured")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise IntegrationError("WEB_CHECK_URL must be an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise IntegrationError("WEB_CHECK_URL must be a bare base URL")
    return value.rstrip("/")


def validate_request(data: dict[str, Any]) -> dict[str, Any]:
    project = validate_slug(data.get("project"), "project")
    run_id = validate_run_id(data.get("run_id"))
    target = validate_target(data.get("url"))

    contact_target = data.get("contact_target", False)
    if not isinstance(contact_target, bool):
        raise IntegrationError("contact_target must be true or false")

    checks = data.get("checks")
    if checks is None:
        checks = list(LOOKUP_CHECKS) + (list(PAGE_CHECKS) if contact_target else [])
    if not isinstance(checks, list) or not checks:
        raise IntegrationError("checks must be a non-empty list")
    clean_checks: list[str] = []
    for check in checks:
        if check in EXCLUDED_CHECKS:
            raise IntegrationError(f"check {check!r} is excluded from Spotlight runs")
        if check not in TIER:
            raise IntegrationError(f"unknown check: {check!r}")
        if TIER[check] == "page" and not contact_target:
            raise IntegrationError(f"check {check!r} contacts the target; set contact_target: true to allow it")
        if check not in clean_checks:
            clean_checks.append(check)

    timeout = data.get("timeout_seconds", 60)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 5 <= timeout <= 300:
        raise IntegrationError("timeout_seconds must be an integer between 5 and 300")

    return {
        "project": project,
        "run_id": run_id,
        "url": target,
        "contact_target": contact_target,
        "checks": clean_checks,
        "timeout_seconds": timeout,
    }


def check_url(instance: str, check: str, target: str) -> str:
    return f"{instance}/api/{check}?{urlencode({'url': target})}"


def _get_json(url: str, timeout: int) -> tuple[int, bytes]:
    request = Request(url, method="GET", headers={"Accept": "application/json", "User-Agent": "Spotlight-WebCheck/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BODY_BYTES + 1)
    except HTTPError as exc:
        # Web-Check reports handler failures as 408/500 with a JSON {"error"} body.
        return exc.code, exc.read(MAX_BODY_BYTES + 1)


def instance_version(instance: str, manifest: RunManifest) -> str | None:
    try:
        status, body = _get_json(f"{instance}/healthz", 10)
        health = json.loads(body)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        manifest.warnings.append(f"healthz unavailable: {exc}")
        return None
    if status != 200 or not isinstance(health, dict):
        manifest.warnings.append(f"healthz returned HTTP {status}")
        return None
    if health.get("ready") is False:
        manifest.warnings.append("instance reports ready: false (routes still registering)")
    version = health.get("version")
    return f"web-check {version}" if isinstance(version, str) else None


def classify(status: int, body: bytes) -> tuple[str, str | None, Any]:
    if len(body) > MAX_BODY_BYTES:
        return "error", f"response larger than {MAX_BODY_BYTES} bytes", None
    try:
        parsed = json.loads(body)
    except ValueError:
        return "error", f"HTTP {status}: response is not JSON", None
    if isinstance(parsed, dict) and "skipped" in parsed:
        return "skipped", str(parsed["skipped"])[:500], parsed
    if isinstance(parsed, dict) and "error" in parsed:
        return "error", str(parsed["error"])[:500], parsed
    if status != 200:
        return "error", f"HTTP {status}", parsed
    return "observed", None, parsed


def run_check(instance: str, check: str, request: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    url = check_url(instance, check, request["url"])
    try:
        status, body = _get_json(url, request["timeout_seconds"])
    except (URLError, TimeoutError, OSError) as exc:
        outcome, detail, parsed = "error", f"request failed: {exc}", None
    else:
        outcome, detail, parsed = classify(status, body)
    if parsed is not None:
        write_json_atomic(output_dir / f"{check}.json", parsed)
    return {
        "check": check,
        "tier": TIER[check],
        "outcome": outcome,
        "detail": detail,
        "raw_path": f"{check}.json" if parsed is not None else None,
        "verification_status": "unverified",
    }


def run(request_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    request = validate_request(load_request(request_path))
    instance = validate_instance(credential("WEB_CHECK_URL"))
    output_dir = integration_run_dir(request["project"], "webcheck", request["run_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_dir / "request.json", request)

    manifest = RunManifest("webcheck", request["project"], request["run_id"], request)
    manifest.command = [check_url(instance, check, request["url"]) for check in request["checks"]]

    if dry_run:
        results: list[dict[str, Any]] = []
        manifest.finish("dry_run")
    else:
        manifest.tool_version = instance_version(instance, manifest)
        results = [run_check(instance, check, request, output_dir) for check in request["checks"]]
        forbid_verified_statuses(results, "webcheck_results")
        observed = sum(1 for item in results if item["outcome"] == "observed")
        manifest.finish("ok" if observed else "error", error=None if observed else "no check returned an observation")

    write_json_atomic(output_dir / "results.json", {"url": request["url"], "results": results})
    manifest.record_outputs(output_dir)
    write_json_atomic(output_dir / "run-manifest.json", manifest.as_dict())
    return {
        "status": manifest.status,
        "run_dir": str(output_dir),
        "manifest": str(output_dir / "run-manifest.json"),
        "results": str(output_dir / "results.json"),
        "outcomes": {item["check"]: item["outcome"] for item in results},
        "planned_requests": manifest.command,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded Web-Check recon for one URL.")
    parser.add_argument("request_json")
    parser.add_argument("--dry-run", action="store_true", help="Validate the request and list the API calls without sending them")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.request_json, dry_run=args.dry_run), indent=2))
        return 0
    except (OSError, IntegrationError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
