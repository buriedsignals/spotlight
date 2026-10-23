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

    def write(self, case: Path, wiki: Path, *extra: str, path: str = "/usr/bin:/bin") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "wiki-destination.py"), str(case), "--workspace", str(wiki), *extra],
                              capture_output=True, text=True, env={"PATH": path})

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
        # Only spotlight/, plus the wiki's shared write lock.
        self.assertTrue(all(p.startswith("spotlight/") or p == ".wiki-lock" for p in written), written)
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

    def test_an_interrupted_run_resumes(self) -> None:
        case, wiki = self.requested("interrupted"), self.wiki()
        before = {name: (case / "data" / name).read_bytes() for name in ("ingestion.json", "knowledge-ids.json")}
        self.assertEqual(self.write(case, wiki).returncode, 0)
        # A crash after the notes: no manifest, and the case write (admission
        # and receipt, one atomic write) never happened.
        (wiki / "spotlight" / "generated-notes.json").unlink()
        for name, data in before.items():
            (case / "data" / name).write_bytes(data)
        again = self.write(case, wiki)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len((wiki / "spotlight" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_two_cases_share_one_wiki(self) -> None:
        wiki = self.wiki()
        for name in ("first", "second"):
            done = self.write(self.requested(name), wiki)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len((wiki / "spotlight" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(len(list((wiki / "spotlight" / "claims").glob("*.md"))), 2)

    def test_entities_link_only_on_an_exact_register_match(self) -> None:
        case, wiki = self.requested("linked"), self.wiki()
        stub = Path(tempfile.mkdtemp(dir=self.root))
        (stub / "bsig").write_text("#!/bin/sh\n"
                                   "case \"$5\" in *'\"Acme\"'*) echo '{\"event\":\"result\",\"data\":{\"result\":{\"status\":\"linked\",\"entity_id\":\"acme-ltd\"}}}';;\n"
                                   "*) echo '{\"event\":\"result\",\"data\":{\"result\":{\"status\":\"candidates\"}}}';; esac\n", encoding="utf-8")
        (stub / "bsig").chmod(0o755)
        done = self.write(case, wiki, path=f"{stub}:/usr/bin:/bin")
        self.assertEqual(done.returncode, 0, done.stderr)
        note = next((wiki / "spotlight" / "claims").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("[[wiki/entities/acme-ltd|Acme]]", note)
        self.assertNotIn("[[wiki/entities/", note.split("Object:")[1].splitlines()[0])  # Doe: candidates only
        self.assertFalse((wiki / "wiki").exists())

    def test_case_text_cannot_plant_links_or_follow_symlinks(self) -> None:
        case = self.prepared("hostile")
        proposal = json.loads((case / "data" / "knowledge-proposal.json").read_text(encoding="utf-8"))
        proposal["claims"][0]["statement"]["predicate"] = "paid [[wiki/entities/someone-else]]"
        (case / "data" / "knowledge-proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        rebuilt = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-knowledge-batch.py"), str(case), "--review"],
                                 capture_output=True, text=True)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.seam(case, "decide-ingest", "requested", "--nonce", self.nonce(case, "ingest"), "--digest", self.view(case, "ingest")["digest"])
        wiki = self.wiki()
        outside = Path(tempfile.mkdtemp(dir=self.root))
        (wiki / "spotlight").mkdir()
        (wiki / "spotlight" / "claims").symlink_to(outside)
        refused = self.write(case, wiki)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("symbolic link", refused.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        (wiki / "spotlight" / "claims").unlink()
        self.assertEqual(self.write(case, wiki).returncode, 0)
        note = next((wiki / "spotlight" / "claims").glob("*.md")).read_text(encoding="utf-8")
        self.assertNotIn("[[wiki/entities/someone-else]]", note)

    def test_a_hand_written_request_is_not_enough(self) -> None:
        case, wiki = self.prepared("forged"), self.wiki()
        state = json.loads((case / "data" / "orchestration.json").read_text(encoding="utf-8"))
        state["decisions"]["ingest"] = {"decision": "requested", "review": {"review_digest": self.view(case, "ingest")["digest"]}}
        (case / "data" / "orchestration.json").write_text(json.dumps(state), encoding="utf-8")
        refused = self.write(case, wiki)
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertFalse((wiki / "spotlight").exists())

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
