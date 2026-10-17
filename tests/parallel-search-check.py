#!/usr/bin/env python3
"""Offline caller tests: real search CLI -> resolver -> MCP -> SearchHit output."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.search import parallel_provider as parallel
from integrations.search.__main__ import main
from integrations.search.search_types import SearchError, SearchHit

dispatcher = importlib.import_module("integrations.search.search")

ROW = {"url": "https://example.gov/filing?a=1&b=2#page-3", "title": "Filing — café",
       "excerpts": ["Exact first passage", "Second passage"], "publish_date": "2026-10-01"}


class Response(io.BytesIO):
    def __init__(self, value, *, sse=False, session=False):
        raw = json.dumps(value).encode() if not isinstance(value, bytes) else value
        if sse:
            # Heartbeats and notifications precede the matching response.
            raw = b': heartbeat\n\ndata: {"jsonrpc":"2.0","method":"notifications/message"}\n\n' + b"data: " + raw + b"\n\n"
        super().__init__(raw)
        self.headers = Message()
        self.headers["Content-Type"] = "text/event-stream" if sse else "application/json"
        if session:
            self.headers["Mcp-Session-Id"] = "transport-session"


class Transport:
    def __init__(self, *, rows=None, text=False, sse=False, fault=None):
        self.rows = [ROW, ROW] if rows is None else rows
        self.text, self.sse, self.fault = text, sse, fault
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append(request)
        # Observe every actual request, including discovery and initialization.
        assert request.full_url == "https://search.parallel.ai/mcp"
        assert request.get_method() == "POST"
        assert request.get_header("User-agent") == "spotlight-search/1.0"
        assert request.get_header("Authorization") is None
        assert 0 < timeout <= 30
        payload = json.loads(request.data)
        method = payload["method"]
        if method != "initialize":
            assert request.get_header("Mcp-session-id") == "transport-session"
            assert request.get_header("Mcp-protocol-version") == "2025-03-26"
        if method == "initialize":
            return Response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}, session=True)
        if method == "notifications/initialized":
            return Response(b"")
        if method == "tools/list":
            return Response({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search"}]}}, sse=self.sse)
        assert method == "tools/call" and payload["params"]["name"] == "web_search"
        args = payload["params"]["arguments"]
        assert args["objective"] == "public procurement filing"
        assert args["search_queries"] == ["public procurement filing"]
        assert args.get("session_id") == "conversation-001"
        assert "model_name" not in args  # no invented model attribution
        if isinstance(self.fault, BaseException):
            raise self.fault
        data = {"results": self.rows, "session_id": "conversation-001"}
        result = {"content": [{"type": "text", "text": json.dumps(data)}]} if self.text else {"structuredContent": data}
        value = {"jsonrpc": "2.0", "id": 3, "result": result}
        if callable(self.fault):
            value = self.fault(value)
        return Response(value, sse=self.sse)


class CallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "AGENTS.md").write_text("---\nsensitive: false\n---\n")
        self.env = patch.dict(os.environ, {"SPOTLIGHT_SEARCH_SESSION_ID": "conversation-001"}, clear=True)
        self.env.start()
        self.checkout = patch.object(dispatcher, "_CHECKOUT", self.root)
        self.checkout.start()

    def tearDown(self):
        self.checkout.stop()
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, *options):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["public procurement filing", *options])
        return rc, out.getvalue(), err.getvalue()

    def test_cli_consumes_json_and_text_responses(self):
        for text, sse in [(False, False), (True, False), (False, True)]:
            with self.subTest(text=text, sse=sse):
                transport = Transport(text=text, sse=sse)
                with patch.object(parallel.urllib.request, "urlopen", transport):
                    rc, out, err = self.cli("--provider", "parallel", "--json", "--limit", "1")
                self.assertEqual((rc, err), (0, ""))
                hits = json.loads(out)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0], {"url": ROW["url"], "title": ROW["title"],
                    "snippet": "Exact first passage\nSecond passage", "date": "2026-10-01", "engine": "parallel"})
                self.assertEqual(len(transport.requests), 4)

    def test_env_selection_and_human_output(self):
        os.environ["SEARCH_PROVIDER"] = "parallel"
        with patch.object(parallel.urllib.request, "urlopen", Transport()):
            rc, out, err = self.cli()
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("1. Filing — café (2026-10-01)", out)
        self.assertIn(ROW["url"], out)
        self.assertNotIn("2.", out)  # deduped

    def test_default_saved_selection_and_explicit_override(self):
        sentinel = [SearchHit(url="https://example.org/saved", engine="saved")]
        with patch.object(parallel.urllib.request, "urlopen", side_effect=AssertionError("unexpected network")), \
             patch.object(dispatcher.searxng_provider, "search", return_value=sentinel) as sovereign, \
             patch.object(dispatcher.firecrawl_provider, "search", return_value=sentinel) as firecrawl:
            self.assertEqual(dispatcher.search("q"), sentinel)
            sovereign.assert_called_once()
            os.environ["SEARCH_PROVIDER"] = "firecrawl"
            self.assertEqual(dispatcher.search("q"), sentinel)
            firecrawl.assert_called_once()
            os.environ["SEARCH_PROVIDER"] = "parallel"
            self.assertEqual(dispatcher.search("q", provider="searxng"), sentinel)
            self.assertEqual(sovereign.call_count, 2)

    def test_no_fallback_after_explicit_parallel_failure(self):
        transport = Transport(fault=TimeoutError())
        with patch.object(parallel.urllib.request, "urlopen", transport), \
             patch.object(dispatcher.searxng_provider, "search", side_effect=AssertionError("fallback")), \
             patch.object(dispatcher.firecrawl_provider, "search", side_effect=AssertionError("fallback")):
            rc, out, err = self.cli("--provider", "parallel")
        self.assertEqual(rc, 3)
        self.assertEqual(out, "")
        self.assertIn("timed out", err)
        self.assertEqual(len(transport.requests), 4)  # no retry

    def test_sensitive_disabled_and_unknown_block_before_network(self):
        with patch.object(parallel.urllib.request, "urlopen", side_effect=AssertionError("egress")):
            for value in ["none", "off", "disabled", "typo"]:
                os.environ["SEARCH_PROVIDER"] = value
                self.assertEqual(self.cli()[0], 3)
            os.environ["SPOTLIGHT_SENSITIVE"] = "true"
            self.assertEqual(self.cli("--provider", "parallel")[0], 3)
            del os.environ["SPOTLIGHT_SENSITIVE"]
            (self.root / "AGENTS.md").write_text("---\nsensitive: true\n---\n")
            self.assertEqual(self.cli("--provider", "parallel")[0], 3)
            (self.root / "AGENTS.md").write_text("---\nsensitive: false\n---\n")
            (self.root / ".spotlight-config.json").write_text('{"sensitive": true, "search_library": "firecrawl"}')
            self.assertEqual(self.cli("--provider", "parallel")[0], 3)
            (self.root / ".spotlight-config.json").write_text("broken config")
            self.assertEqual(self.cli("--provider", "parallel")[0], 3)

    def test_unsupported_options_and_invalid_input_do_not_egress(self):
        with patch.object(parallel.urllib.request, "urlopen", side_effect=AssertionError("egress")):
            for args in [("--union",), ("--categories", "news"), ("--time-range", "month"), ("--limit", "0")]:
                self.assertEqual(self.cli("--provider", "parallel", *args)[0], 3)
            with self.assertRaises(SearchError):
                dispatcher.search(" ", provider="parallel")

    def test_errors_and_cancellation_reach_cli_without_results(self):
        errors = [TimeoutError(), urllib.error.URLError("unavailable"),
                  urllib.error.HTTPError(parallel.ENDPOINT, 429, "quota", {}, None),
                  KeyboardInterrupt(),
                  lambda v: dict(v, result={"isError": True}),
                  lambda v: dict(v, id=4),
                  lambda v: dict(v, error={"message": "failure"}),
                  lambda v: dict(v, result={"structuredContent": {"results": None}}),
                  lambda v: dict(v, result={"content": [None]}),
                  lambda v: b"invalid json"]
        for fault in errors:
            with self.subTest(fault=type(fault).__name__):
                transport = Transport(fault=fault)
                with patch.object(parallel.urllib.request, "urlopen", transport):
                    rc, out, err = self.cli("--provider", "parallel", "--json")
                self.assertEqual(rc, 130 if isinstance(fault, KeyboardInterrupt) else 3)
                self.assertEqual(out, "")
                self.assertTrue(err)
                self.assertEqual(len(transport.requests), 4)

    def test_bad_urls_fields_and_empty_results(self):
        for bad in [dict(ROW, url="javascript:alert(1)"), dict(ROW, url="https://user:pass@example.org"),
                    dict(ROW, url="https://example.org/\nsecret"), dict(ROW, excerpts=[None]),
                    dict(ROW, title=123), dict(ROW, publish_date={})]:
            with patch.object(parallel.urllib.request, "urlopen", Transport(rows=[bad])):
                self.assertEqual(self.cli("--provider", "parallel")[0], 3)
        with patch.object(parallel.urllib.request, "urlopen", Transport(rows=[])):
            rc, out, err = self.cli("--provider", "parallel", "--json")
        self.assertEqual((rc, json.loads(out), err.strip()), (0, [], "no results"))


if __name__ == "__main__":
    unittest.main()
