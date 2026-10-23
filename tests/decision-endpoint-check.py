#!/usr/bin/env python3
"""The decision client's endpoint rules (knowledge model U5).

Another endpoint may serve the Decisions API only over https or loopback
http, with its own named credential; the OpenRouter key and the
zero-retention provider body go only to openrouter.ai; an unknown API is
refused rather than sent a request it cannot read.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "decisions"))
import client  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def refused(block: dict) -> str:
    try:
        client.endpoint_from_config(block)
    except client.DecisionUnavailable as exc:
        return str(exc)
    raise AssertionError(f"accepted {block}")


def main() -> int:
    require(client.endpoint_from_config(None) == (client.DECISIONS_URL, "OPENROUTER_API_KEY"), "default endpoint")
    ours = {"url": "https://decisions.newsroom.example/api/alpha/decisions", "credential_env": "NEWSROOM_DECISIONS_KEY",
            "api": "openrouter-decisions"}
    require(client.endpoint_from_config(ours) == (ours["url"], "NEWSROOM_DECISIONS_KEY"), "https endpoint")
    loopback = dict(ours, url="http://127.0.0.1:8080/decisions")
    require(client.endpoint_from_config(loopback)[0] == loopback["url"], "loopback http endpoint")
    require("https, or http on loopback" in refused(dict(ours, url="http://decisions.newsroom.example/x")), "plain http elsewhere")
    require("own credential_env" in refused(dict(ours, credential_env="OPENROUTER_API_KEY")), "OpenRouter key sent elsewhere")
    require("own credential_env" in refused({k: v for k, v in ours.items() if k != "credential_env"}), "no credential named")
    require("no adapter" in refused(dict(ours, api="clef")), "unknown API")
    require("url, credential_env and api only" in refused(dict(ours, headers={"x": "y"})), "extra fields")

    require(client.endpoint_from_config(dict(ours, url="https://api.openrouter.ai/x"))[1] == "OPENROUTER_API_KEY",
            "an OpenRouter subdomain was treated as another endpoint")
    require("malformed" in refused(dict(ours, url="https://decisions.example:99999/x")), "a bad port crashed")
    require("must not carry credentials" in refused(dict(ours, url="https://user:pw@decisions.example/x")), "userinfo accepted")
    require(client.endpoint_from_config(dict(ours, url="http://[::1]:8080/d"))[0] == "http://[::1]:8080/d", "IPv6 loopback")
    # A model on this computer (Kev in Ninja mode) needs no key; anything else still does.
    local = {"url": "http://127.0.0.1:8009/v1/systemone", "api": "openrouter-decisions"}
    require(client.endpoint_from_config(local) == (local["url"], ""), "a loopback endpoint without a key was refused")

    # The script reads the endpoint from the checkout's install config only.
    import importlib.util
    import tempfile
    spec = importlib.util.spec_from_file_location("decision_signals", ROOT / "scripts" / "decision-signals.py")
    signals = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(signals)
    with tempfile.TemporaryDirectory() as tmp:
        signals.ROOT = Path(tmp)
        (Path(tmp) / ".spotlight-config.json").write_text(json.dumps({"integrations": {"decisions": {"endpoint": ours}}}), encoding="utf-8")
        require(signals.install_endpoint() == (ours["url"], "NEWSROOM_DECISIONS_KEY"), "install config endpoint not read")
        (Path(tmp) / ".spotlight-config.json").write_text(json.dumps({"integrations": {"decisions": "on"}}), encoding="utf-8")
        try:
            signals.install_endpoint()
            raise AssertionError("a malformed decisions block was accepted")
        except client.DecisionUnavailable:
            pass

    sent = []

    def opener(request, timeout):
        sent.append((request.full_url, request.headers.get("Authorization"), json.loads(request.data)))
        return io.BytesIO(json.dumps({"model": "m", "answers": {"q": {"noul": 0.2}}}).encode())

    question = {"q": {"type": "noul"}}
    other = client.JevOpenRouterProvider("newsroom-key", url=ours["url"], opener=opener, retries=0)
    other.decide({"s": 1}, question)
    url, auth, body = sent[-1]
    require(url == ours["url"] and auth == "Bearer newsroom-key" and "provider" not in body,
            f"another endpoint got the OpenRouter routing body or the wrong address: {sent[-1]}")
    default = client.JevOpenRouterProvider("or-key", opener=opener, retries=0)
    default.decide({"s": 1}, question)
    url, auth, body = sent[-1]
    require(url == client.DECISIONS_URL and body["provider"] == client.REQUIRED_PROVIDER, "OpenRouter lost its zero-retention body")
    # Keyless loopback: no Authorization header, and sensitive mode allows it (no egress).
    kev = client.JevOpenRouterProvider("", url=local["url"], sensitive=True, opener=opener, retries=0)
    kev.decide({"s": 1}, question)
    url, auth, body = sent[-1]
    require(url == local["url"] and auth is None and "provider" not in body, f"the local endpoint got a key or routing body: {sent[-1]}")
    for blocked in ({"api_key": "", "url": ours["url"]}, {"api_key": "k", "url": ours["url"], "sensitive": True}):
        try:
            client.JevOpenRouterProvider(**blocked)
            raise AssertionError(f"accepted {blocked}")
        except client.DecisionUnavailable:
            pass
    # Only a literal loopback address is "this computer": localhost can resolve elsewhere.
    require("own credential_env" in refused(dict(local, url="http://localhost:8009/v1/systemone")), "keyless localhost accepted")
    try:
        client.JevOpenRouterProvider("k", url="http://localhost:8009/v1/systemone", sensitive=True)
        raise AssertionError("sensitive mode trusted the name localhost")
    except client.DecisionUnavailable:
        pass

    # A local request never goes through an ambient proxy.
    import http.server
    import os
    import threading
    hits = {"kev": 0, "proxy": 0}

    def handler(name):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                hits[name] += 1
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"model":"kev","answers":{"q":{"noul":0.2}}}')

            def log_message(self, *args):
                pass
        return Handler

    servers = {name: http.server.HTTPServer(("127.0.0.1", 0), handler(name)) for name in hits}
    for server in servers.values():
        threading.Thread(target=server.serve_forever, daemon=True).start()
    saved = {k: os.environ.get(k) for k in ("http_proxy", "HTTP_PROXY", "no_proxy", "NO_PROXY")}
    os.environ.update(http_proxy=f"http://127.0.0.1:{servers['proxy'].server_port}", HTTP_PROXY=f"http://127.0.0.1:{servers['proxy'].server_port}",
                      no_proxy="", NO_PROXY="")
    try:
        client.JevOpenRouterProvider("", url=f"http://127.0.0.1:{servers['kev'].server_port}/v1/systemone", sensitive=True,
                                     retries=0).decide({"s": 1}, question)
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        for server in servers.values():
            server.shutdown()
    require(hits == {"kev": 1, "proxy": 0}, f"a local decision request went through the proxy: {hits}")
    print("decision-endpoint-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
