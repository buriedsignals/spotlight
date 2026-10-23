"""The search CLI saves results to a file, as the `search(query, output_path, limit)`
verb promises. Runs the real CLI against a local stand-in for SearXNG's JSON API.

    python3 tests/test_search_cli.py
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

RESULTS = [
    {"url": "https://example.gov/registry/acme", "title": "Acme Ltd registry entry", "content": "Filed 2021.",
     "publishedDate": "2021-03-04T00:00:00", "engine": "fixture"},
    {"url": "https://example.org/news/acme", "title": "Acme director named", "content": "", "engine": "fixture"},
]


class _SearxStandIn(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        page = 1 if "pageno=1" in self.path else 2
        body = json.dumps({"results": RESULTS if page == 1 else []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_out_writes_the_structured_hits():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SearxStandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {k: v for k, v in os.environ.items() if k not in {"FIRECRAWL_API_KEY", "SEARCH_PROVIDER"}}
    env.update(SEARXNG_URL=f"http://127.0.0.1:{server.server_address[1]}", NO_PROXY="*", no_proxy="*")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "search-acme.json"
            done = subprocess.run(
                [sys.executable, "-m", "integrations.search", "acme director", "--out", str(out)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
            )
            assert done.returncode == 0, done.stderr
            saved = json.loads(out.read_text(encoding="utf-8"))
            assert [hit["url"] for hit in saved] == [r["url"] for r in RESULTS]
            assert saved[0]["title"] == "Acme Ltd registry entry" and saved[0]["date"].startswith("2021-03-04")
            assert "https://example.gov/registry/acme" in done.stdout  # stdout keeps the readable list
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_out_writes_the_structured_hits()
    print("ok  test_out_writes_the_structured_hits\n\n1 passed")
