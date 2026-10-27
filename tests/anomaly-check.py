#!/usr/bin/env python3
"""Anomaly as context and runner (integrations/anomaly): the pinned catalogue
hides the feature unless it matches; Methodology validation and the
Methodology view use Anomaly's own lookup; the investigator's runs check the
opt-in, the request and every output line before anything is recorded; the
Investigation gate shows each signal for the member to accept or dismiss. A
stand-in Anomaly install (catalogue, lookup and one-shot runner) keeps it
offline and records how it was called.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "scripts"):
    sys.path.insert(0, str(_path))
from integrations.anomaly import adapter  # noqa: E402
from spotlight_orchestration import review  # noqa: E402

ZSCORE = "method:numeric.zscore_outliers@1.1.0"
FILINGS = "method:lobbying.foreign_filings@1.2.0"
SIGNAL_SCHEMA = {
    "candidate_id": "string", "category": "string", "detector_hash": "string", "detector_id": "string",
    "detector_version": "string", "severity": "string", "signal_id": "string", "status": "string",
    "summary": "string", "values": "object",
    # Anomaly's schema since #8: field_map for core detectors, column_map and input_hashes for domain ones.
    "provenance": {"approval": "string", "column_map": "object", "executed_at": "string", "field_map": "object",
                   "input_format": "string", "input_hash": "string", "input_hashes": "object", "limits": "object",
                   "parameters": "object", "run_id": "string"},
}
DETECTORS = [
    {"id": "numeric.zscore_outliers", "version": "1.1.0", "implementation_hash": "sha256:" + "a" * 64,
     "required_tables": ["*"], "required_fields": [{"name": "id", "types": None}, {"name": "amount", "types": None}],
     "signal_category": "anomaly", "severity": "medium", "title": "Z-score outliers",
     "description": "Finds numeric outliers.", "expected_output": ["candidate_id"], "assumptions": ["numeric amounts"],
     "false_positives": ["legitimate large payments"], "parameters": {}},
    {"id": "lobbying.foreign_filings", "version": "1.2.0", "implementation_hash": "sha256:" + "b" * 64,
     "required_tables": ["lobbying_registrations", "lobbying_foreign_entities"], "required_fields": [], "data_type": "lobbying",
     "signal_category": "anomaly", "severity": "low", "title": "Foreign filings", "description": "Two tables.",
     "expected_output": ["candidate_id"], "assumptions": ["filings are complete"], "false_positives": ["late filings"],
     "parameters": {}},
]
CONTEXT = {
    "domains": [{"domain": "lobbying", "contract": {"lobbying_registrations": ["id", "amount"], "lobbying_foreign_entities": ["id"]},
                 "sources": [{"id": "us/senate-lda", "title": "Senate LDA", "status": "queryable", "auth": "none",
                              "cost": ["free"], "license": "public domain"}],
                 "detectors": [DETECTORS[1]]},
                {"domain": "procurement", "contract": None,
                 "sources": [{"id": "eu/ted", "title": "TED", "status": "queryable", "auth": "none",
                              "cost": ["free"], "license": "CC-BY"}], "detectors": []}],
    "any_table": [DETECTORS[0]],
}

# The stand-in for `python -I -m anomaly.context|anomaly.oneshot ...`: it
# records each call and answers as behaviour.json says. The runner flags
# every row whose second column is at least 1000.
STAND_IN = textwrap.dedent('''
    import hashlib, json, os, stat, sys
    from pathlib import Path
    home = Path(__file__).resolve().parent
    args = sys.argv[1:]
    module = args[args.index("-m") + 1]
    rest = args[args.index("-m") + 2:]
    with open(home / "calls.jsonl", "a") as log:
        log.write(json.dumps({"module": module, "args": rest, "env": sorted(os.environ), "cwd": os.getcwd()}) + "\\n")
    behaviour = json.loads((home / "behaviour.json").read_text())
    if behaviour.get("exit"):
        print(f"{module}: refused for the test", file=sys.stderr)
        sys.exit(behaviour["exit"])
    if module == "anomaly.context":
        print((home / "context.json").read_text())
        sys.exit(0)
    sha = lambda path: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    requests = json.loads(Path(rest[rest.index("--request") + 1]).read_text())
    if "--table" in rest:
        files, named = {"*": rest[rest.index("--table") + 1]}, False
    else:
        files = dict(rest[i + 1].split("=", 1) for i, arg in enumerate(rest) if arg == "--tables")
        named = True
    for path in files.values():
        assert not os.access(path, os.W_OK), "the table copy must be read-only"
    hashes = {name: sha(path) for name, path in sorted(files.items())}
    input_hash = ("sha256:" + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()) if named else hashes["*"]
    first = Path(sorted(files.values())[0]).read_text().splitlines()[1:]
    for request in requests:
        for n, row in enumerate(first, start=1):
            if float(row.split(",")[1]) < 1000:
                continue
            provenance = {"input_hash": input_hash, "input_format": "tables" if named else "csv",
                          "parameters": {}, "limits": {}, "run_id": "r", "executed_at": "t", "approval": "caller-recorded"}
            provenance["column_map" if named else "field_map"] = request.get("columns", {}) if named else request["fields"]
            if named:
                provenance["input_hashes"] = hashes
            signal = {"status": "lead", "signal_id": f"signal-{n}", "detector_id": request["id"],
                      "detector_version": request["version"], "detector_hash": request["implementation_hash"],
                      "candidate_id": f"row-{n}", "category": "anomaly", "severity": "medium",
                      "summary": "flagged", "values": {"candidate_id": f"row-{n}"}, "provenance": provenance}
            for key, value in behaviour.get("set", {}).items():
                signal[key] = value
            print(json.dumps(signal))
''')

TABLE = "id,amount\n1,12\n2,15\n3,48000\n"


def catalogue(detectors=DETECTORS) -> dict:
    body = {"schema_version": 1, "detectors": copy.deepcopy(detectors), "signal_schema": SIGNAL_SCHEMA}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "catalogue_hash": f"sha256:{digest}"}


def cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """adapter.py's command line, in process so the test's pin applies."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = adapter.main(list(args))
    return subprocess.CompletedProcess(args, code, out.getvalue(), err.getvalue())


