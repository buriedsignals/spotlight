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
    print("decision-endpoint-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
