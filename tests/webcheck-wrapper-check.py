#!/usr/bin/env python3
"""Check the bounded Web-Check wrapper against a local stub instance."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "webcheck"))
import run_webcheck  # noqa: E402
from run_webcheck import LOOKUP_CHECKS, run, validate_request  # noqa: E402

REQUESTS: list[str] = []
# Per-check stub answers, matching Web-Check's middleware: 200 + JSON, 200 +
# {"skipped"}, or 500 + {"error"}.
ANSWERS = {
    "dns": (200, {"A": ["93.184.215.14"], "MX": []}),
    "whois": (200, {"skipped": "No WHOIS server for this TLD"}),
    "archives": (500, {"error": "Wayback CDX timed out"}),
}


class Stub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        REQUESTS.append(self.path)
        parts = urlsplit(self.path)
        if parts.path == "/healthz":
            return self._json(200, {"status": "ok", "ready": True, "version": "2.3.4"})
        if parts.path == "/api/redirect-me":
            self.send_response(302)
            self.send_header("Location", "https://elsewhere.example/")
            self.end_headers()
            return
        check = parts.path.removeprefix("/api/")
        target = parse_qs(parts.query).get("url", [""])[0]
        status, body = ANSWERS.get(check, (200, {"check": check, "target": target}))
        self._json(status, body)

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


def rejects(payload: dict, needle: str) -> None:
    try:
        validate_request(payload)
    except Exception as exc:  # the wrapper raises IntegrationError (a ValueError)
        assert needle in str(exc), f"{needle!r} not in {exc}"
        return
    raise AssertionError(f"expected rejection containing {needle!r}: {payload}")


def main() -> int:
    base = {"project": "case-one", "run_id": "20261014-webcheck", "url": "example.org"}

    plain = validate_request(base)
    assert plain["checks"] == list(LOOKUP_CHECKS) and plain["contact_target"] is False
    rejects({**base, "checks": ["headers"]}, "contact_target")
    rejects({**base, "checks": ["ports"], "contact_target": True}, "excluded")
    rejects({**base, "checks": ["trace-route"], "contact_target": True}, "excluded")
    rejects({**base, "checks": ["nope"]}, "unknown check")
    rejects({**base, "url": "http://127.0.0.1/"}, "private")
    rejects({**base, "url": "http://10.0.0.8"}, "private")
    rejects({**base, "url": "localhost:3000"}, "local hosts")
    rejects({**base, "url": "https://user:pw@example.org"}, "credentials")
    rejects({**base, "url": "file:///etc/passwd"}, "scheme")
    rejects({**base, "url": "example.org;id"}, "metacharacters")
    print("ok request validation")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["WEB_CHECK_URL"] = f"http://127.0.0.1:{server.server_port}/"
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "cases") as tmp_dir:
            project = Path(tmp_dir).name
            request_path = Path(tmp_dir) / "request.json"

            request_path.write_text(json.dumps({**base, "project": project, "checks": ["dns", "whois", "archives"]}))
            result = run(request_path)
            api_calls = [path for path in REQUESTS if path.startswith("/api")]
            assert [urlsplit(path).path for path in api_calls] == ["/api/dns", "/api/whois", "/api/archives"], api_calls
            assert all(parse_qs(urlsplit(path).query)["url"] == ["example.org"] for path in api_calls)
            assert result["outcomes"] == {"dns": "observed", "whois": "skipped", "archives": "error"}, result
            assert result["status"] == "ok"
            run_dir = Path(result["run_dir"])
            manifest = json.loads(Path(result["manifest"]).read_text())
            assert manifest["tool_version"] == "web-check 2.3.4"
            hashed = {item["path"]: item["sha256"] for item in manifest["outputs"]}
            assert set(hashed) == {"request.json", "results.json", "dns.json", "whois.json", "archives.json"}, hashed
            for name, digest in hashed.items():
                assert hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == digest, name
            results = json.loads(Path(result["results"]).read_text())["results"]
            assert {item["verification_status"] for item in results} == {"unverified"}
            print("ok lookup run: only requested checks called, outcomes classified, outputs hashed")

            REQUESTS.clear()
            request_path.write_text(json.dumps({**base, "project": project, "run_id": "page-run", "contact_target": True}))
            result = run(request_path, dry_run=True)
            assert REQUESTS == [], REQUESTS
            planned = [urlsplit(url).path for url in result["planned_requests"]]
            assert "/api/headers" in planned and "/api/ports" not in planned and "/api" not in planned
            print("ok dry run sends nothing and never plans excluded checks")

            run_webcheck.TIER["redirect-me"] = "lookup"
            request_path.write_text(json.dumps({**base, "project": project, "run_id": "redirect", "checks": ["redirect-me"]}))
            result = run(request_path)
            assert result["outcomes"] == {"redirect-me": "error"} and result["status"] == "error", result
            assert not any("elsewhere" in path for path in REQUESTS)
            print("ok instance redirects are refused")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
