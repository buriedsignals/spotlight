#!/usr/bin/env python3
"""scripts/wiki-destination.py after a requested solo ingestion.

The approved batch is kept whole, journaled, admitted and turned into one
note per claim, event and story-arc lineage under spotlight/ only; a batch
changed after the request is refused; a re-run is idempotent; a hand-edited
generated note is not overwritten.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("ingest_review_check", ROOT / "tests" / "ingest-review-check.py")
INGEST = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(INGEST)


class WikiDestination(INGEST.SoloCase):
    def requested(self, name: str) -> Path:
        case = self.prepared(name)
        self.seam(case, "decide-ingest", "requested", "--nonce", self.nonce(case, "ingest"),
                  "--digest", self.view(case, "ingest")["digest"])
        return case

    def write(self, case: Path, wiki: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "wiki-destination.py"), str(case), "--workspace", str(wiki), *extra],
                              capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})

    def wiki(self) -> Path:
        wiki = Path(tempfile.mkdtemp(dir=self.root)) / "wiki"
        (wiki / ".ok").mkdir(parents=True)
        return wiki

    def test_an_approved_batch_becomes_kept_batch_journal_and_notes(self) -> None:
        case, wiki = self.requested("approved"), self.wiki()
        done = self.write(case, wiki)
        self.assertEqual(done.returncode, 0, done.stderr)
        batch = json.loads((case / "data" / "knowledge-batch.json").read_text(encoding="utf-8"))
        kept = list((wiki / "spotlight" / "batches").glob("*.json"))
        self.assertEqual([json.loads(p.read_text(encoding="utf-8"))["batch_id"] for p in kept], [batch["batch_id"]])
        journal = (wiki / "spotlight" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(journal), 1)
        for folder in ("claims", "events", "story-arcs"):
            self.assertEqual(len(list((wiki / "spotlight" / folder).glob("*.md"))), 1, folder)
        claim_note = next((wiki / "spotlight" / "claims").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn('status: "approved"', claim_note)
        self.assertIn('verdict: "verified"', claim_note)
        self.assertIn("Acme paid Doe.", claim_note)
        written = {p.relative_to(wiki).as_posix() for p in wiki.rglob("*") if p.is_file()}
        self.assertTrue(all(p.startswith("spotlight/") for p in written), written)
        marker = json.loads((case / "data" / "ingestion.json").read_text(encoding="utf-8"))
        self.assertEqual((marker["status"], marker["batch_id"]), ("completed", batch["batch_id"]))
        ids = json.loads((case / "data" / "knowledge-ids.json").read_text(encoding="utf-8"))
        self.assertTrue(all("admitted" in e and "pending" not in e for e in ids["records"].values()))
        # The phase seals the receipt.
        self.seam(case, "decide-ingest", "completed")
        self.assertEqual(json.loads(self.seam(case, "status", "--json").stdout)["next_phase"], "complete")

    def test_a_rerun_is_idempotent_and_a_hand_edit_is_kept(self) -> None:
        case, wiki = self.requested("rerun"), self.wiki()
        self.assertEqual(self.write(case, wiki).returncode, 0)
        again = self.write(case, wiki)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len((wiki / "spotlight" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        note = next((wiki / "spotlight" / "claims").glob("*.md"))
        note.write_text(note.read_text(encoding="utf-8") + "\nMy own note.\n", encoding="utf-8")
        refused = self.write(case, wiki)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("edited by hand", refused.stderr)
        self.assertIn("My own note.", note.read_text(encoding="utf-8"))

    def test_a_batch_changed_after_the_request_is_refused(self) -> None:
        case, wiki = self.requested("changed"), self.wiki()
        path = case / "data" / "knowledge-batch.json"
        path.write_bytes(path.read_bytes().replace(b"Acme paid Doe.", b"Acme paid Doe twice."))
        refused = self.write(case, wiki)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("changed after the request", refused.stderr)
        self.assertFalse((wiki / "spotlight").exists())

    def test_nothing_is_written_before_a_request(self) -> None:
        case, wiki = self.prepared("unrequested"), self.wiki()
        refused = self.write(case, wiki)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("not been requested", refused.stderr)


if __name__ == "__main__":
    unittest.main()
