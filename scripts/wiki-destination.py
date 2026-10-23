#!/usr/bin/env python3
"""Write an approved Batch 1.1 into the member's wiki (solo Ingest).

  python3 scripts/wiki-destination.py {CASE_DIR} [--workspace WIKI] [--overwrite-hand-edits]

Runs after the member requested ingestion on the ingest page, for an
activated case in solo mode. In order:

1. The batch must be the one the request bound: its review digest, which
   covers the batch bytes, must equal the digest in the decision receipt.
2. The batch passes the core schema and the cross-record rules against what
   is already admitted, then is admitted: data/knowledge-ids.json moves its
   records from pending to admitted.
3. In the wiki, under spotlight/ only:
   - batches/<batch>.json keeps the approved batch whole (append-only);
   - decisions.jsonl, the local decision journal, gains one line per batch;
   - claims/, events/ and story-arcs/ hold one readable note per lineage,
     regenerated from every kept batch, its status derived from the journal;
   - generated-notes.json lists every generated note's hash. A note whose
     bytes no longer match was edited by hand: the write stops rather than
     overwrite it (--overwrite-hand-edits to replace it).
   Entity mentions link to the shared register (wiki/entities/) only when
   `bsig wiki resolve` finds an exact match; this script never writes the
   register.
4. data/ingestion.json records the completed receipt, which the phase seals.

The wiki lock (the wiki service's .wiki-lock) and the case lock are held for
the whole write. Re-running after an interruption is safe: a kept batch with
the same bytes, a journal line already present and a note that is already
what would be written are not treated as conflicts, and a completed
admission is not repeated.

Exit codes: 0 = written; 2 = refused (the reason is on stderr).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import schema_check  # noqa: E402
from spotlight_orchestration import review  # noqa: E402
from spotlight_orchestration.case_writer import atomic_write_files  # noqa: E402
from spotlight_orchestration.contract import OrchestrationError  # noqa: E402
from spotlight_orchestration.resolver import load_ingestion_marker, status_for  # noqa: E402
from spotlight_orchestration.storage import load_state, transaction  # noqa: E402

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

_SPEC = importlib.util.spec_from_file_location("validate_knowledge_batch", SCRIPT_DIR / "validate-knowledge-batch.py")
RULES = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(RULES)

NAMESPACE = "spotlight"
FOLDERS = {"claim": "claims", "event": "events", "story_arc": "story-arcs"}
SAFE = re.compile(r"[^a-z0-9_-]+")


class DestinationError(Exception):
    """The batch cannot be written; the wiki and the case are left as they were
    for every step not yet done (re-running resumes)."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DestinationError(f"cannot read {path.name}: {exc}") from exc


def _slug(record_id: str) -> str:
    return SAFE.sub("-", record_id.split(":", 1)[-1].lower()).strip("-") or "record"


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _confined(workspace: Path, relative: str) -> Path:
    """A path under spotlight/ with no symbolic link on the way: a link
    planted in the folder could otherwise redirect a write elsewhere."""
    parts = Path(relative).parts
    if not parts or parts[0] != NAMESPACE or ".." in parts:
        raise DestinationError(f"{relative} is outside {NAMESPACE}/")
    current = workspace
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise DestinationError(f"{current} is a symbolic link; Spotlight writes only real folders under {NAMESPACE}/")
    return current


