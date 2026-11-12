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

import protected_cases as PROTECTED  # noqa: E402
from spotlight_orchestration.dependencies import material_only_case  # noqa: E402

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


def code(case: Path, action: str, record_id: str) -> tuple[str, str]:
    """What the page does: a one-time code for one material and the digest it shows for it."""
    return INTAKE.issue_code(case, action, record_id)["nonce"], INTAKE.view(case)["digests"][record_id]


def confirm(case: Path, record_id: str, by: str = "A. Reporter") -> dict:
    return INTAKE.confirm(case, record_id, by, *code(case, "confirm", record_id))


def approve(case: Path, record_id: str, by: str = "A. Reporter") -> dict:
    return INTAKE.approve(case, record_id, by, *code(case, "approve", record_id))


RIGHTS = {"attribution": "not_for_attribution", "sensitivity": "internal",
          "consent": {"publish_content": True, "publish_direct_quotes": False, "obtained_via": "recorded_oral"}}
QUOTE = "the supervisor told the night shift to stop opening containers"
CLAIM = {"proposition": "Dock worker 1 says the night shift was told to stop opening containers.",
         "statement": {"subject": {"text": "the supervisor"}, "predicate": "told the night shift to stop opening containers"}}


def approved_case(root: Path, name: str) -> Path:
    """A case holding one confirmed material whose one kept claim the page approved."""
    case = root / name
    (case / "data").mkdir(parents=True)
    source = root / f"{name}.txt"
    source.write_text(f"In March {QUOTE}.\n", encoding="utf-8")
    record = INTAKE.set_fields(case, INTAKE.add(case, source)["id"], COMPLETE)
    confirm(case, record["id"])
    stored = INTAKE.propose(case, record["id"], {"passages": [{"text": QUOTE, "language": "en", "direct_quote": True, "claims": [CLAIM]}]})
    INTAKE.set_rights(case, stored["passages"][0]["id"], RIGHTS)
    INTAKE.decide(case, stored["claims"][0]["id"], "keep")
    approve(case, record["id"])
    return case


def edit(case: Path, name: str, change) -> None:
    """A change made to a file by hand, with no command: what a shell can do."""
    path = case / "data" / name
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def counted(case: Path) -> bool:
    """Whether the case's material counts anywhere it is relied on: the builder and the case's phase."""
    built = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-knowledge-batch.py"), str(case)], capture_output=True, text=True)
    return built.returncode == 0 and material_only_case(case) and bool(INTAKE.view(case)["approvals"])


