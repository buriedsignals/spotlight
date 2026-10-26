#!/usr/bin/env python3
"""The research merge (scripts/research-merge.py): D2, D3 and D5 applied per
lane, the D4 merge and its determinism, D6 gaps, the clue rules K1-K6 and the
Research page's ranking. Offline lane folders built by research_lane_fixture.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lane_fixture import ROOT, Case, merge, methodology, topic  # noqa: E402

import schema_check  # noqa: E402  (scripts/ is on the path through the fixture)

SCRIPT = ROOT / "scripts" / "research-merge.py"
SCHEMAS = ROOT / "schemas"


def run_merge(case: Case, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(case.root), *extra], text=True, capture_output=True, check=False)


class MergeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Case(Path(self.tmp.name) / "case")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def merged(self) -> tuple[dict, dict]:
        self.case.finish_all()
        result = run_merge(self.case)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.case.data("leads.json"), self.case.data("clues.json")

    def clues(self, source: str) -> list[dict]:
        return [c for c in self.merged()[1]["clues"] if c["source"] == source]


class Merge(MergeTestCase):
    def test_outputs_validate_and_bind_each_other(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="Shareholder: Example Trust", excerpt="Shareholder: Example Trust")
        self.case.finish("ownership-web", status="not_started")
        leads, clues = self.merged()
        self.assertEqual(schema_check.load(SCHEMAS / "research-leads.schema.json").errors(leads), [])
        self.assertEqual(schema_check.load(SCHEMAS / "research-clues.schema.json").errors(clues), [])
        lead_schema = schema_check.load(SCHEMAS / "research-lead.schema.json")
        self.assertEqual([e for entry in leads["leads"] for e in lead_schema.errors(entry["lead"])], [])
        self.assertEqual(clues["leads_sha256"], hashlib.sha256((self.case.root / "data" / "leads.json").read_bytes()).hexdigest())
        self.assertEqual(leads["left_out"], ["ownership-web"])
        self.assertEqual(leads["mode"], "openrouter_swarm")

    def test_d4_syndicated_copies_and_tracking_urls_collapse_into_one_source(self) -> None:
        text = "The harbour contract went to Northwind Holdings."
        self.case.page_lead("contracts-news-courts", "lead-a", page_text=text, excerpt=text, url="https://wire.example.org/story")
        self.case.page_lead("ownership-web", "lead-b", page_text=text, excerpt=text, url="https://mirror.example.net/copy")
        self.case.snippet_lead("ownership-registries", "lead-c", "Northwind award", "https://WWW.wire.example.org/story/?utm_source=x#top")
        self.case.page_lead("ownership-web", "lead-d", page_text="Another story entirely.", excerpt="Another story entirely.")
        leads, _ = self.merged()
        groups = {entry["lead"]["lead_id"]: sorted(c["lead_id"] for c in entry["copies"]) for entry in leads["leads"]}
        self.assertEqual(groups, {"lead-a": ["lead-b", "lead-c"], "lead-d": []})

    def test_d4_a_rerun_is_byte_identical_and_lead_order_does_not_matter(self) -> None:
        for n in range(4):
            self.case.page_lead("ownership-web", f"lead-{n}", page_text=f"Fact {n} about Northwind.", excerpt=f"Fact {n} about Northwind.")
        self.merged()
        first = [(self.case.root / "data" / name).read_bytes() for name in ("leads.json", "clues.json")]
        self.assertEqual(run_merge(self.case).returncode, 0)
        rerun = [(self.case.root / "data" / name).read_bytes() for name in ("leads.json", "clues.json")]
        self.assertEqual(first, rerun)
        # Lines in another order: the lane file's own hash changes, nothing else does.
        self.case.lanes["ownership-web"].reverse()
        self.case.finish("ownership-web")
        self.assertEqual(run_merge(self.case).returncode, 0)
        before, after = json.loads(first[0]), self.case.data("leads.json")
        self.assertEqual(before["leads"], after["leads"])
        self.assertEqual(json.loads(first[1])["clues"], self.case.data("clues.json")["clues"])

    def test_lanes_without_times_rerun_to_the_same_bytes(self) -> None:
        for brief in self.case.methodology["research_plan"]["topics"]:
            self.case.finish(brief["topic_id"], "not_started")
        self.assertEqual(run_merge(self.case).returncode, 0)
        first = (self.case.root / "data" / "leads.json").read_bytes()
        self.assertEqual(run_merge(self.case).returncode, 0)
        self.assertEqual(first, (self.case.root / "data" / "leads.json").read_bytes())

    def test_d3_a_paraphrased_excerpt_is_kept_as_unverifiable(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="Shareholder: Example Trust", excerpt="Example Trust owns it all")
        leads, _ = self.merged()
        self.assertEqual([e["lead"]["status"] for e in leads["leads"]], ["unverifiable"])

    def test_d2_an_invalid_lane_fails_and_contributes_no_lead(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="a", excerpt="a")
        self.case.finish_all()
        path = self.case.folder("ownership-registries") / "leads.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
        self.assertEqual(run_merge(self.case).returncode, 0)
        leads = self.case.data("leads.json")
        self.assertEqual(leads["leads"], [])
        self.assertEqual({l["topic_id"]: l["status"] for l in leads["lanes"]}["ownership-registries"], "failed")

    def test_the_merge_refuses_stray_folders_and_unfinished_lanes(self) -> None:
        self.case.finish("ownership-registries")
        self.case.finish("ownership-web", status="running")
        self.case.finish("contracts-news-courts")
        result = run_merge(self.case)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ownership-web is still running", result.stderr)
        self.case.finish("ownership-web")
        (self.case.root / "research" / "swarm" / "stray").mkdir()
        result = run_merge(self.case)
        self.assertIn("folders not in the approved research plan: stray", result.stderr)
        (self.case.root / "research" / "swarm" / "stray").rmdir()
        (self.case.folder("ownership-web") / "lane.json").unlink()
        self.assertIn("ownership-web has not ended", run_merge(self.case).stderr)
        self.assertFalse((self.case.root / "data" / "leads.json").exists())

    def test_d5_and_d6_are_reported(self) -> None:
        self.case.page_lead("ownership-registries", "lead-1", page_text="a b", excerpt="a b")
        self.case.finish("ownership-registries", spend={"usd": 0.01, "tokens": 10**6})
        self.case.absence_lead("contracts-news-courts", "gap-1", ["Northwind Holdings"], ["harbour award Northwind"])
        self.case.finish_all()
        result = run_merge(self.case)
        report = json.loads(result.stdout)["report"]
        self.assertIn("D5 ownership-registries: spent past its budget (tokens) but did not stop", report)
        leads = self.case.data("leads.json")
        self.assertEqual({l["topic_id"]: l["status"] for l in leads["lanes"]}["ownership-registries"], "partial")
        self.assertEqual(leads["gaps"], [{"direction_id": "contracts",
                                          "queries_tried": ["contracts-news-courts query", "harbour award Northwind"]}])


    def test_triage_reads_the_merge_and_its_rule_clues(self) -> None:
        self.case.page_lead("ownership-web", "a", page_text="Capital: 100 EUR. Listed by OFAC.", excerpt="Capital: 100 EUR. Listed by OFAC.",
                            facts=[{"subject": "Northwind Holdings", "attribute": "capital", "value": "100 EUR"}])
        self.case.page_lead("contracts-news-courts", "b", page_text="Capital: 900 EUR", excerpt="Capital: 900 EUR",
                            facts=[{"subject": "Northwind Holdings", "attribute": "capital", "value": "900 EUR"}])
        self.merged()
        spec = importlib.util.spec_from_file_location("research_triage_check", Path(__file__).with_name("research-triage-check.py"))
        triage_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(triage_check)
        server = triage_check.kev_server([])
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({"integrations": {"decisions": {"research_triage": {"backend": "local"}, "endpoint": {
            "url": f"http://127.0.0.1:{server.server_port}/v1/systemone", "api": "openrouter-decisions"}}}}), encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "SPOTLIGHT_CONFIG": str(config), "OPENROUTER_API_KEY": ""}
        try:
            done = subprocess.run([sys.executable, str(ROOT / "scripts" / "research-triage.py"), str(self.case.root)],
                                  env=env, capture_output=True, text=True, check=False)
        finally:
            server.shutdown()
        self.assertEqual(done.returncode, 0, done.stderr)
        triage = self.case.data("research-triage.json")
        self.assertEqual(triage["invalid_leads"], [])
        self.assertIn("watchlist", next(r for r in triage["leads"] if r["lead_id"] == "a")["clues"])
        self.assertEqual([c["lead_ids"] for c in triage["conflicts"]], [["a", "b"]])


class ClueRules(MergeTestCase):
    def test_k1_an_unnamed_actor_in_two_lanes_from_two_families(self) -> None:
        self.case.page_lead("ownership-registries", "a", page_text="Director: Jan Kowal", excerpt="Director: Jan Kowal",
                            subjects=["Northwind Holdings", "Jan Kowal"], url="https://registry.example.org/1")
        self.case.page_lead("contracts-news-courts", "b", page_text="Jan Kowal signed.", excerpt="Jan Kowal signed.",
                            subjects=["Jan Kowal"], url="https://court.example.net/2")
        clues = self.clues("K1")
        self.assertEqual([(c["type"], c["lead_ids"]) for c in clues], [("new_actor", ["a", "b"])])

    def test_k1_does_not_fire_for_a_named_subject_or_a_single_family(self) -> None:
        for lead_id, url in (("a", "https://news.example.org/1"), ("b", "https://news.example.org/2")):
            topic_id = "ownership-web" if lead_id == "a" else "contracts-news-courts"
            self.case.page_lead(topic_id, lead_id, page_text=f"Jan Kowal {lead_id}", excerpt=f"Jan Kowal {lead_id}",
                                subjects=["Jan Kowal", "Northwind Holdings"], url=url)
        self.assertEqual(self.clues("K1"), [])

    def test_k2_different_values_for_one_subject_and_attribute(self) -> None:
        self.case.page_lead("ownership-registries", "a", page_text="Capital: 100,000 EUR", excerpt="Capital: 100,000 EUR",
                            facts=[{"subject": "Northwind Holdings", "attribute": "share capital", "value": "100,000 EUR"}])
        self.case.page_lead("ownership-web", "b", page_text="Capital of 1,000,000 EUR", excerpt="Capital of 1,000,000 EUR",
                            facts=[{"subject": "Northwind Holdings", "attribute": "Share capital", "value": "1,000,000 EUR"}])
        self.case.page_lead("ownership-web", "c", page_text="Capital: 100,000 EUR (2024)", excerpt="Capital: 100,000 EUR (2024)",
                            facts=[{"subject": "Northwind Holdings", "attribute": "share capital", "value": "100,000 EUR"},
                                   {"subject": "Northwind Holdings", "attribute": "share capital", "value": "5 EUR"}])
        clues = self.clues("K2")
        self.assertEqual(sorted(c["lead_ids"] for c in clues), [["a", "b"], ["b", "c"]])
        self.assertEqual({c["attribute"] for c in clues}, {"share_capital"})

    def test_k3_two_events_of_one_subject_within_the_window(self) -> None:
        self.case.methodology["research_plan"]["timing_window_days"] = 30
        Case.write_json(self.case.root / "data" / "methodology.json", self.case.methodology)
        self.case.page_lead("ownership-registries", "a", page_text="Incorporated 2024-03-01", excerpt="Incorporated 2024-03-01",
                            facts=[{"subject": "Northwind Holdings", "attribute": "incorporated", "value": "2024-03-01"}])
        self.case.page_lead("contracts-news-courts", "b", page_text="Award dated 2024-03-20", excerpt="Award dated 2024-03-20",
                            facts=[{"subject": "Northwind Holdings", "attribute": "contract awarded", "value": "2024-03-20"}])
        self.case.page_lead("ownership-web", "c", page_text="Filed 2025-01-01", excerpt="Filed 2025-01-01",
                            facts=[{"subject": "Northwind Holdings", "attribute": "filed", "value": "2025-01-01"}])
        self.assertEqual([c["lead_ids"] for c in self.clues("K3")], [["a", "b"]])

    def test_k3_needs_a_window_in_the_methodology(self) -> None:
        self.case.page_lead("ownership-registries", "a", page_text="2024-03-01", excerpt="2024-03-01",
                            facts=[{"subject": "N", "attribute": "incorporated", "value": "2024-03-01"}])
        self.case.page_lead("ownership-registries", "b", page_text="2024-03-02", excerpt="2024-03-02",
                            facts=[{"subject": "N", "attribute": "awarded", "value": "2024-03-02"}])
        self.assertEqual(self.clues("K3"), [])

    def test_k4_one_address_for_two_entities_and_not_for_one(self) -> None:
        address = "12 Quay Street, Valletta"
        self.case.page_lead("ownership-registries", "a", page_text=f"Seat: {address}", excerpt=f"Seat: {address}",
                            facts=[{"subject": "Northwind Holdings", "attribute": "registered address", "value": address}])
        self.case.page_lead("ownership-web", "b", page_text=f"Office at {address}", excerpt=f"Office at {address}",
                            facts=[{"subject": "Harbour Supplies Ltd", "attribute": "address", "value": address}])
        self.case.page_lead("ownership-web", "c", page_text="Phone +356 2123 4567", excerpt="Phone +356 2123 4567",
                            facts=[{"subject": "Northwind Holdings", "attribute": "phone", "value": "+356 2123 4567"}])
        clues = self.clues("K4")
        self.assertEqual([c["lead_ids"] for c in clues], [["a", "b"]])

    def test_a_fact_whose_value_is_not_in_the_excerpt_is_ignored(self) -> None:
        address = "12 Quay Street, Valletta"
        self.case.page_lead("ownership-registries", "a", page_text=f"Seat: {address}", excerpt=f"Seat: {address}",
                            facts=[{"subject": "Northwind Holdings", "attribute": "address", "value": address}])
        self.case.page_lead("ownership-web", "b", page_text="Office in Valletta", excerpt="Office in Valletta",
                            facts=[{"subject": "Harbour Supplies Ltd", "attribute": "address", "value": address}])
        self.assertEqual(self.clues("K4"), [])

    def test_k5_a_structured_lookup_for_a_brief_subject_found_nothing(self) -> None:
        self.case.absence_lead("ownership-registries", "gap-1", ["Northwind Holdings"], ["Northwind Holdings beneficial owner"])
        self.case.absence_lead("ownership-web", "gap-2", ["Northwind Holdings"], ["Northwind Holdings scandal"])
        self.case.absence_lead("contracts-news-courts", "gap-3", ["Someone Else"], ["Someone Else lawsuit"])
        clues = self.clues("K5")
        self.assertEqual([(c["type"], c["lead_ids"]) for c in clues], [("absence", ["gap-1"])])
        self.assertIn("Absence is not clearance", clues[0]["warning"])

    def test_k6_a_watchlist_mention_is_always_shown(self) -> None:
        self.case.page_lead("ownership-web", "a", page_text="The director was added to the OFAC SDN list.",
                            excerpt="The director was added to the OFAC SDN list.")
        self.case.page_lead("ownership-web", "b", page_text="Sanktionsliste: Northwind", excerpt="Sanktionsliste: Northwind",
                            summary="Listed.", url="https://www.opensanctions.org/entities/x")
        self.case.page_lead("ownership-web", "c", page_text="The pep rally was cancelled.", excerpt="The pep rally was cancelled.")
        clues = self.clues("K6")
        self.assertEqual(sorted(c["lead_ids"] for c in clues), [["a"], ["b"]])
        self.assertTrue(all(c["always_shown"] and c["type"] == "watchlist" for c in clues))


class Ranking(unittest.TestCase):
    @staticmethod
    def lead(lead_id: str, url: str, stored: str = "page") -> dict:
        lead = {"schema_version": "1.0", "lead_id": lead_id, "topic_id": "ownership-web", "direction_id": "ownership",
                "summary": "s", "subjects": ["N"], "absence": False, "status": "candidate", "stored": stored,
                "capture": {"route": "api", "tool": "t", "source_url": url, "captured_at": "2026-10-23T09:00:00Z"},
                "excerpt": {"text": "s", "language": "en", "direct_quote": True}}
        if stored == "page":
            lead.update(content_ref="sources/x", content_sha256=hashlib.sha256(lead_id.encode()).hexdigest())
        return lead

    def test_clue_type_then_identity_source_assertion_families_and_stored_page(self) -> None:
        leads = {"leads": [{"lead": self.lead(i, f"https://{i}.example{n}.org/") , "merge_key": {}, "copies": []}
                           for n, i in enumerate(["timing", "watch", "plain-primary", "plain-secondary", "plain-name",
                                                  "fam-one", "fam-two"])]}
        leads["leads"].append({"lead": self.lead("snip", "https://snip.example.org/", stored="snippet"), "merge_key": {}, "copies": []})
        clues = {"clues": [
            {"clue_id": "k3-1", "type": "timing", "source": "K3", "lead_ids": ["timing"], "summary": "t"},
            {"clue_id": "k6-1", "type": "watchlist", "source": "K6", "lead_ids": ["watch"], "summary": "w", "always_shown": True},
            {"clue_id": "k1-1", "type": "new_actor", "source": "K1", "lead_ids": ["fam-two", "plain-name"], "summary": "n"},
        ]}
        same = {"identity": "consistent_details", "source": "original_reporting", "assertion": "asserted"}
        triage = {"leads": [
            {"lead_id": "plain-primary", "labels": {"identity": "same_identifier", "source": "primary_record", "assertion": "asserted"}, "clues": []},
            {"lead_id": "plain-secondary", "labels": {"identity": "same_identifier", "source": "secondary", "assertion": "asserted"}, "clues": []},
            {"lead_id": "plain-name", "labels": {**same, "identity": "name_only"}, "clues": []},
            {"lead_id": "fam-one", "labels": same, "clues": ["premise_contradicted"]},
            {"lead_id": "fam-two", "labels": same, "clues": []},
            {"lead_id": "snip", "labels": {"identity": "same_identifier", "source": "primary_record", "assertion": "asserted"}, "clues": []},
        ]}
        order = [r["lead_id"] for r in merge.rank(leads, clues, triage)]
        self.assertEqual(order, ["watch", "fam-one", "fam-two", "plain-name", "timing", "plain-primary", "snip", "plain-secondary"])

    def test_more_source_families_and_stored_pages_rank_first_on_a_tie(self) -> None:
        leads = {"leads": [{"lead": self.lead(i, u, s), "merge_key": {}, "copies": []} for i, u, s in (
            ("a-snippet", "https://a.example.org/", "snippet"), ("b-page", "https://b.example.org/", "page"),
            ("c-two", "https://c.example.org/", "page"), ("d-two", "https://d.example.net/", "page"))]}
        clues = {"clues": [{"clue_id": "k4", "type": "shared_identifier", "source": "K4", "lead_ids": ["c-two", "d-two", "b-page"], "summary": "x"},
                           {"clue_id": "k4b", "type": "shared_identifier", "source": "K4", "lead_ids": ["a-snippet"], "summary": "y"}]}
        ranked = merge.rank(leads, clues, None)
        self.assertEqual([r["lead_id"] for r in ranked], ["b-page", "c-two", "d-two", "a-snippet"])
        self.assertEqual(ranked[0]["source_families"], 2)

    def test_circumstantial_leads_rank_after_same_subject_leads_unless_a_clue_lifts_them(self) -> None:
        leads = {"leads": [{"lead": self.lead(i, f"https://{i}.example{n}.org/"), "merge_key": {}, "copies": []}
                           for n, i in enumerate(["other", "none", "unplaced", "name", "other-clue"])]}
        clues = {"clues": [{"clue_id": "k1", "type": "new_actor", "source": "K1", "lead_ids": ["other-clue"], "summary": "n"}]}
        triage = {"leads": [
            {"lead_id": "other", "labels": {"identity": "different_subject_or_event"}, "clues": []},
            {"lead_id": "none", "labels": {"identity": "none_of_the_brief"}, "clues": []},
            {"lead_id": "name", "labels": {"identity": "name_only"}, "clues": []},
            {"lead_id": "other-clue", "labels": {"identity": "different_subject_or_event"}, "clues": []},
        ]}
        self.assertEqual([r["lead_id"] for r in merge.rank(leads, clues, triage)],
                         ["other-clue", "name", "unplaced", "none", "other"])

    def test_a_k2_pair_the_conflict_question_declined_no_longer_ranks_as_a_conflict(self) -> None:
        leads = {"leads": [{"lead": self.lead(i, f"https://{i}.example.org/"), "merge_key": {}, "copies": []} for i in ("a", "b", "c")]}
        clues = {"clues": [{"clue_id": "k2", "type": "conflict", "source": "K2", "lead_ids": ["a", "b"], "attribute": "x", "summary": "x"},
                           {"clue_id": "k3", "type": "timing", "source": "K3", "lead_ids": ["c"], "summary": "t"}]}
        self.assertEqual([r["lead_id"] for r in merge.rank(leads, clues, None)], ["a", "b", "c"])
        declined = {"leads": [], "conflicts": [{"lead_ids": ["a", "b"], "answer": {"counted": True, "value": "agree"}, "clue": None}]}
        self.assertEqual([r["lead_id"] for r in merge.rank(leads, clues, declined)], ["c", "a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
