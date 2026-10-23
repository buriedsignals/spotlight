#!/usr/bin/env python3
"""The Research gate page model and marks, through the command-line seam the
review server calls, on an offline case built by the real lane fixture, merge
and triage (tests/fixtures/review_case.py research)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEAM = ROOT / "scripts" / "spotlight-orchestration.py"
BUILDER = ROOT / "tests" / "fixtures" / "review_case.py"
APPROVER = ("--approved-by", "A. Reviewer", "--approved-at", "2026-10-23T12:00:00Z")
REFUSED = 3
sys.path.insert(0, str(ROOT / "scripts"))

import research_checks  # noqa: E402


class ResearchPage(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="spotlight-research-page-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, *flags: str) -> Path:
        case = self.root / "case"
        result = subprocess.run([sys.executable, str(BUILDER), str(case), "research", *flags],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return case

    def seam(self, case: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(SEAM), *args, str(case)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def view(self, case: Path) -> dict:
        return json.loads(self.seam(case, "review-view", "research").stdout)

    def edit(self, case: Path, edit: dict, expect: int = 0) -> subprocess.CompletedProcess[str]:
        return self.seam(case, "review-edit", "research", "--edit", json.dumps(edit), expect=expect)

    def items(self, view: dict) -> dict:
        return {item["key"]: item for item in view["items"]}

    def test_clues_come_first_in_rank_order_and_no_lead_is_buried(self) -> None:
        case = self.build()
        view = self.view(case)
        self.assertEqual(view["blocking"], [])
        content = view["content"]
        # Watchlist (K6), then the premise clue triage raised (C1), then the absence (K5).
        self.assertEqual([(c["type"], c["source"], c["protected"]) for c in content["clues"]],
                         [("watchlist", "K6", True), ("premise_complicated", "C1", True), ("absence", "K5", False)])
        self.assertEqual(content["clues"][2]["warning"].split(":")[0], "Absence is not clearance")
        sections = {lead["lead_id"]: lead["section"] for lead in content["leads"]}
        self.assertEqual(sections, {"reg-2": "flagged", "court-1": "flagged", "reg-1": "flagged",
                                    "reg-gap": "direction", "court-2": "direction", "web-3": "set_aside",
                                    "web-1": "snippet", "web-2": "unverifiable"})
        self.assertEqual(len(content["leads"]), content["lead_total"])
        # Leads follow U6's ranking.
        ranked = json.loads(subprocess.run([sys.executable, str(ROOT / "scripts" / "research-merge.py"), "--rank", str(case)],
                                           capture_output=True, text=True, check=True).stdout)
        self.assertEqual([lead["lead_id"] for lead in content["leads"]], [r["lead_id"] for r in ranked])
        unsure = next(lead for lead in content["leads"] if lead["lead_id"] == "reg-1")
        self.assertIn("model_unsure", [f["reason"] for f in unsure["triage"]["flags"]])
        ownership = next(d for d in content["directions"] if d["direction_id"] == "ownership")
        self.assertEqual(ownership["counts"], {"direction": 1, "flagged": 2, "set_aside": 1, "snippet": 1, "unverifiable": 1})

    def test_mode_spend_left_out_and_the_flag_only_notice(self) -> None:
        content = self.view(self.build())["content"]
        run = content["run"]
        self.assertEqual((run["mode"], run["mode_label"]), ("openrouter_swarm", "OpenRouter swarm"))
        self.assertEqual((run["spend"]["round_usd"], run["spend"]["budget_usd"], run["spend"]["reported"]), (0.03, 1, True))
        self.assertEqual(run["left_out"], [{"topic_id": "contracts-archives", "reason": "the round budget was spent"}])
        self.assertTrue(content["triage"]["flag_only"])
        self.assertIn("Triage only flags", content["triage"]["notice"])

    def test_suggested_marks_prefill_and_the_review_keeps_suggested_beside_final(self) -> None:
        case = self.build()
        items = self.items(self.view(case))
        self.assertEqual({k: (i["suggested"], i["final"]) for k, i in items.items() if k.startswith("direction:")},
                         {"direction:ownership": ("keep", "keep"), "direction:contracts": ("drop", "drop"),
                          "direction:sanctions": ("add", "add")})
        absence = next(k for k in items if k.startswith("clue:k5-"))
        self.assertEqual((items[absence]["suggested"], items[absence]["suggestion_source"]), ("dismiss", "investigator suggestion"))
        self.edit(case, {"kind": "mark", "key": "direction:contracts", "value": "keep"})
        review = json.loads((case / "data/research-review.json").read_text(encoding="utf-8"))
        record = review["items"]["direction:contracts"]
        self.assertEqual((record["suggested"], record["final"]), ("drop", "keep"))
        self.assertEqual((review["counts"]["changed"], review["counts"]["accepted_unchanged"]), (1, 5))

    def test_watchlist_and_premise_clues_cannot_be_dismissed(self) -> None:
        case = self.build()
        items = self.items(self.view(case))
        for key in (k for k in items if k.startswith(("clue:k6-", "clue:c1-"))):
            self.assertEqual(items[key]["final"], "pursue")
            refused = self.edit(case, {"kind": "mark", "key": key, "value": "dismiss"}, expect=REFUSED)
            self.assertIn("always pursued", refused.stderr)
        absence = next(k for k in items if k.startswith("clue:k5-"))
        self.edit(case, {"kind": "mark", "key": absence, "value": "pursue"})
        self.assertEqual(self.items(self.view(case))[absence]["final"], "pursue")

    def test_without_suggestions_an_unmarked_clue_blocks_approval(self) -> None:
        case = self.build("--no-suggestions")
        view = self.view(case)
        self.assertEqual(view["content"]["suggestions"], "missing")
        self.assertEqual(view["open_required"], 1)
        self.edit(case, {"kind": "reviewer", "value": "A. Reviewer"})
        view = self.view(case)
        nonce = json.loads(self.seam(case, "issue-review-nonce", "research").stdout)["nonce"]
        refused = self.seam(case, "approve", "research", *APPROVER, "--nonce", nonce, "--digest", view["digest"], expect=REFUSED)
        self.assertIn("open dispositions", refused.stderr)
        absence = next(k for k in self.items(view) if k.startswith("clue:k5-"))
        self.edit(case, {"kind": "mark", "key": absence, "value": "dismiss"})
        view = self.view(case)
        self.seam(case, "approve", "research", *APPROVER, "--nonce", nonce, "--digest", view["digest"])
        receipt = self.view(case)["receipt"]
        self.assertEqual((receipt["channel"], receipt["approved_by"], receipt["review_digest"]), ("page", "A. Reviewer", view["digest"]))
        self.assertEqual(json.loads(self.seam(case, "status", "--json").stdout)["phase"], "investigation")

    def test_a_member_adds_a_direction_and_d8_reads_the_marks(self) -> None:
        case = self.build()
        self.edit(case, {"kind": "add_direction", "key": "Bad Id", "value": "x"}, expect=REFUSED)
        self.edit(case, {"kind": "add_direction", "key": "ownership", "value": "again"}, expect=REFUSED)
        self.edit(case, {"kind": "add_direction", "key": "procurement", "value": "Who else bid for the harbour contract?"})
        items = self.items(self.view(case))
        added = items["direction:procurement"]
        self.assertEqual((added["final"], added["label"], added["suggestion_source"]),
                         ("add", "Who else bid for the harbour contract?", "added by the member"))
        self.edit(case, {"kind": "mark", "key": "direction:procurement", "value": "keep"}, expect=REFUSED)
        review = json.loads((case / "data/research-review.json").read_text(encoding="utf-8"))
        marks = research_checks.marks(review)
        self.assertEqual({k: v for k, v in marks.items() if k.startswith("direction:")},
                         {"direction:ownership": "keep", "direction:contracts": "drop",
                          "direction:sanctions": "add", "direction:procurement": "add"})
        self.assertEqual(sorted(v for k, v in marks.items() if k.startswith("clue:")), ["dismiss", "pursue", "pursue"])
        # A withdrawn new direction leaves the marks.
        self.edit(case, {"kind": "mark", "key": "direction:procurement", "value": None})
        review = json.loads((case / "data/research-review.json").read_text(encoding="utf-8"))
        self.assertNotIn("direction:procurement", research_checks.marks(review))

    def test_the_page_digest_covers_what_the_page_shows(self) -> None:
        case = self.build()
        self.edit(case, {"kind": "reviewer", "value": "A. Reviewer"})
        loaded = self.view(case)
        nonce = json.loads(self.seam(case, "issue-review-nonce", "research").stdout)["nonce"]
        suggestions = case / "data/research-suggestions.json"
        document = json.loads(suggestions.read_text(encoding="utf-8"))
        document["directions"][0]["reason"] = "Changed after the page loaded."
        suggestions.write_text(json.dumps(document), encoding="utf-8")
        self.seam(case, "approve", "research", *APPROVER, "--nonce", nonce, "--digest", loaded["digest"], expect=4)
        self.assertEqual(json.loads(self.seam(case, "status", "--json").stdout)["phase"], "research_approval")

    def test_triage_that_breaks_its_rules_blocks_the_page(self) -> None:
        case = self.build()
        path = case / "data/research-triage.json"
        triage = json.loads(path.read_text(encoding="utf-8"))
        watch = next(r for r in triage["leads"] if "watchlist" in r["clues"])
        watch.update(disposition="set_aside", set_aside_reasons=["none_of_the_brief"])
        path.write_text(json.dumps(triage), encoding="utf-8")
        view = self.view(case)
        self.assertTrue(any(b.startswith("triage broke its rules (D7)") for b in view["blocking"]), view["blocking"])
        # The broken triage is not used: the watchlist lead is not shown as set aside.
        self.assertEqual(next(l for l in view["content"]["leads"] if l["lead_id"] == watch["lead_id"])["section"], "direction")


if __name__ == "__main__":
    unittest.main()
