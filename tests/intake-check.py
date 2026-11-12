#!/usr/bin/env python3
"""The material register (scripts/intake.py): what code alone decides about an
item of material, and what only the journalist's confirmation closes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location("intake", ROOT / "scripts" / "intake.py")
INTAKE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(INTAKE)

_WSPEC = importlib.util.spec_from_file_location("wiki_destination", ROOT / "scripts" / "wiki-destination.py")
WIKI = importlib.util.module_from_spec(_WSPEC)
assert _WSPEC.loader is not None
_WSPEC.loader.exec_module(WIKI)

COMPLETE = {"kind": "interview", "captured_by": "A. Reporter", "source": {"pseudonym": "Dock worker 1"},
            "rights_holder": {"label": "A. Reporter"}}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def refused(call, *args, error: type[Exception] | None = None, **kwargs) -> str:
    try:
        call(*args, **kwargs)
    except (error or INTAKE.Refused) as exc:
        return str(exc)
    raise AssertionError(f"{call.__name__} was not refused")


def register(case: Path) -> list[dict]:
    return json.loads((case / "data" / "journalist-input.json").read_text(encoding="utf-8"))["inputs"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "case"
        (case / "data").mkdir(parents=True)
        outside = Path(tmp) / "outside"
        outside.mkdir()
        email = outside / "from-source.eml"
        email.write_text("Subject: the night shift\n", encoding="utf-8")
        notes = outside / "call.txt"
        notes.write_text("In March the supervisor told the night shift to stop opening containers.\n", encoding="utf-8")

        # add: a copy inside the case, the code's own fingerprint, and the
        # most restrictive handling until someone says otherwise.
        record = INTAKE.add(case, email)
        require(record["content_ref"] == "intake/emails/from-source.eml" and (case / record["content_ref"]).is_file(),
                f"a mail file was not filed under emails: {record}")
        require(record["content_sha256"] == hashlib.sha256(email.read_bytes()).hexdigest(), "the fingerprint is not the file's")
        require(record["state"] == "draft" and record["original_sensitivity"] == "case_only", f"a draft is not restrictive: {record}")
        require(INTAKE.add(case, email)["id"] == record["id"] and len(register(case)) == 1, "the same bytes made a second record")

        # A file whose name does not give its kind waits in unsorted/, and
        # moves once the kind is named.
        unsorted = INTAKE.add(case, notes)
        require(unsorted["content_ref"] == "intake/unsorted/call.txt" and "kind" not in unsorted, f"{unsorted}")
        filled = INTAKE.set_fields(case, unsorted["id"], COMPLETE, actor="agent", model="local-12b")
        require(filled["content_ref"] == "intake/interviews/call.txt" and (case / filled["content_ref"]).is_file()
                and not (case / "intake" / "unsorted" / "call.txt").exists(), f"the file did not follow its kind: {filled}")
        require(filled["provenance"] == {"method": "agent_candidate", "model": "local-12b"}, "an agent's draft is not marked as one")

        # scan: a file dropped into a kind's folder takes that kind.
        dropped = case / "intake" / "documents" / "contract.pdf"
        dropped.parent.mkdir(parents=True)
        dropped.write_bytes(b"%PDF-1.7 contract")
        (case / "intake" / ".DS_Store").write_bytes(b"x")
        found = INTAKE.scan(case)
        require([r["content_ref"] for r in found] == ["intake/documents/contract.pdf"] and found[0]["kind"] == "document", f"{found}")
        require(INTAKE.scan(case) == [], "a second scan registered the same files again")

        # set: only the fields a person or a model may propose.
        require("cannot be set" in refused(INTAKE.set_fields, case, record["id"], {"content_sha256": "0" * 64}), "a fingerprint was settable")
        require("never both" in refused(INTAKE.set_fields, case, record["id"], {"source": {"entity_id": "E1", "pseudonym": "P"}}),
                "a source carried a name and a pseudonym")
        require("kind_detail" in refused(INTAKE.set_fields, case, record["id"], {"kind": "other"}), 'kind "other" passed without a detail')

        # confirm: a complete record, on unchanged bytes, once.
        require("not complete" in refused(INTAKE.confirm, case, record["id"], "A. Reporter"), "an incomplete record was confirmed")
        closed = INTAKE.confirm(case, filled["id"], "A. Reporter")
        require(closed["state"] == "confirmed" and closed["confirmed_by"] == "A. Reporter", f"{closed}")
        require("no longer changes" in refused(INTAKE.set_fields, case, filled["id"], {"kind": "document"}), "a confirmed record changed")
        INTAKE.set_fields(case, found[0]["id"], {**COMPLETE, "kind": "document"})
        dropped.write_bytes(b"%PDF-1.7 another contract")
        require("registered bytes" in refused(INTAKE.confirm, case, found[0]["id"], "A. Reporter"), "changed material was confirmed")

        # propose: a passage is the material's own words at one place, or
        # nothing is recorded. It starts with no rights at all.
        claim = {"proposition": "Dock worker 1 says the night shift was told in March to stop opening containers.",
                 "statement": {"subject": {"text": "the supervisor"}, "predicate": "told the night shift to stop opening containers"}}
        quote = "the supervisor told the night shift to stop opening containers"
        paraphrase = {"passages": [{"text": "the boss told nights to stop checking", "language": "en", "claims": [claim]}]}
        require("word for word" in refused(INTAKE.propose, case, filled["id"], paraphrase), "a paraphrase was recorded as a passage")
        require(not (case / "data" / "intake-claims.json").exists(), "a refused proposal wrote the file")
        stored = INTAKE.propose(case, filled["id"], {"passages": [{"text": quote, "language": "en", "direct_quote": True, "claims": [claim]}]},
                                actor="agent", model="local-12b")
        passage, drawn = stored["passages"][0], stored["claims"][0]
        require(passage["anchor_ref"] == {"path": "intake/interviews/call.txt", "line_start": 1, "line_end": 1} and passage["rights"] == {},
                f"the passage is not anchored, or starts with rights: {passage}")
        require(drawn["state"] == "proposed" and drawn["passage_id"] == passage["id"], f"{drawn}")
        again = INTAKE.propose(case, filled["id"], {"passages": [{"text": quote, "language": "en", "claims": [claim]}]})
        require(len(again["passages"]) == 1 and len(again["claims"]) == 1, "the same passage and claim were recorded twice")

        # A recording has no words to quote until its text is added.
        audio = outside / "call.m4a"
        audio.write_bytes(b"\x00\x01audio")
        recording = INTAKE.add(case, audio, kind="interview")
        spoken = {"passages": [{"text": "Nobody put it in writing", "language": "en", "claims": [claim]}]}
        require("derive" in refused(INTAKE.propose, case, recording["id"], spoken), "a passage was taken from a recording with no text")
        transcript = outside / "transcript.txt"
        transcript.write_text("Nobody put it in writing, it was said at the start of the shift.\n", encoding="utf-8")
        INTAKE.derive(case, recording["id"], transcript, "transcript")
        from_text = INTAKE.propose(case, recording["id"], spoken)["passages"][-1]
        require(from_text["anchor_ref"].get("derivative_id") == "D1", f"the passage does not name the text it was found in: {from_text}")

        # approve: only a confirmed record, only once every claim is decided,
        # and any later change takes the approval back.
        require("not confirmed" in refused(INTAKE.approve, case, recording["id"], "A. Reporter"), "claims of an unconfirmed record were approved")
        require("not yet kept or dropped" in refused(INTAKE.approve, case, filled["id"], "A. Reporter"), "an undecided claim was approved")
        INTAKE.decide(case, drawn["id"], "keep")
        approval = INTAKE.approve(case, filled["id"], "A. Reporter")
        require(approval["claims"] == [drawn["id"]] and approval["by"] == "A. Reporter", f"{approval}")
        require("not valid" in refused(INTAKE.set_rights, case, passage["id"], {"attribution": "loud"}), "an unknown attribution was stored")
        INTAKE.set_rights(case, passage["id"], {"attribution": "not_for_attribution", "sensitivity": "publishable",
                                               "consent": {"publish_content": True, "publish_direct_quotes": False, "obtained_via": "recorded_oral"}})
        require(filled["id"] not in json.loads((case / "data" / "intake-claims.json").read_text(encoding="utf-8"))["approvals"],
                "an approval outlived a change to its passage")
        INTAKE.decide(case, drawn["id"], "drop")
        require("no kept claim" in refused(INTAKE.approve, case, filled["id"], "A. Reporter"), "an approval with nothing kept was recorded")

        # Ingest, with no investigation: the batch builder takes the approved
        # claims as what the source said. No rights on the passage, no batch.
        INTAKE.decide(case, drawn["id"], "keep")
        INTAKE.set_rights(case, passage["id"], {})
        INTAKE.approve(case, filled["id"], "A. Reporter")
        solo = Path(tmp) / "solo.json"  # no install config: the solo edition
        newsroom = Path(tmp) / "newsroom.json"
        newsroom.write_text(json.dumps({"knowledge_destination": {"mode": "newsroom"}}), encoding="utf-8")
        build = lambda config=solo: subprocess.run([sys.executable, str(ROOT / "scripts" / "build-knowledge-batch.py"), str(case)],  # noqa: E731
                                                   capture_output=True, text=True, env={**os.environ, "SPOTLIGHT_CONFIG": str(config)})
        without_rights = build()
        require(without_rights.returncode == 2 and "attribution, consent, sensitivity" in without_rights.stderr, without_rights.stderr)
        rights = {"attribution": "not_for_attribution", "sensitivity": "internal",
                  "consent": {"publish_content": True, "publish_direct_quotes": False, "obtained_via": "recorded_oral"}}
        INTAKE.set_rights(case, passage["id"], rights)
        INTAKE.approve(case, filled["id"], "A. Reporter")
        # The newsroom's service does not take journalist input yet, so there
        # the material stays in the case.
        held = build(newsroom)
        require(held.returncode == 2 and not (case / "data" / "knowledge-batch.json").exists(), f"a newsroom batch took the material: {held.stdout}{held.stderr}")
        built = build()
        require(built.returncode == 0, built.stderr)
        batch = json.loads((case / "data" / "knowledge-batch.json").read_text(encoding="utf-8"))
        material = batch["journalist_inputs"][0]
        require(material["content_sha256"] == hashlib.sha256(notes.read_bytes()).hexdigest() and material["source"] == {"pseudonym": "Dock worker 1"}
                and "state" not in material, f"the material's record is not the register's: {material}")
        sent = batch["source_expressions"][0]
        require(sent["text"] == quote and sent["origin_ref"]["type"] == "journalist_input" and sent["origin_ref"]["id"] == material["id"]
                and all(sent[name] == rights[name] for name in rights), f"the passage lost its origin or its rights: {sent}")
        require(batch["claims"][0]["proposition"] == claim["proposition"] and "assessments" not in batch,
                "a claim from material was given a verdict")
        position = batch["source_positions"][0]
        require(position["actor"] == {"label": "Dock worker 1"} and position["stance"] == "asserts", f"{position}")
        # The wiki: a note for the claim, and a place only a protected case may use.
        notes_out = WIKI.notes_for([batch], {}, type("Plain", (), {"link": staticmethod(lambda name, source="": name)})())
        note = next(iter(notes_out.values()))
        require("Dock worker 1 asserts" in note and f'supports: "{quote}" <interview' in note and "## Assessment" not in note,
                f"the claim's note does not show the material as its source: {note}")
        protected = Path(tmp).resolve() / "cases_protected" / "harbour"
        protected.mkdir(parents=True)
        elsewhere = Path(tmp) / "member-wiki"
        require(WIKI.destination(protected, None) == protected.parent / "wiki" and (protected.parent / "wiki" / "spotlight").is_dir(),
                "a protected case is not written to its folder's own wiki")
        require("own wiki" in refused(WIKI.destination, protected, elsewhere, error=WIKI.DestinationError), "a protected case was sent to another wiki")
        require(WIKI.destination(case, elsewhere) == elsewhere, "an ordinary case lost its wiki")

        (case / "intake" / "interviews" / "call.txt").write_text("Rewritten after approval.\n", encoding="utf-8")
        require(build().returncode == 2, "a batch was built from material that changed after it was registered")

        # The command line refuses with exit 2 and leaves the register alone.
        before = (case / "data" / "journalist-input.json").read_bytes()
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "intake.py"), str(case), "confirm", "JI9", "--by", "x"],
                                capture_output=True, text=True)
        require(result.returncode == 2 and "no material record JI9" in result.stderr, result.stderr)
        require((case / "data" / "journalist-input.json").read_bytes() == before, "a refused command changed the register")

    print("material register: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
