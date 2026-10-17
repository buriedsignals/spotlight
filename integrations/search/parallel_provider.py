"""Optional anonymous Parallel Search MCP backing for the search seam.

Uses the server's Streamable HTTP transport with Python's standard library.
No API keys, credential files, automatic retries or provider fallback are used.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .search_types import SearchError, SearchHit

ENDPOINT = "https://search.parallel.ai/mcp"
USER_AGENT = "spotlight-search/1.0"
PROTOCOL = "2025-03-26"
TIMEOUT = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _message(response, request_id):
    """Read a bounded JSON response or one matching SSE response event."""
    if response.headers.get_content_type() != "text/event-stream":
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SearchError("Parallel MCP response is too large")
        return json.loads(raw)
    size = 0
    data = []
    while True:
        line = response.readline(MAX_RESPONSE_BYTES - size + 1)
        size += len(line)
        if size > MAX_RESPONSE_BYTES:
            raise SearchError("Parallel MCP response is too large")
        if not line:
            raise SearchError("Parallel MCP stream ended without a response")
        text = line.decode("utf-8").rstrip("\r\n")
        if not text and data:
            value = json.loads("\n".join(data))
            data = []
            if isinstance(value, dict) and value.get("id") == request_id:
                return value
        elif text.startswith("data:"):
            data.append(text[5:].lstrip(" "))


class _Client:
    def __init__(self):
        self.deadline = time.monotonic() + TIMEOUT
        self.session = None

    def request(self, method, params=None, *, request_id=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if request_id is not None:
            payload["id"] = request_id
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SearchError("Parallel search timed out after 30s")
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                if request_id is None:
                    return None
                value = _message(response, request_id)
        except urllib.error.HTTPError as exc:
            note = " (rate limited; try again later)" if exc.code == 429 else ""
            raise SearchError(f"Parallel Search MCP HTTP {exc.code}{note}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise SearchError("Parallel Search MCP connection failed or timed out") from exc
        except (ValueError, UnicodeError) as exc:
            raise SearchError("Parallel Search MCP returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != request_id:
            raise SearchError("Parallel Search MCP returned an invalid response")
        if "error" in value or not isinstance(value.get("result"), dict):
            raise SearchError("Parallel Search MCP request failed")
        return value["result"]


def search(query: str, *, limit: int = 10) -> list[SearchHit]:
    if not query.strip() or limit < 1:
        raise SearchError("Parallel search needs a nonempty query and positive limit")
    session = os.environ.get("SPOTLIGHT_SEARCH_SESSION_ID")
    if session is not None and not 1 <= len(session) <= 100:
        raise SearchError("SPOTLIGHT_SEARCH_SESSION_ID must contain 1–100 characters")
    client = _Client()
    initialized = client.request("initialize", {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "spotlight", "version": "1.0"},
    }, request_id=1)
    if initialized.get("protocolVersion") != PROTOCOL:
        raise SearchError("Parallel Search MCP negotiated an unsupported protocol version")
    client.request("notifications/initialized")
    tools = client.request("tools/list", {}, request_id=2).get("tools")
    if not isinstance(tools, list) or not any(isinstance(t, dict) and t.get("name") == "web_search" for t in tools):
        raise SearchError("Parallel Search MCP does not expose web_search")
    arguments = {"objective": query, "search_queries": [query]}
    if session:
        arguments["session_id"] = session
    result = client.request("tools/call", {"name": "web_search", "arguments": arguments}, request_id=3)
    if result.get("isError"):
        raise SearchError("Parallel search failed; check service availability or anonymous usage limits")
    data = result.get("structuredContent")
    if data is None:
        content = result.get("content") or []
        try:
            data = json.loads(next(c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text"))
        except (StopIteration, KeyError, TypeError, ValueError) as exc:
            raise SearchError("Parallel search returned no structured results") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchError("Parallel search returned invalid results")
    hits = []
    seen = set()
    for row in data["results"]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            raise SearchError("Parallel search returned an invalid result URL")
        url = row["url"]
        try:
            parsed = urllib.parse.urlsplit(url)
            valid = parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid or any(c.isspace() or ord(c) < 32 for c in url):
            raise SearchError("Parallel search returned an invalid result URL")
        excerpts = row.get("excerpts", [])
        if (not isinstance(excerpts, list) or any(not isinstance(s, str) for s in excerpts)
                or row.get("title") is not None and not isinstance(row["title"], str)
                or row.get("publish_date") is not None and not isinstance(row["publish_date"], str)):
            raise SearchError("Parallel search returned invalid result fields")
        if url not in seen:
            seen.add(url)
            hits.append(SearchHit(url=url, title=row.get("title") or "", snippet="\n".join(excerpts)[:300],
                                  date=row.get("publish_date"), engine="parallel"))
    return hits[:limit]
