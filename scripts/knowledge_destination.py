#!/usr/bin/env python3
"""Read-only access to Spotlight's retired 1.0 knowledge graph.

Spotlight 1.0 kept approved knowledge in a local SQLite graph
(`<workspace>/.knowledge-workspace/spotlight.sqlite`). Nothing writes it any
more: new cases go to the member wiki through `scripts/wiki-destination.py`.
Engine's update runs the two commands left here on a member's old graph, then
deletes it:

    knowledge_destination.py count-rows --workspace-root ROOT --db DB
    knowledge_destination.py export-notes --workspace-root ROOT --db DB
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

DATABASE_SCHEMA_VERSION = 2


class ContractError(ValueError):
    """Raised when the graph or the export target violates the contract."""


# The 1.0 graph's schema, checked object for object before any read.
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    destination_id TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    source_project TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    review_manifest_json TEXT NOT NULL,
    approval_receipt_json TEXT NOT NULL,
    approval_evidence_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id TEXT PRIMARY KEY,
    reviewer_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    disposition TEXT NOT NULL,
    rationale TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id)
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_version INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (batch_id, kind, record_id, record_version)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    origin_project TEXT NOT NULL,
    origin_finding_id TEXT NOT NULL,
    origin_finding_fingerprint TEXT NOT NULL,
    proposition TEXT NOT NULL,
    status TEXT NOT NULL,
    event_link_disposition TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    event_link_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    UNIQUE (id, version, origin_project, origin_finding_id, origin_finding_fingerprint)
);

CREATE TABLE IF NOT EXISTS source_expression_refs (
    claim_id TEXT NOT NULL,
    claim_version INTEGER NOT NULL,
    project TEXT NOT NULL,
    expression_id TEXT NOT NULL,
    expression_fingerprint TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (claim_id, claim_version, project, expression_id, expression_fingerprint),
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    label TEXT NOT NULL,
    actors_json TEXT NOT NULL,
    action TEXT NOT NULL,
    object TEXT NOT NULL,
    place TEXT NOT NULL,
    event_time TEXT NOT NULL,
    status TEXT NOT NULL,
    story_link_disposition TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    story_link_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS story_arcs (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS claim_event_memberships (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    claim_id TEXT NOT NULL,
    claim_version INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    relation TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    FOREIGN KEY (claim_id, claim_version) REFERENCES claims(id, version),
    FOREIGN KEY (event_id, event_version) REFERENCES events(id, version)
);

CREATE TABLE IF NOT EXISTS event_story_arc_memberships (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    event_id TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    story_arc_id TEXT NOT NULL,
    story_arc_version INTEGER NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    review_decision_id TEXT REFERENCES review_decisions(id),
    payload_json TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (id, version),
    FOREIGN KEY (event_id, event_version) REFERENCES events(id, version),
    FOREIGN KEY (story_arc_id, story_arc_version) REFERENCES story_arcs(id, version)
);

CREATE INDEX IF NOT EXISTS claim_event_by_claim
    ON claim_event_memberships(claim_id, claim_version, status);
CREATE INDEX IF NOT EXISTS claim_event_by_event
    ON claim_event_memberships(event_id, event_version, status);
CREATE INDEX IF NOT EXISTS event_story_by_event
    ON event_story_arc_memberships(event_id, event_version, status);
CREATE INDEX IF NOT EXISTS event_story_by_arc
    ON event_story_arc_memberships(story_arc_id, story_arc_version, status);
CREATE INDEX IF NOT EXISTS claims_by_origin
    ON claims(origin_project, origin_finding_id, origin_finding_fingerprint, id);
CREATE INDEX IF NOT EXISTS source_expressions_by_expression
    ON source_expression_refs(project, expression_id, expression_fingerprint,
                              claim_id, claim_version);

CREATE TABLE IF NOT EXISTS case_policy_receipts (
    receipt_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    signature_evidence_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (case_id, destination_id, policy_revision)
);

CREATE TABLE IF NOT EXISTS projection_heads (
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    current_generation INTEGER NOT NULL,
    current_job_id TEXT NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    PRIMARY KEY (case_id, destination_id)
);

CREATE TABLE IF NOT EXISTS projection_jobs (
    job_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('graph_commit', 'case_policy')),
    source_ref TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'failed', 'completed', 'superseded')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    final_receipt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (case_id, destination_id, generation),
    UNIQUE (source_kind, source_ref)
);

CREATE TABLE IF NOT EXISTS projection_final_receipts (
    receipt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES projection_jobs(job_id),
    case_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    desired_projection_set_sha256 TEXT NOT NULL,
    workspace_receipt_ref TEXT NOT NULL,
    workspace_receipt_sha256 TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_running_projection_per_destination
    ON projection_jobs(destination_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS projection_jobs_ready
    ON projection_jobs(destination_id, status, generation);
CREATE INDEX IF NOT EXISTS claims_by_proposition
    ON claims(proposition, id, version);
"""


