"""The preflight search probe checks the backing `search` will actually use.
With SEARCH_PROVIDER=firecrawl SearXNG is never queried; otherwise one real
SearXNG query decides, and zero results is a failure. Runs the real CLI against
a local stand-in for SearXNG's JSON API that counts the requests it gets.

    python3 tests/test_search_probe.py
"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent


def stand_in(results):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            body = json.dumps({"results": results, "unresponsive_engines": [["google", "CAPTCHA"]]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, requests


def probe(server, tmp, **extra):
    bin_dir = pathlib.Path(tmp) / "bin"
    bin_dir.mkdir(exist_ok=True)
    firecrawl = bin_dir / "firecrawl"
    firecrawl.write_text("#!/bin/sh\necho 'the probe must not run a Firecrawl query' >&2\nexit 9\n", encoding="utf-8")
    firecrawl.chmod(0o755)
    config = pathlib.Path(tmp) / "config.json"
    config.write_text(json.dumps({"sensitive": False, "firecrawl_mode": "backup"}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"FIRECRAWL_API_KEY", "SEARCH_PROVIDER", "SPOTLIGHT_SENSITIVE"}}
    env.update(
        SEARXNG_URL=f"http://127.0.0.1:{server.server_address[1]}", NO_PROXY="*", no_proxy="*",
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}", SPOTLIGHT_CONFIG=str(config), **extra,
    )
    return subprocess.run([sys.executable, "-m", "integrations.search.probe"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_firecrawl_provider_skips_the_searxng_probe():
    server, requests = stand_in([{"url": "https://example.org/", "title": "x"}])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            done = probe(server, tmp, SEARCH_PROVIDER="firecrawl")
            assert done.returncode == 0, (done.stdout, done.stderr)
            assert requests == [], requests
            assert done.stdout.startswith("search: firecrawl"), done.stdout
            refused = probe(server, tmp, SEARCH_PROVIDER="firecrawl", SPOTLIGHT_SENSITIVE="true")
            assert refused.returncode == 1 and "sensitive mode" in refused.stdout, (refused.stdout, refused.stderr)
            assert requests == [], requests
    finally:
        server.shutdown()
        server.server_close()


def test_searxng_is_probed_otherwise_and_zero_results_fail():
    server, requests = stand_in([{"url": "https://example.org/", "title": "x"}])
    empty, empty_requests = stand_in([])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            done = probe(server, tmp)
            assert done.returncode == 0 and len(requests) == 1, (done.stdout, done.stderr, requests)
            assert done.stdout.startswith("searxng results: 1"), done.stdout
            dead = probe(empty, tmp)
            assert dead.returncode == 1 and len(empty_requests) == 1, (dead.stdout, dead.stderr)
            assert "CAPTCHA" in dead.stdout, dead.stdout
    finally:
        for s in (server, empty):
            s.shutdown()
            s.server_close()


if __name__ == "__main__":
    test_firecrawl_provider_skips_the_searxng_probe()
    print("ok  test_firecrawl_provider_skips_the_searxng_probe")
    test_searxng_is_probed_otherwise_and_zero_results_fail()
    print("ok  test_searxng_is_probed_otherwise_and_zero_results_fail\n\n2 passed")
