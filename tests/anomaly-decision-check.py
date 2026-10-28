#!/usr/bin/env python3
"""scripts/anomaly-decision.py and the Investigation gate's Anomaly check.

A stand-in Anomaly install (tests/anomaly-check.py) and a stand-in decision
model on loopback, which records each request and answers with the
probability the test sets.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decision = load("anomaly_decision", ROOT / "scripts" / "anomaly-decision.py")
standin = load("anomaly_check", ROOT / "tests" / "anomaly-check.py")
from spotlight_orchestration import anomaly_gate, review  # noqa: E402
from integrations.anomaly import adapter  # noqa: E402

PAYMENTS = "id,amount\n1,12\n2,15\n3,14\n4,11\n5,13\n6,16\n7,12\n8,48000\n"
BRIEF = "Payments from the city to Acme in 2024.\nWere any payments unusually large compared with the rest?\n"


class FakeDecisions:
    """A decision endpoint on 127.0.0.1 that answers one noul question."""

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                answer = {key: {"noul": fake.probability} for key in body["questions"]}
                payload = json.dumps({"model": "d1-test", "answers": answer}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def close(self) -> None:
        self.server.shutdown()


class DecisionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.anomaly = standin.StandIn(self.tmp / "anomaly")
        patcher = mock.patch.dict(os.environ, {"ANOMALY_HOME": str(self.anomaly.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.case = self.tmp / "case"
        (self.case / "data").mkdir(parents=True)
        (self.case / "research").mkdir()
        (self.case / "research" / "payments.csv").write_text(PAYMENTS)
        (self.case / "brief-directions.txt").write_text(BRIEF)
        (self.case / "data" / "methodology.json").write_text(json.dumps({"questions": ["Which payments stand out?"]}))

    def endpoint(self, probability: float) -> FakeDecisions:
        fake = FakeDecisions(probability)
        self.addCleanup(fake.close)
        return fake

    def decide(self, config: dict, sensitive: bool = False, env: dict | None = None) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            outcome = decision.decide(self.case, config, sensitive, env or {})
            decision.record(self.case, outcome)
        self.log = out.getvalue()
        return json.loads((self.case / decision.DECISION_FILE).read_text())

    def methodology(self) -> dict:
        return json.loads((self.case / "data" / "methodology.json").read_text())


class Judgment(DecisionTestCase):
    def test_a_local_yes_adds_the_section_the_member_approves_and_logs_the_probability(self) -> None:
        fake = self.endpoint(0.91)
        config = {"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}}
        before = json.dumps(config, sort_keys=True)
        record = self.decide(config)
        self.assertEqual((record["path"], record["decision"], record["tables"]), ("local-asked", True, ["research/payments.csv"]))
        self.assertIn("local-asked p=0.910", self.log)
        [domain] = adapter.plan_domains(self.methodology())
        self.assertEqual(domain["domain"], adapter.ANY_TABLE)
        self.assertIn("p=0.91", domain["why"])
        self.assertEqual(adapter.plan_problems(self.methodology()), [], "the section is one the validator accepts")
        # Local: no switch, no opt-in; the state has d1's fixed keys and sample rows.
        state = fake.requests[0]["body"]["state"]
        self.assertEqual(sorted(state), ["brief", "methodology", "tables"])
        self.assertEqual(state["brief"]["questions"], ["Were any payments unusually large compared with the rest?"])
        self.assertEqual(state["tables"][0]["header"], ["id", "amount"])
        self.assertEqual(len(state["tables"][0]["sample_rows"]), decision.SAMPLE_ROWS)
        self.assertNotIn("Authorization", fake.requests[0]["headers"])
        self.assertEqual(json.dumps(config, sort_keys=True), before, "the step never changes the install config")

    def test_a_local_no_changes_nothing(self) -> None:
        fake = self.endpoint(0.02)
        record = self.decide({"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}})
        self.assertEqual((record["path"], record["decision"]), ("local-asked", False))
        self.assertNotIn("anomaly", self.methodology())

    def test_sensitive_mode_still_asks_a_local_model(self) -> None:
        fake = self.endpoint(0.8)
        record = self.decide({"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}}, sensitive=True)
        self.assertEqual(record["path"], "local-asked")
        self.assertEqual(len(fake.requests), 1)

    def test_the_cloud_route_needs_the_switch_and_the_opt_in_and_never_runs_in_sensitive_mode(self) -> None:
        with mock.patch.object(decision.client.JevOpenRouterProvider, "decide", side_effect=AssertionError("no request")):
            self.assertEqual(self.decide({})["reason"], "decision checks are off")
            on = {"integrations": {"decisions": {"enabled": True}}}
            self.assertEqual(self.decide(on)["reason"], "the case has not opted in to decision checks")
            (self.case / "data" / "decision-signals.json").write_text(json.dumps({"opt_in": {"by": "user", "at": "t"}}))
            self.assertEqual(self.decide(on, sensitive=True)["path"], "cloud-skipped")
        sent = {}
        def answer(provider, state, questions):
            sent.update(state)
            return {"model": "jev", "answers": {key: {"noul": 0.7} for key in questions}}
        with mock.patch.object(decision.client.JevOpenRouterProvider, "decide", answer):
            record = self.decide(on, env={"OPENROUTER_API_KEY": "sk-or-test-key-0123456789"})
        self.assertEqual((record["path"], record["decision"]), ("cloud-asked", True))
        self.assertNotIn("sample_rows", sent["tables"][0], "the cloud route gets headers only")
        self.assertIn("note", sent)

    def test_no_tables_a_plan_with_a_section_or_no_install_asks_nothing(self) -> None:
        fake = self.endpoint(0.9)
        config = {"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}}
        (self.case / "research" / "payments.csv").unlink()
        self.assertEqual(self.decide(config)["path"], "skipped")
        (self.case / "research" / "payments.csv").write_text(PAYMENTS)
        self.anomaly.install({"schema_version": 1, "detectors": [], "catalogue_hash": "sha256:wrong"})
        self.assertEqual(self.decide(config)["reason"], "Anomaly is not installed")
        self.assertEqual(fake.requests, [])

    def test_an_endpoint_that_fails_changes_nothing(self) -> None:
        record = self.decide({"integrations": {"decisions": {"endpoint": {"url": "http://127.0.0.1:9/v1/systemone", "api": "openrouter-decisions"}}}})
        self.assertEqual(record["path"], "unavailable")
        self.assertNotIn("anomaly", self.methodology())


class TaskBlockAndGate(DecisionTestCase):
    def approve(self) -> dict:
        methodology = self.methodology()
        return {"methodology": {"approved_by": "journalist:test", "anomaly_methods": adapter.approved_methods(methodology)}}

    def test_after_a_yes_the_task_names_each_table_and_the_gate_page_notes_a_table_without_a_run(self) -> None:
        fake = self.endpoint(0.9)
        self.decide({"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}})
        block = decision.task_block(self.case)
        self.assertIn("research/payments.csv: columns id, amount", block)
        self.assertIn('"fields": {"id": "id", "amount": "amount"}', block)
        for line in block.splitlines():
            if "anomaly_run {" in line:
                needs = line.split("needs ", 1)[1].split(". anomaly_run", 1)[0].split(", ")
                call = json.loads(line.split("anomaly_run ", 1)[1])
                self.assertEqual(sorted(call["fields"]), sorted(needs), "every example names every role it needs")
        approvals = self.approve()
        (self.case / "data" / "orchestration.json").write_text(json.dumps({"approvals": approvals}))
        # A note on the gate page, never a reason to withhold the gate.
        self.assertEqual(anomaly_gate.tables_without_run(self.case, approvals), ["research/payments.csv"])
        content, _ = review._anomaly_content(self.case, {"approvals": approvals})
        self.assertEqual(content["not_run"], ["research/payments.csv"], "the Investigation gate page names it")
        out = standin.cli("run", str(self.case), stdin=json.dumps(
            {"method": standin.ZSCORE, "table": "research/payments.csv", "fields": {"id": "id", "amount": "amount"}}))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(anomaly_gate.tables_without_run(self.case, approvals), [])
        self.assertEqual(decision.task_block(self.case), "", "a table with a run is not asked for again")

    def test_without_a_yes_the_task_and_the_gate_are_unchanged(self) -> None:
        fake = self.endpoint(0.1)
        self.decide({"integrations": {"decisions": {"endpoint": {"url": fake.url, "api": "openrouter-decisions"}}}})
        self.assertEqual(decision.task_block(self.case), "")
        self.assertEqual(anomaly_gate.tables_without_run(self.case, {"methodology": {"anomaly_methods": {"m": "h"}}}), [])
        (self.case / decision.DECISION_FILE).unlink()
        self.assertEqual(anomaly_gate.tables_without_run(self.case, {"methodology": {"anomaly_methods": {"m": "h"}}}), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
