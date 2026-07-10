#!/usr/bin/env python3
"""Verify that an OpenAI-compatible endpoint serves the expected model id."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: verify-openai-model.py <base-url> <expected-model-id>", file=sys.stderr)
        return 64

    base_url, expected = sys.argv[1].rstrip("/"), sys.argv[2]
    endpoint = f"{base_url}/v1/models"
    try:
        request = Request(endpoint, headers={"Accept": "application/json"})
        with urlopen(request, timeout=2) as response:  # noqa: S310 - loopback endpoint supplied by launcher
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"could not query {endpoint}: {exc}", file=sys.stderr)
        return 3

    data = payload.get("data", []) if isinstance(payload, dict) else []
    actual = [item.get("id") for item in data if isinstance(item, dict) and item.get("id")]
    if expected not in actual:
        shown = ", ".join(actual) if actual else "<none>"
        print(f"expected model {expected!r}, endpoint advertises: {shown}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