def _reject_symlink_path(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ContractError(f"symlink path is not allowed: {current}")


def resolve_beneath(base: Path, candidate: Path, label: str) -> Path:
    base = resolve_root(base, f"{label} root")
    unresolved = candidate.expanduser()
    if any(part.startswith("-") for part in unresolved.parts if part not in {"/", ""}):
        raise ContractError(f"{label} contains a leading-dash path component")
    if not unresolved.is_absolute():
        unresolved = base / unresolved
    _reject_symlink_path(unresolved)
    resolved = unresolved.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ContractError(f"{label} must resolve beneath {base}") from exc
    return resolved


def resolve_root(path: Path, label: str) -> Path:
    unresolved = path.expanduser()
    _reject_symlink_path(unresolved.absolute())
    resolved = unresolved.resolve()
    if not resolved.is_dir():
        raise ContractError(f"{label} is not a directory: {resolved}")
    return resolved


# Windows has no uid and reports POSIX-shaped modes that only mirror the
# read-only bit; per-user profile ACLs already scope these files there. Keep
# the POSIX ownership/mode contract where it is meaningful and skip it on NT.
_POSIX_OWNERSHIP = hasattr(os, "getuid")


def _owned_by_current_user(metadata: os.stat_result) -> bool:
    return not _POSIX_OWNERSHIP or metadata.st_uid == os.getuid()


def _mode_is_owner_only(metadata: os.stat_result) -> bool:
    return not _POSIX_OWNERSHIP or not (stat.S_IMODE(metadata.st_mode) & 0o077)


def _verify_database_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != DATABASE_SCHEMA_VERSION:
        raise ContractError(
            f"unsupported database schema version {version}; "
            f"expected {DATABASE_SCHEMA_VERSION}"
        )
    row = connection.execute(
        "SELECT schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if row is None or row[0] != DATABASE_SCHEMA_VERSION:
        raise ContractError("database schema metadata is missing or incompatible")
    expected_connection = sqlite3.connect(":memory:")
    try:
        expected_connection.executescript(SCHEMA_SQL)
        expected = expected_connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        expected_connection.close()
    actual = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    def normalized_schema_row(item: sqlite3.Row | tuple[Any, ...]) -> tuple[Any, ...]:
        kind, name, table, sql = tuple(item)
        normalized_sql = None
        if sql is not None:
            normalized_sql = re.sub(r"\s+", "", sql.lower()).replace(
                "ifnotexists", ""
            )
        return kind, name, table, normalized_sql

    if [normalized_schema_row(item) for item in actual] != [
        normalized_schema_row(item) for item in expected
    ]:
        raise ContractError("database schema objects do not match the reference contract")


# Approved knowledge and the signed case policies that govern it: a graph
# with any of these is kept.
GRAPH_TABLES = ("batches", "claims", "events", "story_arcs", "claim_event_memberships",
                "event_story_arc_memberships", "source_expression_refs", "review_decisions",
                "case_policy_receipts")


def count_graph_rows(connection: sqlite3.Connection) -> dict[str, Any]:
    """Rows of approved knowledge per table, for the 1.0 graph's retirement:
    Engine checks export-notes wrote the same counts before it deletes the
    database."""
    tables = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in GRAPH_TABLES}
    return {"schema_version": "spotlight-graph-rows/v1", "tables": tables, "total": sum(tables.values())}


LEGACY_GRAPH_DIR = Path("spotlight") / "legacy-graph"
# The lossless archive stays beside the database, in Engine's folder that the
# wiki neither indexes nor serves: it keeps rows of every classification.
LEGACY_GRAPH_ARCHIVE = Path(".knowledge-workspace") / "spotlight-legacy-graph.json"
ARCHIVE_TABLES = GRAPH_TABLES + ("batch_items", "schema_metadata")


def _legacy_note_name(record_id: str) -> str:
    # Ids hold ':' (not allowed in Windows file names); the hash keeps two ids
    # that differ only there apart.
    slug = re.sub(r"[^a-z0-9._-]+", "-", record_id.split(":", 1)[-1]).strip("-.")[:80] or "record"
    return f"{slug}-{hashlib.sha256(record_id.encode()).hexdigest()[:8]}.md"


def _legacy_frontmatter(fields: dict[str, Any]) -> str:
    # JSON scalars are valid YAML, so the frontmatter is exact without a YAML library.
    return "\n".join(["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()] + ["---", ""])


def _legacy_text(value: Any) -> str:
    """Row text as plain prose: wiki links, block ids and headings in it stay
    literal, and it stays on one line."""
    text = " ".join(str(value).split())
    text = text.replace("[[", "[\\[").replace("^", "\\^")
    return "\\" + text if text.startswith(("#", "-", ">", "---")) else text


def _shareable_cases(rows: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Cases whose latest active policy is shareable. Internal and personal
    cases, and cases with no readable policy, get no notes."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows["case_policy_receipts"]:
        if row["case_id"] not in latest or row["policy_revision"] > latest[row["case_id"]]["policy_revision"]:
            latest[row["case_id"]] = row
    shareable = set()
    for case, row in latest.items():
        try:
            classification = json.loads(row["payload_json"]).get("classification")
        except (TypeError, ValueError):
            continue
        if row["status"] == "active" and classification == "shareable":
            shareable.add(case)
    return shareable


def _legacy_notes(rows: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """One note per approved claim, event and story arc at its latest version,
    from shareable cases only, linked through approved memberships."""
    def latest(table: str) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for row in rows[table]:
            if row["id"] not in best or row["version"] > best[row["id"]]["version"]:
                best[row["id"]] = row
        return [best[key] for key in sorted(best) if best[key]["status"] == "approved"]

    shareable = _shareable_cases(rows)
    claims = [c for c in latest("claims") if c["origin_project"] in shareable]
    claim_ids = {c["id"]: c["version"] for c in claims}
    claim_events = [m for m in latest("claim_event_memberships") if claim_ids.get(m["claim_id"]) == m["claim_version"]]
    all_events = {e["id"]: e for e in latest("events")}
    events = [all_events[i] for i in sorted({m["event_id"] for m in claim_events})
              if i in all_events and any(m["event_version"] == all_events[i]["version"] for m in claim_events if m["event_id"] == i)]
    event_ids = {e["id"]: e["version"] for e in events}
    event_arcs = [m for m in latest("event_story_arc_memberships") if event_ids.get(m["event_id"]) == m["event_version"]]
    all_arcs = {a["id"]: a for a in latest("story_arcs")}
    arcs = [all_arcs[i] for i in sorted({m["story_arc_id"] for m in event_arcs})
            if i in all_arcs and any(m["story_arc_version"] == all_arcs[i]["version"] for m in event_arcs if m["story_arc_id"] == i)]

    names = {r["id"]: _legacy_note_name(r["id"]) for r in claims + events + arcs}
    def link(folder: str, record_id: str) -> str:
        return f"[{record_id}](../{folder}/{names[record_id]})"

    notes: dict[str, str] = {}
    for claim in claims:
        linked = sorted({m["event_id"] for m in claim_events if m["claim_id"] == claim["id"]} & set(event_ids))
        body = [_legacy_text(claim["proposition"]), ""]
        if linked:
            body += ["Events:", *[f"- {link('events', e)}" for e in linked], ""]
        notes[f"claims/{names[claim['id']]}"] = _legacy_frontmatter({
            "type": "spotlight-legacy-claim", "id": claim["id"], "version": claim["version"],
            "case": claim["origin_project"], "finding": claim["origin_finding_id"], "batch": claim["batch_id"],
        }) + "\n".join(body)
    for event in events:
        linked = sorted({m["story_arc_id"] for m in event_arcs if m["event_id"] == event["id"]} & {a["id"] for a in arcs})
        actors = json.loads(event["actors_json"] or "[]")
        names_of = ", ".join(_legacy_text(a.get("name", a) if isinstance(a, dict) else a) for a in actors) or "none"
        body = [_legacy_text(event["label"]), "", f"- Actors: {names_of}", f"- Action: {_legacy_text(event['action'])}",
                f"- Object: {_legacy_text(event['object'])}", f"- Place: {_legacy_text(event['place'])}",
                f"- Time: {_legacy_text(event['event_time'])}", ""]
        if linked:
            body += ["Story arcs:", *[f"- {link('story-arcs', a)}" for a in linked], ""]
        notes[f"events/{names[event['id']]}"] = _legacy_frontmatter({
            "type": "spotlight-legacy-event", "id": event["id"], "version": event["version"], "batch": event["batch_id"],
        }) + "\n".join(body)
    for arc in arcs:
        notes[f"story-arcs/{names[arc['id']]}"] = _legacy_frontmatter({
            "type": "spotlight-legacy-story-arc", "id": arc["id"], "version": arc["version"], "batch": arc["batch_id"],
        }) + "\n".join([_legacy_text(arc["title"]), "", _legacy_text(arc["description"]), ""])
    notes["README.md"] = (
        "# Spotlight 1.0 graph (archived)\n\n"
        "Spotlight 1.0 kept approved knowledge in a database that is now retired. "
        "These notes are the approved claims of shareable cases, with their events and story "
        "arcs, at their latest version. Every row of every case is kept unchanged in "
        "`.knowledge-workspace/spotlight-legacy-graph.json`. Nothing updates them.\n"
    )
    return notes


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return  # Windows cannot open a directory to flush it; the rename is durable on NTFS
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durable(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _publish(target: Path, files: dict[str, bytes]) -> None:
    """Write files into target whole or not at all, flushed to disk; a target
    already holding exactly these files is left as it is."""
    if target.exists():
        existing = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file() and not p.is_symlink()}
        if existing == files:
            return
        raise ContractError(f"{target} already exists and holds a different graph")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for rel, data in files.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_durable(path, data)
        for directory in sorted({(staging / rel).parent for rel in files}, key=lambda p: -len(p.parts)):
            _fsync_dir(directory)
        os.rename(staging, target)
        _fsync_dir(target.parent)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def export_graph_notes(connection: sqlite3.Connection, workspace_root: Path) -> dict[str, Any]:
    """Write the 1.0 graph out before Engine deletes the database: every row
    of every table to the archive, and readable notes into the wiki. Both are
    flushed to disk, and the counts returned are read back from the archive
    on disk, so Engine compares what was written, not what was meant."""
    rows = {
        table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ARCHIVE_TABLES
    }
    archive = (json.dumps({"schema_version": "spotlight-legacy-graph/v1", "tables": rows}, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    root = workspace_root.expanduser().resolve(strict=True)
    archive_path = root / LEGACY_GRAPH_ARCHIVE
    notes_dir = root / LEGACY_GRAPH_DIR
    for path in (archive_path, notes_dir):
        _reject_symlink_path(path)
    if archive_path.exists():
        if archive_path.is_symlink() or archive_path.read_bytes() != archive:
            raise ContractError(f"{archive_path} already exists and holds a different graph")
    else:
        archive_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = archive_path.with_name(f".{archive_path.name}.{os.getpid()}")
        _write_durable(temporary, archive)
        os.replace(temporary, archive_path)
        _fsync_dir(archive_path.parent)
    _publish(notes_dir, {rel: text.encode("utf-8") for rel, text in _legacy_notes(rows).items()})
    written = json.loads(archive_path.read_bytes())["tables"]
    counts = {table: len(written.get(table) or []) for table in GRAPH_TABLES}
    return {"schema_version": "spotlight-graph-export/v1", "archive": LEGACY_GRAPH_ARCHIVE.as_posix(),
            "notes": LEGACY_GRAPH_DIR.as_posix(), "tables": counts, "total": sum(counts.values())}


def open_existing_database(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve(strict=False)
    _reject_symlink_path(path)
    if not path.is_file():
        raise ContractError(f"database not found: {path}")
    metadata = path.stat()
    mode = stat.S_IMODE(metadata.st_mode)
    if not _mode_is_owner_only(metadata):
        raise ContractError(
            f"database permissions must be owner-only (0600), got {mode:04o}"
        )
    if not _owned_by_current_user(metadata) or metadata.st_nlink != 1:
        raise ContractError("database must be owned by the current user and not hard-linked")
    uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, timeout=5.0, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA busy_timeout = 5000")
    _verify_database_schema(connection)
    return connection


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcommands = parser.add_subparsers(dest="command", required=True)
    count_parser = subcommands.add_parser(
        "count-rows", help="count the graph's knowledge rows, read-only (Engine's retirement check)"
    )
    count_parser.add_argument("--workspace-root", required=True, type=Path)
    count_parser.add_argument("--db", required=True, type=Path)
    export_parser = subcommands.add_parser(
        "export-notes", help="archive the graph and write its shareable records as wiki notes (Engine's retirement, before it deletes the database)"
    )
    export_parser.add_argument("--workspace-root", required=True, type=Path)
    export_parser.add_argument("--db", required=True, type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace_root = resolve_root(args.workspace_root, "workspace root")
        database = resolve_beneath(workspace_root, args.db, "database")
        connection = open_existing_database(database)
        try:
            if args.command == "count-rows":
                print_json(count_graph_rows(connection))
            else:
                print_json(export_graph_notes(connection, args.workspace_root))
            return 0
        finally:
            connection.close()
    except (ContractError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