def register(case: Path) -> list[dict]:
    return json.loads((case / "data" / "journalist-input.json").read_text(encoding="utf-8"))["inputs"]


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = str(Path(raw).resolve())  # the case's real path, as the review ledger is read through it
        os.environ["SPOTLIGHT_CONFIG"] = str(Path(tmp) / "solo.json")  # no install config: the solo edition
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
        require("not complete" in refused(confirm, case, record["id"]), "an incomplete record was confirmed")
        closed = confirm(case, filled["id"])
        require(closed["state"] == "confirmed" and closed["confirmed_by"] == "A. Reporter", f"{closed}")
        require("no longer changes" in refused(INTAKE.set_fields, case, filled["id"], {"kind": "document"}), "a confirmed record changed")
        INTAKE.set_fields(case, found[0]["id"], {**COMPLETE, "kind": "document"})
        dropped.write_bytes(b"%PDF-1.7 another contract")
        require("registered bytes" in refused(confirm, case, found[0]["id"]), "changed material was confirmed")

        # A link where a folder of the case should be: nothing is copied through it.
        leak = Path(tmp) / "leak"
        leak.mkdir()
        (case / "intake" / "datasets").symlink_to(leak, target_is_directory=True)
        table = outside / "payments.csv"
        table.write_text("payee,amount\n", encoding="utf-8")
        require("link" in refused(INTAKE.add, case, table) and not any(leak.iterdir()), "material was copied out of the case through a link")
        (case / "intake" / "datasets").unlink()

        # A refused edit leaves the file where the register says it is.
        loose = INTAKE.add(case, outside / "memo.txt") if (outside / "memo.txt").write_text("A memo.\n", encoding="utf-8") else None
        require("not valid" in refused(INTAKE.set_fields, case, loose["id"], {"kind": "field_note", "captured_by": 12}), "a malformed field was stored")
        require((case / loose["content_ref"]).is_file() and loose["content_ref"] == "intake/unsorted/memo.txt",
                "a refused edit moved the file away from where the register names it")

        # propose: a passage is the material's own words at one place, or
        # nothing is recorded. It starts with no rights at all.
        claim = {"proposition": "Dock worker 1 says the night shift was told in March to stop opening containers.",
                 "statement": {"subject": {"text": "the supervisor"}, "predicate": "told the night shift to stop opening containers"}}
        quote = "the supervisor told the night shift to stop opening containers"
        paraphrase = {"passages": [{"text": "the boss told nights to stop checking", "language": "en", "claims": [claim]}]}
        require("word for word" in refused(INTAKE.propose, case, filled["id"], paraphrase), "a paraphrase was recorded as a passage")
        require(not (case / "data" / "intake-claims.json").exists(), "a refused proposal wrote the file")
        # A look-alike character is not the material's own word.
        circled = INTAKE.add(case, outside / "sum.txt", kind="field_note") if (outside / "sum.txt").write_text("The amount is \u2460 million.\n", encoding="utf-8") else None
        lookalike = {"passages": [{"text": "The amount is 1 million.", "language": "en", "claims": [claim]}]}
        require("word for word" in refused(INTAKE.propose, case, circled["id"], lookalike), "a passage the material does not hold was accepted")
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
        require("not confirmed" in refused(approve, case, recording["id"]), "claims of an unconfirmed record were approved")
        require("not yet kept or dropped" in refused(approve, case, filled["id"]), "an undecided claim was approved")
        INTAKE.decide(case, drawn["id"], "keep")
        approval = approve(case, filled["id"])
        require(approval["claims"] == [drawn["id"]] and approval["by"] == "A. Reporter", f"{approval}")
        require("not valid" in refused(INTAKE.set_rights, case, passage["id"], {"attribution": "loud"}), "an unknown attribution was stored")
        INTAKE.set_rights(case, passage["id"], {"attribution": "not_for_attribution", "sensitivity": "publishable",
                                               "consent": {"publish_content": True, "publish_direct_quotes": False, "obtained_via": "recorded_oral"}})
        require(filled["id"] not in json.loads((case / "data" / "intake-claims.json").read_text(encoding="utf-8"))["approvals"],
                "an approval outlived a change to its passage")
        INTAKE.decide(case, drawn["id"], "drop")
        require("no kept claim" in refused(approve, case, filled["id"]), "an approval with nothing kept was recorded")

        # Ingest, with no investigation: the batch builder takes the approved
        # claims as what the source said. No rights on the passage, no batch.
        INTAKE.decide(case, drawn["id"], "keep")
        INTAKE.set_rights(case, passage["id"], {})
        approve(case, filled["id"])
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
        approve(case, filled["id"])
        # The newsroom's service does not take journalist input yet, so there
        # the material stays in the case.
        held = build(newsroom)
        require(held.returncode == 2 and "keeps in the case" in held.stderr, f"a newsroom batch took the material: {held.stdout}{held.stderr}")
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
        # The protected wiki as a link to another wiki: nothing is written through it.
        linked = Path(tmp).resolve() / "linked" / "cases_protected" / "harbour"
        linked.mkdir(parents=True)
        elsewhere.mkdir()
        (linked.parent / "wiki").symlink_to(elsewhere, target_is_directory=True)
        require("link" in refused(WIKI.destination, linked, None, error=WIKI.DestinationError) and not any(elsewhere.iterdir()),
                "a protected case was written into another wiki through a link")
        alias = Path(tmp).resolve() / "plain-name"
        alias.symlink_to(protected.parent, target_is_directory=True)
        require(PROTECTED.protected_case(alias / "harbour"), "a link to the protected folder is not protected")

        (case / "intake" / "interviews" / "call.txt").write_text("Rewritten after approval.\n", encoding="utf-8")
        require(build().returncode == 2, "a batch was built from material that changed after it was registered")

        # The review page's reads and its one bulk write. A passage comes with
        # a few lines around it, from the file it was taken from and only while
        # that file still has the bytes it was taken from.
        long = outside / "long.txt"
        long.write_text("".join(f"line {n}\n" for n in range(1, 4)) + "the foreman signed the second shift off\nthe foreman kept no log\n"
                        + "".join(f"line {n}\n" for n in range(6, 12)), encoding="utf-8")
        page_record = INTAKE.add(case, long, kind="interview")
        INTAKE.set_fields(case, page_record["id"], COMPLETE)
        both = {"statement": {"subject": {"text": "the foreman"}, "predicate": "signed off"}}
        INTAKE.propose(case, page_record["id"], {"passages": [{"text": "the foreman signed the second shift off\nthe foreman kept no log", "language": "en",
                       "claims": [{"proposition": "The foreman signed off a shift.", **both}, {"proposition": "The foreman kept no log.", **both}]}]})
        shown = INTAKE.view(case)
        page_passage = next(p for p in shown["passages"] if p["input_id"] == page_record["id"])
        numbered = [(line["n"], line["in_passage"]) for line in page_passage["context"]["lines"]]
        require(numbered == [(1, False), (2, False), (3, False), (4, True), (5, True), (6, False), (7, False), (8, False)],
                f"the passage is not shown in its context: {numbered}")
        require(page_passage["context"]["lines"][3]["text"] == "the foreman signed the second shift off" and not page_passage["context"]["truncated"],
                f"{page_passage['context']}")
        require(shown["name_required"] is False and any(r["id"] == page_record["id"] for r in shown["inputs"]), "the view lacks the register or the edition")
        kept = INTAKE.keep_all(case, page_record["id"])
        require(len(kept) == 2 and all(c["state"] == "kept" for c in kept), f"keep_all did not keep both claims: {kept}")
        require("no claim left" in refused(INTAKE.keep_all, case, page_record["id"]), "keep_all passed with nothing left to decide")
        require("no material record" in refused(INTAKE.keep_all, case, "JI99"), "keep_all took an unknown record")
        confirm(case, page_record["id"])
        approve(case, page_record["id"])
        INTAKE.decide(case, kept[0]["id"], "drop")
        require(page_record["id"] not in INTAKE.view(case)["approvals"], "an approval outlived a decision")
        # The file the passage came from changed, or became a link: no context is shown.
        long_copy = case / page_record["content_ref"]
        original = long_copy.read_bytes()
        long_copy.write_bytes(original + b"added later\n")
        require(next(p for p in INTAKE.view(case)["passages"] if p["input_id"] == page_record["id"])["context"] is None, "context was read from changed bytes")
        long_copy.write_bytes(original)
        outside_copy = outside / "secret.txt"
        outside_copy.write_bytes(original)
        long_copy.unlink()
        long_copy.symlink_to(outside_copy)
        require(next(p for p in INTAKE.view(case)["passages"] if p["input_id"] == page_record["id"])["context"] is None, "context was read through a link")
        # Who a page's decision is recorded for follows the edition.
        require(INTAKE.decider("anyone") == {"by": "Member"}, "the solo edition took a name")
        os.environ["SPOTLIGHT_CONFIG"] = str(newsroom)
        try:
            require(INTAKE.decider(" A. Reporter ") == {"by": "A. Reporter"} and INTAKE.view(case)["name_required"] is True, "the newsroom edition did not ask for a name")
            require("name" in refused(INTAKE.decider, None) and "name" in refused(INTAKE.decider, "  "), "the newsroom edition accepted no name")
        finally:
            os.environ["SPOTLIGHT_CONFIG"] = str(solo)

        # Every confirmation and approval needs the page's one-time code, for that material and the digest the
        # page showed. Without it, with a spent code, with another material's, or with a stale digest, nothing is saved.
        draft = INTAKE.add(case, outside / "plain.txt") if (outside / "plain.txt").write_text("A plain note.\n", encoding="utf-8") else None
        INTAKE.set_fields(case, draft["id"], {**COMPLETE, "kind": "field_note"})
        before = (case / "data" / "journalist-input.json").read_bytes()
        require("Intake page" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter"), "a confirmation without a code was accepted")
        require("Intake page" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter", nonce="0" * 64), "a confirmation without a digest was accepted")
        require("not issued" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter", "0" * 64, INTAKE.view(case)["digests"][draft["id"]]),
                "a code the ledger never issued was accepted")
        approval_code = code(case, "approve", filled["id"])
        require("not issued" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter", approval_code[0], INTAKE.view(case)["digests"][draft["id"]]),
                "an approval code confirmed a record")
        other_code = code(case, "confirm", filled["id"])
        require("not issued" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter", *other_code), "a code issued for one material confirmed another")
        stale = code(case, "confirm", draft["id"])
        INTAKE.set_fields(case, draft["id"], {"captured_by": "Another Reporter"})
        require("changed since the page showed it" in refused(INTAKE.confirm, case, draft["id"], "A. Reporter", *stale), "a digest of older fields confirmed the record")
        require(json.loads((case / "data" / "journalist-input.json").read_text())["inputs"][-1]["state"] == "draft"
                and (case / "data" / "journalist-input.json").read_bytes() != before, "a refusal changed more than the edit")
        used = code(case, "confirm", draft["id"])
        INTAKE.confirm(case, draft["id"], "A. Reporter", *used)
        INTAKE.propose(case, draft["id"], {"passages": [{"text": "A plain note.", "language": "en", "claims": [CLAIM]}]})
        INTAKE.decide(case, next(c["id"] for c in INTAKE.view(case)["claims"] if c["passage_id"] == INTAKE.view(case)["passages"][-1]["id"]), "keep")
        ledger_before = (case / "data" / "review-nonces.json").read_bytes()
        claims_before = (case / "data" / "intake-claims.json").read_bytes()
        require("Intake page" in refused(INTAKE.approve, case, draft["id"], "A. Reporter"), "an approval without a code was accepted")
        require("not issued" in refused(INTAKE.approve, case, draft["id"], "A. Reporter", *approval_code),
                "a code issued for one material approved another")
        require("not issued" in refused(INTAKE.approve, case, draft["id"], "A. Reporter", *used), "a confirmation code approved a material")
        require((case / "data" / "intake-claims.json").read_bytes() == claims_before and (case / "data" / "review-nonces.json").read_bytes() == ledger_before,
                "a refused approval changed the claims or spent a code")
        once = code(case, "approve", draft["id"])
        INTAKE.approve(case, draft["id"], "A. Reporter", *once)
        INTAKE.decide(case, INTAKE.view(case)["claims"][-1]["id"], "drop")
        INTAKE.decide(case, INTAKE.view(case)["claims"][-1]["id"], "keep")
        require("already been used" in refused(INTAKE.approve, case, draft["id"], "A. Reporter", *once), "a spent code approved again")
        stored_ledger = json.loads((case / "data" / "review-nonces.json").read_text())["nonces"]
        require(all(len(key) == 64 for key in stored_ledger) and used[0] not in json.dumps(stored_ledger), "the ledger keeps a code, not only its hash")

        # What is not backed is not decided: a record or approval written by hand, copied from another case, or
        # changed after the page approved it does not count in the builder, in the case's phase or on the page.
        approved = approved_case(Path(tmp), "approved")
        require(counted(approved), "a page-approved material does not count")
        for number, (label, name, change) in enumerate((
            ("a changed passage right", "intake-claims.json", lambda d: d["passages"][0]["rights"].update(sensitivity="publishable")),
            ("a rewritten claim", "intake-claims.json", lambda d: d["claims"][0].update(proposition="Dock worker 1 says something else.")),
            ("an added kept claim", "intake-claims.json", lambda d: d["claims"].append({**d["claims"][0], "id": "JC9", "proposition": "Another."})),
            ("a changed direct-quote flag", "intake-claims.json", lambda d: d["passages"][0].update(direct_quote=False)),
            ("a changed record", "journalist-input.json", lambda d: d["inputs"][0].update(original_sensitivity="publishable")),
        )):
            voided = approved_case(Path(tmp), f"voided-{number}")
            edit(voided, name, change)
            require(not counted(voided) and INTAKE.view(voided)["approvals"] == {}, f"{label} made by hand left the approval standing")
        written = approved_case(Path(tmp), "written")
        edit(written, "intake-claims.json", lambda d: d["approvals"]["JI1"].pop("review"))
        require(not counted(written), "an approval with no receipt counted")
        edit(written, "intake-claims.json", lambda d: d["approvals"]["JI1"].update(review={"channel": "page", "nonce_sha256": "1" * 64}, dependency_digest="1" * 64))
        require(not counted(written), "an approval naming a code the ledger never held counted")
        (written / "data" / "review-nonces.json").unlink()
        stolen = json.loads((approved / "data" / "intake-claims.json").read_text())["approvals"]["JI1"]
        edit(written, "intake-claims.json", lambda d: d["approvals"].update(JI1=stolen))
        require(not counted(written), "an approval copied from another case counted")
        # A record confirmed by hand is a draft: it shows as one, and its claims cannot be approved.
        by_hand = Path(tmp) / "by-hand"
        (by_hand / "data").mkdir(parents=True)
        (by_hand.parent / "by-hand.txt").write_text("A note.\n", encoding="utf-8")
        held = INTAKE.set_fields(by_hand, INTAKE.add(by_hand, by_hand.parent / "by-hand.txt")["id"], COMPLETE)
        edit(by_hand, "journalist-input.json", lambda d: d["inputs"][0].update(state="confirmed", confirmed_by="x", confirmed_at="2026-01-01T00:00:00Z"))
        require(INTAKE.view(by_hand)["inputs"][0]["state"] == "draft", "a record confirmed by hand shows as confirmed")
        require("not confirmed yet" in refused(approve, by_hand, held["id"]), "the claims of a record confirmed by hand were approved")
        confirm(by_hand, held["id"])
        require(INTAKE.view(by_hand)["inputs"][0]["state"] == "confirmed", "the page could not confirm a record whose state was edited by hand")

        # The command line refuses with exit 2 and leaves the register alone.
        before = (case / "data" / "journalist-input.json").read_bytes()
        command = lambda *args: subprocess.run([sys.executable, str(ROOT / "scripts" / "intake.py"), str(case), *args], capture_output=True, text=True)  # noqa: E731
        result = command("confirm", "JI9", "--by", "x", "--nonce", "0" * 64, "--digest", "0" * 64)
        require(result.returncode == 2 and "no material record JI9" in result.stderr, result.stderr)
        for action in ("confirm", "approve"):
            result = command(action, filled["id"], "--by", "Member")
            require(result.returncode == 2 and "Intake page" in result.stderr, f"{action} on the command line without a code: {result.stderr}")
        require((case / "data" / "journalist-input.json").read_bytes() == before, "a refused command changed the register")

    print("material register: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
