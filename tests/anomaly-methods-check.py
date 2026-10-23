#!/usr/bin/env python3
"""Anomaly clue methods (integrations/anomaly): the pinned catalogue hides the
feature unless it matches, Methodology validation and the Methodology view see
the methods, and approved methods run on lane tables into clues through the
research merge. A stand-in Anomaly install (catalogue + one-shot runner) keeps
it offline; the stand-in records how it was called.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lane_fixture import ROOT, Case, merge, methodology, topic  # noqa: E402

import schema_check  # noqa: E402

sys.path.insert(0, str(ROOT))
from integrations.anomaly import adapter  # noqa: E402
from spotlight_orchestration import review  # noqa: E402

ZSCORE = "method:numeric.zscore_outliers@1.1.0"
DUPLICATES = "method:table.duplicate_rows@1.1.0"
NAMED_TABLES = "method:us_lobbying.foreign_filings@1.1.0"
SIGNAL_SCHEMA = {
    "candidate_id": "string", "category": "string", "detector_hash": "string", "detector_id": "string",
    "detector_version": "string", "severity": "string", "signal_id": "string", "status": "string",
    "summary": "string", "values": "object",
    "provenance": {"approval": "string", "executed_at": "string", "field_map": "object", "input_format": "string",
                   "input_hash": "string", "limits": "object", "parameters": "object", "run_id": "string"},
}
DETECTORS = [
    {"id": "numeric.zscore_outliers", "version": "1.1.0", "implementation_hash": "sha256:" + "a" * 64,
     "required_tables": ["*"], "required_fields": [{"name": "id", "types": None}, {"name": "amount", "types": None}],
     "signal_category": "anomaly", "severity": "medium", "title": "Z-score outliers",
     "description": "Finds numeric outliers.", "expected_output": ["candidate_id"], "assumptions": ["numeric amounts"],
     "false_positives": ["legitimate large payments"], "parameters": {}},
    {"id": "table.duplicate_rows", "version": "1.1.0", "implementation_hash": "sha256:" + "e" * 64,
     "required_tables": ["*"], "required_fields": [{"name": "id", "types": None}], "signal_category": "anomaly",
     "severity": "medium", "title": "Duplicate rows", "description": "Finds repeated rows.",
     "expected_output": ["candidate_id"], "parameters": {}},
    {"id": "us_lobbying.foreign_filings", "version": "1.1.0", "implementation_hash": "sha256:" + "b" * 64,
     "required_tables": ["senate_filings"], "required_fields": [], "signal_category": "anomaly", "severity": "low",
     "title": "Foreign filings", "description": "Needs a prepared case.", "expected_output": ["candidate_id"],
     "parameters": {}},
]

# The stand-in runner: `python -I -m anomaly.oneshot --table T --request R --approved`.
# It records each call and answers as behaviour.json says.
STAND_IN = textwrap.dedent('''
    import hashlib, json, os, stat, sys
    from pathlib import Path
    home = Path(__file__).resolve().parent
    args = sys.argv[1:]
    table = Path(args[args.index("--table") + 1])
    requests = json.loads(Path(args[args.index("--request") + 1]).read_text())
    with open(home / "calls.jsonl", "a") as log:
        log.write(json.dumps({"args": args, "requests": requests, "env": sorted(os.environ),
                              "table_mode": stat.S_IMODE(table.stat().st_mode)}) + "\\n")
    behaviour = json.loads((home / "behaviour.json").read_text())
    if behaviour.get("exit"):
        print("anomaly.oneshot: refused for the test", file=sys.stderr)
        sys.exit(behaviour["exit"])
    input_hash = "sha256:" + hashlib.sha256(table.read_bytes()).hexdigest()
    for request in requests:
        for n, row in enumerate(table.read_text().splitlines()[1:], start=1):
            if float(row.split(",")[1]) < 1000:
                continue
            signal = {"status": "lead", "signal_id": f"signal-{n}", "detector_id": request["id"],
                      "detector_version": request["version"], "detector_hash": request["implementation_hash"],
                      "candidate_id": f"row-{n}", "category": "anomaly", "severity": "medium",
                      "summary": "Z-score outliers", "values": {"candidate_id": f"row-{n}", "amount": row},
                      "provenance": {"input_hash": input_hash, "input_format": "csv", "field_map": request["fields"],
                                     "parameters": {}, "limits": {}, "run_id": os.urandom(4).hex(),
                                     "executed_at": os.urandom(4).hex(), "approval": "caller-recorded"}}
            for key, value in behaviour.get("set", {}).items():
                if value is None:
                    signal.pop(key, None)
                elif key.startswith("provenance."):
                    signal["provenance"][key.split(".", 1)[1]] = value
                else:
                    signal[key] = value
            print(json.dumps(signal))
''')

TABLE = "id,amount\n1,12\n2,15\n3,48000\n"


def catalogue(detectors=DETECTORS) -> dict:
    body = {"schema_version": 1, "detectors": copy.deepcopy(detectors), "signal_schema": SIGNAL_SCHEMA}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "catalogue_hash": f"sha256:{digest}"}


class StandIn:
    """An Anomaly install: detectors/catalogue.json and a .venv python that is the stand-in runner."""

    def __init__(self, home: Path, document: dict | None = None) -> None:
        self.home = home
        self.document = document or catalogue()
        (home / "detectors").mkdir(parents=True)
        (home / "detectors" / "catalogue.json").write_text(json.dumps(self.document, indent=2))
        python = home / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        (home / "standin.py").write_text(STAND_IN)
        python.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{home / 'standin.py'}' \"$@\"\n")
        python.chmod(0o755)
        self.behave()
        self.pin = {"release": "0" * 40, "schema_version": 1, "catalogue_hash": self.document["catalogue_hash"]}

    def behave(self, **behaviour) -> None:
        (self.home / "behaviour.json").write_text(json.dumps(behaviour))

    def calls(self) -> list[dict]:
        log = self.home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


class AnomalyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.anomaly = StandIn(self.tmp / "anomaly")
        self.env(ANOMALY_HOME=str(self.anomaly.home))
        self.pin(self.anomaly.pin)

    def env(self, **values: str | None) -> None:
        clean = {"SPOTLIGHT_CONFIG": str(self.tmp / "no-config.json"), "OPENROUTER_API_KEY": "sk-or-test-secret"}
        patcher = mock.patch.dict(os.environ, clean)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def pin(self, pin: dict) -> None:
        patcher = mock.patch.object(adapter, "PIN", pin)
        patcher.start()
        self.addCleanup(patcher.stop)


class Catalogue(AnomalyTestCase):
    def test_the_pinned_catalogue_offers_only_single_table_methods(self) -> None:
        self.assertTrue(adapter.state()["available"])
        offered = adapter.methods()
        self.assertEqual([m["method"] for m in offered], [ZSCORE, DUPLICATES])
        self.assertEqual(offered[0]["required_fields"], ["id", "amount"])

    def test_anything_but_the_pinned_catalogue_hides_the_feature(self) -> None:
        tampered = catalogue()
        tampered["detectors"][0]["required_tables"] = ["senate_filings"]  # content changed, pinned hash kept
        tampered["catalogue_hash"] = self.anomaly.pin["catalogue_hash"]
        other = catalogue([DETECTORS[0]])  # a valid catalogue, but another release
        cases = {"no install": None, "another release": other, "content changed under the pinned hash": tampered}
        for label, document in cases.items():
            with self.subTest(label):
                os.environ["ANOMALY_HOME"] = str(self.anomaly.home)
                if document is None:
                    os.environ.pop("ANOMALY_HOME")
                else:
                    (self.anomaly.home / "detectors" / "catalogue.json").write_text(json.dumps(document))
                self.assertFalse(adapter.state()["available"])
                self.assertEqual(adapter.methods(), [])
                self.assertIsNone(adapter.view(methodology()))

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


class PlanValidation(AnomalyTestCase):
    def plan(self, *refs: str) -> dict:
        return methodology(topic("ownership-registries", "ownership", "registries", anomaly_methods=list(refs)))

    def test_named_methods_must_be_single_table_methods_of_the_pinned_catalogue(self) -> None:
        self.assertEqual(adapter.plan_problems(self.plan(ZSCORE)), [])
        problems = adapter.plan_problems(self.plan(ZSCORE, NAMED_TABLES, "method:numeric.zscore_outliers@1.0.0",
                                                   "method:made.up@1.0.0"))
        self.assertEqual(len(problems), 3)
        self.assertFalse(any(ZSCORE + " " in p for p in problems))

    def test_methods_named_without_the_catalogue_fail_and_a_plan_without_methods_never_needs_it(self) -> None:
        os.environ.pop("ANOMALY_HOME")
        self.assertEqual(adapter.plan_problems(methodology()), [])
        self.assertEqual(len(adapter.plan_problems(self.plan(ZSCORE))), 1)


    def test_the_methodology_validator_refuses_methods_it_cannot_run(self) -> None:
        plan = methodology(topic("ownership-ad-hoc", "ownership", "ad-hoc"))
        plan["research_plan"]["egress"] = [{"topic_id": "ownership-ad-hoc", "destination": "openrouter"}]
        case = Case(self.tmp / "case", plan)
        config = self.tmp / "config.json"
        config.write_text(json.dumps({"model_tier": "frontier", "integrations": {}}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANOMALY_HOME", "SPOTLIGHT_SENSITIVE", "SPOTLIGHT_MODEL_BASE_URL")}

        def validate() -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(ROOT / "scripts" / "validate-methodology-navigator.py"),
                                   str(case.root), "--config", str(config)], text=True, capture_output=True,
                                  check=False, env=env)

        self.assertEqual(validate().returncode, 0, validate().stderr)
        plan["research_plan"]["topics"][0]["anomaly_methods"] = [ZSCORE]
        case.write_json(case.root / "data" / "methodology.json", plan)
        refused = validate()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("method catalogue is unavailable", refused.stderr)


class MethodView(AnomalyTestCase):
    def view(self, method: dict) -> dict:
        case = Case(self.tmp / "case", method)
        (case.root / "brief-directions.txt").write_text("Who owns Northwind Holdings?\n", encoding="utf-8")
        return review.build_view(case.root, "methodology")["content"]

    def test_the_methodology_view_lists_fitting_methods_and_each_topics_methods(self) -> None:
        content = self.view(methodology(topic("ownership-registries", "ownership", "registries", anomaly_methods=[ZSCORE])))
        shown = content["anomaly_methods"]
        self.assertEqual([m["method"] for m in shown["methods"]], [ZSCORE, DUPLICATES])
        self.assertEqual(shown["topics"]["ownership-registries"][0]["title"], "Z-score outliers")

    def test_the_view_shows_nothing_when_the_feature_is_hidden(self) -> None:
        os.environ.pop("ANOMALY_HOME")
        self.assertNotIn("anomaly_methods", self.view(methodology()))


class MethodClues(AnomalyTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.case = Case(self.tmp / "case", methodology(
            topic("ownership-registries", "ownership", "registries", anomaly_methods=[ZSCORE]),
            topic("ownership-web", "ownership", "web")))

    def table_lead(self, topic_id: str = "ownership-registries", excerpt: str = "3,48000") -> dict:
        ref, sha = self.case.store(topic_id, TABLE.encode(), ".csv")
        brief = self.case.brief(topic_id)
        lead = {"schema_version": "1.0", "lead_id": "payments", "topic_id": topic_id,
                "direction_id": brief["direction_id"], "summary": "Payments table from the register.",
                "subjects": ["Northwind Holdings"], "absence": False, "status": "candidate", "stored": "page",
                "capture": {"route": "api", "tool": "registry", "source_url": "https://register.example.org/payments.csv",
                            "captured_at": "2026-10-23T09:14:02Z"},
                "content_ref": ref, "content_sha256": sha,
                "provenance": {"method": "agent_candidate", "model": "deepseek/deepseek-v4-flash"},
                "excerpt": {"text": excerpt, "language": "en", "direct_quote": True}}
        self.case.lanes.setdefault(topic_id, []).append(lead)
        return lead

    def inputs(self, topic_id: str = "ownership-registries", **item) -> None:
        entry = {"method": ZSCORE, "lead_id": "payments", "format": "csv", "fields": {"id": "id", "amount": "amount"},
                 **item}
        self.case.write_json(self.case.folder(topic_id) / "method-inputs.json", {"schema_version": "1.0", "inputs": [entry]})

    def run_merge(self) -> tuple[list[dict], list[str]]:
        self.case.finish_all()
        report = merge.run(self.case.root, None)
        return [c for c in self.case.data("clues.json")["clues"] if c["source"].startswith("method:")], report

    def test_an_approved_method_turns_its_signals_into_a_tagged_clue(self) -> None:
        self.table_lead()
        self.inputs()
        clues, report = self.run_merge()
        self.assertEqual(len(clues), 1)
        self.assertEqual((clues[0]["source"], clues[0]["type"], clues[0]["lead_ids"]), (ZSCORE, "method_signal", ["payments"]))
        self.assertIn("row-3", clues[0]["summary"])
        self.assertEqual(schema_check.load(ROOT / "schemas" / "research-clues.schema.json").errors(self.case.data("clues.json")), [])
        self.assertIn(f"methods on payments: {ZSCORE} gave 1 signal(s)", report)
        [call] = self.anomaly.calls()
        self.assertEqual(call["args"][:3], ["-I", "-m", "anomaly.oneshot"])
        self.assertIn("--approved", call["args"])
        self.assertEqual(call["requests"], [{"id": "numeric.zscore_outliers", "version": "1.1.0",
                                             "implementation_hash": DETECTORS[0]["implementation_hash"],
                                             "fields": {"id": "id", "amount": "amount"}}])
        self.assertEqual(call["table_mode"] & 0o222, 0, "the runner's table copy is read-only")
        self.assertNotIn("OPENROUTER_API_KEY", call["env"])
        self.assertNotIn("ANOMALY_HOME", call["env"])

    def test_a_rerun_writes_byte_identical_clues(self) -> None:
        self.table_lead()
        self.inputs()
        self.run_merge()
        first = (self.case.root / "data" / "clues.json").read_bytes()
        merge.run(self.case.root, None)
        self.assertEqual((self.case.root / "data" / "clues.json").read_bytes(), first)
        self.assertEqual(len(self.anomaly.calls()), 2)

    def test_inputs_that_are_not_approved_or_not_verified_never_run(self) -> None:
        cases = {
            "method not approved for the topic": dict(method=DUPLICATES),
            "topic without methods": dict(topic_id="ownership-web"),
            "lead failed D3": dict(excerpt="not in the table"),
        }
        for label, setup in cases.items():
            with self.subTest(label):
                self.setUp()
                self.table_lead(topic_id=setup.get("topic_id", "ownership-registries"),
                                excerpt=setup.get("excerpt", "3,48000"))
                self.inputs(topic_id=setup.get("topic_id", "ownership-registries"), method=setup.get("method", ZSCORE))
                clues, _report = self.run_merge()
                self.assertEqual((clues, self.anomaly.calls()), ([], []))

    def test_output_that_breaks_the_declared_schema_raises_no_clue(self) -> None:
        cases = {
            "missing expected output column": {"values": {"amount": "3,48000"}},
            "another implementation": {"detector_hash": "sha256:" + "c" * 64},
            "another table": {"provenance.input_hash": "sha256:" + "d" * 64},
            "a field the schema does not declare": {"extra": "x"},
            "a field missing": {"signal_id": None},
            "not a lead": {"status": "finding"},
        }
        for label, overrides in cases.items():
            with self.subTest(label):
                self.setUp()
                self.anomaly.behave(set=overrides)
                self.table_lead()
                self.inputs()
                clues, report = self.run_merge()
                self.assertEqual(clues, [])
                self.assertTrue(any(line.startswith("methods on payments") and "output line 1" in line for line in report), report)

    def test_a_refusing_runner_or_a_hidden_feature_reports_and_still_merges(self) -> None:
        self.anomaly.behave(exit=2)
        self.table_lead()
        self.inputs()
        clues, report = self.run_merge()
        self.assertEqual(clues, [])
        self.assertTrue(any("refused for the test" in line for line in report), report)
        os.environ.pop("ANOMALY_HOME")
        clues, report = self.run_merge()
        self.assertEqual(clues, [])
        self.assertIn("methods not run: ANOMALY_HOME is not set", report)


if __name__ == "__main__":
    unittest.main()
