#!/usr/bin/env python3
"""The one writer for a case's material: its register (data/journalist-input.json)
and the passages and claims taken from it (data/intake-claims.json).

Material is what a journalist brings to a case: an interview, a source's
email, field notes, a document a source handed over, a dataset, a photo or a
video. Each item is one file under {CASE_DIR}/intake/, in a folder per kind,
and one record here. The record follows `journalist_input` in Knowledge Batch
1.1; the original file stays in the case and is never sent anywhere.

Usage:
  python3 scripts/intake.py {CASE_DIR} add --file PATH [--kind KIND] [--actor agent --model ID]
  python3 scripts/intake.py {CASE_DIR} scan
  python3 scripts/intake.py {CASE_DIR} set JI1 [--actor agent --model ID] < fields.json
  python3 scripts/intake.py {CASE_DIR} confirm JI1 --by NAME
  python3 scripts/intake.py {CASE_DIR} list
  python3 scripts/intake.py {CASE_DIR} derive JI1 --file transcript.txt --type transcript
  python3 scripts/intake.py {CASE_DIR} propose JI1 [--actor agent --model ID] < proposal.json
  python3 scripts/intake.py {CASE_DIR} decide JC1 keep|drop|edit [< {"proposition": ..., "statement": ...}]
  python3 scripts/intake.py {CASE_DIR} rights JP1 < rights.json
  python3 scripts/intake.py {CASE_DIR} approve JI1 --by NAME
  python3 scripts/intake.py {CASE_DIR} claims
  python3 scripts/intake.py {CASE_DIR} view
  python3 scripts/intake.py {CASE_DIR} keep-all JI1
  python3 scripts/intake.py {CASE_DIR} decider [--by NAME]

- add copies a file into the case and drafts its record; scan drafts a record
  for every file already dropped under intake/. A file whose kind the name
  does not give goes to intake/unsorted/ until `set` names its kind.
- set fills a draft's fields: kind, kind_detail, captured_by, source,
  rights_holder, original_sensitivity, and passage_defaults (the rights each
  passage taken from this material starts with). An agent may propose them; this script
  alone sets the id, the file's place, its fingerprint and the capture time.
- confirm is the journalist's: it accepts a complete record whose file still
  has the registered bytes, and closes it. A confirmed record does not change;
  different bytes are a new record.

A draft starts as restrictive as the model allows (`case_only`): what is not
stated is not publishable.

Passages and claims (no investigation is involved, and no verdict is given):

- derive adds a text version of material that holds no text to quote from (a
  transcript of a recording, the text of a scan). The original is unchanged,
  so this is allowed on a confirmed record too.
- propose takes passages, each with the claims drawn from it:
    {"passages": [{"text": "exact words of the material", "language": "en",
                   "direct_quote": true, "speaker": "Dock worker 1",
                   "claims": [{"proposition": "...", "statement": {"subject": {"text": "..."}, "predicate": "..."}}]}]}
  A passage is accepted only when its text is in the material word for word,
  at exactly one place; this script records where. It starts with the
  material's passage_defaults, or with no rights at all.
- decide is the journalist's keep, drop or edit of one claim; rights sets one
  passage's attribution, consent and sensitivity.
- approve is the journalist's approval of the kept claims of one confirmed
  record, once none is left undecided. It rebuilds the case's knowledge batch
  for the Ingest page. Any later change to that material's
  passages or claims removes the approval.
- The review page's three: view returns what the page shows (the register, the
  passages each with a few lines of context from the file they were taken from,
  the claims and the approvals); keep-all keeps every claim of one material
  that is still undecided; decider names who a page's confirmation or approval
  is recorded for: the fixed Member in the solo edition, else the name given.

Exit codes: 0 = done (the register, or the record, as JSON on stdout);
2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import schema_check  # noqa: E402
from evidence_anchors import locate_quote, normalized, source_lines  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.review import SOLO_DECIDER, ingest_mode, rebuild_ingest_batch  # noqa: E402
from spotlight_orchestration.storage import transaction  # noqa: E402

TARGET = "data/journalist-input.json"
INTAKE = "intake"
UNSORTED = "unsorted"
CLAIMS = "data/intake-claims.json"
DERIVED = "derived"
SCHEMAS = SCRIPT_DIR.parent / "schemas"
SCHEMA = SCHEMAS / "journalist-input.schema.json"
CLAIMS_SCHEMA = SCHEMAS / "intake-claims.schema.json"
LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
# Batch 1.1's kinds, each with its folder under intake/.
FOLDERS = {
    "interview": "interviews", "source_email": "emails", "field_note": "notes", "document": "documents",
    "dataset": "datasets", "photo_or_video": "media", "other": "other",
}
# The kinds a file name gives away. An audio file may be an interview or a
# field recording, and a PDF anything, so those wait for `set`.
KIND_BY_SUFFIX = {
    **dict.fromkeys((".eml", ".msg", ".mbox"), "source_email"),
    **dict.fromkeys((".csv", ".tsv", ".xlsx", ".xls", ".ods", ".parquet"), "dataset"),
    **dict.fromkeys((".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".gif", ".webp",
                     ".mp4", ".mov", ".mkv", ".avi", ".webm"), "photo_or_video"),
}
SENSITIVITIES = ("case_only", "internal", "publishable")
ACTORS = {"journalist": "journalist", "agent": "agent_candidate"}
# What the review page shows of a passage: its lines, and a few around them.
CONTEXT_LINES = 3
MAX_PASSAGE_LINES = 40
MAX_LINE_CHARS = 2000
SETTABLE = ("kind", "kind_detail", "captured_by", "source", "rights_holder", "original_sensitivity", "passage_defaults")


class Refused(Exception):
    """The request cannot be applied; the register is left as it was."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(case: Path) -> dict[str, Any]:
    path = case / TARGET
    if not path.exists():
        return {"schema_version": "1.0", "inputs": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{TARGET} cannot be read: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != "1.0" or not isinstance(doc.get("inputs"), list):
        raise Refused(f"{TARGET} has an unsupported shape")
    return doc


def _load_claims(case: Path) -> dict[str, Any]:
    path = case / CLAIMS
    if not path.exists():
        return {"schema_version": "1.0", "passages": [], "claims": [], "approvals": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{CLAIMS} cannot be read: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != "1.0":
        raise Refused(f"{CLAIMS} has an unsupported shape")
    return doc


def _publish(case: Path, doc: dict[str, Any], descriptor: int, target: str = TARGET) -> None:
    errors = schema_check.load(CLAIMS_SCHEMA if target == CLAIMS else SCHEMA).errors(doc)
    if errors:
        raise Refused(f"{target} would not be valid: " + "; ".join(errors[:6]))
    atomic_write_files(case, {target: (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")},
                       data_descriptor=descriptor)


def _shape_errors(value: Any, schema: Path, definition: str) -> list[str]:
    """`value` against one definition of one of Spotlight's schemas."""
    return schema_check.load(schema).errors(value, {"$ref": f"#/definitions/{definition}"})


def _next(prefix: str, records: list[dict[str, Any]]) -> str:
    return f"{prefix}{max((int(record['id'][len(prefix):]) for record in records), default=0) + 1}"


def _provenance(actor: str, model: str | None) -> dict[str, str]:
    if actor not in ACTORS:
        raise Refused(f"--actor must be one of {', '.join(ACTORS)}")
    if actor == "agent" and not model:
        raise Refused("--actor agent needs --model")
    return {"method": ACTORS[actor], **({"model": model} if actor == "agent" else {})}


def _material_file(case: Path, path: Path) -> Path:
    """A plain file under the case's intake/ folder, never reached through a link."""
    root = (case / INTAKE).resolve()
    if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
        raise Refused(f"{path} is not a plain file under {INTAKE}/")
    return path


def _relative(case: Path, path: Path) -> str:
    return path.resolve().relative_to(case.resolve()).as_posix()


def _folder(case: Path, name: str) -> Path:
    """A folder under intake/ made of real directories of the case, so a copy
    or a move can never land outside it through a link."""
    root = case / INTAKE
    folder = root / name
    for path in (root, folder):
        if path.is_symlink():
            raise Refused(f"{_shown(case, path)} is a link, not a folder of the case; material is not written through it")
        path.mkdir(exist_ok=True)
    if folder.resolve() != case.resolve() / INTAKE / name:
        raise Refused(f"{INTAKE}/{name} is not a folder of the case")
    return folder


def _shown(case: Path, path: Path) -> str:
    return path.relative_to(case).as_posix()


def _copy_new(source: Path, target: Path) -> None:
    """Copy to a path that must not exist yet, never through a link left there."""
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    except FileExistsError as exc:
        raise Refused(f"{target.name} already exists under {INTAKE}/ and is not replaced") from exc


def passage_in_lines(path: Path, text: str, line_start: int, line_end: int) -> bool:
    """Whether those lines of the file hold the text literally. Only runs of
    white space may differ: a look-alike character is a different word."""
    squeeze = lambda value: " ".join(value.split())  # noqa: E731
    return squeeze(text) in squeeze("\n".join(source_lines(path)[line_start - 1:line_end]))


def _free(folder: Path, name: str) -> Path:
    """A path in `folder` no other file holds: name, name-2, name-3."""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, count = folder / name, 1
    while candidate.exists():
        count += 1
        candidate = folder / f"{stem}-{count}{suffix}"
    return candidate


def _draft(doc: dict[str, Any], case: Path, path: Path, kind: str | None, provenance: dict[str, str]) -> dict[str, Any]:
    taken = {record["id"] for record in doc["inputs"]}
    number = len(doc["inputs"]) + 1
    while f"JI{number}" in taken:
        number += 1
    record = {
        "id": f"JI{number}", "state": "draft", **({"kind": kind} if kind else {}),
        "captured_at": _now(), "original_sensitivity": SENSITIVITIES[0],
        "content_ref": _relative(case, path), "content_sha256": _sha256(path), "provenance": provenance,
    }
    doc["inputs"].append(record)
    return record


def _by_sha(doc: dict[str, Any], sha: str) -> dict[str, Any] | None:
    return next((record for record in doc["inputs"] if record["content_sha256"] == sha), None)


def _record(doc: dict[str, Any], record_id: str) -> dict[str, Any]:
    found = next((record for record in doc["inputs"] if record["id"] == record_id), None)
    if found is None:
        raise Refused(f"no material record {record_id}")
    return found


def add(case: Path, source: Path, kind: str | None = None, actor: str = "journalist", model: str | None = None) -> dict[str, Any]:
    """Copy one file into the case and draft its record. The same bytes twice are one record."""
    if kind is not None and kind not in FOLDERS:
        raise Refused(f"--kind must be one of {', '.join(FOLDERS)}")
    if source.is_symlink() or not source.is_file():
        raise Refused(f"{source} is not a plain file")
    if source.name.startswith("."):
        raise Refused("a hidden file is not registered as material")
    provenance = _provenance(actor, model)
    kind = kind or KIND_BY_SUFFIX.get(source.suffix.lower())
    with transaction(case) as descriptor:
        doc = _load(case)
        existing = _by_sha(doc, _sha256(source))
        if existing is not None:
            return existing
        target = _free(_folder(case, FOLDERS[kind] if kind else UNSORTED), source.name)
        _copy_new(source, target)
        record = _draft(doc, case, target, kind, provenance)
        _publish(case, doc, descriptor)
    return record


def scan(case: Path) -> list[dict[str, Any]]:
    """Draft a record for every file dropped under intake/ that has none."""
    root = case / INTAKE
    created: list[dict[str, Any]] = []
    with transaction(case) as descriptor:
        doc = _load(case)
        registered = {record["content_ref"] for record in doc["inputs"]}
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            if path.is_symlink() or not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if _relative(case, path) in registered or _by_sha(doc, _sha256(path)) is not None:
                continue
            folder = path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else None
            kind = next((name for name, value in FOLDERS.items() if value == folder), None) or KIND_BY_SUFFIX.get(path.suffix.lower())
            created.append(_draft(doc, case, path, kind, _provenance("journalist", None)))
        if created:
            _publish(case, doc, descriptor)
    return created


def set_fields(case: Path, record_id: str, fields: Any, actor: str = "journalist", model: str | None = None) -> dict[str, Any]:
    """Fill a draft. A named kind moves the file to that kind's folder."""
    if not isinstance(fields, dict) or not fields:
        raise Refused("the fields must be a JSON object")
    unknown = sorted(set(fields) - set(SETTABLE))
    if unknown:
        raise Refused(f"{', '.join(unknown)} cannot be set here; settable: {', '.join(SETTABLE)}")
    provenance = _provenance(actor, model)
    with transaction(case) as descriptor:
        doc = _load(case)
        record = _record(doc, record_id)
        if record["state"] != "draft":
            raise Refused(f"{record_id} is confirmed and no longer changes; different material is a new record")
        merged = {**record, **fields, "provenance": provenance}
        if merged.get("kind") != "other":
            merged.pop("kind_detail", None)
        _check_fields(merged)
        # The fields are checked before the file moves, and a failed write
        # moves it back: the register never names a place the file has left.
        errors = schema_check.load(SCHEMA).errors({"schema_version": "1.0", "inputs": [merged]})
        if errors:
            raise Refused("the fields are not valid: " + "; ".join(errors[:6]))
        kind = merged.get("kind")
        current = moved = None
        if kind and Path(record["content_ref"]).parts[1] != FOLDERS[kind]:
            current = _material_file(case, case / record["content_ref"])
            moved = _free(_folder(case, FOLDERS[kind]), current.name)
            current.rename(moved)
            merged["content_ref"] = _relative(case, moved)
        before = dict(record)
        record.clear()
        record.update(merged)
        try:
            _publish(case, doc, descriptor)
        except BaseException:
            if moved is not None:
                moved.rename(current)
            record.clear()
            record.update(before)
            raise
    return record


def _check_fields(record: dict[str, Any]) -> None:
    """What the register's schema cannot say about a record's fields."""
    source = record.get("source")
    if source is not None and (not isinstance(source, dict) or len(source) != 1):
        raise Refused("source is one of entity_id or pseudonym, never both: a protected source has only a pseudonym")
    if record.get("kind") == "other" and not record.get("kind_detail"):
        raise Refused('kind "other" needs kind_detail')


def confirm(case: Path, record_id: str, by: str) -> dict[str, Any]:
    """The journalist's acceptance of a complete record whose file is unchanged."""
    if not by.strip():
        raise Refused("--by names who confirms")
    with transaction(case) as descriptor:
        doc = _load(case)
        record = _record(doc, record_id)
        if record["state"] == "confirmed":
            return record
        missing = [name for name in ("kind", "captured_by", "source", "rights_holder") if not record.get(name)]
        if missing:
            raise Refused(f"{record_id} is not complete: {', '.join(missing)}")
        _check_fields(record)
        path = _material_file(case, case / record["content_ref"])
        if _sha256(path) != record["content_sha256"]:
            raise Refused(f"{record['content_ref']} no longer has the registered bytes; changed material is a new record")
        record.update({"state": "confirmed", "confirmed_by": by.strip(), "confirmed_at": _now()})
        _publish(case, doc, descriptor)
    return record


def derive(case: Path, record_id: str, source: Path, text_type: str) -> dict[str, Any]:
    """Add a text version of the material (a transcript, a scan's text) that passages can quote."""
    if source.is_symlink() or not source.is_file() or not text_type.strip():
        raise Refused("derive needs a plain file and what it is (--type transcript)")
    with transaction(case) as descriptor:
        doc = _load(case)
        record = _record(doc, record_id)
        target = _free(_folder(case, DERIVED), f"{record_id}-{source.name}")
        _copy_new(source, target)
        derivatives = record.setdefault("derivatives", [])
        derivatives.append({"id": _next("D", derivatives), "type": text_type.strip(),
                            "content_ref": _relative(case, target), "content_sha256": _sha256(target)})
        _publish(case, doc, descriptor)
    return record


def _locate(case: Path, record: dict[str, Any], text: str, label: str) -> dict[str, Any]:
    """Where the material holds `text` word for word: one place, in the
    original or in one of its text versions."""
    files = [(record["content_ref"], None)] + [(d["content_ref"], d["id"]) for d in record.get("derivatives", [])]
    found = []
    for path, derivative_id in files:
        span = locate_quote(_material_file(case, case / path), text)
        # locate_quote reads look-alike characters as the same; a passage of
        # material is the file's own characters.
        if span and passage_in_lines(case / path, text, *span):
            found.append({"path": path, "line_start": span[0], "line_end": span[1],
                          **({"derivative_id": derivative_id} if derivative_id else {})})
    if len(found) != 1:
        searched = ", ".join(path for path, _derivative in files)
        raise Refused(f"{label}: the text is not in {searched} word for word at exactly one place. Quote the material's own "
                      "words, a span long enough to occur once; for a recording or a scan, add its text first with `derive`.")
    return found[0]


def propose(case: Path, record_id: str, proposal: Any, actor: str = "journalist", model: str | None = None) -> dict[str, Any]:
    """Record passages of one item of material and the claims drawn from each. All or nothing."""
    provenance = _provenance(actor, model)
    items = proposal.get("passages") if isinstance(proposal, dict) else None
    if not isinstance(items, list) or not items:
        raise Refused('the proposal needs "passages", each with its "claims"')
    with transaction(case) as descriptor:
        record = _record(_load(case), record_id)
        doc = _load_claims(case)
        for index, item in enumerate(items):
            label = f"passages[{index}]"
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise Refused(f"{label}: text is required, the material's exact words")
            if not isinstance(item.get("language"), str) or not LANGUAGE.match(item["language"]):
                raise Refused(f'{label}: language is required, a BCP 47 tag such as "en" or "fr-CH"')
            claims = item.get("claims")
            if not isinstance(claims, list) or not claims:
                raise Refused(f"{label}: a passage comes with at least one claim drawn from it")
            anchor = _locate(case, record, item["text"], label)
            passage = next((p for p in doc["passages"] if p["input_id"] == record_id
                            and normalized(p["text"]) == normalized(item["text"])), None)
            if passage is None:
                passage = {
                    "id": _next("JP", doc["passages"]), "input_id": record_id, "text": item["text"].strip(),
                    "anchor_ref": anchor, "anchor_sha256": _sha256(case / anchor["path"]), "language": item["language"],
                    "direct_quote": item.get("direct_quote") is True,
                    **({"speaker": item["speaker"]} if isinstance(item.get("speaker"), str) and item["speaker"].strip() else {}),
                    "rights": dict(record.get("passage_defaults") or {}), "provenance": provenance,
                }
                doc["passages"].append(passage)
            for position, claim in enumerate(claims):
                where = f"{label}.claims[{position}]"
                if not isinstance(claim, dict) or not isinstance(claim.get("proposition"), str) or not claim["proposition"].strip():
                    raise Refused(f"{where}: proposition is required, one thing the passage says")
                errors = _shape_errors(claim.get("statement"), SCHEMAS / "knowledge-proposal.schema.json", "claim/properties/statement")
                if errors:
                    raise Refused(f"{where}: statement needs a subject and a predicate ({'; '.join(errors[:3])})")
                if any(c["passage_id"] == passage["id"] and c["proposition"] == claim["proposition"].strip() for c in doc["claims"]):
                    continue
                doc["claims"].append({"id": _next("JC", doc["claims"]), "passage_id": passage["id"],
                                      "proposition": claim["proposition"].strip(), "statement": claim["statement"],
                                      "state": "proposed", "provenance": provenance})
        doc["approvals"].pop(record_id, None)
        _publish(case, doc, descriptor, CLAIMS)
    return doc


def _passage(doc: dict[str, Any], passage_id: str) -> dict[str, Any]:
    found = next((passage for passage in doc["passages"] if passage["id"] == passage_id), None)
    if found is None:
        raise Refused(f"no material passage {passage_id}")
    return found


def decide(case: Path, claim_id: str, action: str, fields: Any = None) -> dict[str, Any]:
    """The journalist keeps, drops or rewrites one claim."""
    if action not in ("keep", "drop", "edit"):
        raise Refused("decide takes keep, drop or edit")
    with transaction(case) as descriptor:
        doc = _load_claims(case)
        claim = next((c for c in doc["claims"] if c["id"] == claim_id), None)
        if claim is None:
            raise Refused(f"no material claim {claim_id}")
        if action == "edit":
            if not isinstance(fields, dict) or not set(fields) or set(fields) - {"proposition", "statement"}:
                raise Refused("an edit gives proposition, statement or both")
            if "proposition" in fields and (not isinstance(fields["proposition"], str) or not fields["proposition"].strip()):
                raise Refused("proposition cannot be empty")
            if "statement" in fields:
                errors = _shape_errors(fields["statement"], SCHEMAS / "knowledge-proposal.schema.json", "claim/properties/statement")
                if errors:
                    raise Refused(f"statement needs a subject and a predicate ({'; '.join(errors[:3])})")
            claim.update({**fields, "provenance": {"method": "journalist"}})
        claim["state"] = "dropped" if action == "drop" else "kept"
        doc["approvals"].pop(_passage(doc, claim["passage_id"])["input_id"], None)
        _publish(case, doc, descriptor, CLAIMS)
    return claim


def set_rights(case: Path, passage_id: str, rights: Any) -> dict[str, Any]:
    """The journalist sets one passage's attribution, consent and sensitivity."""
    errors = _shape_errors(rights, SCHEMA, "passage_rights")
    if errors:
        raise Refused("the rights are not valid: " + "; ".join(errors[:4]))
    with transaction(case) as descriptor:
        doc = _load_claims(case)
        passage = _passage(doc, passage_id)
        passage["rights"] = rights
        doc["approvals"].pop(passage["input_id"], None)
        _publish(case, doc, descriptor, CLAIMS)
    return passage


def _claims_of(doc: dict[str, Any], record_id: str) -> list[dict[str, Any]]:
    passages = {passage["id"] for passage in doc["passages"] if passage["input_id"] == record_id}
    return [claim for claim in doc["claims"] if claim["passage_id"] in passages]


def keep_all(case: Path, record_id: str) -> list[dict[str, Any]]:
    """The journalist keeps every claim of one material that is still undecided."""
    with transaction(case) as descriptor:
        _record(_load(case), record_id)
        doc = _load_claims(case)
        proposed = [claim for claim in _claims_of(doc, record_id) if claim["state"] == "proposed"]
        if not proposed:
            raise Refused(f"{record_id} has no claim left to decide")
        for claim in proposed:
            claim["state"] = "kept"
        doc["approvals"].pop(record_id, None)
        _publish(case, doc, descriptor, CLAIMS)
    return proposed


def approve(case: Path, record_id: str, by: str) -> dict[str, Any]:
    """The journalist's approval of one confirmed record's kept claims."""
    if not by.strip():
        raise Refused("--by names who approves")
    with transaction(case) as descriptor:
        if _record(_load(case), record_id)["state"] != "confirmed":
            raise Refused(f"{record_id} is not confirmed yet: its record is approved first, then its claims")
        doc = _load_claims(case)
        claims = _claims_of(doc, record_id)
        undecided = [claim["id"] for claim in claims if claim["state"] == "proposed"]
        if undecided:
            raise Refused(f"{record_id} has claims not yet kept or dropped: {', '.join(undecided)}")
        kept = [claim["id"] for claim in claims if claim["state"] == "kept"]
        if not kept:
            raise Refused(f"{record_id} has no kept claim to approve")
        doc["approvals"][record_id] = {"by": by.strip(), "at": _now(), "claims": kept}
        _publish(case, doc, descriptor, CLAIMS)
    # The Ingest page shows a built batch: a case that holds only material has
    # no analyst to build it, so the approval does. The builder takes the lock itself.
    rebuild_ingest_batch(case.resolve())
    return doc["approvals"][record_id]


def _context(case: Path, passage: dict[str, Any]) -> dict[str, Any] | None:
    """The passage's own lines with a few around them, numbered as the anchor
    numbers them. None when its file is no longer a plain file under intake/
    or no longer has the bytes the passage was taken from."""
    anchor = passage["anchor_ref"]
    try:
        path = _material_file(case, case / anchor["path"])
    except Refused:
        return None
    if _sha256(path) != passage["anchor_sha256"]:
        return None
    lines = source_lines(path)
    first = max(1, anchor["line_start"] - CONTEXT_LINES)
    shown_end = min(anchor["line_end"], anchor["line_start"] + MAX_PASSAGE_LINES - 1)
    truncated = shown_end < anchor["line_end"]
    last = min(len(lines), shown_end if truncated else shown_end + CONTEXT_LINES)
    return {
        "lines": [{"n": n, "text": lines[n - 1][:MAX_LINE_CHARS], "in_passage": anchor["line_start"] <= n <= anchor["line_end"]}
                  for n in range(first, last + 1)],
        "truncated": truncated,
    }


def view(case: Path) -> dict[str, Any]:
    """Everything the review page shows of a case's material, in one read."""
    claims = _load_claims(case)
    return {
        "name_required": ingest_mode() == "newsroom",
        "inputs": _load(case)["inputs"],
        "passages": [{**passage, "context": _context(case, passage)} for passage in claims["passages"]],
        "claims": claims["claims"],
        "approvals": claims["approvals"],
    }


def decider(by: str | None) -> dict[str, str]:
    """Who a page's decision is recorded for: the solo edition's fixed Member,
    or, in the newsroom edition, the name the person entered."""
    if ingest_mode() != "newsroom":
        return {"by": SOLO_DECIDER}
    if not (by or "").strip():
        raise Refused("enter your name before deciding")
    return {"by": by.strip()}


def _stdin_json() -> Any:
    try:
        return json.load(sys.stdin)
    except ValueError as exc:
        raise Refused(f"the input is not valid JSON: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("case", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add")
    add_parser.add_argument("--file", type=Path, required=True)
    add_parser.add_argument("--kind")
    commands.add_parser("scan")
    set_parser = commands.add_parser("set")
    set_parser.add_argument("id")
    propose_parser = commands.add_parser("propose")
    propose_parser.add_argument("id")
    for sub in (add_parser, set_parser, propose_parser):
        sub.add_argument("--actor", default="journalist")
        sub.add_argument("--model")
    for name in ("confirm", "approve"):
        sub = commands.add_parser(name)
        sub.add_argument("id")
        sub.add_argument("--by", required=True)
    derive_parser = commands.add_parser("derive")
    derive_parser.add_argument("id")
    derive_parser.add_argument("--file", type=Path, required=True)
    derive_parser.add_argument("--type", required=True)
    decide_parser = commands.add_parser("decide")
    decide_parser.add_argument("id")
    decide_parser.add_argument("action")
    commands.add_parser("rights").add_argument("id")
    commands.add_parser("list")
    commands.add_parser("claims")
    commands.add_parser("view")
    commands.add_parser("keep-all").add_argument("id")
    commands.add_parser("decider").add_argument("--by")
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            result: Any = add(args.case, args.file, args.kind, args.actor, args.model)
        elif args.command == "scan":
            result = scan(args.case)
        elif args.command == "set":
            result = set_fields(args.case, args.id, _stdin_json(), args.actor, args.model)
        elif args.command == "confirm":
            result = confirm(args.case, args.id, args.by)
        elif args.command == "derive":
            result = derive(args.case, args.id, args.file, args.type)
        elif args.command == "propose":
            result = propose(args.case, args.id, _stdin_json(), args.actor, args.model)
        elif args.command == "decide":
            result = decide(args.case, args.id, args.action, _stdin_json() if args.action == "edit" else None)
        elif args.command == "rights":
            result = set_rights(args.case, args.id, _stdin_json())
        elif args.command == "approve":
            result = approve(args.case, args.id, args.by)
        elif args.command == "claims":
            result = _load_claims(args.case)
        elif args.command == "view":
            result = view(args.case)
        elif args.command == "keep-all":
            result = keep_all(args.case, args.id)
        elif args.command == "decider":
            result = decider(args.by)
        else:
            result = _load(args.case)
    except (Refused, OrchestrationError, schema_check.SchemaError) as exc:
        print(f"intake: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
