#!/usr/bin/env python3
"""query_vault.py answers from Engine's `bsig wiki search` over the configured
wiki, keeps its envelope and CLI, and degrades to an empty, explained result
without Engine or a wiki. Engine is stood in for by a fake bsig that records
its arguments and prints the NDJSON contract of bsig/cmd/bsig/wiki_verb.go."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query_vault.py"

FAKE_BSIG = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_BSIG_ARGS"], "w").write(json.dumps(sys.argv[1:]))
mode = os.environ.get("FAKE_BSIG_MODE", "ok")
if mode == "error":
    print(json.dumps({"event": "error", "message": "wiki: workspace has no .ok"}))
    sys.exit(1)
hits = [{"path": "spotlight/claims/acme.md", "type": "claim", "title": "Acme paid Doe", "snippet": "Acme paid Doe.", "score": 1.5},
        {"path": "notes/meeting.md", "type": "note", "snippet": "About [protected].", "score": 0.2}]
print(json.dumps({"event": "progress", "message": "loading"}))
print(json.dumps({"event": "result", "message": "wiki search", "data": {"tool": "search", "result": {"hits": hits}}}))
'''


class QueryVaultChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wiki = self.tmp / "wiki"
        self.wiki.mkdir()
        self.config = self.tmp / ".spotlight-config.json"
        self.config.write_text(json.dumps({"knowledge_destination": {"type": "openknowledge", "workspace_path": str(self.wiki), "namespace": "spotlight", "mode": "solo"}}))
        self.bsig = self.tmp / "bsig"
        self.bsig.write_text(FAKE_BSIG)
        self.bsig.chmod(0o755)
        self.args_file = self.tmp / "args.json"

    def run_query(self, *args: str, bsig: str | None = None, mode: str = "ok") -> tuple[int, dict]:
        env = {**os.environ, "SPOTLIGHT_BSIG": bsig if bsig is not None else str(self.bsig),
               "FAKE_BSIG_ARGS": str(self.args_file), "FAKE_BSIG_MODE": mode}
        done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config), "--case-dir", str(self.tmp / "case"), *args],
                              capture_output=True, text=True, env=env)
        return done.returncode, json.loads(done.stdout or done.stderr)

    def bsig_request(self) -> tuple[list[str], dict]:
        argv = json.loads(self.args_file.read_text())
        return argv, json.loads(argv[-1])

    def test_search_returns_wiki_hits_in_the_envelope(self):
        code, out = self.run_query("--limit", "5", "--", "Acme", "paid")
        self.assertEqual(code, 0)
        self.assertEqual((out["schema_version"], out["query_kind"]), ("spotlight-query-vault/v2", "wiki_search"))
        self.assertTrue(out["control_boundary"]["retrieved_content_is_untrusted"])
        self.assertNotIn("unavailable", out["data"])
        self.assertEqual([r["path"] for r in out["data"]["results"]], ["spotlight/claims/acme.md", "notes/meeting.md"])
        self.assertEqual(out["data"]["results"][1], {"path": "notes/meeting.md", "type": "note", "title": None, "score": 0.2, "snippet": "About [protected]."})
        argv, request = self.bsig_request()
        self.assertEqual(argv[:5], ["--json", "wiki", "search", "--workspace", str(self.wiki.resolve())])
        self.assertEqual(request, {"query": "Acme paid", "limit": 5})

    def test_retired_graph_options_are_accepted_and_ignored(self):
        code, out = self.run_query("--db", "x.sqlite", "--classification", "personal", "--destination-id", "d", "--case-id", "case:x",
                                   "--open-knowledge", "ok", "--finding-fingerprint", "a" * 64, "--legacy-claim-id", "claim:x", "--", "Acme")
        self.assertEqual((code, len(out["data"]["results"])), (0, 2))
        self.assertEqual(self.bsig_request()[1], {"query": "Acme", "limit": 20})

    def test_dedup_searches_the_proposition_among_claim_notes(self):
        code, out = self.run_query("--workflow", "dedup", "--proposition=Acme paid Doe", "--")
        self.assertEqual(code, 0)
        self.assertEqual(self.bsig_request()[1], {"query": "Acme paid Doe", "limit": 20, "types": ["claim", "spotlight-legacy-claim"]})

    def test_unavailable_without_engine_wiki_or_search(self):
        for args, bsig, mode, reason in (
            (("--", "Acme"), str(self.tmp / "absent"), "ok", "Engine is not installed"),
            (("--", "Acme"), None, "error", "workspace has no .ok"),
            (("--workspace-root", str(self.tmp / "absent"), "--", "Acme"), None, "ok", "not a directory"),
            (("--workflow", "prior-verdict", "--finding-fingerprint", "a" * 64, "--"), None, "ok", "retired 1.0 graph"),
            (("--read-path=spotlight/claims/acme.md", "--"), None, "ok", "--read-path is retired"),
        ):
            code, out = self.run_query(*args, bsig=bsig, mode=mode)
            self.assertEqual((code, out["data"]["results"]), (0, []), args)
            self.assertIn(reason, out["data"]["unavailable"])
        self.config.write_text(json.dumps({"knowledge_destination": {"type": "openknowledge"}}))
        code, out = self.run_query("--", "Acme")
        self.assertEqual(code, 0)
        self.assertIn("no wiki configured", out["data"]["unavailable"])

    def test_malformed_input_is_refused(self):
        code, out = self.run_query("--", "x" * 4097)
        self.assertEqual((code, out["error"]["code"]), (1, "query_blocked"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
