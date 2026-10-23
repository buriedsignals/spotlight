#!/usr/bin/env python3
"""Checks for the retired 1.0 graph's two remaining commands, count-rows and
export-notes, which Engine's update runs before it deletes a member's graph.
The graph writer is gone, so the graph is built here directly with sqlite."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "knowledge_destination.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORT = load_module("knowledge_destination", SCRIPT)


def run_cli(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def build_graph(path: Path) -> None:
    """A 1.0 graph as the retired writer left it: one batch, a shareable and a
    personal case, a claim linked to an event linked to a story arc."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(PORT.SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {PORT.DATABASE_SCHEMA_VERSION}")
        connection.execute("INSERT INTO schema_metadata VALUES (1, ?, 'destination:spotlight-local')", (PORT.DATABASE_SCHEMA_VERSION,))
        connection.execute("INSERT INTO batches VALUES ('batch:b', '1.0', 'k', ?, 'case-a', 't', 't', '{}', '{}', '{}', '{}')", ("a" * 64,))
        for receipt, case, classification in (("receipt:a", "case-a", "shareable"), ("receipt:b", "case-b", "personal")):
            connection.execute(
                "INSERT INTO case_policy_receipts VALUES (?, ?, 'destination:spotlight-local', 1, 'active', ?, ?, '{}', 't')",
                (receipt, case, "b" * 64, json.dumps({"classification": classification})),
            )
        for claim, case in (("claim:shared", "case-a"), ("claim:private", "case-b")):
            connection.execute(
                "INSERT INTO claims VALUES (?, 1, NULL, ?, 'F1', ?, ?, 'approved', 'linked', NULL, NULL, '{}', 'batch:b')",
                (claim, case, "c" * 64, f"proposition of {claim}"),
            )
        connection.execute("INSERT INTO events VALUES ('event:e', 1, NULL, 'E', '[]', 'paid', 'money', 'Bern', '2026', 'approved', 'linked', NULL, NULL, '{}', 'batch:b')")
        connection.execute("INSERT INTO story_arcs VALUES ('story-arc:s', 1, NULL, 'Arc', 'About', 'approved', NULL, '{}', 'batch:b')")
        connection.execute("INSERT INTO claim_event_memberships VALUES ('relation:claim-event:1', 1, NULL, 'claim:shared', 1, 'event:e', 1, 'describes', 'approved', NULL, '{}', 'batch:b')")
        connection.execute("INSERT INTO event_story_arc_memberships VALUES ('relation:event-story:1', 1, NULL, 'event:e', 1, 'story-arc:s', 1, 'part', 'approved', NULL, '{}', 'batch:b')")
        connection.commit()
    finally:
        connection.close()
    os.chmod(path, 0o600)


def check_legacy_notes() -> None:
    """Archived graph notes: approved records of shareable cases at their
    latest version, linked only through live memberships, with row text kept
    literal."""
    def policy(case, classification, revision=1, status="active"):
        return {"case_id": case, "policy_revision": revision, "status": status,
                "payload_json": json.dumps({"classification": classification})}

    def claim(cid, version, status, case, text):
        return {"id": cid, "version": version, "status": status, "origin_project": case,
                "origin_finding_id": "F1", "batch_id": "batch:b", "proposition": text}

    rows = {table: [] for table in PORT.ARCHIVE_TABLES}
    rows["case_policy_receipts"] = [policy("case-a", "internal", 1), policy("case-a", "shareable", 2),
                                    policy("case-b", "personal"), policy("case-c", "shareable", status="revoked")]
    rows["claims"] = [
        claim("claim:a", 1, "approved", "case-a", "old wording"),
        claim("claim:a", 2, "approved", "case-a", "## Heading [[wiki/entities/x]] ^id"),
        claim("claim:gone", 1, "approved", "case-a", "x"), claim("claim:gone", 2, "rejected", "case-a", "x"),
        claim("claim:private", 1, "approved", "case-b", "personal case"),
        claim("claim:revoked", 1, "approved", "case-c", "revoked policy"),
    ]
    rows["events"] = [{"id": "event:e", "version": 1, "status": "approved", "label": "E", "actors_json": "[]",
                       "action": "a", "object": "o", "place": "p", "event_time": "t", "batch_id": "batch:b"},
                      {"id": "event:stale", "version": 1, "status": "approved", "label": "S", "actors_json": "[]",
                       "action": "a", "object": "o", "place": "p", "event_time": "t", "batch_id": "batch:b"}]
    rows["claim_event_memberships"] = [
        {"id": "relation:claim-event:1", "version": 1, "status": "approved", "claim_id": "claim:a", "claim_version": 2,
         "event_id": "event:e", "event_version": 1},
        {"id": "relation:claim-event:2", "version": 1, "status": "approved", "claim_id": "claim:a", "claim_version": 1,
         "event_id": "event:stale", "event_version": 1},
        {"id": "relation:claim-event:3", "version": 1, "status": "approved", "claim_id": "claim:a", "claim_version": 2,
         "event_id": "event:stale", "event_version": 1},
        {"id": "relation:claim-event:3", "version": 2, "status": "rejected", "claim_id": "claim:a", "claim_version": 2,
         "event_id": "event:stale", "event_version": 1},
    ]
    notes = PORT._legacy_notes(rows)
    claims = {k: v for k, v in notes.items() if k.startswith("claims/")}
    assert len(claims) == 1, sorted(notes)
    text = next(iter(claims.values()))
    assert '"claim:a"' in text and '"version": 2' not in text and "version: 2" in text, text
    assert "old wording" not in text and "\\## Heading [\\[wiki/entities/x]] \\^id" in text, text
    events = [k for k in notes if k.startswith("events/")]
    assert len(events) == 1 and not events[0].startswith("events/stale-"), events
    assert "event:stale" not in text, text


