#!/usr/bin/env python3
"""Regression check for the arbiter query→case-study matcher (run_match.py).

The matcher is the deterministic core of the query-first selection flow: a
user describes what they want to investigate, and the saved /topics menu is
ranked so the closest case studies are offered before the full list.

Asserts:
  1. a query naming a case study ranks it first with full coverage;
  2. a partially overlapping query still surfaces the right case study;
  3. an unrelated query yields NO matches and every topic in `others`
     (the caller's full-menu fallback);
  4. prefix matches earn partial (not full) credit;
  5. stopword-only and empty queries match nothing;
  6. malformed topic items (non-dict, missing fields) are tolerated;
   7. --query-file behaves identically to --query for hostile free text;
   8. the matches list respects --top and never duplicates into `others`.
   9. zero, missing, and null post counts are hidden before ranking;
  10. the escape hatch restores zero-post topics for diagnostics.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATCHER = ROOT / "integrations" / "arbiter" / "run_match.py"

# Synthetic menu. Every row is invented: this fixture ships in a public
# repository, so it must never name a real person or restate a real allegation.
# The lexical properties the assertions below depend on are what matters, and
# each is preserved deliberately:
#   t_federation — the direct-hit row; "country", "federation" and "complaint"
#                  all appear as exact title tokens, and "federation" is long
#                  enough to be the prefix partner of "federations" in the
#                  partial-credit case;
#   t_ministry   — reachable only through the hostile free-text query, whose
#                  signal tokens are "ministry", "fuel" and "subsidy";
#   t_monument   — reachable by near-miss vocabulary ("monument" ~ "monuments"),
#                  never by the words the other two rows own.
# The shared "Social Media" prefix is kept on all three so the stopword
# assertions still have filler to prove they discard.
MENU = {
    "items": [
        {
            "id": "t_federation",
            "slug": "social-media-reactions-to-country-a-vs-federation-b-complaint",
            "title": "Social Media Reactions to Country A vs Federation B Complaint",
            "description": "Country A files a complaint with Federation B against Country C",
            "platforms": ["youtube", "twitter"],
            "window": {"from": "2026-07-06T18:30:00.000Z", "to": "2026-07-08T18:30:00.000Z"},
            "post_count": 244,
            "entity_count": 18,
            "starred": True,
        },
        {
            "id": "t_ministry",
            "slug": "social-media-coverage-of-ministry-c-fuel-subsidy-claims",
            "title": "Social Media Coverage of Ministry C Fuel-Subsidy Claims",
            "description": "ministry c fuel subsidy allegations",
            "platforms": ["twitter"],
            "window": {"from": "2026-07-04T18:30:00.000Z", "to": "2026-07-08T18:30:00.000Z"},
            "post_count": 23,
            "entity_count": 7,
            "starred": False,
        },
        {
            "id": "t_monument",
            "slug": "monument-d-donation-claims-on-social-media",
            "title": "Monument D Donation Claims on Social Media",
            "description": "claims about missing monument d building-fund donations",
            "platforms": ["youtube"],
            "window": {"from": "2026-07-01T18:30:00.000Z", "to": "2026-07-05T18:30:00.000Z"},
            "post_count": 397,
        },
        "not-a-dict",
        {"unexpected": True},
    ]
}


def run(menu_path: Path, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(MATCHER), str(menu_path), "--format", "json", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"matcher failed: {proc.stdout.strip()} {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def run_text(menu_path: Path, *args: str) -> str:
    """Run the matcher in terminal-rendering mode and return stdout."""
    proc = subprocess.run(
        [sys.executable, str(MATCHER), str(menu_path), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"matcher failed: {proc.stdout.strip()} {proc.stderr.strip()}")
    return proc.stdout


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        menu = Path(tmp) / "topics.json"
        menu.write_text(json.dumps(MENU), encoding="utf-8")

        # 1. Direct hit: full coverage, ranked first.
        result = run(menu, "--query", "country federation complaint")
        assert result["matches"], "expected a match for a direct query"
        top = result["matches"][0]
        assert top["id"] == "t_federation", top
        assert top["score"] == 1.0, top
        assert sorted(top["matched_terms"]) == ["complaint", "country", "federation"], top
        assert top["entity_count"] == 18, top
        assert top["starred"] is True, top

        # 2. Partial overlap still surfaces the right case study.
        result = run(menu, "--query", "monument donation rumours")
        assert [m["id"] for m in result["matches"]] == ["t_monument"], result["matches"]
        assert result["matches"][0]["score"] >= 0.3

        # 3. Unrelated query: nothing matches, EVERY topic lands in others.
        result = run(menu, "--query", "crypto pump and dump scheme")
        assert result["matches"] == [], result["matches"]
        other_ids = {entry["id"] for entry in result["others"]}
        assert {"t_federation", "t_ministry", "t_monument"} <= other_ids, other_ids

        # 4. Prefix match earns partial credit only.
        result = run(menu, "--query", "federations", "--threshold", "0.1")
        assert result["matches"], "prefix match should clear a low threshold"
        assert result["matches"][0]["id"] == "t_federation"
        assert 0 < result["matches"][0]["score"] < 1.0, result["matches"][0]

        # 5. Stopword-only and empty queries match nothing (and never crash).
        for query in ("what is the true story about this", "   "):
            result = run(menu, "--query", query)
            assert result["matches"] == [], f"stopword/empty query matched: {query!r}"

        # 5b. Stopwords carry no signal: filler words shared with every title
        #     ("social", "media", "of") must not drag unrelated topics in.
        result = run(menu, "--query", "the social media story of federation")
        assert [m["id"] for m in result["matches"]] == ["t_federation"], result["matches"]

        # 6. Malformed items were tolerated throughout (implicit in the runs
        #    above) and never appear in the output.
        result = run(menu, "--query", "federation")
        all_ids = {e["id"] for e in result["matches"]} | {e["id"] for e in result["others"]}
        assert "" not in {i for i in all_ids if i is None} and "not-a-dict" not in all_ids

        # 7. --query-file equals --query for hostile free text: an apostrophe,
        #    nested quotes, a percent sign, and a command substitution must all
        #    survive as ordinary characters and rank the same way either way.
        hostile = "what about ministry c's fuel \"subsidy\" claims? 100% $(true)"
        query_file = Path(tmp) / "query.txt"
        query_file.write_text(hostile, encoding="utf-8")
        via_file = run(menu, "--query-file", str(query_file))
        via_arg = run(menu, "--query", hostile)
        assert via_file["matches"] == via_arg["matches"]
        assert via_file["matches"][0]["id"] == "t_ministry"
        assert via_file["matches"][0]["entity_count"] == 7
        assert via_file["matches"][0]["starred"] is False

        # 8. --top caps matches; matches never duplicate into others.
        result = run(menu, "--query", "social media federation ministry monument", "--top", "1")
        assert len(result["matches"]) == 1
        match_ids = {e["id"] for e in result["matches"]}
        assert match_ids.isdisjoint({e["id"] for e in result["others"]})

        zero_post_menu = Path(tmp) / "zero-post-topics.json"
        zero_post_items = [
            {"id": "positive-match", "title": "B Alpha Signal", "post_count": 12},
            {"id": "zero-match", "title": "A Alpha Signal", "post_count": 0},
            {"id": "positive-other", "title": "Different Topic", "post_count": 7},
            {"id": "null-other", "title": "Null Count Topic", "post_count": None},
            {"id": "missing-other", "title": "Missing Count Topic"},
        ]
        zero_post_menu.write_text(json.dumps({"items": zero_post_items}), encoding="utf-8")

        # 9. Filtering happens before ranking and --top, and preserves exact
        # positive counts from the API.
        result = run(zero_post_menu, "--query", "alpha signal", "--top", "1")
        assert result["hidden_zero_post"] == 3, result
        assert [entry["id"] for entry in result["matches"]] == ["positive-match"], result
        assert [entry["id"] for entry in result["others"]] == ["positive-other"], result
        assert result["matches"][0]["post_count"] == 12
        assert result["others"][0]["post_count"] == 7

        # 10. Browse All still uses the default filter.
        result = run(zero_post_menu, "--query", "")
        assert result["matches"] == [], result
        assert {entry["id"] for entry in result["others"]} == {
            "positive-match",
            "positive-other",
        }
        assert result["hidden_zero_post"] == 3

        # 11. The explicit diagnostic escape hatch reverses the filtering.
        result = run(
            zero_post_menu,
            "--query",
            "alpha signal",
            "--top",
            "1",
            "--include-zero-post",
        )
        assert result["hidden_zero_post"] == 0, result
        assert result["matches"][0]["id"] == "zero-match", result
        assert {entry["id"] for entry in result["matches"] + result["others"]} == {
            entry["id"] for entry in zero_post_items
        }

        # 12. Terminal output reports a hidden count exactly once and never
        # claims to hide topics when the escape hatch is active.
        output = run_text(zero_post_menu, "--query", "alpha signal")
        assert output.count("Hid 3 case studies with 0 posts.\n") == 1, output
        output = run_text(
            zero_post_menu, "--query", "alpha signal", "--include-zero-post"
        )
        assert "Hid " not in output, output

        # 13. Positive-only menus are unchanged and emit no filter notice.
        positive_menu = Path(tmp) / "positive-topics.json"
        positive_menu.write_text(
            json.dumps({"items": zero_post_items[:1] + zero_post_items[2:3]}),
            encoding="utf-8",
        )
        result = run(positive_menu, "--query", "")
        assert result["hidden_zero_post"] == 0
        assert len(result["others"]) == 2
        assert "Hid " not in run_text(positive_menu, "--query", "")

        # 14. An all-ineligible menu still reports why the picker is empty.
        hidden_menu = Path(tmp) / "hidden-topics.json"
        hidden_menu.write_text(
            json.dumps({"items": zero_post_items[1:2] + zero_post_items[3:]}),
            encoding="utf-8",
        )
        result = run(hidden_menu, "--query", "")
        assert result["hidden_zero_post"] == 3
        assert result["matches"] == [] and result["others"] == []
        assert "Hid 3 case studies with 0 posts." in run_text(
            hidden_menu, "--query", ""
        )

        single_hidden_menu = Path(tmp) / "single-hidden-topic.json"
        single_hidden_menu.write_text(
            json.dumps({"items": zero_post_items[:2]}), encoding="utf-8"
        )
        output = run_text(single_hidden_menu, "--query", "")
        assert "Hid 1 case study with 0 posts.\n" in output, output
        assert "Hid 1 case studies" not in output, output

        # 15. --out refuses to write through a symlink, which write_text would
        #     otherwise follow onto the link's target.
        target = Path(tmp) / "match-target.json"
        target.write_text("original\n", encoding="utf-8")
        link = Path(tmp) / "match-link.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            link = None
        if link is not None:
            refused = subprocess.run(
                [sys.executable, str(MATCHER), str(menu), "--query", "federation",
                 "--out", str(link)],
                capture_output=True, text=True, timeout=30,
            )
            assert refused.returncode == 2, (refused.returncode, refused.stderr)
            assert "symlink" in refused.stderr, refused.stderr
            assert target.read_text(encoding="utf-8") == "original\n", "target overwritten"

        plain = Path(tmp) / "match-out.json"
        written = subprocess.run(
            [sys.executable, str(MATCHER), str(menu), "--query", "federation",
             "--format", "json", "--out", str(plain)],
            capture_output=True, text=True, timeout=30,
        )
        assert written.returncode == 0, written.stderr
        assert json.loads(plain.read_text(encoding="utf-8"))["matches"][0]["id"] == (
            "t_federation"
        ), "a plain destination must still be written"

    print("arbiter match: OK — ranking, fallback, prefix credit, and hostile queries verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
