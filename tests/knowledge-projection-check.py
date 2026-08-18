#!/usr/bin/env python3
"""Focused U5 deterministic projection checks."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import knowledge_projection as kp  # noqa: E402
import spotlight_safe as safe  # noqa: E402


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fixture() -> dict:
    claims = [{"id": f"claim:c{i:03d}", "version": 1, "status": "approved", "proposition": f"Normal sentence {i}. It remains readable.", "origin": {"project": "projection-fixture", "finding_id": f"f{i}", "finding_fingerprint": sha(str(i))}, "provenance": {"actor": "journalist:fixture", "method": "journalist", "recorded_at": "2026-08-18T00:00:00Z"}, "source_expression_refs": [{"project": "projection-fixture", "expression_id": f"SX{i}", "expression_fingerprint": sha("expression-" + str(i)), "relation": "supports"}]} for i in range(100)]
    events = [{"id": f"event:e{i}", "version": 1, "status": "approved", "label": f"Event {i}", "core": {"actors": ["Reporter"], "action": "reported", "object": f"development {i}", "place": "Zurich", "time": "2026-08-18"}} for i in range(3)]
    ce = [{"id": f"relation:claim-event:r{i:03d}", "version": 1, "status": "approved", "claim": {"id": c["id"], "version": 1}, "event": {"id": events[i % 3]["id"], "version": 1}, "relation": "supports"} for i, c in enumerate(claims)]
    story = {"id": "story-arc:one", "version": 1, "status": "approved", "title": "One story", "description": "A normal story description."}
    es = [{"id": f"relation:event-story:r{i}", "version": 1, "status": "approved", "event": {"id": e["id"], "version": 1}, "story_arc": {"id": story["id"], "version": 1}, "role": "part_of"} for i, e in enumerate(events)]
    records = []
    for kind, values in (("claim", claims), ("event", events), ("story_arc", [story]), ("claim_event_membership", ce), ("event_story_arc_membership", es)):
        records += [{"kind": kind, "id": x["id"], "version": 1, "payload_sha256": kp.digest(x)} for x in values]
    return {
        "job": {"job_id": "job:test", "case_id": "case:projection-fixture", "destination_id": "destination:local", "generation": 1, "desired_projection_set_sha256": sha("desired"), "created_at": "2026-08-18T00:00:00Z"},
        "policy": {"receipt_id": "receipt:policy", "policy_revision": 1, "status": "active", "classification": "internal", "allowed_destinations": ["destination:local"], "issued_at": "2020-01-01T00:00:00Z", "expires_at": "9999-01-01T00:00:00Z"},
        "batch": {"source_case": {"project": "projection-fixture"}},
        "graph_ref": {"receipt_id": "receipt:graph", "commit_sha256": sha("graph"), "snapshot_at": "2026-08-18T00:00:00Z"},
        "records": sorted(records, key=lambda x: (x["kind"], x["id"])),
        "signed_case": {"provenance_revision": 1, "provenance_receipt_id": "receipt:case", "provenance_sha256": sha("case"), "artifacts": [{"path": "data/findings.json", "role": "findings", "sha256": sha("findings")}]},
        "case_policy": {"receipt_id": "receipt:policy", "policy_revision": 1, "receipt_sha256": sha("policy")},
        "graph": {"claims": claims, "events": events, "story_arcs": [story], "claim_event_memberships": ce, "event_story_arc_memberships": es},
        "previous_binding": None,
    }


def preconditions(snapshot: dict) -> dict:
    path = "investigations/projection-fixture.md"
    story = next(page["path"] for page in kp.render_pages(snapshot) if page["kind"] == "story_page")
    return {
        path: {"expected_version": sha("existing"), "expected_outside_sha256": sha("outside"), "expected_managed_sha256": sha("managed")},
        story: {"expected_version": "absent", "expected_outside_sha256": kp.EMPTY_SHA256},
    }


def receipt(destination: str, operations: list[dict]) -> dict:
    value = {"schema_version": "spotlight-workspace-final-receipt/v1", "receipt_id": "", "package_sha256": sha("package"), "desired_projection_set_sha256": sha("desired-old"), "case_id": "case:projection-fixture", "classification": "internal", "destination_id": destination, "graph_receipt_id": "receipt:graph", "operations": operations}
    value["receipt_id"] = "receipt:" + hashlib.sha256(kp.canonical_bytes(value)).hexdigest()
    return value


def local_activation(destination: str = "destination:local") -> dict:
    return {
        "schema_version": "spotlight-knowledge-activation/v1", "status": "active",
        "assurance": "local_conformance", "destination_id": destination,
        "project_id": "local:/tmp/workspace", "namespace": "spotlight",
        "projection_namespace": "investigations", "story_namespace": "stories",
        "graph_database_path": "/tmp/workspace/.knowledge-workspace/spotlight.sqlite",
        "provider_policy_sha256": sha("provider"),
        "workflow_migration_receipt": {"path": ".knowledge-workspace/spotlight-workflow-migration.json", "sha256": sha("migration")},
    }


class ProjectionChecks(unittest.TestCase):
    def test_receipt_identity_matches_spotlight_golden(self):
        value = {
            "schema_version": "spotlight-workspace-final-receipt/v1", "receipt_id": "",
            "package_sha256": "a" * 64, "desired_projection_set_sha256": "b" * 64,
            "case_id": "case:alpha", "classification": "personal",
            "destination_id": "destination:newsroom", "graph_receipt_id": "receipt:graph-one",
            "operations": [{"operation_id": "operation:upsert-one", "kind": "managed_block_upsert", "path": "investigations/alpha.md", "owner_id": "owner:spotlight-alpha", "final_version": "c" * 64}],
        }
        self.assertEqual(hashlib.sha256(kp.canonical_bytes(value)).hexdigest(), "f490978dce2e72b00a04dc0c65d46af9eeceba7673ae0409bb94bcfe6dd330fe")

    def test_hundred_claims_one_block_one_story_and_determinism(self):
        source = fixture()
        first = kp.build_projection(source, preconditions(source))
        second = kp.build_projection(copy.deepcopy(source), preconditions(source))
        self.assertEqual(kp.canonical_bytes(first), kp.canonical_bytes(second))
        paths = [x["path"] for x in first["package"]["operations"]]
        self.assertEqual(sum(x.startswith("investigations/") for x in paths), 1)
        self.assertEqual(sum(x.startswith("stories/") for x in paths), 1)
        self.assertFalse(any("claims/" in x or "events/" in x for x in paths))
        self.assertIn("investigations/projection-fixture.md", paths)
        self.assertIn("Normal sentence 0. It remains readable.", first["package"]["operations"][0]["content"])
        self.assertIn("Events:", first["package"]["operations"][0]["content"])
        self.assertIn("Stories:", first["package"]["operations"][0]["content"])
        self.assertIn("Origin: `projection-fixture:f0`", first["package"]["operations"][0]["content"])
        self.assertIn("Source expressions: `projection-fixture:SX0`", first["package"]["operations"][0]["content"])
        self.assertIn("Provenance: journalist:fixture", first["package"]["operations"][0]["content"])
        self.assertEqual(len(first["manifest"]["graph"]["records"]), 207)

    def test_changed_claim_has_limited_content_diff(self):
        source = fixture(); before = kp.build_projection(source, preconditions(source))
        changed = copy.deepcopy(source); changed["graph"]["claims"][0]["proposition"] = "Changed sentence."
        after = kp.build_projection(changed, preconditions(changed))
        differing = [a["path"] for a, b in zip(before["package"]["operations"], after["package"]["operations"]) if a.get("content") != b.get("content")]
        self.assertEqual(len(differing), 2)

    def test_injection_control_traversal_and_oversize(self):
        source = fixture(); source["graph"]["claims"][0]["proposition"] = '<script>alert("x")</script> [link](javascript:x)'
        content = kp.build_projection(source, preconditions(source))["package"]["operations"][0]["content"]
        self.assertNotIn("<script>", content); self.assertNotIn("[link]", content)
        source["graph"]["claims"][0]["proposition"] = "bad\x00text"
        with self.assertRaises(kp.ProjectionError): kp.build_projection(source, preconditions(source))
        with self.assertRaises(safe.SafetyError): safe.projection_path("../stories", "story-arc:x")
        source = fixture(); source["graph"]["claims"][0]["proposition"] = "x" * 65537
        with self.assertRaises(kp.ProjectionError): kp.build_projection(source, preconditions(source))
        self.assertEqual(safe.markdown_text("Normal sentence. Still readable."), "Normal sentence. Still readable.")
        self.assertNotIn("\n#", safe.markdown_text("safe\n# injected heading"))

    def test_candidates_are_not_rendered(self):
        source = fixture(); source["graph"]["claims"].append({"id": "claim:candidate", "version": 1, "status": "candidate", "proposition": "MUST NOT APPEAR", "origin": {"project": "x", "finding_id": "x", "finding_fingerprint": sha("x")}})
        text = "".join(x.get("content", "") for x in kp.build_projection(source, preconditions(source))["package"]["operations"])
        self.assertNotIn("MUST NOT APPEAR", text)

    def test_case_graph_does_not_inherit_foreign_story_membership(self):
        source = fixture()
        batch = {"source_case": source["batch"]["source_case"], **source["graph"]}
        foreign_story = {"id": "story-arc:foreign", "version": 1, "status": "approved", "title": "Foreign", "description": "Not authorized here."}
        foreign_edge = {"id": "relation:event-story:foreign", "version": 1, "status": "approved", "event": {"id": "event:e0", "version": 1}, "story_arc": {"id": foreign_story["id"], "version": 1}, "role": "part_of"}
        projected = {name: list(values) for name, values in source["graph"].items()}
        projected["story_arcs"].append(foreign_story)
        projected["event_story_arc_memberships"].append(foreign_edge)

        def rows(_connection, table):
            return [{"payload_json": json.dumps(value)} for value in projected[table]]

        with mock.patch.object(kp.kd, "projected_rows", side_effect=rows):
            selected = kp.select_case_graph(object(), batch)
        self.assertNotIn(foreign_story["id"], {item["id"] for item in selected["story_arcs"]})
        self.assertNotIn(foreign_edge["id"], {item["id"] for item in selected["event_story_arc_memberships"]})

    def test_removal_only_from_verified_previous_receipt(self):
        source = fixture(); source["policy"]["status"] = "revoked"
        story_path = safe.projection_path("stories", "story-arc:old")
        owner = "owner:spotlight-" + safe.projection_slug(source["job"]["case_id"]) + "-" + Path(story_path).stem
        old = receipt("destination:local", [{"operation_id": "operation:old", "kind": "managed_block_upsert", "path": story_path, "owner_id": owner, "final_version": sha("old") }])
        source["previous_binding"] = {"workspace_receipt_ref": old["receipt_id"], "workspace_receipt_sha256": kp.digest(old)}
        with self.assertRaises(kp.ProjectionError) as missing:
            kp.build_projection(source, preconditions(source), None)
        self.assertEqual(missing.exception.code, "workspace_receipt_required")
        built = kp.build_projection(source, preconditions(source), old)
        self.assertEqual([x["kind"] for x in built["package"]["operations"]], ["managed_block_upsert", "managed_page_removal"])
        self.assertIn("withdrawn", built["package"]["operations"][0]["content"])
        self.assertNotIn("deleted_hashes", built["package"]["operations"][1])
        tampered = copy.deepcopy(old); tampered["operations"][0]["path"] = "stories/other.md"
        with self.assertRaises(kp.ProjectionError): kp.build_projection(source, {}, tampered)

    def test_rendering_is_independent_of_wall_clock(self):
        source = fixture()
        source["policy"]["issued_at"] = "9998-01-01T00:00:00Z"
        source["policy"]["expires_at"] = "9999-01-01T00:00:00Z"
        built = kp.build_projection(source, preconditions(source))
        self.assertEqual(len(built["package"]["operations"]), 2)
        self.assertNotIn("withdrawn", built["package"]["operations"][0]["content"])

    def test_local_worker_requires_matching_activation_before_projection(self):
        with mock.patch.object(kp, "serialized_worker_lock", return_value=nullcontext()), mock.patch.object(kp, "_check_current", return_value={"destination_id": "destination:local"}), mock.patch.object(kp, "_workspace_prefix", return_value=(Path("/tmp/workspace"), "spotlight")), mock.patch.object(kp, "resolve_snapshot") as resolve:
            with self.assertRaises(kp.ProjectionError) as caught:
                kp.run_worker(Path("x"), "job:test", Path("x"), Path("x"), {}, None, None)
        self.assertEqual(caught.exception.code, "local_activation_invalid")
        resolve.assert_not_called()

    def test_worker_activation_binds_workspace_graph_and_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            metadata = root / ".knowledge-workspace"
            metadata.mkdir()
            migration = metadata / "spotlight-workflow-migration.json"
            migration.write_text('{"schema_version":"spotlight-workflow-migration/v1"}\n', encoding="utf-8")
            database = metadata / "spotlight.sqlite"
            database.touch()
            activation = local_activation()
            activation.update({
                "project_id": "local:" + str(root), "graph_database_path": str(database),
                "workflow_migration_receipt": {
                    "path": ".knowledge-workspace/spotlight-workflow-migration.json",
                    "sha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
                },
            })
            kp.validate_worker_activation(activation, "destination:local", root, database, "spotlight")
            activation["workflow_migration_receipt"]["sha256"] = "0" * 64
            with self.assertRaises(kp.ProjectionError):
                kp.validate_worker_activation(activation, "destination:local", root, database, "spotlight")

    def test_active_worker_lock_blocks_recovery_without_reclaim(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "knowledge.sqlite"
            database.touch()
            with kp.serialized_worker_lock(database):
                with self.assertRaises(kp.ProjectionError) as caught:
                    kp.run_worker(database, "job:test", Path(tmp), Path(tmp), {}, None, local_activation())
            self.assertEqual(caught.exception.code, "serialized_runner_busy")

    def test_stale_head_blocks_before_calls(self):
        source = fixture()
        with mock.patch.object(kp, "serialized_worker_lock", return_value=nullcontext()), mock.patch.object(kp, "resolve_snapshot", return_value=source), mock.patch.object(kp, "_check_current", side_effect=kp.ProjectionError("job_superseded", "stale")):
            with self.assertRaises(kp.ProjectionError): kp.run_worker(Path("x"), "job:test", Path("x"), Path("x"), preconditions(source), None, local_activation())

    def test_startup_and_operator_use_same_serialized_queue_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "knowledge.sqlite"
            kd = kp.kd
            kd.initialize_database(database, "destination:local")
            connection = kd.connect_database(database)
            try:
                job = kd.enqueue_projection_job(connection, "case:projection-fixture", "destination:local", "graph_commit", "receipt:graph", sha("graph"))
                connection.commit()
                job_id = job["job_id"]
            finally:
                connection.close()
            seen = []
            self.assertEqual(kp.startup_drain(database, lambda value: seen.append((value["job_id"], value["status"]))), [job_id])
            self.assertEqual(seen, [(job_id, "pending")])
            connection = kd.connect_database(database)
            try:
                kd.claim_projection_job_exact(connection, job_id)
            finally:
                connection.close()
            seen.clear()
            self.assertEqual(kp.operator_retry(database, lambda value: seen.append((value["job_id"], value["status"]))), [job_id])
            self.assertEqual(seen, [(job_id, "failed")])
            case_map = Path(tmp) / "case-map.json"
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            case_map.write_text(json.dumps({"case:projection-fixture": str(case_dir)}), encoding="utf-8")
            with mock.patch.object(kp, "run_worker", return_value={"status": "completed"}) as worker:
                rc = kp.main(["--database", str(database), "--root", tmp, "--drain-mode", "operator", "--case-map", str(case_map)])
            self.assertEqual(rc, 0)
            worker.assert_called_once()

    def test_source_hash_failure_has_zero_calls_and_marks_running_retryable(self):
        failures = []
        error = kp.ProjectionError("signed_case_hash_stale", "stale")
        with mock.patch.object(kp, "serialized_worker_lock", return_value=nullcontext()), mock.patch.object(kp, "resolve_snapshot", side_effect=error), mock.patch.object(kp, "_check_current", return_value={"destination_id": "destination:local"}), mock.patch.object(kp, "_workspace_prefix", return_value=(Path("/tmp/workspace"), "spotlight")), mock.patch.object(kp, "validate_worker_activation"), mock.patch.object(kp, "_mark_running_failed", side_effect=lambda *x: failures.append(x)):
            with self.assertRaises(kp.ProjectionError):
                kp.run_worker(Path("x"), "job:test", Path("x"), Path("x"), {}, None, local_activation())
        self.assertEqual(failures[0][1], "job:test")

    def test_ordinary_running_failure_transitions_to_failed(self):
        class Cursor:
            def fetchone(self): return {"status": "running"}
        class Connection:
            def execute(self, *args): return Cursor()
            def close(self): pass
        connection = Connection()
        with mock.patch.object(kp.kd, "connect_database", return_value=connection), mock.patch.object(kp.kd, "fail_projection_job") as fail:
            kp._mark_running_failed(Path("x"), "job:test", kp.ProjectionError("engine_call_failed", "failed"))
        fail.assert_called_once()
        self.assertIn("engine_call_failed", fail.call_args.args[2])

    def test_local_transaction_is_atomic_content_free_and_recoverable(self):
        source = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / ".knowledge-workspace"
            metadata.mkdir()
            (metadata / "routes.json").write_text(
                '{"schema_version":"knowledge-routes/v1","routes":{"spotlight_verified":"spotlight"}}\n',
                encoding="utf-8",
            )
            investigation = root / "spotlight" / "investigations" / "projection-fixture.md"
            investigation.parent.mkdir(parents=True)
            investigation.write_text("Reporter-owned notes.\n", encoding="utf-8")
            os.chmod(investigation, 0o640)
            inspections = [
                {"path": page["path"], "owner_id": page["owner_id"], "kind": page["kind"]}
                for page in kp.render_pages(source)
            ]
            with kp.workspace_projection_lock(root):
                prepared = kp.prepare_local_projection(root, inspections, None)
                built = kp.build_projection(source, prepared["preconditions"])
                stage = kp.stage_local_projection(built["package"])
                committed = kp.commit_local_projection(prepared["workspace"], prepared["prefix"], built["package"], stage)
            self.assertIn("Reporter-owned notes.", investigation.read_text(encoding="utf-8"))
            self.assertIn("<!-- spotlight-projection:block:v1", investigation.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(investigation.stat().st_mode), 0o640)
            story = root / "spotlight" / next(page["path"] for page in kp.render_pages(source) if page["kind"] == "story_page")
            self.assertEqual(stat.S_IMODE(story.stat().st_mode), 0o600)
            self.assertIn("<!-- spotlight-projection:page:v1", story.read_text(encoding="utf-8"))
            receipt_body = json.dumps(committed["receipt"], sort_keys=True)
            self.assertNotIn('"content"', receipt_body)
            self.assertEqual(kp.local_projection_status(root, source["job"]["desired_projection_set_sha256"])["state"], "committed")

            # A crash after workspace commit but before graph completion sees
            # changed files; recovery must reconstruct the original package CAS.
            with kp.workspace_projection_lock(root):
                fresh = kp.prepare_local_projection(root, inspections, None)
                recovered = kp.recorded_local_preconditions(root, source["job"]["desired_projection_set_sha256"], inspections)
                self.assertNotEqual(recovered, fresh["preconditions"])
                retry = kp.build_projection(source, recovered)
                retry_stage = kp.stage_local_projection(retry["package"])
                retried = kp.commit_local_projection(root, fresh["prefix"], retry["package"], retry_stage)
            self.assertEqual(retried["receipt"], committed["receipt"])

    def test_local_transaction_rejects_outside_change_and_lock_symlink(self):
        source = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / ".knowledge-workspace"
            metadata.mkdir()
            (metadata / "routes.json").write_text('{"schema_version":"knowledge-routes/v1","routes":{"spotlight_verified":"spotlight"}}', encoding="utf-8")
            investigation = root / "spotlight" / "investigations" / "projection-fixture.md"
            investigation.parent.mkdir(parents=True)
            investigation.write_text("Original.\n", encoding="utf-8")
            inspections = [{"path": page["path"], "owner_id": page["owner_id"], "kind": page["kind"]} for page in kp.render_pages(source)]
            prepared = kp.prepare_local_projection(root, inspections, None)
            built = kp.build_projection(source, prepared["preconditions"])
            stage = kp.stage_local_projection(built["package"])
            investigation.write_text("Changed by reporter.\n", encoding="utf-8")
            with kp.workspace_projection_lock(root):
                with self.assertRaises(kp.ProjectionError) as caught:
                    kp.commit_local_projection(root, prepared["prefix"], built["package"], stage)
            self.assertEqual(caught.exception.code, "workspace_conflict")
            lock = metadata / "projection.lock"
            lock.unlink()
            lock.symlink_to(root / "foreign-lock")
            with self.assertRaises(kp.ProjectionError) as linked:
                with kp.workspace_projection_lock(root):
                    pass
            self.assertEqual(linked.exception.code, "workspace_symlink_denied")

    def test_cases_root_discovers_reviewed_batches_and_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = root / "alpha"
            (case / "data").mkdir(parents=True)
            (case / "data" / "knowledge-batch.json").write_text("{}", encoding="utf-8")
            batch = {"source_case": {"project": "alpha"}}
            with mock.patch.object(kp.kd, "load_json_snapshot", return_value=(batch, sha("batch"))), mock.patch.object(kp.kd, "validate_batch", return_value=[]):
                self.assertEqual(kp.discover_case_directories(root), {"case:alpha": str(case.resolve())})
            duplicate = root / "case:alpha"
            (duplicate / "data").mkdir(parents=True)
            (duplicate / "data" / "knowledge-batch.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(kp.kd, "load_json_snapshot", return_value=(batch, sha("batch"))), mock.patch.object(kp.kd, "validate_batch", return_value=[]):
                with self.assertRaises(kp.ProjectionError) as caught:
                    kp.discover_case_directories(root)
            self.assertEqual(caught.exception.code, "queue_case_duplicate")

    def test_current_head_transaction_covers_workspace_commit_and_completion(self):
        source = fixture(); observed = []
        class Lock:
            in_transaction = False
            result = None
            def execute(self, sql, params=()):
                if sql == "BEGIN IMMEDIATE": self.in_transaction = True; return self
                self.result = {"job_id": "job:test"}; return self
            def fetchone(self): return self.result
            def commit(self): self.in_transaction = False
            def rollback(self): self.in_transaction = False
            def close(self): pass
        lock = Lock()
        def commit(_root, _prefix, package, stage):
            observed.append(lock.in_transaction)
            outcomes = [{"operation_id": x["operation_id"], "kind": x["kind"], "path": x["path"], "owner_id": x["owner_id"], "final_version": sha(x["operation_id"])} for x in package["operations"]]
            final = receipt("destination:local", outcomes)
            final["package_sha256"] = stage["package_sha256"]
            final["desired_projection_set_sha256"] = package["desired_projection_set_sha256"]
            final["receipt_id"] = ""
            final["receipt_id"] = "receipt:" + hashlib.sha256(kp.canonical_bytes(final)).hexdigest()
            return {"package_sha256": stage["package_sha256"], "receipt": final}
        prepared = {"workspace": Path("x"), "prefix": "", "preconditions": preconditions(source), "previous_receipt": None}
        with mock.patch.object(kp, "serialized_worker_lock", return_value=nullcontext()), mock.patch.object(kp, "resolve_snapshot", return_value=source), mock.patch.object(kp, "_check_current", return_value={"status": "pending", "destination_id": "destination:local"}), mock.patch.object(kp.kd, "connect_database", return_value=lock), mock.patch.object(kp.kd, "claim_projection_job_exact", return_value={"job_id": "job:test"}) as claim, mock.patch.object(kp.kd, "complete_projection_job", return_value={"status": "completed"}) as complete, mock.patch.object(kp, "_workspace_prefix", return_value=(Path("x"), "")), mock.patch.object(kp, "validate_worker_activation"), mock.patch.object(kp, "workspace_projection_lock", return_value=nullcontext()), mock.patch.object(kp, "prepare_local_projection", return_value=prepared), mock.patch.object(kp, "recorded_local_preconditions", return_value=None), mock.patch.object(kp, "commit_local_projection", side_effect=commit):
            kp.run_worker(Path("x"), "job:test", Path("x"), Path("x"), {}, None, local_activation())
        self.assertEqual(observed, [True])
        claim.assert_called_once_with(lock, "job:test")
        self.assertTrue(complete.call_args.kwargs["in_transaction"])

    def test_completed_current_job_replays_durable_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = receipt("destination:local", [])
            receipt_path = kp._receipt_path(root, final["receipt_id"])
            kp._atomic_json(root, receipt_path, final)
            binding = {
                "workspace_receipt_ref": final["receipt_id"],
                "workspace_receipt_sha256": kp.digest(final),
            }

            class Result:
                def execute(self, _sql, _params=()): return self
                def fetchone(self): return binding
                def close(self): pass

            current = {"job_id": "job:test", "status": "completed", "destination_id": "destination:local"}
            with mock.patch.object(kp, "serialized_worker_lock", return_value=nullcontext()), mock.patch.object(kp, "_check_current", return_value=current), mock.patch.object(kp, "_workspace_prefix", return_value=(root, "spotlight")), mock.patch.object(kp, "validate_worker_activation"), mock.patch.object(kp.kd, "open_existing_database", return_value=Result()):
                replayed = kp.run_worker(Path("graph.sqlite"), "job:test", Path("case"), root, {}, None, {})
            self.assertTrue(replayed["replayed"])
            self.assertEqual(replayed["workspace_receipt"], final)


if __name__ == "__main__":
    unittest.main(verbosity=2)