def main() -> int:
    check_legacy_notes()
    print("ok   archived graph notes keep shareable, current, approved records only")
    with tempfile.TemporaryDirectory() as raw_tmp:
        workspace = Path(raw_tmp).resolve()
        database = workspace / ".knowledge-workspace" / "spotlight.sqlite"
        build_graph(database)
        db_args = ("--workspace-root", str(workspace), "--db", ".knowledge-workspace/spotlight.sqlite")

        before = database.read_bytes()
        counted = json.loads(run_cli("count-rows", *db_args).stdout)
        assert database.read_bytes() == before, "count-rows is read-only"
        assert counted["schema_version"] == "spotlight-graph-rows/v1" and set(counted["tables"]) == set(PORT.GRAPH_TABLES), counted
        assert counted["tables"]["claims"] == 2 and counted["tables"]["case_policy_receipts"] == 2 and counted["total"] == 9, counted
        print("ok   count-rows counts every knowledge table, read-only")

        exported = json.loads(run_cli("export-notes", *db_args).stdout)
        assert exported["tables"] == counted["tables"] and exported["total"] == counted["total"], exported
        archive_path = workspace / ".knowledge-workspace" / "spotlight-legacy-graph.json"
        archived = json.loads(archive_path.read_text(encoding="utf-8"))["tables"]
        assert {t: len(archived[t]) for t in PORT.GRAPH_TABLES} == counted["tables"], archived
        assert "batch_items" in archived and "schema_metadata" in archived
        assert stat.S_IMODE(archive_path.stat().st_mode) == 0o600
        legacy = workspace / "spotlight" / "legacy-graph"
        claim_notes = sorted((legacy / "claims").glob("*.md"))
        assert len(claim_notes) == 1 and '"claim:shared"' in claim_notes[0].read_text(encoding="utf-8"), claim_notes
        assert len(list((legacy / "events").glob("*.md"))) == 1 and len(list((legacy / "story-arcs").glob("*.md"))) == 1
        assert "claim:private" not in "".join(p.read_text(encoding="utf-8") for p in legacy.rglob("*.md"))
        assert all(":" not in p.name for p in legacy.rglob("*")), "note names must be valid on Windows"
        print("ok   export-notes archives every row and writes shareable records as notes")

        notes_before = sorted((p.relative_to(legacy), p.read_bytes()) for p in legacy.rglob("*") if p.is_file())
        again = json.loads(run_cli("export-notes", *db_args).stdout)
        assert again == exported
        assert sorted((p.relative_to(legacy), p.read_bytes()) for p in legacy.rglob("*") if p.is_file()) == notes_before
        (legacy / "README.md").write_text("edited", encoding="utf-8")
        refused = run_cli("export-notes", *db_args, ok=False)
        assert refused.returncode != 0 and "different graph" in refused.stderr, refused
        shutil.rmtree(legacy)
        print("ok   export-notes re-runs unchanged and refuses a different export")

        if hasattr(os, "getuid"):
            os.chmod(database, 0o644)
            loose = run_cli("count-rows", *db_args, ok=False)
            assert loose.returncode == 1 and "owner-only" in loose.stderr, loose
            os.chmod(database, 0o600)
        tampered = sqlite3.connect(database)
        try:
            tampered.execute("CREATE TABLE extra (x)")
            tampered.commit()
        finally:
            tampered.close()
        refused = run_cli("count-rows", *db_args, ok=False)
        assert refused.returncode == 1 and "do not match" in refused.stderr, refused
        print("ok   a group-readable or foreign-schema database is refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
