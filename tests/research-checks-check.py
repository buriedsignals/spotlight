#!/usr/bin/env python3
"""Checks D1-D3, D5, D6, D8 and D10 of the Research phase (scripts/research_checks.py).

Each check has a passing case and at least one refused case. D4 (merge), the
clue rules and ranking are in tests/research-merge-check.py; D7 is owned by
tests/research-triage-check.py; D9 by harness/pi/src/launcher.test.ts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lane_fixture import ROOT, Case, checks, clone, methodology, topic  # noqa: E402

CATALOGUE = {"web", "registries", "archives", "social", "technical", "news-courts", "local-corpus",
             "prior-knowledge", "ad-hoc"}
VALIDATOR = ROOT / "scripts" / "validate-methodology-navigator.py"
RESEARCH_OFF = "Research is off; turn on Research in the app to add a research plan"


def d1(method: dict, *, sensitive: bool = False, newsroom: bool = False, local_model: bool = True) -> list[str]:
    return checks.d1_lane_plan(method, catalogue=CATALOGUE, sensitive=sensitive, newsroom=newsroom, local_model=local_model)


def plan_of(method: dict) -> dict:
    return method["research_plan"]


def local_corpus(method: dict, **extra) -> dict:
    brief = topic("ownership-local-corpus", "ownership", "local-corpus", route="local", **extra)
    plan_of(method)["topics"].append(brief)
    plan_of(method)["egress"].append({"topic_id": brief["topic_id"], "destination": "local_model"})
    return brief


class D1LanePlan(unittest.TestCase):
    def test_a_runnable_plan_passes(self) -> None:
        method = methodology()
        local_corpus(method)
        self.assertEqual(d1(method), [])
        # Consent is the Methodology approval: an older plan's model-written stamp stays valid and is not read.
        plan_of(method)["consent"].update(decided_by="Tom Vaillant", decided_at="2025-01-15T12:00:00Z")
        self.assertEqual(d1(method), [])

    def test_a_case_without_a_research_plan_has_nothing_to_check(self) -> None:
        method = methodology()
        del method["research_plan"]
        self.assertEqual(d1(method), [])

    def test_each_broken_rule_is_named(self) -> None:
        def no_budget(p): p.pop("round_budget_usd")
        def over_ceiling(p): p["case_budget_usd"] = 6
        def round_over_case(p): p.update(round_budget_usd=3, case_budget_usd=2)
        def bad_topic_id(p): p["topics"][0]["topic_id"] = "registries-ownership"; p["egress"][0]["topic_id"] = "registries-ownership"
        def no_questions(p): p["topics"][0]["questions"] = []
        def no_lane_type(p): p["topics"][0].pop("lane_type")
        def unknown_lane(p): p["topics"][0].update(lane_type="darkweb", topic_id="ownership-darkweb"); p["egress"][0]["topic_id"] = "ownership-darkweb"
        def unknown_direction(p): p["topics"][0].update(direction_id="family", topic_id="family-registries"); p["egress"][0]["topic_id"] = "family-registries"
        def no_token_budget(p): p["topics"][0].pop("token_budget")
        # gold-mozilla s0: 5,000 tokens let neither lane make a call.
        def token_budget_below_one_call(p): p["topics"][0]["token_budget"] = 5000
        def no_egress(p): p["egress"] = p["egress"][1:]
        def no_opt_in(p): p["consent"]["swarm"] = False
        def local_only_cloud(p): p["mode"] = "local_only"
        def key_on_ad_hoc(p):
            p["topics"].append(topic("ownership-ad-hoc", "ownership", "ad-hoc"))
            p["egress"].append({"topic_id": "ownership-ad-hoc", "destination": "public_web"})
            p["keys"].append({"name": "REGISTRY_API_KEY", "topics": ["ownership-ad-hoc"]})
        def too_many_methods(p):
            p["topics"][0]["anomaly_methods"] = [f"method:m{i}@1.0" for i in range(6)]
            p["topics"][1]["anomaly_methods"] = [f"method:n{i}@1.0" for i in range(5)]
        def unknown_egress_topic(p): p["egress"].append({"topic_id": "ghost-web", "destination": "public_web"})
        # Plans the lane runner refuses or leaves out must fail here, against the schema.
        def unknown_destination(p): p["egress"][0]["destination"] = "openrouter_swarm"
        # A web lane with no route named still runs on the swarm, so it needs the opt-in.
        def web_lane_without_opt_in(p):
            brief = topic("ownership-web", "ownership", "web")
            brief.pop("route")
            p["topics"] = [brief]
            p["egress"] = [{"topic_id": "ownership-web", "destination": "public_web"}]
            p["consent"]["swarm"] = False
        # A topic route the lane runner refuses: a web lane runs only on the swarm.
        def web_lane_on_local_route(p): p["topics"][0]["route"] = "local"
        def subscription_route_in_swarm_mode(p): p["topics"][0]["route"] = "subscription"

        cases = {
            "round_budget_usd must be set": no_budget,
            "above the $5 per-case ceiling": over_ceiling,
            "round_budget_usd is above the case budget": round_over_case,
            "topic id must be <direction_id>-<lane_type>[-n]": bad_topic_id,
            "needs at least one question": no_questions,
            "needs a lane type": no_lane_type,
            "is not in the lane catalogue": unknown_lane,
            "is not a direction_id of investigation_plan": unknown_direction,
            "needs a token budget": no_token_budget,
            "token_budget 5000 is below one lane call; set at least 20000": token_budget_below_one_call,
            'add a research_plan.egress entry {"topic_id": "ownership-registries"': no_egress,
            "needs the member's swarm opt-in": no_opt_in,
            "local_only mode runs no cloud topic": local_only_cloud,
            "an ad-hoc topic gets no keys": key_on_ad_hoc,
            "at most 10 across the plan": too_many_methods,
            "egress names an unknown topic": unknown_egress_topic,
            "research_plan.egress[0].destination: must be one of": unknown_destination,
            "(ownership-web): a swarm topic needs the member's swarm opt-in": web_lane_without_opt_in,
            "a registries lane runs on the swarm route, not 'local'": web_lane_on_local_route,
            "the subscription route needs mode subscription_subagents": subscription_route_in_swarm_mode,
        }
        for expected, breaker in cases.items():
            with self.subTest(expected):
                method = methodology()
                breaker(plan_of(method))
                problems = d1(method)
                self.assertTrue(any(expected in p for p in problems), problems)

    # gold-mozilla a2 on 3f37dcc3: a list-shaped plan was read as no plan and passed.
    def test_a_plan_that_is_not_an_object_is_named(self) -> None:
        method = methodology()
        method["research_plan"] = [plan_of(method)]
        self.assertEqual(len(d1(method)), 1)
        self.assertIn("research_plan must be an object", d1(method)[0])

    def test_sensitive_mode_allows_only_the_local_corpus_lane(self) -> None:
        method = methodology()
        problems = d1(method, sensitive=True)
        self.assertEqual(sum("sensitive mode allows only the local corpus lane" in p for p in problems), 3)
        only_local = methodology(topic("ownership-local-corpus", "ownership", "local-corpus", route="local"))
        plan_of(only_local)["egress"] = [{"topic_id": "ownership-local-corpus", "destination": "local_model"}]
        self.assertEqual(d1(only_local, sensitive=True), [])

    def test_journalist_input_only_on_the_granted_local_corpus_lane(self) -> None:
        method = methodology()
        local_corpus(method, journalist_input=True)
        self.assertIn("journalist input was not granted in the consent record", " ".join(d1(method)))
        plan_of(method)["consent"]["journalist_input"] = True
        self.assertEqual(d1(method), [])
        plan_of(method)["topics"][0]["journalist_input"] = True  # a swarm registries topic
        problems = " ".join(d1(method))
        self.assertIn("journalist input is read only by the local corpus lane", problems)
        self.assertIn("runs only on the local model", problems)

    def test_the_local_corpus_lane_never_goes_to_the_cloud_and_needs_a_local_model(self) -> None:
        method = methodology()
        brief = local_corpus(method)
        brief["route"] = "subscription"
        self.assertIn("runs only on the local model", " ".join(d1(method)))
        brief["route"] = "local"
        self.assertIn("needs a local model endpoint", " ".join(d1(method, local_model=False)))

    def test_research_off_refuses_any_research_plan_and_on_keeps_a_local_only_plan(self) -> None:
        local_only = methodology(topic("ownership-local-corpus", "ownership", "local-corpus", route="local"))
        plan_of(local_only)["egress"] = [{"topic_id": "ownership-local-corpus", "destination": "local_model"}]
        no_plan = methodology()
        del no_plan["research_plan"]
        with unittest.mock.patch.dict(os.environ, {"SPOTLIGHT_RESEARCH_SWARMS": "off"}):
            self.assertEqual(checks.d1_from_config(local_only, {}, local_model=True), [RESEARCH_OFF])
            self.assertEqual(checks.d1_from_config(no_plan, {}, local_model=True), [])
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("SPOTLIGHT_RESEARCH_SWARMS", None)
            for config in ({"integrations": []}, {"integrations": {"research_swarms": True}},
                           {"integrations": {"research_swarms": {"enabled": "true"}}}):
                problems = checks.d1_from_config(no_plan, config, local_model=True)
                self.assertEqual(len(problems), 1, config)
                self.assertIn("must be", problems[0])
        with unittest.mock.patch.dict(os.environ, {"SPOTLIGHT_RESEARCH_SWARMS": "maybe"}):
            self.assertEqual(checks.d1_from_config(no_plan, {}, local_model=True),
                             ['SPOTLIGHT_RESEARCH_SWARMS must be "on" or "off".'])
        with unittest.mock.patch.dict(os.environ, {"SPOTLIGHT_RESEARCH_SWARMS": "on", "SPOTLIGHT_SENSITIVE": ""}):
            self.assertEqual(checks.d1_from_config(local_only, {}, local_model=True), [])

    def test_newsroom_mode_sends_nothing_to_openrouter(self) -> None:
        method = methodology()
        problems = d1(method, newsroom=True)
        self.assertTrue(any("newsroom mode allows no OpenRouter swarm" in p for p in problems))
        self.assertTrue(any("newsroom mode sends nothing to OpenRouter" in p for p in problems))
        # A web lane takes its catalogue route, the swarm, which the newsroom edition points at its own endpoint.
        brief = topic("ownership-web", "ownership", "web")
        brief.pop("route")
        newsroom = methodology(brief, mode="newsroom")
        plan_of(newsroom)["egress"] = [{"topic_id": "ownership-web", "destination": "newsroom"}]
        self.assertEqual(d1(newsroom, newsroom=True), [])

    def test_the_methodology_validator_runs_d1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            method = methodology(topic("ownership-ad-hoc", "ownership", "ad-hoc"))
            plan_of(method)["egress"] = [{"topic_id": "ownership-ad-hoc", "destination": "openrouter"}]
            Case(case, method)
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"model_tier": "frontier", "integrations": {}}), encoding="utf-8")

            def validate(**extra: str) -> subprocess.CompletedProcess[str]:
                env = {k: v for k, v in os.environ.items()
                       if k not in ("SPOTLIGHT_SENSITIVE", "SPOTLIGHT_MODEL_BASE_URL", "SPOTLIGHT_RESEARCH_SWARMS")}
                return subprocess.run([sys.executable, str(VALIDATOR), str(case), "--config", str(config)],
                                      text=True, capture_output=True, check=False, env={**env, **extra})

            # Subscription path: no launcher, the install config's switch is off.
            self.assertIn(RESEARCH_OFF, validate().stderr)
            config.write_text(json.dumps({"model_tier": "frontier", "integrations": {"research_swarms": {"enabled": True}}}),
                              encoding="utf-8")
            self.assertEqual(validate().returncode, 0, validate().stderr)
            # Pi path: the harness passes its switch, which wins over the config.
            self.assertIn(RESEARCH_OFF, validate(SPOTLIGHT_RESEARCH_SWARMS="off").stderr)
            # Present but blank is off too, as the harness reads it; only an absent variable defers to the config.
            self.assertIn(RESEARCH_OFF, validate(SPOTLIGHT_RESEARCH_SWARMS="").stderr)
            # Any other present value is a configuration error, never a silent off.
            self.assertIn('SPOTLIGHT_RESEARCH_SWARMS must be "on" or "off"', validate(SPOTLIGHT_RESEARCH_SWARMS="maybe").stderr)
            self.assertEqual(validate(SPOTLIGHT_RESEARCH_SWARMS="yes").returncode, 0)
            method["research_plan"]["consent"]["swarm"] = False
            Case.write_json(case / "data" / "methodology.json", method)
            result = validate()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("needs the member's swarm opt-in", result.stderr)


class LaneChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Case(Path(self.tmp.name) / "case")
        self.plan = self.case.methodology["research_plan"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def lane(self, topic_id: str = "ownership-registries"):
        return checks.read_lane(self.case.root, self.case.brief(topic_id))

    # D2
    def test_d2_a_lane_written_as_specified_validates(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="Shareholder: Example Trust", excerpt="Shareholder: Example Trust")
        self.case.finish_all()
        lane, leads, raw, problems = self.lane()
        self.assertEqual(problems, [])
        self.assertEqual([l["lead_id"] for l in leads], ["lead-1"])
        self.assertEqual(checks.d2_unapproved_folders(self.case.root, self.plan), [])

    def test_d2_refuses_unapproved_folders_bad_lines_and_foreign_leads(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="a", excerpt="a")
        self.case.finish_all()
        (self.case.root / "research" / "swarm" / "stray-web").mkdir()
        self.assertEqual(checks.d2_unapproved_folders(self.case.root, self.plan), ["stray-web"])
        leads_file = self.case.folder("ownership-registries") / "leads.jsonl"
        good = leads_file.read_text(encoding="utf-8")
        for content, expected in (
            (good + "not json\n", "is not JSON"),
            (good + json.dumps({"lead_id": "x"}) + "\n", "leads.jsonl line 2"),
            (good.replace('"ownership-registries"', '"ownership-web"'), "belongs to another topic"),
            (good + good, "repeats lead_id"),
        ):
            with self.subTest(expected):
                leads_file.write_text(content, encoding="utf-8")
                problems = self.lane()[3]
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_d2_refuses_a_lane_json_for_another_brief_and_a_symlink(self) -> None:
        self.case.finish("ownership-registries", lane_type="web")
        self.assertIn("lane_type 'web' differs from the approved brief", " ".join(self.lane()[3]))
        self.case.finish("ownership-registries")
        (self.case.folder("ownership-registries") / "sources").mkdir(exist_ok=True)
        (self.case.folder("ownership-registries") / "sources" / "link").symlink_to(self.case.root / "data" / "methodology.json")
        self.assertIn("is a symlink", " ".join(self.lane()[3]))

    # D3
    def test_d3_a_verbatim_excerpt_passes_even_across_a_line_break(self) -> None:
        lead = self.case.page_lead("ownership-registries", "lead-1", page_text="Shareholder:\n  Example Trust (100 shares)",
                                   excerpt="Shareholder: Example Trust (100 shares)")
        self.assertIsNone(checks.d3_excerpt(self.case.folder("ownership-registries"), lead))

    def test_d3_a_paraphrase_a_changed_file_or_a_snippet_is_unverifiable(self) -> None:
        folder = self.case.folder("ownership-registries")
        paraphrase = self.case.page_lead("ownership-registries", "lead-1", page_text="Shareholder: Example Trust",
                                         excerpt="Example Trust is the sole shareholder")
        self.assertEqual(checks.d3_excerpt(folder, paraphrase), "excerpt is not verbatim in the stored source")
        changed = self.case.page_lead("ownership-registries", "lead-2", page_text="Director: A. Person", excerpt="Director: A. Person")
        (folder / changed["derivatives"][0]["content_ref"]).write_text("Director: B. Person", encoding="utf-8")
        self.assertIn("sha256 does not match", checks.d3_excerpt(folder, changed))
        escaped = dict(changed, content_ref="../../../data/methodology.json")
        self.assertIn("is not a file under sources/", checks.d3_excerpt(folder, escaped))
        snippet = self.case.snippet_lead("ownership-registries", "lead-3", "Northwind wins", "https://news.example.org/a")
        self.assertIn("snippet only", checks.d3_excerpt(folder, snippet))

    # D5
    def test_d5_a_lane_over_its_budget_must_have_stopped(self) -> None:
        brief = self.case.brief("ownership-registries")
        within = {"status": "completed", "budget": {"usd": 0.2}, "spend": {"usd": 0.1, "tokens": 5000}}
        self.assertEqual(checks.d5_budget(brief, within), [])
        over_tokens = {"status": "completed", "spend": {"usd": 0.1, "tokens": brief["token_budget"] + 1}}
        self.assertIn("did not stop", checks.d5_budget(brief, over_tokens)[0])
        self.assertEqual(checks.d5_budget(brief, {**over_tokens, "status": "partial"}), [])

    # D6
    def test_d6_lists_directions_without_a_qualifying_lead_with_the_queries_tried(self) -> None:
        verified = {"direction_id": "ownership", "absence": False, "status": "candidate"}
        unverifiable = {"direction_id": "contracts", "absence": False, "status": "unverifiable"}
        absence = {"direction_id": "contracts", "absence": True, "status": "candidate", "queries_tried": ["harbour award"]}
        lanes = {"contracts-news-courts": {"queries_tried": ["harbour tender"]}}
        gaps = checks.d6_coverage(["ownership", "contracts"], [verified, unverifiable, absence], lanes, self.plan)
        self.assertEqual(gaps, [{"direction_id": "contracts", "queries_tried": ["harbour award", "harbour tender"]}])
        self.assertEqual(checks.d6_coverage(["ownership"], [verified], {}, self.plan), [])


def review(**marks: str) -> dict:
    return {"items": {target: {"target": target, "final": mark} for target, mark in marks.items()}}


class D8Amendment(unittest.TestCase):
    def setUp(self) -> None:
        self.approved = methodology()

    def amended(self) -> dict:
        return clone(self.approved)

    def test_an_amendment_that_follows_the_marks_passes(self) -> None:
        after = self.amended()
        after["investigation_plan"][0]["steps"].append({"order": 2, "action": "Read the trust deed", "tool": "fetch", "cites": ["clue:k1-abc"]})
        del after["investigation_plan"][1]
        marks = review(**{"clue:k1-abc": "pursue", "direction:contracts": "drop"})
        self.assertEqual(checks.d8_amendment(self.approved, after, marks), [])

    def test_an_unmarked_change_is_refused(self) -> None:
        cases = {
            "cites no pursued clue or direction": lambda m: m["investigation_plan"][0]["steps"].append(
                {"order": 2, "action": "Search everything", "tool": "search", "cites": ["clue:k1-abc"]}),
            "dropped but not marked drop": lambda m: m["investigation_plan"].pop(1),
            "added but not marked add": lambda m: m["investigation_plan"].append(
                {"direction": "Family", "direction_id": "family", "questions": [], "steps": []}),
            "an approved step was changed or removed": lambda m: m["investigation_plan"][0]["steps"][0].update(tool="search"),
            "only new steps may change it": lambda m: m["investigation_plan"][0]["questions"].append("Anything else?"),
            "lead changed": lambda m: m.update(lead="A different premise"),
        }
        marks = review(**{"clue:k1-abc": "dismiss"})
        for expected, change in cases.items():
            with self.subTest(expected):
                after = self.amended()
                change(after)
                problems = checks.d8_amendment(self.approved, after, marks)
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_a_drop_mark_the_amendment_ignored_is_refused(self) -> None:
        problems = checks.d8_amendment(self.approved, self.amended(), review(**{"direction:contracts": "drop"}))
        self.assertEqual(problems, ["direction contracts was marked drop but is still in the plan"])


class D10DirectionsCovered(unittest.TestCase):
    def test_every_direction_and_pursued_clue_needs_a_finding_or_a_gap(self) -> None:
        method = methodology()
        marks = review(**{"clue:k1-abc": "pursue", "clue:k4-def": "dismiss"})
        findings = {"findings": [{"id": "F1", "research_refs": ["direction:ownership"]}]}
        self.assertEqual(checks.d10_directions_covered(method, findings, marks),
                         ["clue:k1-abc has no finding and no explicit gap",
                          "direction:contracts has no finding and no explicit gap"])
        findings["research_gaps"] = [{"ref": "direction:contracts", "reason": "The award is not published."},
                                     {"ref": "clue:k1-abc", "reason": "The trust deed is not public."}]
        self.assertEqual(checks.d10_directions_covered(method, findings, marks), [])

    def test_an_empty_reason_is_not_a_gap_and_old_cases_are_not_checked(self) -> None:
        method = methodology()
        findings = {"findings": [{"id": "F1", "research_refs": ["direction:ownership"]}],
                    "research_gaps": [{"ref": "direction:contracts", "reason": " "}]}
        self.assertEqual(checks.d10_directions_covered(method, findings, None),
                         ["direction:contracts has no finding and no explicit gap"])
        del method["research_plan"]
        self.assertEqual(checks.d10_directions_covered(method, {"findings": []}, None), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