class StandIn:
    """An Anomaly install: the catalogue, the lookup's answer and a .venv python that is the stand-in."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.document = catalogue()
        (home / "detectors").mkdir(parents=True)
        (home / "detectors" / "catalogue.json").write_text(json.dumps(self.document, indent=2))
        (home / "context.json").write_text(json.dumps(CONTEXT))
        python = home / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        (home / "standin.py").write_text(STAND_IN)
        python.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{home / 'standin.py'}' \"$@\"\n")
        python.chmod(0o755)
        self.behave()
        self.pin = {"release": "0" * 40, "schema_version": 1, "catalogue_hash": self.document["catalogue_hash"]}

    def behave(self, **behaviour) -> None:
        (self.home / "behaviour.json").write_text(json.dumps(behaviour))

    def calls(self, module: str | None = None) -> list[dict]:
        log = self.home / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if module is None or call["module"] == module]


class AnomalyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.anomaly = StandIn(self.tmp / "anomaly")
        patcher = mock.patch.dict(os.environ, {"SPOTLIGHT_CONFIG": str(self.tmp / "no-config.json"),
                                               "OPENROUTER_API_KEY": "sk-or-test-secret",
                                               "ANOMALY_HOME": str(self.anomaly.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        pin = mock.patch.object(adapter, "PIN", self.anomaly.pin)
        pin.start()
        self.addCleanup(pin.stop)
        self.case = self.tmp / "case"
        (self.case / "data").mkdir(parents=True)
        (self.case / "research").mkdir()
        (self.case / "research" / "payments.csv").write_text(TABLE)

    def opt_in(self, *domains: str) -> None:
        plan = {"anomaly": {"domains": [{"domain": d, "sources": [], "why": "test"} for d in domains]}} if domains else {}
        (self.case / "data" / "methodology.json").write_text(json.dumps(plan))


class Catalogue(AnomalyTestCase):
    def test_anything_but_the_pinned_catalogue_hides_the_feature(self) -> None:
        self.assertTrue(adapter.state()["available"])
        tampered = catalogue()
        tampered["detectors"][0]["required_tables"] = ["lobbying_registrations"]  # content changed, pinned hash kept
        tampered["catalogue_hash"] = self.anomaly.pin["catalogue_hash"]
        cases = {"no install": None, "another release": catalogue(DETECTORS[:1]),
                 "content changed under the pinned hash": tampered}
        for label, document in cases.items():
            with self.subTest(label):
                os.environ["ANOMALY_HOME"] = str(self.anomaly.home)
                if document is None:
                    os.environ.pop("ANOMALY_HOME")
                else:
                    (self.anomaly.home / "detectors" / "catalogue.json").write_text(json.dumps(document))
                self.assertFalse(adapter.state()["available"])
                # The lookup answers that the feature is off, and lists nothing.
                out = cli("context")
                self.assertEqual(out.returncode, 0, out.stderr)
                listed = json.loads(out.stdout)
                self.assertEqual((listed["available"], listed["domains"], listed["any_table"]), (False, [], []))
        self.assertEqual(self.anomaly.calls(), [], "a hidden feature never runs Anomaly")

    def test_preflight_reports_unconfigured_then_the_unpinned_install(self) -> None:
        def status(**env: str) -> dict:
            base = {k: v for k, v in os.environ.items() if k != "ANOMALY_HOME"}
            out = subprocess.run([sys.executable, str(ROOT / "integrations" / "preflight.py"), "--json"],
                                 env={**base, **env}, capture_output=True, text=True, cwd=ROOT, check=False)
            return next(r for r in json.loads(out.stdout)["integrations"] if r["id"] == "anomaly")

        self.assertEqual(status()["status"], "unconfigured")
        # The stand-in is not the release the shipped manifest pins.
        unpinned = status(ANOMALY_HOME=str(self.anomaly.home))
        self.assertEqual(unpinned["status"], "yellow")
        self.assertIn("pinned release", unpinned["smoke_error"])


class Context(AnomalyTestCase):
    def test_the_lookup_runs_anomaly_from_the_pinned_install_with_no_keys(self) -> None:
        self.assertEqual(adapter.context(domain="lobbying"), CONTEXT)
        [call] = self.anomaly.calls("anomaly.context")
        self.assertEqual(call["args"], ["--domain", "lobbying"])
        self.assertEqual(Path(call["cwd"]).resolve(), self.anomaly.home)
        self.assertNotIn("OPENROUTER_API_KEY", call["env"])
        self.assertNotIn("ANOMALY_HOME", call["env"])


class PlanValidation(AnomalyTestCase):
    def test_every_named_domain_and_source_is_one_the_lookup_lists(self) -> None:
        plan = lambda *domains: {"anomaly": {"domains": [dict(d, why="w") for d in domains]}}
        self.assertEqual(adapter.plan_problems(plan({"domain": "lobbying", "sources": ["us/senate-lda"]},
                                                    {"domain": "any_table", "sources": ["eu/ted"]})), [])
        problems = adapter.plan_problems(plan({"domain": "lobbying", "sources": ["eu/ted"]}, {"domain": "weather", "sources": []}))
        self.assertEqual(problems, ["anomaly.domains[0]: 'eu/ted' is not an Anomaly source for lobbying",
                                    "anomaly.domains[1]: 'weather' is not an Anomaly domain"])
        os.environ.pop("ANOMALY_HOME")
        self.assertIn("lookup is unavailable", adapter.plan_problems(plan({"domain": "lobbying", "sources": []}))[0])
        self.assertEqual(adapter.plan_problems({}), [], "a plan without an anomaly section never needs Anomaly")

    def test_the_methodology_page_shows_the_plan_with_each_domains_detectors(self) -> None:
        shown = adapter.view({"anomaly": {"domains": [{"domain": "lobbying", "sources": ["us/senate-lda"], "why": "w"}]}})
        self.assertTrue(shown["available"])
        [domain] = shown["domains"]
        self.assertEqual((domain["sources"][0]["title"], [d["method"] for d in domain["detectors"]]), ("Senate LDA", [FILINGS]))
        self.assertIsNone(adapter.view({}))


class Run(AnomalyTestCase):
    def run_cli(self, request: dict) -> subprocess.CompletedProcess:
        return cli("run", str(self.case), stdin=json.dumps(request))

    def runs(self) -> list[dict]:
        return adapter.signal_runs(self.case)

    def test_a_core_detector_runs_on_one_case_table_and_its_signals_are_recorded(self) -> None:
        self.opt_in("any_table")
        out = self.run_cli({"method": ZSCORE, "table": "research/payments.csv", "fields": {"id": "id", "amount": "amount"}})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["candidates"], ["row-3"])
        [run] = self.runs()
        sha = "sha256:" + hashlib.sha256(TABLE.encode()).hexdigest()
        self.assertEqual((run["method"], run["tables"], run["false_positives"]),
                         (ZSCORE, {"*": {"path": "research/payments.csv", "sha256": sha}}, ["legitimate large payments"]))
        [call] = self.anomaly.calls("anomaly.oneshot")
        self.assertIn("--approved", call["args"])
        self.assertNotIn("OPENROUTER_API_KEY", call["env"])
        self.assertEqual((self.case / "research" / "payments.csv").read_text(), TABLE, "the table is never changed")

    def test_a_domain_detector_takes_one_file_per_contract_table(self) -> None:
        self.opt_in("lobbying")
        (self.case / "research" / "foreign-entities.csv").write_text("id,x\n9,5000\n")
        out = self.run_cli({"method": FILINGS, "tables": {"lobbying_registrations": "research/payments.csv",
                                                           "lobbying_foreign_entities": "research/foreign-entities.csv"}})
        self.assertEqual(out.returncode, 0, out.stderr)
        [call] = self.anomaly.calls("anomaly.oneshot")
        self.assertEqual(sorted(arg.split("=")[0] for arg in call["args"] if "=" in arg), ["lobbying_foreign_entities", "lobbying_registrations"])
        self.assertEqual(len(self.runs()[0]["signals"]), 1)

    def test_a_request_that_cannot_run_is_refused_before_anomaly_runs(self) -> None:
        core = {"method": ZSCORE, "table": "research/payments.csv", "fields": {"id": "id", "amount": "amount"}}
        (self.case / "data" / "notes.csv").write_text(TABLE)
        os.symlink(self.case / "research" / "payments.csv", self.case / "research" / "link.csv")
        cases = {
            "no opt-in": (None, core, "did not opt in"),
            "a domain the plan does not name": ("any_table", {"method": FILINGS, "tables": {"lobbying_registrations": "research/payments.csv", "lobbying_foreign_entities": "research/payments.csv"}}, "does not name"),
            "a method outside the catalogue": ("any_table", {**core, "method": "method:made.up@1.0.0"}, "not a method in the pinned"),
            "a core detector given tables": ("any_table", {"method": ZSCORE, "tables": {"x": "research/payments.csv"}}, "give exactly method, table and fields"),
            "a field role left unmapped": ("any_table", {**core, "fields": {"id": "id"}}, "missing ['amount']"),
            "a domain detector missing a table": ("lobbying", {"method": FILINGS, "tables": {"lobbying_registrations": "research/payments.csv"}}, "reads exactly the tables"),
            "a file outside research/ and evidence/": ("any_table", {**core, "table": "data/notes.csv"}, "under research/ or evidence/"),
            "a path out of the case": ("any_table", {**core, "table": "research/../../anomaly/standin.py"}, "under research/ or evidence/"),
            "a symlink": ("any_table", {**core, "table": "research/link.csv"}, "not a regular case file"),
            "a file the runner cannot read": ("any_table", {**core, "table": "research/notes.txt"}, ".csv, .jsonl or .parquet"),
        }
        for label, (domain, request, reason) in cases.items():
            with self.subTest(label):
                self.opt_in(*([domain] if domain else []))
                out = self.run_cli(request)
                self.assertEqual(out.returncode, 2, out.stdout)
                self.assertIn(reason, out.stderr)
        self.assertEqual(self.anomaly.calls("anomaly.oneshot"), [])
        self.assertEqual(self.runs(), [])

    def test_output_that_breaks_the_declared_schema_or_a_refusing_runner_records_nothing(self) -> None:
        self.opt_in("any_table")
        core = {"method": ZSCORE, "table": "research/payments.csv", "fields": {"id": "id", "amount": "amount"}}
        for label, behaviour, reason in (("a finding, not a lead", {"set": {"status": "finding"}}, "status is 'finding'"),
                                         ("another detector's hash", {"set": {"detector_hash": "sha256:" + "f" * 64}}, "detector_hash"),
                                         ("a refusing runner", {"exit": 2}, "the runner refused")):
            with self.subTest(label):
                self.anomaly.behave(**behaviour)
                out = self.run_cli(core)
                self.assertEqual(out.returncode, 2)
                self.assertIn(reason, out.stderr)
        self.assertEqual(self.runs(), [])


class Gate(AnomalyTestCase):
    def test_each_signal_is_an_item_the_member_accepts_or_dismisses(self) -> None:
        self.opt_in("any_table")
        adapter.run_method(self.case, {"method": ZSCORE, "table": "research/payments.csv", "fields": {"id": "id", "amount": "amount"}})
        content, items = review._anomaly_content(self.case)
        self.assertEqual((content["total"], content["runs"][0]["false_positives"]), (1, ["legitimate large payments"]))
        [item] = items
        self.assertEqual((item["kind"], item["key"], item["suggested"], item["required"]), ("signal", "signal:signal-3", None, False))
        by_key = {item["key"]: item}
        self.assertEqual(review._validated_edit({"kind": "mark", "key": item["key"], "value": "accept"}, by_key)["value"], "accept")
        with self.assertRaises(review.OrchestrationError):
            review._validated_edit({"kind": "mark", "key": item["key"], "value": "verified"}, by_key)
        self.assertEqual(review._anomaly_content(self.tmp / "nowhere"), (None, []))


if __name__ == "__main__":
    unittest.main()
