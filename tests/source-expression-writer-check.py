#!/usr/bin/env python3
"""The single passage writer and the case-contract writers.

Covers the source-expression pilot's blocker 4 (interrupted and concurrent
producer writes) with fault injection: a write that fails mid-publish leaves
the previous file byte for byte, and concurrent writers never lose an
update.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WRITER = load("write_source_expressions", SCRIPTS / "write-source-expressions.py")
CASES = load("validate_case_check", ROOT / "tests" / "validate-case-check.py")
from spotlight_orchestration import case_writer  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402

PASSAGES = [f"Passage number {i}." for i in range(8)]


def make_case(root: Path) -> Path:
    """An activated case born under the new default: a born event, no
    artifact hashes until its gate approval binds them."""
    case = root / "case"
    CASES.write_activated_case(case)
    contract_path = case / "data" / "case-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["activation_events"] = [{
        "event_id": "born-test", "kind": "born", "activated_contract_version": "1.1",
        "activated_at": "2026-10-22T12:00:00Z", "tool_version": "test"}]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    passages = case / "research" / "passages.txt"
    passages.write_text("\n".join(PASSAGES) + "\n", encoding="utf-8")
    bundle_path = case / "data" / "evidence-bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    item = json.loads(json.dumps(bundle["items"][0]))
    item.update({"id": "E2", "raw_path": "research/passages.txt",
                 "sha256": hashlib.sha256(passages.read_bytes()).hexdigest()})
    item.pop("text_derivatives", None)
    bundle["items"].append(item)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return case


def proposal(index: int, finding: str = "F1", relation: str = "context") -> dict:
    line = 1 + index
    return {"expressions": [{
        "id": f"SXW{index}",
        "text": PASSAGES[index],
        "anchor_ref": {"path": "research/passages.txt", "line_start": line, "line_end": line},
        "original_evidence_bundle_id": "E2",
        "language": "en",
        "finding_links": [{"finding_id": finding, "relation": relation}],
    }]}


def run_writer(case: Path, body: dict, actor: str = "investigator") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--actor", actor, "--cycle", "1"],
        input=json.dumps(body), capture_output=True, text=True,
    )


def expressions(case: Path) -> dict:
    return json.loads((case / "data" / "source-expressions.json").read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        case = make_case(root)
        target = case / "data" / "source-expressions.json"

        # The writer computes fingerprints and hashes and merges by id.
        result = run_writer(case, proposal(0))
        require(result.returncode == 0, result.stderr)
        added = next(e for e in expressions(case)["expressions"] if e["id"] == "SXW0")
        require(len(added["expression_fingerprint"]) == 64 and added["created_by"] == "investigator", "writer did not build the record")
        require(added["anchor_sha256"] == hashlib.sha256((case / "research/passages.txt").read_bytes()).hexdigest(), "anchor hash not computed")
        # Shakedown on 26683b3c: the fact-checker could not cite passages it had written,
        # because the result carried no ref objects; it now returns the stored fingerprints.
        link = added["finding_links"][0]
        expected = {"expression_id": "SXW0", "expression_fingerprint": added["expression_fingerprint"],
                    "finding_fingerprint": link["finding_fingerprint"], "link_fingerprint": link["link_fingerprint"]}
        require(json.loads(result.stdout)["source_expression_refs"] == {link["finding_id"]: {link["relation"]: [expected]}},
                "the writer did not return the fact-check refs for its passages")
        # Gold run 1 on 99c6567e: the fact-checker cited passages by bare id and the case blocked.
        # Gold asf s1 on 8c7bf163: it cited them as {expression_id, relation} objects and looped.
        # A ref that carries a fingerprint is left for validate-case to judge.
        fact_check = case / "data" / "fact-check.json"
        original = fact_check.read_bytes()
        id_only = {"expression_id": "SXW0", "relation": "supports"}
        stale = {"expression_id": "SXW0", "link_fingerprint": "0" * 64}
        fact_check.write_text(json.dumps({"claims": [{"finding_id": link["finding_id"], "evidence_for": [{"source_expression_refs": ["SXW0", id_only, stale, "SX-UNKNOWN"]}]}]}))
        subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--resolve-fact-check-refs"], check=True, capture_output=True)
        require(json.loads(fact_check.read_text())["claims"][0]["evidence_for"][0]["source_expression_refs"] == [expected, expected, stale, "SX-UNKNOWN"],
                "a bare id or id-only ref was not resolved to its full ref (or a fingerprinted or unknown ref was changed)")
        fact_check.write_bytes(original)
        again = run_writer(case, proposal(0, relation="context"), actor="fact-checker")
        require(again.returncode == 0 and next(e for e in expressions(case)["expressions"] if e["id"] == "SXW0")["created_by"] == "investigator",
                "re-proposing an expression changed its author")
        changed = proposal(0)
        changed["expressions"][0]["text"] = PASSAGES[1]
        changed["expressions"][0]["anchor_ref"] = {"path": "research/passages.txt", "line_start": 2, "line_end": 2}
        before = target.read_bytes()
        refused = run_writer(case, changed)
        require(refused.returncode == 2 and "different passage" in refused.stderr and target.read_bytes() == before,
                "a changed passage under an existing id was accepted")
        # r1/r2: the 12B sent the hashes and fingerprints the writer computes; they are
        # dropped with a note, and the writer's own values are written.
        forged = proposal(1)
        forged["expressions"][0].update({"expression_fingerprint": "0" * 64, "anchor_sha256": "1" * 64, "cycle": 9})
        written = run_writer(case, forged)
        stored = next(e for e in expressions(case)["expressions"] if e["id"] == "SXW1")
        require(written.returncode == 0 and stored["expression_fingerprint"] != "0" * 64 and stored["anchor_sha256"] != "1" * 64
                and stored["cycle"] == 1 and "ignored ['anchor_sha256', 'cycle', 'expression_fingerprint']" in written.stdout,
                f"caller-supplied computed fields were kept or refused: {written.stderr}")
        before = target.read_bytes()
        # Ingest refuses a passage without a language, so the writer does too (E2E D14).
        no_language = proposal(1)
        no_language["expressions"][0].pop("language", None)
        refused = run_writer(case, no_language)
        require(refused.returncode == 2 and "language is required" in refused.stderr and target.read_bytes() == before,
                "a passage without a language was written")
        # Gold run: a 12B teacher sent anchor_ref as "path:81-81"; the path is read and the text located.
        string_anchor = proposal(1)
        string_anchor["expressions"][0]["anchor_ref"] = "research/passages.txt:2-2"
        written = run_writer(case, string_anchor)
        require(written.returncode == 0 and target.read_bytes() == before, f"a string anchor_ref was not read as its path: {written.stderr}")
        refused = run_writer(case, {"expressions": [{**proposal(1)["expressions"][0], "anchor_ref": ["research/passages.txt"]}]})
        require(refused.returncode == 2 and "must be an object" in refused.stderr and target.read_bytes() == before,
                "an anchor_ref that is neither an object nor a path was not refused with its expected shape")
        # 73308129 shakedown: a teacher sent line numbers as strings and lifecycle entries with other keys.
        string_lines = proposal(2)
        string_lines["expressions"][0]["anchor_ref"] = {"path": "research/passages.txt", "line_start": "3", "line_end": "3"}
        written = run_writer(case, string_lines)
        require(written.returncode == 0, f"line numbers sent as digit strings were refused: {written.stderr}")
        before = target.read_bytes()
        wrong_keys = {"lifecycle": [{"action": "supersede", "id": "SXW0", "successor_id": "SXW9"}]}
        refused = run_writer(case, wrong_keys)
        require(refused.returncode == 2 and '"expression_id": "P1"' in refused.stderr and '"action": "supersede"' in refused.stderr
                and target.read_bytes() == before, "a lifecycle entry with the wrong keys was not refused with the expected shape")
        # Text the file does not hold is refused, whatever the anchor says.
        wrong_text = proposal(3)
        wrong_text["expressions"][0]["text"] = "Not what line 4 says."
        refused = run_writer(case, wrong_text)
        require(refused.returncode == 2 and "is not in research/passages.txt nor in any other file of data/evidence-bundle.json" in refused.stderr
                and WRITER.ESCAPE in refused.stderr and target.read_bytes() == before,
                "a passage that is not in any acquired source was written, or its refusal gave no honest exit")
        # U3 census (84674fd9): a near-miss quote is shown the closest lines,
        # verbatim with line numbers, and told a line counts only if it states
        # the claim, else the verdict is unverified.
        near = proposal(3)
        near["expressions"][0]["text"] = "Passage number three."
        refused = run_writer(case, near)
        hint = refused.stderr.split("The lines closest to it in the files searched are below.", 1)
        listed = [line for line in hint[-1].splitlines() if line.startswith("research/passages.txt line ")]
        require(refused.returncode == 2 and len(hint) == 2
                and "A line counts only if it states what the finding claims; then quote it exactly as written. "
                    "If none does, set the verdict to unverified." in hint[1]
                and 1 <= len(listed) <= 3
                and all(line.split(": ", 1)[1] == PASSAGES[int(line.split(" line ")[1].split(":")[0]) - 1] for line in listed)
                and target.read_bytes() == before,
                f"a near-miss quote was not shown at most three verbatim closest lines with the unverified fallback: {refused.stderr}")
        require("The lines closest to it" not in run_writer(case, wrong_text).stderr,
                "a quote close to no line was shown candidate lines")
        # rc3 john-lewis (52bf6183): a quote that is in the named file, which no
        # evidence item lists, was refused as "not in" that file. It is told
        # the file needs its evidence item first.
        unlisted_file = case / "research" / "unlisted.txt"
        unlisted_file.write_text("A line only an unlisted file holds.\n", encoding="utf-8")
        unlisted = proposal(5)
        unlisted["expressions"][0].update({"text": "A line only an unlisted file holds.", "anchor_ref": {"path": "research/unlisted.txt"}, "original_evidence_bundle_id": None})
        refused = run_writer(case, unlisted)
        require(refused.returncode == 2
                and "the text is in research/unlisted.txt, but no item in data/evidence-bundle.json lists that file" in refused.stderr
                and "is not in research/unlisted.txt" not in refused.stderr and target.read_bytes() == before,
                f"a quote in a file no evidence item lists was not told to add the item: {refused.stderr}")
        unlisted_file.unlink()
        # incr-test2 sp98: the anchored file is unlisted but the same text is
        # in listed files; the anchor decides the place, and the listed files
        # are offered, not "appears in N places".
        twin = case / "research" / "twin.txt"
        twin.write_text(PASSAGES[6] + "\n", encoding="utf-8")
        anchored = proposal(6)
        anchored["expressions"][0].update({"anchor_ref": {"path": "research/twin.txt"}, "original_evidence_bundle_id": "FC-E9"})
        refused = run_writer(case, anchored)
        require(refused.returncode == 2
                and "the text is in research/twin.txt, but no item in data/evidence-bundle.json lists that file" in refused.stderr
                and "(FC-E9 is not in it yet)" in refused.stderr
                and "Or anchor it to research/passages.txt" in refused.stderr and "places" not in refused.stderr
                and target.read_bytes() == before,
                f"a quote anchored in an unlisted file was not told to add it or use the listed file: {refused.stderr}")
        twin.unlink()
        # fc-stall item 12: a passage linked to a finding not written yet is
        # told to write the finding first.
        early = proposal(2, finding="D9-F1")
        early["expressions"][0]["id"] = "SXW90"
        refused = run_writer(case, early)
        require(refused.returncode == 2 and "finding 'D9-F1' does not exist in data/findings.json yet. Write the finding first" in refused.stderr
                and target.read_bytes() == before, f"a passage before its finding was not told to write the finding first: {refused.stderr}")
        # fc-stall item 6: a new passage given an id already taken is told a
        # free id in the same numbering.
        taken = proposal(4)
        taken["expressions"][0]["id"] = "SXW0"
        refused = run_writer(case, taken)
        require(refused.returncode == 2 and "already exists with a different passage. For a new passage, give it a free id such as 'SXW" in refused.stderr
                and "give it a free id such as 'SXW0'" not in refused.stderr and target.read_bytes() == before,
                f"an id collision did not name a free id: {refused.stderr}")
        # u3 p1 eu-ai-act: a file that several evidence items list is cited by
        # the first of them, with a note, instead of a refusal.
        bundle_path = case / "data" / "evidence-bundle.json"
        saved_bundle = bundle_path.read_bytes()
        bundle = json.loads(saved_bundle)
        extra = json.loads(json.dumps(next(i for i in bundle["items"] if i["id"] == "E2")))
        extra["id"] = "E3"
        bundle["items"].append(extra)
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        shared = proposal(7)
        shared["expressions"][0]["original_evidence_bundle_id"] = None
        result = run_writer(case, shared)
        stored_shared = next((e for e in expressions(case)["expressions"] if e["id"] == "SXW7"), {})
        require(result.returncode == 0 and stored_shared.get("original_evidence_bundle_id") == "E2"
                and "cites evidence item E2 (also listed by E3)" in result.stdout,
                f"a file two items list was not cited by the first: {result.stdout} {result.stderr}")
        bundle_path.write_bytes(saved_bundle)
        target.write_bytes(before)

        # r1/r2 source_expressions refusals the writer now resolves itself: each
        # case below was refused on 97f066b6.
        def stored(expression_id: str) -> dict:
            return next(e for e in expressions(case)["expressions"] if e["id"] == expression_id)

        def relaxed(expression_id: str, index: int, **fields) -> subprocess.CompletedProcess:
            body = proposal(index)
            body["expressions"][0].update({"id": expression_id, **fields})
            for key in [key for key, value in fields.items() if value is None]:
                body["expressions"][0].pop(key)
            return run_writer(case, body)

        # 199 of 232 "unknown fields" refusals were the tag sent as `lang`.
        done = relaxed("SXR-LANG", 4, language=None, lang="en")
        require(done.returncode == 0 and stored("SXR-LANG")["language"] == "en" and "read lang as language" in done.stdout,
                f"lang was not read as language: {done.stderr}")
        refused = relaxed("SXR-LANG2", 4, lang="fr")
        require(refused.returncode == 2 and "differ" in refused.stderr, "lang and language that differ were accepted")
        refused = relaxed("SXR-QUOTE", 4, quote="Passage number 4.")
        require(refused.returncode == 2 and "unknown fields ['quote']" in refused.stderr and "allowed fields are text, anchor_ref" in refused.stderr,
                "an invented field was accepted, or its refusal did not name the allowed fields")
        # No anchor: the text is found in the evidence item's own file.
        done = relaxed("SXR-NOANCHOR", 6, anchor_ref=None)
        require(done.returncode == 0 and stored("SXR-NOANCHOR")["anchor_ref"] == {"path": "research/passages.txt", "line_start": 7, "line_end": 7},
                f"a passage without anchor_ref was not located: {done.stderr}")
        # No anchor, and the passage is in the item's OCR text: the derivative is searched too.
        ocr = case / "research" / "ocr.txt"
        ocr.write_text("Scanned page: the board met on 3 May.\n", encoding="utf-8")
        bundle_path = case / "data" / "evidence-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        next(item for item in bundle["items"] if item["id"] == "E2")["text_derivatives"] = [{
            "id": "E2-ocr", "derivative_type": "ocr", "path": "research/ocr.txt",
            "sha256": hashlib.sha256(ocr.read_bytes()).hexdigest(), "human_verification_required": False}]
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        done = relaxed("SXR-OCR", 4, text="the board met on 3 May.", anchor_ref=None)
        require(done.returncode == 0 and stored("SXR-OCR")["anchor_ref"]["path"] == "research/ocr.txt"
                and stored("SXR-OCR")["text"] == "Scanned page: the board met on 3 May.", f"a passage in a text derivative was not located: {done.stderr}")
        # An anchor whose lines do not select the text: the text is found in that file.
        done = relaxed("SXR-WRONGLINES", 7, anchor_ref={"path": "research/passages.txt", "line_start": 1, "line_end": 1})
        require(done.returncode == 0 and stored("SXR-WRONGLINES")["anchor_ref"]["line_start"] == 8, f"a wrong line range was not relocated: {done.stderr}")
        # Line-wrapped or re-spaced text matches on validate-case's NFKC whitespace rule;
        # the stored text is the file's own lines.
        done = relaxed("SXR-SPACING", 5, text="Passage   number\n5.", anchor_ref={"path": "research/passages.txt"})
        require(done.returncode == 0 and stored("SXR-SPACING")["text"] == PASSAGES[5] and "text set to those lines" in done.stdout,
                f"a whitespace-normalised match was not anchored to the file's lines: {done.stderr}")
        # Text found more than once is refused with the candidate ranges, never chosen.
        before = target.read_bytes()
        refused = relaxed("SXR-AMBIGUOUS", 5, text="Passage number", anchor_ref=None)
        require(refused.returncode == 2 and "appears in 8 places" in refused.stderr and "quote a longer span that is unique" in refused.stderr and "research/passages.txt lines 1-1" in refused.stderr
                and target.read_bytes() == before, "an ambiguous passage was anchored")

        # 12B r1 quotes that were the source's words but not exact, or cited the
        # wrong file or item, or were the model's own text (each refused on #206).
        other = case / "research" / "other.txt"
        other.write_text("**Revenue** was `42` million in 2024.\nMinutes: the board met on 9 June.\n", encoding="utf-8")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        item = json.loads(json.dumps(next(i for i in bundle["items"] if i["id"] == "E2")))
        item.update({"id": "E3", "raw_path": "research/other.txt", "sha256": hashlib.sha256(other.read_bytes()).hexdigest()})
        item.pop("text_derivatives", None)
        bundle["items"].append(item)
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        # A markdown-only difference: the file's own line is stored, never the model's version.
        done = relaxed("SXR-MD", 4, text='"Revenue was 42 million in 2024."', anchor_ref={"path": "research/other.txt"}, original_evidence_bundle_id="E3")
        require(done.returncode == 0 and stored("SXR-MD")["text"] == "**Revenue** was `42` million in 2024.",
                f"a quote differing only in markdown and wrapping quotes was not stored as the file's line: {done.stderr}")
        # The named file lacks the quote and one other evidence file has it: that file and its item are cited.
        done = relaxed("SXR-DERIVE", 4, text="Minutes: the board met on 9 June.")
        require(done.returncode == 0 and stored("SXR-DERIVE")["anchor_ref"]["path"] == "research/other.txt"
                and stored("SXR-DERIVE")["original_evidence_bundle_id"] == "E3" and "cites evidence item E3" in done.stdout,
                f"the one acquired source holding the quote was not derived: {done.stderr}")
        # The quote is exact in the named file, but that file is another item's: that item is cited.
        done = relaxed("SXR-OWNER", 4, text="Minutes: the board met on 9 June.",
                       anchor_ref={"path": "research/other.txt", "line_start": 2, "line_end": 2})
        require(done.returncode == 0 and stored("SXR-OWNER")["original_evidence_bundle_id"] == "E3",
                f"an anchor in another item's file was not cited to that item: {done.stderr}")
        # A bundle item that does not exist is replaced by the one that holds the quote.
        done = relaxed("SXR-NOITEM", 4, text="Minutes: the board met on 9 June.", original_evidence_bundle_id="E99", anchor_ref=None)
        require(done.returncode == 0 and stored("SXR-NOITEM")["original_evidence_bundle_id"] == "E3", f"a missing bundle item was not derived: {done.stderr}")
        # A quote much shorter than its line says the passage is wider.
        done = relaxed("SXR-WIDE", 4, text="9 June.", anchor_ref={"path": "research/other.txt"}, original_evidence_bundle_id="E3")
        require(done.returncode == 0 and "wider than your quote" in done.stdout, f"a widened passage was not flagged: {done.stdout}")
        # The model's own placeholder is in no acquired source: refused, with the honest exit.
        before = target.read_bytes()
        refused = relaxed("SXR-PLACEHOLDER", 4, text="Passage for F2")
        require(refused.returncode == 2 and WRITER.ESCAPE in refused.stderr and target.read_bytes() == before,
                "a placeholder passage was written, or its refusal gave no honest exit")
        # Derivation searches only the files the caller may read.
        body = proposal(4)
        body["expressions"][0].update({"id": "SXR-SCOPE", "text": "Minutes: the board met on 9 June."})
        scoped = subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--actor", "fact-checker", "--cycle", "1",
                                 "--readable", "research/passages.txt"], input=json.dumps(body), capture_output=True, text=True)
        require(scoped.returncode == 2 and WRITER.ESCAPE in scoped.stderr and target.read_bytes() == before,
                "derivation searched a file outside --readable")

        # Interrupted write: the publish fails after staging; the previous file
        # stays byte for byte and no temporary file is left behind.
        before = target.read_bytes()
        real_replace = case_writer.os.replace

        def failing_replace(*args, **kwargs):
            raise OSError("injected failure during publish")

        case_writer.os.replace = failing_replace
        try:
            WRITER.write(case, proposal(2), "investigator", 1)
            raise AssertionError("an interrupted publish reported success")
        except (OSError, OrchestrationError):
            pass
        finally:
            case_writer.os.replace = real_replace
        require(target.read_bytes() == before, "an interrupted write changed the file")
        leftovers = [p.name for p in (case / "data").iterdir() if p.name.startswith(".source-expressions.json.")]
        require(not leftovers, f"an interrupted write left {leftovers}")

        # Concurrent writers: each reads its proposal from a file, so all six
        # start at once and contend for the case lock; every update survives.
        files = []
        for i in range(2, 8):
            path = root / f"proposal-{i}.json"
            path.write_text(json.dumps(proposal(i)), encoding="utf-8")
            files.append(path)
        procs = [
            subprocess.Popen(
                [sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case),
                 "--actor", "investigator" if i % 2 else "fact-checker", "--cycle", "1", "--file", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for i, path in zip(range(2, 8), files)
        ]
        for i, proc in zip(range(2, 8), procs):
            _, err = proc.communicate()
            require(proc.returncode == 0, f"concurrent writer {i} failed: {err}")
        ids = {e["id"] for e in expressions(case)["expressions"]}
        require({"SXW0"} | {f"SXW{i}" for i in range(2, 8)} <= ids, f"a concurrent write was lost: {sorted(ids)}")
        validation = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(validation.returncode == 0, validation.stdout + validation.stderr)

        # Source positions go through the same writer and publish with the
        # passages in one locked write.
        positions_path = case / "data" / "source-positions.json"

        def position(**overrides) -> dict:
            body = {"id": "SP1", "finding_id": "F1", "actor": {"label": "Acme spokesperson"},
                    "stance": "disputes", "source_expression_ids": ["SXW2"],
                    "attribution_verbatim": "Passage number 2"}
            body.update(overrides)
            return {"expressions": [], "positions": [body]}

        made = run_writer(case, position())
        require(made.returncode == 0 and json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["created_by"] == "investigator",
                "a valid position was not written: " + made.stderr)
        positions_before, passages_before = positions_path.read_bytes(), target.read_bytes()
        for bad, reason in (
            (position(id="SP2", attribution_verbatim="the ministry said"), "does not appear"),
            (position(id="SP2", source_expression_ids=["SXW404"]), "does not exist"),
            (position(id="SP2", finding_id="F404"), "does not exist"),
            (position(stance="asserts"), "already exists"),
            (position(id="SP2", created_by="human"), "unknown fields"),
        ):
            refused = run_writer(case, bad)
            require(refused.returncode == 2 and reason in refused.stderr, f"a bad position was accepted: {bad} {refused.stderr}")
            require(positions_path.read_bytes() == positions_before and target.read_bytes() == passages_before,
                    "a refused position changed a file")
        unioned = run_writer(case, position(source_expression_ids=["SXW3"], attribution_verbatim="Passage number 2"))
        require(unioned.returncode == 0 and json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["source_expression_ids"] == ["SXW2", "SXW3"],
                "re-proposing a position did not union its passages: " + unioned.stderr)
        # A refused position refuses the passages proposed with it.
        mixed = proposal(5)
        mixed["expressions"][0]["id"] = "SXW-MIXED"
        mixed["positions"] = position(id="SP2", attribution_verbatim="not in any passage")["positions"]
        refused = run_writer(case, mixed)
        require(refused.returncode == 2 and target.read_bytes() == passages_before
                and "SXW-MIXED" not in {e["id"] for e in expressions(case)["expressions"]},
                "passages were written although their position was refused")
        # Withdrawing every cited passage is refused while the position stands.
        supersede = {"expressions": [], "lifecycle": [
            {"expression_id": "SXW2", "event": "withdrawn", "reason": "test"},
            {"expression_id": "SXW3", "event": "withdrawn", "reason": "test"}]}
        stuck = run_writer(case, supersede)
        require(stuck.returncode == 2 and "cites no active passage" in stuck.stderr and target.read_bytes() == passages_before,
                "a lifecycle change that leaves a position citing nothing active was accepted: " + stuck.stderr)
        # Withdrawing the position frees the passages to change.
        withdrawn = run_writer(case, {"expressions": [], "withdraw_positions": [{"id": "SP1", "reason": "statement retracted"}]})
        require(withdrawn.returncode == 0 and json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["withdrawn"]["reason"] == "statement retracted",
                "a position could not be withdrawn: " + withdrawn.stderr)
        require(run_writer(case, position(source_expression_ids=["SXW3"])).returncode == 2, "a withdrawn position was re-proposed")
        # An inactive cited passage is reported by the validator.
        inactive = expressions(case)
        for expression in inactive["expressions"]:
            if expression["id"] in ("SXW2", "SXW3"):
                expression["lifecycle_events"].append({"event": "withdrawn", "timestamp": "2026-10-22T13:00:00Z", "actor": "human", "reason": "t"})
        live = json.loads(positions_path.read_text(encoding="utf-8"))
        live["positions"][0].pop("withdrawn")
        errors = CASES.vc.validate_source_positions(live, json.loads((case / "data" / "findings.json").read_text(encoding="utf-8")), inactive)
        require(any("cites no active passage" in e for e in errors), f"an inactive-only position was accepted: {errors}")
        # Positions are written for a new live position again.
        require(run_writer(case, position(id="SP3", source_expression_ids=["SXW4"], attribution_verbatim="Passage number 4")).returncode == 0,
                "a second position was refused")
        # A cited passage must be linked to the position's finding.
        unlinked = expressions(case)
        for expression in unlinked["expressions"]:
            if expression["id"] == "SXW3":
                expression["finding_links"] = [dict(expression["finding_links"][0], finding_id="F2")]
        errors = CASES.vc.validate_source_positions(json.loads(positions_path.read_text(encoding="utf-8")),
                                                     json.loads((case / "data" / "findings.json").read_text(encoding="utf-8")), unlinked)
        require(any("not linked to finding" in e for e in errors), f"an unlinked passage was accepted: {errors}")

        # A two-file publish that fails on its second file puts the first
        # back: findings and passages never disagree after a failure.
        findings_path = case / "data" / "findings.json"
        original_findings, original_passages = findings_path.read_bytes(), target.read_bytes()
        changed_claim = json.loads(original_findings)
        changed_claim["findings"][0]["claim"] = "Acme paid Doe in full."
        findings_path.write_text(json.dumps(changed_claim), encoding="utf-8")
        staged_findings = findings_path.read_bytes()
        calls = {"n": 0}

        def fail_second_replace(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("injected failure on the second file")
            return real_replace(*args, **kwargs)

        case_writer.os.replace = fail_second_replace
        try:
            WRITER.fingerprint_findings(case)
            raise AssertionError("a failed two-file publish reported success")
        except (OSError, OrchestrationError):
            pass
        finally:
            case_writer.os.replace = real_replace
        require(findings_path.read_bytes() == staged_findings and target.read_bytes() == original_passages,
                "a failed two-file publish left one file changed")
        findings_path.write_bytes(original_findings)

        # Finding fingerprints come from code, and a changed claim refreshes
        # its passage links in the same write.
        findings_path = case / "data" / "findings.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        findings["findings"][0]["claim"] = "Acme paid Doe twice."
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        fp = subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--fingerprint-findings"],
                            capture_output=True, text=True)
        require(fp.returncode == 0 and '"findings_changed": 1' in fp.stdout, fp.stdout + fp.stderr)
        doc = expressions(case)
        errors = CASES.vc.validate_source_expressions(case, doc, json.loads(findings_path.read_text(encoding="utf-8")), None, None, enforce_positive_verdicts=False)
        require(not [e for e in errors if "fingerprint" in e], f"stale fingerprints after --fingerprint-findings: {errors}")

        # A claim rewritten in a new cycle leaves the previous fact-check's
        # expression refs stale. The investigator's passages still publish,
        # and the case validator hands the stale refs to the fact-checker,
        # whose next fact-check replaces them.
        before = target.read_bytes()
        new_cycle = proposal(7, relation="supports")
        new_cycle["expressions"][0]["id"] = "SXN1"
        result = run_writer(case, new_cycle)
        require(result.returncode == 0, f"a stale previous fact-check blocked a new-cycle passage: {result.stderr}")
        require(target.read_bytes() != before, "the new-cycle passage was not published")
        checked = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        stale = [line for line in checked.stderr.splitlines() if "stale expression ref '" in line]
        require(checked.returncode == 1 and stale and all("[fact-checker] fact-check.json" in line for line in stale),
                f"stale fact-check refs are not routed to the fact-checker: {checked.stderr}")
        findings["findings"][0]["claim"] = "Acme paid Doe."
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        subprocess.run([sys.executable, str(SCRIPTS / "write-source-expressions.py"), str(case), "--fingerprint-findings"], check=True, capture_output=True)

        # Locator re-validation (pilot blocker 2): an edited stored artifact
        # shows up as a failed locator.
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["cases"] == 1 and report["revalidation_rate"] == 1.0, report)
        passages_path = case / "research" / "passages.txt"
        original_passages = passages_path.read_bytes()
        passages_path.write_text("rewritten\n" * 8, encoding="utf-8")
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["revalidation_rate"] is not None and report["revalidation_rate"] < 1.0, report)
        (root / "unreadable" / "data").mkdir(parents=True)
        (root / "unreadable" / "data" / "case-contract.json").write_text("{}", encoding="utf-8")
        report = json.loads(subprocess.run([sys.executable, str(SCRIPTS / "revalidate-locators.py"), str(root), "--json"],
                                           capture_output=True, text=True, check=True).stdout)
        require(report["revalidation_rate"] is None and report["skipped_cases"], "an unreadable case was reported as stable")
        import shutil
        shutil.rmtree(root / "unreadable")
        passages_path.write_bytes(original_passages)

        # Born activation and the gate-approval binding.
        born = root / "born"
        (born / "data").mkdir(parents=True)
        (born / "data" / "methodology.json").write_text(json.dumps({"project": "born-case"}), encoding="utf-8")
        made = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(made.returncode == 0, made.stderr)
        contract = json.loads((born / "data" / "case-contract.json").read_text(encoding="utf-8"))
        require([e["kind"] for e in contract["activation_events"]] == ["born"] and contract["project"] == "born-case", contract)
        require(not CASES.vc.validate_case_contract(contract), CASES.vc.validate_case_contract(contract))
        twice = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(twice.returncode == 2, "a case was activated twice")
        # A run that stopped after the contract but before the passages file
        # (an older publish order) is finished, not refused.
        (born / "data" / "source-expressions.json").unlink()
        resumed = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(resumed.returncode == 0 and (born / "data" / "source-expressions.json").is_file(), resumed.stderr)
        # A run that stopped after the empty passages file but before the
        # contract (the current publish order) is finished too, not wedged.
        (born / "data" / "case-contract.json").unlink()
        finished = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(born)], capture_output=True, text=True)
        require(finished.returncode == 0 and (born / "data" / "case-contract.json").is_file(), finished.stderr)
        pilot = root / "pilot"
        (pilot / "data").mkdir(parents=True)
        (pilot / "data" / "source-expressions.json").write_text("{}", encoding="utf-8")
        refused_pilot = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(pilot)], capture_output=True, text=True)
        require(refused_pilot.returncode == 2 and not (pilot / "data" / "case-contract.json").exists(), "a case with passages was re-born")
        legacy = root / "legacy"
        (legacy / "data").mkdir(parents=True)
        (legacy / "data" / "findings.json").write_text(json.dumps(CASES.valid_findings()), encoding="utf-8")
        reborn = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "activate-new", str(legacy)], capture_output=True, text=True)
        require(reborn.returncode == 2 and not (legacy / "data" / "case-contract.json").exists(), "a legacy case was re-born")

        # Once the gate is approved, the artifacts must match the binding.
        # Only the gate approval binds (there is no bind command); here the
        # approval's own order is reproduced: bind, then record the approval.
        from spotlight_orchestration.case_contract import bind
        from spotlight_orchestration.storage import transaction
        with transaction(case) as descriptor:
            bind(case, descriptor)
        (case / "data" / "orchestration.json").write_text(json.dumps({"approvals": {"investigation_gate": {}}}), encoding="utf-8")
        no_cli = subprocess.run([sys.executable, str(SCRIPTS / "case-contract.py"), "bind", str(case)], capture_output=True, text=True)
        require(no_cli.returncode == 2 and "invalid choice" in no_cli.stderr, "a bind command exists")
        fresh = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(fresh.returncode == 0, "a freshly bound approved case does not validate: " + fresh.stdout[-1200:] + fresh.stderr[-1200:])
        require(run_writer(case, {"expressions": [], "lifecycle": [{"expression_id": "SXW7", "event": "withdrawn", "reason": "test"}]}).returncode == 0,
                "writer refused a lifecycle event")
        after = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(after.returncode != 0 and "approval covers" in after.stdout + after.stderr,
                "a change after approval was not caught by the binding: " + after.stdout[-800:] + after.stderr[-800:])
        # The binding covers the positions that existed at approval.
        bound = json.loads((case / "data" / "case-contract.json").read_text(encoding="utf-8"))["activation_events"][-1]["artifact_hashes"]
        require(bound.get("source_positions_sha256") == hashlib.sha256(positions_path.read_bytes()).hexdigest(),
                "the gate binding does not cover the source positions")
        bound_positions = positions_path.read_bytes()
        changed = json.loads(bound_positions)
        changed["positions"][-1]["actor"]["label"] = "Someone else"
        positions_path.write_text(json.dumps(changed), encoding="utf-8")
        edited = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(edited.returncode != 0 and "source_positions_sha256 does not match" in edited.stdout + edited.stderr,
                "positions edited after approval were not caught")
        positions_path.write_bytes(bound_positions)
        positions_path.unlink()
        dropped = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(case)], capture_output=True, text=True)
        require(dropped.returncode != 0 and "source-positions.json is missing" in dropped.stdout + dropped.stderr,
                "positions removed after approval were not caught")

        # A migrated case's activation hashes predate positions: positions
        # written before its gate approval are not an integrity failure.
        migrated = root / "migrated"
        CASES.write_activated_case(migrated)
        passage = json.loads((migrated / "data" / "source-expressions.json").read_text(encoding="utf-8"))["expressions"][0]
        (migrated / "data" / "source-positions.json").write_text(json.dumps({
            "schema_version": "1.0", "project": json.loads((migrated / "data" / "findings.json").read_text(encoding="utf-8"))["project"],
            "created_at": "2026-10-22T12:00:00Z",
            "positions": [{"id": "SP1", "finding_id": passage["finding_links"][0]["finding_id"], "actor": {"label": "A source"},
                           "stance": "asserts", "source_expression_ids": [passage["id"]],
                           "attribution_verbatim": passage["text"], "created_by": "investigator", "cycle": 1}]}), encoding="utf-8")
        before_gate = subprocess.run([sys.executable, str(SCRIPTS / "validate-case.py"), str(migrated)], capture_output=True, text=True)
        require(before_gate.returncode == 0, "positions on a migrated case before its gate failed: " + before_gate.stdout[-800:] + before_gate.stderr[-800:])

    # 12B r3 arm A on f43df365: true passages refused for a missing item hash
    # (unexplained), unfingerprinted findings, a line break sent as the two
    # characters \\n, and bundle items under another key (unexplained).
    with tempfile.TemporaryDirectory() as tmp:
        case = make_case(Path(tmp))
        findings_path, bundle_path = case / "data" / "findings.json", case / "data" / "evidence-bundle.json"
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        for finding in findings["findings"]:
            finding.pop("finding_fingerprint", None)
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        item = next(item for item in bundle["items"] if item["id"] == "E2")
        digest = item.pop("sha256")
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        body = proposal(2, relation="supports")
        del body["expressions"][0]["anchor_ref"]
        body["expressions"][0]["text"] = PASSAGES[2] + "\\n" + PASSAGES[3]
        written = run_writer(case, body, actor="fact-checker")
        require(written.returncode == 0, written.stderr)
        pinned = next(i for i in json.loads(bundle_path.read_text(encoding="utf-8"))["items"] if i["id"] == "E2")
        require(pinned.get("sha256") == digest and pinned.get("sha256_source") == "computed_at_passage_write",
                f"a hashless item whose own file holds the passage was not pinned and marked: {pinned}")
        item.update(sha256=digest, sha256_source="computed_at_passage_write")
        stored = next(e for e in expressions(case)["expressions"] if e["id"] == "SXW2")
        require(stored["text"] == PASSAGES[2] + "\n" + PASSAGES[3] and stored["anchor_ref"]["line_start"] == 3 and stored["anchor_ref"]["line_end"] == 4,
                f"an escaped line break was not located as one: {stored}")
        require(all(len(f.get("finding_fingerprint", "")) == 64 for f in json.loads(findings_path.read_text(encoding="utf-8"))["findings"]),
                "the write did not fingerprint the findings")
        # The same whole line repeated in one file is anchored at its first occurrence.
        repeated = case / "research" / "repeated.txt"
        repeated.write_text("EIN: 04-3585301\nMission\nEIN: 04-3585301\n", encoding="utf-8")
        bundle["items"].append({**item, "id": "E3", "raw_path": "research/repeated.txt", "sha256": hashlib.sha256(repeated.read_bytes()).hexdigest()})
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        twice = {"expressions": [{"id": "SXW9", "text": "EIN: 04-3585301", "original_evidence_bundle_id": "E3", "language": "en",
                                  "finding_links": [{"finding_id": "F1", "relation": "supports"}]}]}
        once = run_writer(case, twice, actor="fact-checker")
        anchored = next((e for e in expressions(case)["expressions"] if e["id"] == "SXW9"), {}).get("anchor_ref", {})
        require(once.returncode == 0 and anchored.get("line_start") == 1 and anchored.get("line_end") == 1, once.stderr)
        bundle["evidence_bundle"] = bundle.pop("items")
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        itemless = run_writer(case, proposal(5))
        require(itemless.returncode != 0 and 'data/evidence-bundle.json has no "items" list' in itemless.stderr, itemless.stderr)

    print("source-expression writer and case contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
