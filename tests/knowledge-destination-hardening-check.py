#!/usr/bin/env python3
"""Adversarial regression checks for the Knowledge Destination reference adapter."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "knowledge_destination.py"
FIXTURE = ROOT / "tests" / "fixtures" / "knowledge-batch.sample.json"
SPEC = importlib.util.spec_from_file_location("knowledge_destination", SCRIPT)
assert SPEC and SPEC.loader
PORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PORT)


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def must_reject(action, expected: str) -> None:
    try:
        action()
    except PORT.ContractError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected rejection containing {expected!r}")


def rebind_decision(batch: dict, decision_id: str, kind: str, row: dict) -> None:
    decision = next(item for item in batch["review_decisions"] if item["id"] == decision_id)
    decision["subject"] = {
        "kind": kind,
        "id": row["id"] if kind != "source_case" else row["project"],
        "version": row.get("version", 1),
        "payload_sha256": PORT.payload_sha256(row),
    }


def authorized_commit(database: Path, batch: dict) -> dict:
    manifest = PORT.review_manifest(
        batch,
        {"project": batch["source_case"]["project"], "artifact_hashes": {}},
        "reference:test",
    )
    receipt = {
        "schema_version": "1.0",
        "namespace": PORT.APPROVAL_NAMESPACE,
        "destination_id": "reference:test",
        "payload_sha256": manifest["payload_sha256"],
        "review_manifest_sha256": manifest["review_manifest_sha256"],
        "reviewer_id": "journalist:fixture",
        "approved_at": "2026-08-18T10:00:00Z",
        "decision": "approved",
    }
    if not database.exists():
        PORT.initialize_database(database, "reference:test")
    return PORT._commit_verified_batch(
        database, batch, PORT.payload_sha256(batch), manifest, receipt,
        {
            "signature": "test-only",
            "allowed_signers": "test-only",
            "allowed_signers_sha256": PORT.hashlib.sha256(b"test-only").hexdigest(),
            "verifier": str(PORT.SSH_KEYGEN),
        },
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp).resolve()

        duplicate = tmp / "duplicate.json"
        duplicate.write_text('{"schema_version":"1.0","schema_version":"9.9"}', encoding="utf-8")
        must_reject(lambda: PORT.load_json(duplicate), "duplicate JSON key")
        must_reject(
            lambda: PORT.resolve_beneath(tmp, tmp.parent / "escape.sqlite", "database"),
            "must resolve beneath",
        )
        outside = tmp / "outside"
        outside.mkdir()
        symlink = tmp / "link"
        symlink.symlink_to(outside, target_is_directory=True)
        must_reject(
            lambda: PORT.resolve_beneath(tmp, symlink / "knowledge.sqlite", "database"),
            "symlink path",
        )

        database = tmp / "private" / "knowledge.sqlite"
        base = fixture()
        forged_reviewer = copy.deepcopy(base)
        forged_reviewer["review_decisions"][1]["reviewer_id"] = "journalist:forged"
        must_reject(
            lambda: authorized_commit(
                tmp / "forged-reviewer" / "knowledge.sqlite", forged_reviewer
            ),
            "cannot authenticate decisions from other reviewers",
        )
        authorized_commit(database, base)
        assert os.stat(database).st_mode & 0o077 == 0

        migration_db = tmp / "migration" / "knowledge.sqlite"
        migration_db.parent.mkdir(parents=True)
        source = sqlite3.connect(database)
        migrated_raw = sqlite3.connect(migration_db)
        try:
            source.backup(migrated_raw)
            migrated_raw.execute("DROP INDEX one_running_projection_per_destination")
            migrated_raw.execute("DROP INDEX projection_jobs_ready")
            migrated_raw.execute("DROP INDEX claims_by_proposition")
            migrated_raw.execute("DROP TABLE projection_final_receipts")
            migrated_raw.execute("DROP TABLE projection_jobs")
            migrated_raw.execute("DROP TABLE projection_heads")
            migrated_raw.execute("DROP TABLE case_policy_receipts")
            migrated_raw.execute("UPDATE schema_metadata SET schema_version = 1")
            migrated_raw.execute("PRAGMA user_version = 1")
            migrated_raw.commit()
        finally:
            migrated_raw.close()
            source.close()
        os.chmod(migration_db, 0o600)
        migrated = PORT.connect_database(migration_db)
        try:
            assert migrated.execute("PRAGMA user_version").fetchone()[0] == 2
            backfilled = migrated.execute(
                "SELECT source_ref, generation, status FROM projection_jobs"
            ).fetchone()
            assert tuple(backfilled) == (base["batch_id"], 1, "pending")
        finally:
            migrated.close()

        retarget = copy.deepcopy(base)
        retarget["batch_id"] = "batch:river-monitoring:retarget"
        retarget["idempotency_key"] = "1" * 64
        retarget["claims"][0]["version"] = 2
        retarget["claims"][0]["supersedes_version"] = 1
        retarget["claims"][0]["origin"]["finding_id"] = "F999"
        retarget["claims"][0]["origin"]["finding_fingerprint"] = "2" * 64
        old_decision_id = retarget["claims"][0]["review_decision_id"]
        new_decision_id = "decision:claim:night-discharge:retarget"
        retarget["claims"][0]["review_decision_id"] = new_decision_id
        decision = next(
            item for item in retarget["review_decisions"] if item["id"] == old_decision_id
        )
        decision["id"] = new_decision_id
        retarget["events"] = []
        retarget["story_arcs"] = []
        retarget["claim_event_memberships"] = []
        retarget["event_story_arc_memberships"] = []
        rebind_decision(
            retarget,
            new_decision_id,
            "claim",
            retarget["claims"][0],
        )
        must_reject(
            lambda: authorized_commit(database, retarget),
            "immutable identity",
        )

        pending = copy.deepcopy(base)
        pending["batch_id"] = "batch:river-monitoring:pending"
        pending["idempotency_key"] = "3" * 64
        pending["claims"] = []
        pending["events"] = []
        pending["story_arcs"] = []
        pending["claim_event_memberships"] = []
        pending["event_story_arc_memberships"] = []
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                "UPDATE claims SET event_link_disposition = 'pending' "
                "WHERE id = 'claim:night-discharge:001' AND version = 1"
            )
            must_reject(lambda: PORT.enforce_coverage_invariants(connection), "pending")
            connection.rollback()
        finally:
            connection.close()

        candidate = copy.deepcopy(base)
        candidate["batch_id"] = "batch:river-monitoring:candidate-object"
        candidate["idempotency_key"] = "4" * 64
        candidate["claims"][0]["version"] = 2
        candidate["claims"][0]["supersedes_version"] = 1
        candidate["claims"][0]["status"] = "candidate"
        candidate["claims"][0].pop("review_decision_id", None)
        candidate["claims"][0]["event_link_disposition"] = "pending"
        candidate["events"] = []
        candidate["story_arcs"] = []
        candidate["claim_event_memberships"] = []
        candidate["event_story_arc_memberships"] = []
        authorized_commit(database, candidate)
        connection = PORT.open_existing_database(database)
        try:
            canonical = PORT.traverse_claim(connection, "claim:night-discharge:001")
            review = PORT.traverse_claim(
                connection, "claim:night-discharge:001", include_candidates=True
            )
        finally:
            connection.close()
        assert canonical["claim"]["version"] == 1
        assert review["claim"]["version"] == 2

        state_db = tmp / "state-history" / "knowledge.sqlite"
        authorized_commit(state_db, base)
        rejected_version = copy.deepcopy(base)
        rejected_version["batch_id"] = "batch:river-monitoring:rejected-v2"
        rejected_version["idempotency_key"] = "8" * 64
        rejected_version["events"] = []
        rejected_version["story_arcs"] = []
        rejected_version["claim_event_memberships"] = [
            copy.deepcopy(base["claim_event_memberships"][0])
        ]
        rejected_version["event_story_arc_memberships"] = []
        claim = rejected_version["claims"][0]
        claim["version"] = 2
        claim["supersedes_version"] = 1
        claim["status"] = "rejected"
        claim["event_link_disposition"] = "pending"
        decision = next(
            row for row in rejected_version["review_decisions"]
            if row["id"] == claim["review_decision_id"]
        )
        decision["id"] = "decision:claim:night-discharge:rejected-v2"
        decision["disposition"] = "rejected"
        claim["review_decision_id"] = decision["id"]
        rebind_decision(rejected_version, decision["id"], "claim", claim)
        relation = rejected_version["claim_event_memberships"][0]
        relation["version"] = 2
        relation["supersedes_version"] = 1
        relation["claim"]["version"] = 2
        relation["status"] = "rejected"
        relation_decision = next(
            row for row in rejected_version["review_decisions"]
            if row["id"] == relation["review_decision_id"]
        )
        relation_decision["id"] = "decision:relation:claim-event:rejected-v2"
        relation_decision["disposition"] = "rejected"
        relation["review_decision_id"] = relation_decision["id"]
        rebind_decision(
            rejected_version,
            relation_decision["id"],
            "claim_event_membership",
            relation,
        )
        authorized_commit(state_db, rejected_version)
        candidate_v3 = copy.deepcopy(rejected_version)
        candidate_v3["batch_id"] = "batch:river-monitoring:candidate-v3"
        candidate_v3["idempotency_key"] = "9" * 64
        candidate_v3["claims"][0]["version"] = 3
        candidate_v3["claims"][0]["supersedes_version"] = 2
        candidate_v3["claims"][0]["status"] = "candidate"
        candidate_v3["claims"][0].pop("review_decision_id", None)
        candidate_v3["claim_event_memberships"][0]["version"] = 3
        candidate_v3["claim_event_memberships"][0]["supersedes_version"] = 2
        candidate_v3["claim_event_memberships"][0]["claim"]["version"] = 3
        candidate_v3["claim_event_memberships"][0]["status"] = "candidate"
        candidate_v3["claim_event_memberships"][0].pop("review_decision_id", None)
        candidate_v3["review_decisions"] = [candidate_v3["review_decisions"][0]]
        authorized_commit(state_db, candidate_v3)
        connection = PORT.open_existing_database(state_db)
        try:
            assert PORT.projected_one(
                connection, "claims", "claim:night-discharge:001"
            ) is None
            visible_candidate = PORT.projected_one(
                connection, "claims", "claim:night-discharge:001", True
            )
            assert visible_candidate is not None and visible_candidate["version"] == 3
            assert PORT.projected_relation_rows(
                connection,
                "claim_event_memberships",
                "id = ?",
                ("relation:claim-event:night-discharge:001",),
                False,
            ) == []
        finally:
            connection.close()

        candidate_graph_db = tmp / "candidate-graph" / "knowledge.sqlite"
        authorized_commit(candidate_graph_db, base)
        candidate_graph = copy.deepcopy(base)
        candidate_graph["batch_id"] = "batch:river-monitoring:candidate-graph"
        candidate_graph["idempotency_key"] = "5" * 64
        source_decision_id = candidate_graph["source_case"]["review_decision_id"]
        candidate_graph["review_decisions"] = [
            row for row in candidate_graph["review_decisions"]
            if row["id"] == source_decision_id
        ]
        candidate_graph["claims"] = []
        for collection in (
            "events", "story_arcs", "claim_event_memberships",
            "event_story_arc_memberships",
        ):
            for row in candidate_graph[collection]:
                row["version"] = 2
                row["supersedes_version"] = 1
                row["status"] = "candidate"
                row.pop("review_decision_id", None)
        candidate_graph["claim_event_memberships"][0]["event"]["version"] = 2
        candidate_graph["event_story_arc_memberships"][0]["event"]["version"] = 2
        candidate_graph["event_story_arc_memberships"][0]["story_arc"]["version"] = 2
        authorized_commit(candidate_graph_db, candidate_graph)
        connection = PORT.open_existing_database(candidate_graph_db)
        try:
            canonical_graph = PORT.traverse_claim(
                connection, "claim:night-discharge:001"
            )
            review_graph = PORT.traverse_claim(
                connection, "claim:night-discharge:001", include_candidates=True
            )
        finally:
            connection.close()
        assert canonical_graph["events"][0]["event"]["version"] == 1
        assert review_graph["events"][0]["event"]["version"] == 2
        assert review_graph["events"][0]["story_arcs"][0]["story_arc"]["version"] == 2

        rollback_db = tmp / "rollback" / "knowledge.sqlite"
        authorized_commit(rollback_db, base)
        connection = PORT.open_existing_database(rollback_db)
        tables = (
            "batches", "batch_items", "review_decisions", "claims", "source_expression_refs",
            "events", "story_arcs", "claim_event_memberships",
            "event_story_arc_memberships", "projection_heads", "projection_jobs",
            "projection_final_receipts", "case_policy_receipts",
        )
        try:
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
        finally:
            connection.close()
        dangling = copy.deepcopy(base)
        dangling["batch_id"] = "batch:river-monitoring:rollback"
        dangling["idempotency_key"] = "6" * 64
        dangling["source_case"]["review_decision_id"] = "decision:batch:rollback"
        source_decision = copy.deepcopy(dangling["review_decisions"][0])
        source_decision["id"] = "decision:batch:rollback"
        source_decision["subject"] = {
            "kind": "source_case",
            "id": dangling["source_case"]["project"],
            "version": 1,
            "payload_sha256": PORT.payload_sha256(dangling["source_case"]),
        }
        dangling["review_decisions"] = [source_decision]
        dangling["claims"] = []
        dangling["events"] = []
        dangling["story_arcs"] = []
        relation = dangling["claim_event_memberships"][0]
        relation["id"] = "relation:claim-event:rollback"
        relation["event"]["id"] = "event:missing:rollback"
        relation["status"] = "candidate"
        relation.pop("review_decision_id", None)
        dangling["event_story_arc_memberships"] = []
        must_reject(
            lambda: authorized_commit(rollback_db, dangling),
            "database rejected batch",
        )
        connection = PORT.open_existing_database(rollback_db)
        try:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
        finally:
            connection.close()
        assert after == before

        connection = PORT.open_existing_database(database)
        original_relation_projection = PORT.projected_relation_rows
        PORT.projected_relation_rows = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical traversal must page relations in SQL")
        )
        try:
            forward_page = PORT.traverse_claim(
                connection, "claim:night-discharge:001", nested_offset=1
            )
            reverse_page = PORT.traverse_story_arc(
                connection, "story-arc:after-dark-river-pollution", nested_offset=1
            )
        finally:
            PORT.projected_relation_rows = original_relation_projection
            connection.close()
        assert forward_page["events"][0]["story_arcs"] == []
        assert forward_page["events"][0]["story_arc_page"]["offset"] == 1
        assert reverse_page["events"][0]["claims"] == []
        assert reverse_page["events"][0]["claim_page"]["offset"] == 1

        incompatible_db = tmp / "incompatible" / "knowledge.sqlite"
        authorized_commit(incompatible_db, base)
        raw = sqlite3.connect(incompatible_db)
        try:
            raw.execute("PRAGMA user_version = 99")
            raw.commit()
        finally:
            raw.close()
        must_reject(
            lambda: PORT.open_existing_database(incompatible_db),
            "unsupported database schema version",
        )

        hardlink_db = tmp / "hardlink" / "knowledge.sqlite"
        authorized_commit(hardlink_db, base)
        linked_path = hardlink_db.with_name("knowledge-linked.sqlite")
        os.link(hardlink_db, linked_path)
        must_reject(
            lambda: PORT.open_existing_database(hardlink_db),
            "not hard-linked",
        )
        must_reject(
            lambda: authorized_commit(hardlink_db, base),
            "not hard-linked",
        )

        scale_db = tmp / "scale" / "knowledge.sqlite"
        scale = copy.deepcopy(base)
        scale["batch_id"] = "batch:river-monitoring:scale"
        scale["idempotency_key"] = "7" * 64
        scale["events"] = []
        scale["story_arcs"] = []
        scale["claim_event_memberships"] = []
        scale["event_story_arc_memberships"] = []
        source_decision = copy.deepcopy(scale["review_decisions"][0])
        scale["review_decisions"] = [source_decision]
        scale["claims"] = []
        for index in range(250):
            claim = copy.deepcopy(base["claims"][0])
            claim["id"] = f"claim:scale:{index:04d}"
            claim["origin"]["finding_id"] = f"F{index:04d}"
            claim["origin"]["finding_fingerprint"] = f"{index:064x}"
            claim["origin"].pop("legacy_claim_id", None)
            claim["proposition"] = f"Scale fixture proposition {index}."
            claim["event_link_disposition"] = "pending"
            claim["source_expression_refs"] = []
            decision_id = f"decision:claim:scale:{index:04d}"
            claim["review_decision_id"] = decision_id
            decision = {
                "id": decision_id,
                "reviewer_id": "journalist:fixture",
                "decided_at": "2026-08-18T10:00:00Z",
                "disposition": "approved",
                "rationale": "Approved scale fixture record.",
                "subject": {
                    "kind": "claim",
                    "id": claim["id"],
                    "version": 1,
                    "payload_sha256": PORT.payload_sha256(claim),
                },
            }
            scale["claims"].append(claim)
            scale["review_decisions"].append(decision)
        authorized_commit(scale_db, scale)
        connection = PORT.open_existing_database(scale_db)
        try:
            original_projected_rows = PORT.projected_rows
            PORT.projected_rows = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("coverage must not materialize full projected tables")
            )
            try:
                scaled_coverage = PORT.coverage_report(connection, limit=25)
            finally:
                PORT.projected_rows = original_projected_rows
        finally:
            connection.close()
        assert scaled_coverage["summary"]["approved_claims"] == 250
        assert len(scaled_coverage["claims"]) == 25
        assert scaled_coverage["page"]["truncated"] is True

    print("ok   duplicate JSON keys are rejected")
    print("ok   database paths are contained and symlinks are rejected")
    print("ok   database permissions are owner-only")
    print("ok   schema v1 migrates additively and backfills projection intent")
    print("ok   stable identities cannot be retargeted")
    print("ok   the receipt signer authenticates every embedded decision")
    print("ok   link dispositions cannot contradict canonical edges")
    print("ok   candidate versions do not suppress approved projections")
    print("ok   candidates cannot resurrect rejected canonical versions")
    print("ok   candidate endpoint chains are visible only in review traversal")
    print("ok   failed batches roll back every table")
    print("ok   canonical forward/reverse relation pages are SQL-bounded")
    print("ok   incompatible database schemas are rejected")
    print("ok   hard-linked databases are rejected on read and commit")
    print("ok   SQL coverage aggregation avoids full Python materialization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
