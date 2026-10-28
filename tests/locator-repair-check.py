#!/usr/bin/env python3
"""After the fact-checker, code repairs an evidence item's line range when its
quote is not in the cited lines but occurs exactly once in that same cited
case file (write-source-expressions.py --resolve-fact-check-refs), and the
anchor check reads a literal \\n in a quote as a line break, as the passage
writer does. Gold sens4 on 65561532: the fact-checker counted the lines of
read_file text itself and cited a saved wiki note two lines short (calder),
and quoted a table row with a literal \\n (marlow); every quote was in its
file exactly once."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from evidence_anchors import validate_source_ref  # noqa: E402

NOTE = (
    "Wiki note: calder-directors-2023.md\n\n"
    "# Calder Medical Supplies directors 2023 (earlier work)\n\n"
    "The 2023 filing lists two directors of Calder Medical Supplies: Ines Tarrant (chair) and Joss Penhale (finance).\n\n"
    "| Consideration | £24,000 |\n"
    "| Transferor | Pell Holdings Limited |\n\n"
    "Source: registry extract.\n"
    "Source: registry extract.\n"
)
DIRECTORS = "The 2023 filing lists two directors of Calder Medical Supplies: Ines Tarrant (chair) and Joss Penhale (finance)."
TABLE_ROWS = "| Consideration | £24,000 |\\n| Transferor | Pell Holdings Limited |"  # a literal backslash-n


def evidence(quote: str, start: int, end: int) -> dict:
    return {"description": "wiki note", "local_file": "research/wiki/calder-directors-2023.md", "quote": quote,
            "source_ref": {"path": "research/wiki/calder-directors-2023.md", "line_start": start, "line_end": end}}


class LocatorRepair(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name).resolve() / "case"
        (self.case / "data").mkdir(parents=True)
        (self.case / "research" / "wiki").mkdir(parents=True)
        self.note = self.case / "research" / "wiki" / "calder-directors-2023.md"
        self.note.write_text(NOTE)

    def repair(self, items: list[dict]) -> tuple[list[dict], str]:
        claims = [{"id": str(index + 1), "finding_id": f"F{index + 1}", "verdict": "verified", "evidence_for": [item]}
                  for index, item in enumerate(items)]
        (self.case / "data" / "fact-check.json").write_text(json.dumps({"claims": claims}))
        done = subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(self.case), "--resolve-fact-check-refs"],
                              capture_output=True, text=True, check=True)
        stored = json.loads((self.case / "data" / "fact-check.json").read_text())["claims"]
        return [claim["evidence_for"][0] for claim in stored], done.stdout

    def test_a_miscounted_range_and_a_literal_newline_quote_are_located_where_the_quote_occurs_once(self) -> None:
        before = self.note.read_bytes()
        (directors, rows), output = self.repair([evidence(DIRECTORS, 3, 3), evidence(TABLE_ROWS, 1, 2)])
        self.assertEqual((directors["source_ref"]["line_start"], directors["source_ref"]["line_end"]), (5, 5))
        self.assertEqual(directors["locator_repaired_from"], [3, 3])
        self.assertEqual((rows["source_ref"]["line_start"], rows["source_ref"]["line_end"]), (7, 8))
        self.assertEqual(rows["locator_repaired_from"], [1, 2])
        self.assertEqual((directors["quote"], rows["quote"]), (DIRECTORS, TABLE_ROWS), "the quote text is never changed")
        self.assertEqual(self.note.read_bytes(), before, "the cited file is never changed")
        for item in (directors, rows):
            self.assertEqual(validate_source_ref(self.case, item["source_ref"], item["quote"])[1], [])
        self.assertIn("locator repaired by code: claim 1 evidence_for[0] in research/wiki/calder-directors-2023.md: lines 3-3 -> 5-5", output)
        self.assertIn('"locators_repaired": 2', output)

    def test_a_correct_range_with_a_literal_newline_quote_passes_without_a_repair(self) -> None:
        self.assertEqual(validate_source_ref(self.case, evidence(TABLE_ROWS, 7, 8)["source_ref"], TABLE_ROWS)[1], [])
        [rows], _ = self.repair([evidence(TABLE_ROWS, 7, 8)])
        self.assertNotIn("locator_repaired_from", rows)

    def test_an_ambiguous_or_missing_quote_is_left_for_the_model(self) -> None:
        ambiguous, missing = evidence("Source: registry extract.", 1, 1), evidence("Ines Tarrant resigned in 2022.", 5, 5)
        (first, second), output = self.repair([ambiguous, missing])
        for item, original in ((first, ambiguous), (second, missing)):
            self.assertNotIn("locator_repaired_from", item)
            self.assertEqual(item["source_ref"], original["source_ref"])
        self.assertIn('"locators_repaired": 0', output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