def _md(value: Any) -> str:
    """Case and model text inside a note: never a wiki link or markup of its own."""
    return str(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


@contextlib.contextmanager
def wiki_lock(workspace: Path, wait: float = 60.0):
    """The wiki's shared write lock (the same .wiki-lock file the wiki service
    takes), so two cases or a service write never interleave with ours."""
    handle = open(workspace / ".wiki-lock", "a+b")
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise DestinationError("another process is writing the wiki; try again") from None
                time.sleep(0.2)
        yield
    finally:
        with contextlib.suppress(OSError):
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def workspace_from_config() -> Path:
    try:
        config = json.loads((SCRIPT_DIR.parent / ".spotlight-config.json").read_text(encoding="utf-8"))
        return Path(config["knowledge_destination"]["workspace_path"]).expanduser().resolve()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DestinationError(f"no wiki workspace in .spotlight-config.json (knowledge_destination.workspace_path): {exc}") from exc


class Resolver:
    """Exact register matches through `bsig wiki resolve`; without Engine, no
    links. The register links on a strong identifier, which Batch 1.1 core
    mentions do not carry, so today a name alone links only when the
    register's resolve says "linked"."""

    def __init__(self, workspace: Path):
        self.bsig = shutil.which("bsig")
        self.workspace = workspace
        self.cache: dict[str, str | None] = {}

    def entity(self, name: str) -> str | None:
        if not self.bsig or not name.strip():
            return None
        if name not in self.cache:
            self.cache[name] = None
            try:
                done = subprocess.run([self.bsig, "wiki", "resolve", "--workspace", str(self.workspace), json.dumps({"name": name})],
                                      capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                return None
            for line in done.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                result = (event.get("data") or {}).get("result") if isinstance(event, dict) else None
                if isinstance(result, dict) and result.get("status") == "linked" and isinstance(result.get("entity_id"), str):
                    self.cache[name] = result["entity_id"]
        return self.cache[name]

    def link(self, name: str) -> str:
        entity = self.entity(name)
        if entity and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", entity):
            label = name.replace("|", "/").replace("[", "(").replace("]", ")").replace("\n", " ")
            return f"[[wiki/entities/{entity}|{label}]]"
        return _md(name)


def _frontmatter(fields: dict[str, Any]) -> str:
    # JSON scalars and flow sequences are valid YAML, so the frontmatter is
    # exact without a YAML library.
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()] + ["---", ""]
    return "\n".join(lines)


def check_approval(case: Path, descriptor: int) -> tuple[dict[str, Any], bytes, str]:
    if not review.activated_case(case):
        raise DestinationError("only an activated case writes a Batch 1.1 to the wiki")
    if review.ingest_mode() != "solo":
        raise DestinationError("newsroom-mode cases write nothing to this computer's wiki")
    state = load_state(case, descriptor)
    status = status_for(case, state, descriptor)
    detail = status.get("ingest") or {}
    # The resolver accepts a requested decision only when the nonce ledger
    # backs it; a hand-written state never reaches this step.
    if status.get("next_phase") != "ingest" or detail.get("resume_at") not in ("ingest", "seal"):
        raise DestinationError("ingestion has not been requested")
    decision = state["decisions"].get("ingest")
    bound = (decision.get("review") or {}).get("review_digest") if isinstance(decision, dict) else None
    batch_path = case / review.INGEST_BATCH
    if not batch_path.is_file():
        raise DestinationError("data/knowledge-batch.json is missing")
    raw = batch_path.read_bytes()
    if not bound or review.review_digest(case, "ingest", descriptor) != bound:
        raise DestinationError("the batch or its review changed after the request; review it and request again")
    return decision, raw, bound


def notes_for(batches: list[dict[str, Any]], journal: dict[str, dict[str, Any]], resolver: Resolver) -> dict[str, str]:
    """Readable notes per claim, event and story-arc lineage, from every kept batch."""
    records: dict[str, dict[str, Any]] = {}
    versions: dict[str, list[tuple[int, str]]] = {}
    by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for batch in batches:
        for array, record_type in RULES.ARRAYS.items():
            for record in batch.get(array, []):
                by_key[(record_type, record["id"], record["version"])] = record
                if record_type in FOLDERS:
                    versions.setdefault(record["id"], []).append((record["version"], batch["batch_id"]))
                    current = records.get(record["id"])
                    if current is None or record["version"] > current["record"]["version"]:
                        records[record["id"]] = {"type": record_type, "record": record, "batch": batch}

    def latest(record_type: str, record_id: str) -> dict[str, Any] | None:
        found = [r for (t, i, _), r in by_key.items() if t == record_type and i == record_id]
        return max(found, key=lambda r: r["version"]) if found else None

    def about(record_type: str, ref_field: str, target_id: str) -> list[dict[str, Any]]:
        latest_rows: dict[str, dict[str, Any]] = {}
        for (t, i, _), r in by_key.items():
            if t == record_type and (r.get(ref_field) or {}).get("id") == target_id:
                if i not in latest_rows or r["version"] > latest_rows[i]["version"]:
                    latest_rows[i] = r
        return list(latest_rows.values())

    def note_link(record_id: str, label: str) -> str:
        kind = records.get(record_id, {}).get("type")
        return f"[[{NAMESPACE}/{FOLDERS[kind]}/{_slug(record_id)}|{_slug(record_id)}]]" if kind else _md(label)

    out: dict[str, str] = {}
    for record_id, entry in sorted(records.items()):
        record, record_type, batch = entry["record"], entry["type"], entry["batch"]
        decided = journal.get(batch["batch_id"], {})
        history = sorted(versions[record_id])
        body: list[str] = []
        fields: dict[str, Any] = {"type": record_type.replace("_", "-"), "record_id": record_id, "version": record["version"],
                                  "status": "approved" if decided else "unreviewed", "case_id": batch["case_id"],
                                  "batch_id": batch["batch_id"], "approved_at": decided.get("decided_at"), "tags": ["spotlight"]}
        if record_type == "claim":
            fields["title"] = record["proposition"]
            statement = record["statement"]
            body += [f"# {_md(record['proposition'])}", "",
                     f"- Subject: {resolver.link(statement['subject']['text'])}",
                     f"- Predicate: {_md(statement['predicate'])}"]
            if "object" in statement:
                body.append(f"- Object: {resolver.link(statement['object']['text'])}")
            for assessment in about("assessment", "claim_ref", record_id):
                fields.update({"verdict": assessment["verdict"], "confidence": assessment["confidence"]})
                body += ["", "## Assessment", "", f"{assessment['verdict']}, {assessment['confidence']} confidence.", "", _md(assessment["rationale"])]
                for flag in assessment.get("flags", []):
                    disposition = flag["disposition"]
                    body.append(f"- Flag `{flag['code']}`: {_md(flag['reason'])} Disposition: {disposition['action']}"
                                + (f" ({_md(disposition['reason'])})" if disposition.get("reason") else ""))
            passages = about("claim_source_expression", "claim_ref", record_id)
            if passages:
                body += ["", "## Passages", ""]
                for link in sorted(passages, key=lambda r: r["id"]):
                    passage = latest("source_expression", link["source_expression_ref"]["id"]) or {}
                    evidence = latest("evidence", ((passage.get("origin_ref") or {}).get("id")) or "") or {}
                    source = (evidence.get("capture") or {}).get("source_url", "")
                    speaker = f" ({_md(passage['speaker'])})" if passage.get("speaker") else ""
                    body.append(f"- {link['role']}: \"{_md(passage.get('text', ''))}\"{speaker} <{_md(source)}>".rstrip(" <>"))
            positions = about("source_position", "claim_ref", record_id)
            if positions:
                body += ["", "## Source positions", ""]
                body += [f"- {resolver.link(p['actor']['label'])} {p['stance']}: \"{_md(p['attribution_verbatim'])}\"" for p in positions]
            events = about("claim_event_membership", "claim_ref", record_id)
            if events:
                body += ["", "## Events", ""] + [f"- {note_link(m['event_ref']['id'], m['event_ref']['id'])}" for m in events]
        elif record_type == "event":
            actors = ", ".join(f"{resolver.link(a['name'])} ({a['role']})" for a in record["actors"])
            fields["title"] = f"{', '.join(a['name'] for a in record['actors'])} {record['action']}"
            body += [f"# {_md(fields['title'])}", "", f"- Actors: {actors}", f"- Action: {_md(record['action'])}"]
            if "object" in record:
                body.append(f"- Object: {resolver.link(record['object']['text'])}")
            if "time" in record:
                time = record["time"]
                if "value" in time:
                    when = time["value"]
                elif "start" in time and "end" in time:
                    when = f"{time['start']} to {time['end']}"
                else:
                    when = f"from {time['start']}" if "start" in time else f"until {time['end']}"
                body.append(f"- Time: {_md(when)}")
            if "place" in record:
                body.append(f"- Place: {resolver.link(record['place']['name'])}")
            claims = about("claim_event_membership", "event_ref", record_id)
            body += ["", "## Claims", ""] + [f"- {note_link(m['claim_ref']['id'], m['claim_ref']['id'])}" for m in claims]
            arcs = about("event_story_arc_membership", "event_ref", record_id)
            if arcs:
                body += ["", "## Story arcs", ""] + [f"- {note_link(m['story_arc_ref']['id'], m['story_arc_ref']['id'])}" for m in arcs]
        else:
            fields["title"] = record["title"]
            body += [f"# {_md(record['title'])}", "", _md(record["description"])]
            events = about("event_story_arc_membership", "story_arc_ref", record_id)
            body += ["", "## Events", ""] + [f"- {note_link(m['event_ref']['id'], m['event_ref']['id'])}" for m in events]
        body += ["", "## Versions", ""] + [f"- v{v}: batch {b}" for v, b in history]
        fields = {k: v for k, v in fields.items() if v is not None}
        out[f"{NAMESPACE}/{FOLDERS[record_type]}/{_slug(record_id)}.md"] = _frontmatter(fields) + "\n".join(body) + "\n"
    return out


def _admitted_store(ids: dict[str, Any]) -> dict[str, Any]:
    store: dict[str, Any] = {}
    for entry in ids.get("records", {}).values():
        if isinstance(entry.get("admitted"), dict) and isinstance(entry["admitted"].get("record"), dict):
            store.setdefault(next(a for a, t in RULES.ARRAYS.items() if t == entry["type"]), []).append(entry["admitted"]["record"])
    return store


def write(case: Path, workspace: Path, overwrite_hand_edits: bool = False) -> dict[str, Any]:
    if not (workspace / ".ok").is_dir() and not (workspace / NAMESPACE).is_dir():
        raise DestinationError(f"{workspace} is not a member wiki (no .ok/ or {NAMESPACE}/)")
    # The wiki lock, then the case lock, held for the whole write: no review
    # edit, builder run or other case can change what is checked below.
    with wiki_lock(workspace), transaction(case) as descriptor:
        decision, raw, bound = check_approval(case, descriptor)
        batch = json.loads(raw)
        batch_sha = _sha(raw)
        marker = load_ingestion_marker(case, descriptor)
        done = marker.get("status") == "completed" and marker.get("batch_sha256") == batch_sha
        ids = _load(case / "data" / "knowledge-ids.json")
        errors = schema_check.load(SCRIPT_DIR.parent / "schemas" / "knowledge-batch-1.1.schema.json").errors(batch)
        if not done:
            # Before admission the rules run against what is already admitted;
            # a completed run admitted this batch already and is only redone.
            errors += RULES.validate(batch, [_admitted_store(ids)])
        if errors:
            raise DestinationError("the approved batch is not valid: " + "; ".join(errors[:6]))
        batch_ids = {(t, r["id"], r["version"]) for a, t in RULES.ARRAYS.items() for r in batch.get(a, [])}

        # Wiki: the batch whole, then the journal, then the regenerated notes.
        batch_file = _confined(workspace, f"{NAMESPACE}/batches/{_slug(batch['batch_id'])}.json")
        if batch_file.is_file() and batch_file.read_bytes() != raw:
            raise DestinationError(f"{batch_file.name} already holds a different batch; kept batches are append-only")
        if not batch_file.is_file():
            _write_atomic(batch_file, raw)
        journal_path = _confined(workspace, f"{NAMESPACE}/decisions.jsonl")
        journal: dict[str, dict[str, Any]] = {}
        lines = journal_path.read_text(encoding="utf-8").splitlines() if journal_path.is_file() else []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                raise DestinationError("decisions.jsonl has an unreadable line; it is append-only and must not be edited") from None
            journal[item.get("batch_id")] = item
        if batch["batch_id"] not in journal:
            item = {"batch_id": batch["batch_id"], "case_id": batch["case_id"], "batch_sha256": batch_sha, "review_digest": bound,
                    "admitted_at": _now(), "records": sorted([list(k) for k in batch_ids])}
            _write_atomic(journal_path, ("\n".join(lines + [json.dumps(item, sort_keys=True)]) + "\n").encode("utf-8"))
            journal[batch["batch_id"]] = item
        kept = []
        batches_dir = _confined(workspace, f"{NAMESPACE}/batches")
        for path in sorted(batches_dir.glob("*.json")):
            doc = _load(path)
            if doc.get("batch_id") in journal:  # only journaled batches feed the notes
                kept.append(doc)
        manifest_path = _confined(workspace, f"{NAMESPACE}/generated-notes.json")
        manifest = _load(manifest_path) if manifest_path.is_file() else {"schema_version": "1.0", "notes": {}}
        base = workspace / ".ok" / "schemas" / "base.schema.json"
        base_checker = schema_check.load(base) if base.is_file() else None
        notes = notes_for(kept, journal, Resolver(workspace))
        edited = []
        for rel, text in notes.items():
            target = _confined(workspace, rel)
            if target.is_file():
                current = _sha(target.read_bytes())
                # Ours when it is what we wrote last or what we write now (a
                # run interrupted before the manifest was saved).
                if current not in (manifest["notes"].get(rel), _sha(text.encode("utf-8"))):
                    edited.append(rel)
        if edited and not overwrite_hand_edits:
            raise DestinationError("generated notes were edited by hand: " + ", ".join(sorted(edited)[:8])
                                   + "; move the edits elsewhere or rerun with --overwrite-hand-edits")
        for rel, text in notes.items():
            if base_checker is not None:
                front = {k: json.loads(v) for k, v in (line.split(": ", 1) for line in text.split("---\n")[1].splitlines())}
                problems = base_checker.errors(front)
                if problems:
                    raise DestinationError(f"{rel} fails the wiki's base schema: {problems[:3]}")
            data = text.encode("utf-8")
            _write_atomic(_confined(workspace, rel), data)
            manifest["notes"][rel] = _sha(data)
        _write_atomic(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))

        # Case: admission and the completed receipt, in one write.
        if not done:
            for entry in ids.get("records", {}).values():
                pending = entry.get("pending")
                if isinstance(pending, dict) and (entry["type"], entry["id"], pending.get("version")) in batch_ids:
                    entry["admitted"] = entry.pop("pending")
            marker.update({"status": "completed", "destination": "wiki", "batch_id": batch["batch_id"],
                           "batch_sha256": batch_sha, "notes_written": len(notes), "completed_at": _now()})
            atomic_write_files(case, {
                "data/knowledge-ids.json": (json.dumps(ids, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                "data/ingestion.json": (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            }, data_descriptor=descriptor)
    return {"ok": True, "batch_id": batch["batch_id"], "notes": len(notes), "already_admitted": done, "workspace": str(workspace)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case_dir")
    parser.add_argument("--workspace", help="the member wiki (default: knowledge_destination.workspace_path)")
    parser.add_argument("--overwrite-hand-edits", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace = Path(args.workspace).expanduser().resolve() if args.workspace else workspace_from_config()
        result = write(Path(args.case_dir).resolve(), workspace, args.overwrite_hand_edits)
    except (DestinationError, OrchestrationError, OSError) as exc:
        print(f"wiki-destination: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
