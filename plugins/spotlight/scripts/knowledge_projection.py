#!/usr/bin/env python3
"""Deterministic, local-conformance knowledge projection worker.

The local transaction boundary lives here: Spotlight owns its graph/outbox and
the projection-owned Markdown bytes, while Open Knowledge owns indexing those
files. Production deliberately remains blocked until an independent destination
and trust boundary exist.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import knowledge_destination as kd
import spotlight_safe as safe


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
TABLE_KINDS = {
    "claims": "claim", "events": "event", "story_arcs": "story_arc",
    "claim_event_memberships": "claim_event_membership",
    "event_story_arc_memberships": "event_story_arc_membership",
}


class ProjectionError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _json(row: Any) -> dict[str, Any]:
    return json.loads(row["payload_json"])


def _latest_policy(connection: Any, case_id: str, destination_id: str) -> tuple[Any, dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM case_policy_receipts WHERE case_id=? AND destination_id=? "
        "ORDER BY policy_revision DESC LIMIT 1", (case_id, destination_id),
    ).fetchone()
    if row is None:
        raise ProjectionError("case_policy_missing", "no signed case policy exists")
    return row, _json(row)


def _batch_for_job(connection: Any, job: Any) -> tuple[Any, dict[str, Any]]:
    if job["source_kind"] == "graph_commit":
        row = connection.execute("SELECT * FROM batches WHERE batch_id=?", (job["source_ref"],)).fetchone()
        if row is None or row["payload_sha256"] != job["source_sha256"]:
            raise ProjectionError("graph_receipt_invalid", "job graph batch binding is stale")
        return row, _json(row)
    # Policy jobs do not point at a graph batch. Resolve the latest receipted
    # batch for the case independently and bind both sources in the manifest.
    rows = connection.execute("SELECT * FROM batches ORDER BY committed_at DESC, batch_id DESC").fetchall()
    for row in rows:
        batch = _json(row)
        if kd._case_id_for_project(batch["source_case"]["project"]) == job["case_id"]:
            return row, batch
    raise ProjectionError("graph_receipt_missing", "case policy has no receipted graph snapshot")


def select_case_graph(connection: Any, batch: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    project = batch["source_case"]["project"]
    batch_keys = {
        table: {(item["id"], item["version"]) for item in batch[table]}
        for table in TABLE_KINDS
    }
    selected: dict[str, list[dict[str, Any]]] = {}
    for table in TABLE_KINDS:
        values = []
        for row in kd.projected_rows(connection, table):
            payload = _json(row)
            if (payload["id"], payload["version"]) not in batch_keys[table]:
                continue
            if table == "claims" and payload.get("origin", {}).get("project") != project:
                continue
            values.append(payload)
        selected[table] = values
    claim_keys = {(x["id"], x["version"]) for x in selected["claims"]}
    ce = [x for x in selected["claim_event_memberships"] if (x["claim"]["id"], x["claim"]["version"]) in claim_keys]
    event_keys = {(x["event"]["id"], x["event"]["version"]) for x in ce}
    selected["events"] = [x for x in selected["events"] if (x["id"], x["version"]) in event_keys]
    selected["claim_event_memberships"] = ce
    es = [x for x in selected["event_story_arc_memberships"] if (x["event"]["id"], x["event"]["version"]) in event_keys]
    story_keys = {(x["story_arc"]["id"], x["story_arc"]["version"]) for x in es}
    selected["story_arcs"] = [x for x in selected["story_arcs"] if (x["id"], x["version"]) in story_keys]
    selected["event_story_arc_memberships"] = [x for x in es if (x["story_arc"]["id"], x["story_arc"]["version"]) in story_keys]
    return selected


def resolve_snapshot(database: Path, job_id: str, case_dir: Path) -> dict[str, Any]:
    connection = kd.open_existing_database(database)
    try:
        kd.verify_database(connection)
        job = connection.execute("SELECT * FROM projection_jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None:
            raise ProjectionError("job_missing", "projection job does not exist")
        head = connection.execute(
            "SELECT * FROM projection_heads WHERE case_id=? AND destination_id=?",
            (job["case_id"], job["destination_id"]),
        ).fetchone()
        if head is None or head["current_job_id"] != job_id or job["status"] == "superseded":
            raise ProjectionError("job_superseded", "projection job is not the current head")
        batch_row, batch = _batch_for_job(connection, job)
        manifest = json.loads(batch_row["review_manifest_json"])
        source = kd.verify_source_case(case_dir, batch)
        if source != manifest.get("source_snapshot"):
            raise ProjectionError("signed_case_hash_stale", "case artifacts no longer match the signed graph review")
        policy_row, policy = _latest_policy(connection, job["case_id"], job["destination_id"])
        if job["source_kind"] == "case_policy" and (
            policy["receipt_id"] != job["source_ref"] or policy_row["payload_sha256"] != job["source_sha256"]
        ):
            raise ProjectionError("case_policy_stale", "policy job is not bound to the current signed policy")

        # A case projection is an approved view of that case's exact batch, not
        # a traversal over every globally canonical relation sharing an event
        # ID. Restrict every kind to the versioned records explicitly reviewed
        # in this batch before following edges. This prevents a case from
        # inheriting another case's story membership through a shared event.
        selected = select_case_graph(connection, batch)

        artifacts = []
        roles = {"findings_sha256": ("data/findings.json", "findings"), "source_expressions_sha256": ("data/source-expressions.json", "source_expressions"), "case_contract_sha256": ("data/case-contract.json", "case_contract")}
        for key, value in sorted(source["artifact_hashes"].items()):
            if key not in roles:
                raise ProjectionError("signed_case_artifact_unknown", "verified case snapshot returned an artifact without an explicit path/role contract")
            path, role = roles[key]
            artifacts.append({"path": path, "role": role, "sha256": value})
        source_hash = digest(source)
        records = []
        for table, kind in TABLE_KINDS.items():
            for item in selected[table]:
                records.append({"kind": kind, "id": item["id"], "version": item["version"], "payload_sha256": kd.payload_sha256(item)})
        records.sort(key=lambda x: (x["kind"], x["id"], x["version"]))
        previous = connection.execute(
            "SELECT * FROM projection_final_receipts WHERE case_id=? AND destination_id=? AND generation<? "
            "ORDER BY generation DESC LIMIT 1", (job["case_id"], job["destination_id"], job["generation"]),
        ).fetchone()
        return {
            "job": dict(job), "policy": policy, "batch": batch, "records": records,
            "graph": selected,
            "graph_ref": {"receipt_id": "receipt:" + batch_row["payload_sha256"], "commit_sha256": batch_row["payload_sha256"], "snapshot_at": batch_row["committed_at"]},
            "signed_case": {"provenance_revision": 1, "provenance_receipt_id": "receipt:" + source_hash, "provenance_sha256": source_hash, "artifacts": artifacts},
            "case_policy": {"receipt_id": policy["receipt_id"], "policy_revision": policy["policy_revision"], "receipt_sha256": policy_row["payload_sha256"]},
            "previous_binding": dict(previous) if previous is not None else None,
        }
    finally:
        connection.close()


def _text(value: str, label: str) -> str:
    try:
        return safe.markdown_text(value, label)
    except safe.SafetyError as exc:
        raise ProjectionError("unsafe_projection_text", str(exc)) from exc


def render_pages(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    job, graph = snapshot["job"], snapshot["graph"]
    # Defense in depth: the verified resolver already returns canonical approved
    # versions, but the pure renderer still refuses non-approved material.
    claims = sorted((x for x in graph["claims"] if x.get("status") == "approved"), key=lambda x: x["id"])
    events = sorted((x for x in graph["events"] if x.get("status") == "approved"), key=lambda x: x["id"])
    stories = sorted((x for x in graph["story_arcs"] if x.get("status") == "approved"), key=lambda x: x["id"])
    ce = [x for x in graph["claim_event_memberships"] if x.get("status") == "approved"]
    es = [x for x in graph["event_story_arc_memberships"] if x.get("status") == "approved"]
    claim_anchors = {x["id"]: "claim-" + safe.projection_slug(x["id"]) for x in claims}
    event_anchors = {x["id"]: "event-" + safe.projection_slug(x["id"]) for x in events}
    event_by_id = {e["id"]: e for e in events}
    project = snapshot["batch"]["source_case"]["project"]
    try:
        safe.validate_slug(project)
    except safe.SafetyError as exc:
        raise ProjectionError("unsafe_investigation_path", "source project is not an exact safe investigation slug") from exc
    inv_path = f"investigations/{project}.md"
    lines = ["## Reviewed knowledge projection", "", f"Generation {job['generation']} · graph `{snapshot['graph_ref']['commit_sha256']}`", "", "### Contents", ""]
    for event in events:
        lines.append(f"- [{_text(event['label'], 'event label')}](#{event_anchors[event['id']]})")
    lines += ["", "### Claims", ""]
    story_by_id = {s["id"]: s for s in stories}
    case_story_root = "stories/" + safe.projection_slug(job["case_id"])
    story_paths = {s["id"]: case_story_root + "/" + Path(safe.projection_path("stories", s["id"])).name for s in stories}
    stories_by_event: dict[str, list[str]] = {e["id"]: [] for e in events}
    for rel in es:
        if rel["event"]["id"] in stories_by_event and rel["story_arc"]["id"] in story_by_id:
            stories_by_event[rel["event"]["id"]].append(rel["story_arc"]["id"])
    events_by_claim: dict[str, list[str]] = {c["id"]: [] for c in claims}
    for rel in ce:
        if rel["claim"]["id"] in events_by_claim and rel["event"]["id"] in event_anchors:
            events_by_claim[rel["claim"]["id"]].append(rel["event"]["id"])
    for claim in claims:
        event_ids = sorted(set(events_by_claim[claim["id"]]))
        story_ids = sorted({sid for eid in event_ids for sid in stories_by_event[eid]})
        event_links = ", ".join(f"[{_text(event_by_id[eid]['label'], 'event label')}](#{event_anchors[eid]})" for eid in event_ids) or "none"
        story_links = ", ".join(f"[{_text(story_by_id[sid]['title'], 'story title')}](../{story_paths[sid]})" for sid in story_ids) or "none"
        origin = claim["origin"]
        expressions = ", ".join(
            f"`{_text(ref['project'], 'expression project')}:{_text(ref['expression_id'], 'expression id')}` "
            f"({_text(ref['relation'], 'expression relation')}, `{ref['expression_fingerprint']}`)"
            for ref in sorted(claim["source_expression_refs"], key=lambda ref: (ref["project"], ref["expression_id"], ref["relation"]))
        ) or "none"
        provenance = claim["provenance"]
        lines += [
            f'<a id="{claim_anchors[claim["id"]]}"></a>',
            f"- **{_text(claim['proposition'], 'claim proposition')}**  ",
            f"  `{claim['id']}` · version {claim['version']} · Events: {event_links} · Stories: {story_links}  ",
            f"  Origin: `{_text(origin['project'], 'origin project')}:{_text(origin['finding_id'], 'origin finding id')}` · finding fingerprint `{origin['finding_fingerprint']}`  ",
            f"  Source expressions: {expressions}  ",
            f"  Provenance: {_text(provenance['actor'], 'provenance actor')} · {_text(provenance['method'], 'provenance method')} · {_text(provenance['recorded_at'], 'provenance time')}",
        ]
    lines += ["", "### Events", ""]
    for event in events:
        core = event["core"]
        lines += [f'<a id="{event_anchors[event["id"]]}"></a>', f"#### {_text(event['label'], 'event label')}", "", f"{_text(core['action'], 'event action')} — {_text(core['object'], 'event object')} · {_text(core['place'], 'event place')} · {_text(core['time'], 'event time')}", ""]
    pages = [{"path": inv_path, "kind": "investigation_block", "owner_id": "owner:spotlight-" + safe.projection_slug(job["case_id"]), "content": safe.bounded_content("\n".join(lines).rstrip() + "\n")}]
    claims_by_event: dict[str, list[dict[str, Any]]] = {e["id"]: [] for e in events}
    by_claim = {c["id"]: c for c in claims}
    for rel in ce:
        if rel["claim"]["id"] in by_claim and rel["event"]["id"] in claims_by_event:
            claims_by_event[rel["event"]["id"]].append(by_claim[rel["claim"]["id"]])
    for story in stories:
        story_events = sorted([event_by_id[r["event"]["id"]] for r in es if r["story_arc"]["id"] == story["id"]], key=lambda x: x["id"])
        body = [f"# {_text(story['title'], 'story title')}", "", _text(story["description"], "story description"), "", "## Contents", ""]
        for event in story_events:
            body.append(f"- [{_text(event['label'], 'event label')}](#{event_anchors[event['id']]})")
        for event in story_events:
            body += ["", f'<a id="{event_anchors[event["id"]]}"></a>', f"## {_text(event['label'], 'event label')}", ""]
            for claim in sorted(claims_by_event[event["id"]], key=lambda x: x["id"]):
                body.append(f"- [{_text(claim['proposition'], 'claim proposition')}](../../{inv_path}#{claim_anchors[claim['id']]})")
        pages.append({"path": story_paths[story["id"]], "kind": "story_page", "owner_id": "owner:spotlight-" + safe.projection_slug(job["case_id"]) + "-" + Path(story_paths[story["id"]]).stem, "content": safe.bounded_content("\n".join(body).rstrip() + "\n")})
    return pages


def verify_previous_receipt(receipt: dict[str, Any], destination_id: str, binding: dict[str, Any] | None = None) -> dict[str, Any]:
    if receipt.get("schema_version") != "spotlight-workspace-final-receipt/v1" or receipt.get("destination_id") != destination_id:
        raise ProjectionError("workspace_receipt_invalid", "previous receipt scope is invalid")
    required = {"schema_version", "receipt_id", "package_sha256", "desired_projection_set_sha256", "case_id", "classification", "destination_id", "graph_receipt_id", "operations"}
    if frozenset(receipt) != frozenset(required):
        raise ProjectionError("workspace_receipt_invalid", "workspace receipt has unexpected or missing fields")
    if not isinstance(receipt["operations"], list):
        raise ProjectionError("workspace_receipt_invalid", "workspace receipt inventories are invalid")
    claimed = receipt["receipt_id"]
    body = json.loads(json.dumps(receipt))
    body["receipt_id"] = ""
    # Receipt identity uses UTF-8, recursive lexicographic keys, compact
    # separators, and raw Unicode.
    expected = "receipt:" + hashlib.sha256(canonical_bytes(body)).hexdigest()
    if claimed != expected:
        raise ProjectionError("workspace_receipt_tampered", "previous workspace receipt ID is invalid")
    receipt_hash = digest(receipt)
    if binding is not None and (binding.get("workspace_receipt_ref") != claimed or binding.get("workspace_receipt_sha256") != receipt_hash):
        raise ProjectionError("workspace_receipt_unbound", "previous receipt is not the latest completed projection receipt")
    return receipt


def build_projection(snapshot: dict[str, Any], preconditions: dict[str, Any], previous_receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    job, policy = snapshot["job"], snapshot["policy"]
    binding = snapshot.get("previous_binding")
    prior = verify_previous_receipt(previous_receipt, job["destination_id"], binding) if previous_receipt and binding else None
    if binding and not previous_receipt:
        raise ProjectionError("workspace_receipt_required", "latest completed projection receipt is required for safe reconciliation")
    if previous_receipt and not binding:
        raise ProjectionError("workspace_receipt_unbound", "no prior completed projection binds this receipt")
    # Rendering is a pure function of the signed intent. Expiry is enforced at
    # query time; consulting the wall clock here would change bytes on replay.
    active = (
        policy["status"] == "active"
        and job["destination_id"] in policy["allowed_destinations"]
    )
    pages = render_pages(snapshot)
    if not active:
        pages = [pages[0] | {"content": "## Reviewed knowledge projection\n\nProjection withdrawn by the current signed case policy.\n"}]
    desired_paths = {p["path"] for p in pages}
    operations = []
    for page in pages:
        if page["kind"] == "investigation_block":
            cas = preconditions.get(page["path"])
            if not isinstance(cas, dict) or not all(k in cas for k in ("expected_version", "expected_outside_sha256")):
                raise ProjectionError("workspace_precondition_missing", "investigation CAS precondition is required")
            expected_version, outside = cas["expected_version"], cas["expected_outside_sha256"]
        else:
            cas = preconditions.get(page["path"])
            if not isinstance(cas, dict) or not all(k in cas for k in ("expected_version", "expected_outside_sha256")):
                raise ProjectionError("workspace_precondition_missing", "story CAS precondition is required")
            expected_version, outside = cas["expected_version"], cas["expected_outside_sha256"]
            if prior and expected_version != "absent":
                old = next((x for x in prior["operations"] if x.get("kind") == "managed_block_upsert" and x.get("path") == page["path"]), None)
                if old is None or old.get("owner_id") != page["owner_id"] or old.get("final_version") != expected_version:
                    raise ProjectionError("workspace_receipt_invalid", "prior story inventory does not match prepared ownership and version")
        op = {"operation_id": "operation:upsert:" + digest(page)[:32], "kind": "managed_block_upsert", "path": page["path"], "owner_id": page["owner_id"], "expected_version": expected_version, "expected_outside_sha256": outside, "desired_sha256": hashlib.sha256(page["content"].encode()).hexdigest(), "content": page["content"]}
        if page["kind"] == "story_page" and cas.get("expected_managed_sha256"):
            op["expected_managed_sha256"] = cas["expected_managed_sha256"]
        if page["kind"] == "investigation_block" and preconditions[page["path"]].get("expected_managed_sha256"):
            op["expected_managed_sha256"] = preconditions[page["path"]]["expected_managed_sha256"]
        operations.append(op)
    if prior:
        owner_prefix = "owner:spotlight-" + safe.projection_slug(job["case_id"]) + "-"
        for old in prior["operations"]:
            if old.get("kind") == "managed_block_upsert" and old.get("path", "").startswith("stories/") and old["path"] not in desired_paths:
                expected_owner = owner_prefix + Path(old["path"]).stem
                if old.get("owner_id") != expected_owner or not safe.PROJECTION_PATH_RE.fullmatch(old["path"]):
                    raise ProjectionError("workspace_receipt_invalid", "removal inventory is not owned by this exact case")
                version = old.get("final_version")
                if not isinstance(version, str) or len(version) != 64:
                    raise ProjectionError("workspace_receipt_invalid", "removal inventory lacks final version")
                operations.append({"operation_id": "operation:remove:" + digest(old)[:32], "kind": "managed_page_removal", "path": old["path"], "owner_id": old["owner_id"], "expected_version": version, "deleted_sha256": version})
    operations.sort(key=lambda x: (x["path"], x["kind"], x["operation_id"]))
    if not operations:
        raise ProjectionError("no_projection_operations", "projection has no safe workspace operation")
    page_refs = [{"path": p["path"], "kind": p["kind"], "owner_id": p["owner_id"], "content_sha256": hashlib.sha256(p["content"].encode()).hexdigest()} for p in pages]
    # U2's signed current-head intent is authoritative. It already changes for a
    # new graph commit or policy generation; page hashes below bind exact output.
    desired = job["desired_projection_set_sha256"]
    manifest = {"schema_version": "spotlight-projection-manifest/v1", "manifest_id": "manifest:" + desired, "case_id": job["case_id"], "destination_id": job["destination_id"], "classification": policy["classification"], "generation": job["generation"], "created_at": job["created_at"], "graph": {**snapshot["graph_ref"], "records": snapshot["records"]}, "signed_case": snapshot["signed_case"], "case_policy": snapshot["case_policy"], "desired_projection_set_sha256": desired, "pages": page_refs}
    package = {"schema_version": "spotlight-workspace-projection-package/v1", "package_id": "package:" + desired, "idempotency_key": desired, "case_id": job["case_id"], "classification": policy["classification"], "destination_id": job["destination_id"], "graph_receipt_id": snapshot["graph_ref"]["receipt_id"], "desired_projection_set_sha256": desired, "operations": operations}
    return {"manifest": manifest, "package": package}


OWNER_RE = re.compile(r"^owner:[a-z0-9][a-z0-9._:-]{0,248}$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
JOURNAL_SCHEMA = "spotlight-projection-journal/v1"


def _bytes_sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _managed_markers(kind: str, owner: str) -> tuple[str, str]:
    if not OWNER_RE.fullmatch(owner):
        raise ProjectionError("workspace_owner_invalid", "managed owner is invalid")
    return (
        f'<!-- spotlight-projection:{kind}:v1 owner="{owner}" begin -->',
        f'<!-- spotlight-projection:{kind}:v1 owner="{owner}" end -->',
    )


def _render_managed(kind: str, owner: str, content: str) -> str:
    begin, end = _managed_markers(kind, owner)
    if content and not content.endswith("\n"):
        content += "\n"
    suffix = "\n" if kind == "page" else ""
    return begin + "\n" + content + end + suffix


def _parse_block(body: str, owner: str) -> dict[str, Any]:
    begin, end = _managed_markers("block", owner)
    namespace = "<!-- spotlight-projection:block:v1 "
    count = body.count(namespace)
    if count == 0:
        return {"found": False, "prefix": body, "suffix": "", "managed_sha256": "", "outside_sha256": _bytes_sha(body.encode())}
    if count != 2 or body.count(begin) != 1 or body.count(end) != 1:
        raise ProjectionError("workspace_marker_invalid", "duplicate, malformed, or foreign managed-block marker")
    start, end_start = body.find(begin), body.find(end)
    if start < 0 or end_start <= start:
        raise ProjectionError("workspace_marker_invalid", "managed-block markers are out of order")
    finish = end_start + len(end)
    prefix, suffix = body[:start], body[finish:]
    managed = body[start:finish]
    return {"found": True, "prefix": prefix, "suffix": suffix, "managed_sha256": _bytes_sha(managed.encode()), "outside_sha256": _bytes_sha((prefix + suffix).encode())}


def _validate_page(body: str, owner: str) -> None:
    begin, end = _managed_markers("page", owner)
    namespace = "<!-- spotlight-projection:page:v1 "
    if body.count(namespace) != 2 or body.count(begin) != 1 or body.count(end) != 1 or not body.startswith(begin + "\n"):
        raise ProjectionError("workspace_marker_invalid", "story page is not fully owned by this projection")
    end_start = body.rfind(end)
    trailing = body[end_start + len(end):] if end_start >= 0 else "invalid"
    if end_start < len(begin) + 1 or trailing not in {"", "\n"}:
        raise ProjectionError("workspace_marker_invalid", "story page contains bytes outside its ownership markers")


def _workspace_prefix(root: Path) -> tuple[Path, str]:
    try:
        workspace = root.expanduser().resolve(strict=True)
        route_path = workspace / ".knowledge-workspace" / "routes.json"
        raw, _ = _read_regular(workspace, route_path)
        routes = json.loads(raw)
        prefix = routes["routes"]["spotlight_verified"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProjectionError("workspace_route_invalid", "sealed Spotlight workspace route is unavailable") from exc
    if routes.get("schema_version") != "knowledge-routes/v1" or not isinstance(prefix, str):
        raise ProjectionError("workspace_route_invalid", "sealed Spotlight workspace route is invalid")
    clean = Path(prefix)
    if clean.is_absolute() or str(clean) in {"", ".", ".."} or ".." in clean.parts:
        raise ProjectionError("workspace_route_invalid", "sealed Spotlight workspace route escapes the project")
    return workspace, clean.as_posix()


def _reject_symlinks(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ProjectionError("workspace_path_invalid", "projection path escapes the workspace") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ProjectionError("workspace_symlink_denied", "projection paths cannot traverse symlinks")


def _workspace_target(root: Path, prefix: str, relative: str) -> Path:
    if not safe.PROJECTION_PATH_RE.fullmatch(relative) or ".." in Path(relative).parts:
        raise ProjectionError("workspace_path_invalid", "projection path is outside investigations/stories")
    target = root / prefix / relative
    _reject_symlinks(root, target)
    return target


def _read_regular(root: Path, target: Path) -> tuple[bytes, int]:
    _reject_symlinks(root, target)
    info = os.lstat(target)
    if not stat.S_ISREG(info.st_mode):
        raise ProjectionError("workspace_target_invalid", "projection target must be a regular file")
    return target.read_bytes(), stat.S_IMODE(info.st_mode)


def _ensure_parent(root: Path, target: Path) -> None:
    _reject_symlinks(root, target.parent)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlinks(root, target.parent)


def _atomic_write(root: Path, target: Path, body: bytes, mode: int) -> None:
    _ensure_parent(root, target)
    descriptor, temporary = tempfile.mkstemp(prefix=".spotlight-projection-", dir=target.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_json(root: Path, target: Path, value: dict[str, Any]) -> None:
    _atomic_write(root, target, canonical_bytes(value) + b"\n", 0o600)


def _journal_path(root: Path, idempotency_key: str) -> Path:
    return root / ".knowledge-workspace" / "projection-journals" / (_safe_key(idempotency_key) + ".json")


def _receipt_path(root: Path, receipt_id: str) -> Path:
    return root / ".knowledge-workspace" / "projection-receipts" / (_safe_key(receipt_id) + ".json")


@contextmanager
def workspace_projection_lock(root: Path):
    lock_path = root / ".knowledge-workspace" / "projection.lock"
    _ensure_parent(root, lock_path)
    _reject_symlinks(root, lock_path)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProjectionError("workspace_busy", "another local workspace projection is active") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def prepare_local_projection(root: Path, inspections: list[dict[str, str]], previous_binding: dict[str, Any] | None) -> dict[str, Any]:
    workspace, prefix = _workspace_prefix(root)
    prepared: dict[str, dict[str, str]] = {}
    for inspection in inspections:
        relative, owner, kind = inspection.get("path"), inspection.get("owner_id"), inspection.get("kind")
        if not isinstance(relative, str) or relative in prepared or not isinstance(owner, str):
            raise ProjectionError("workspace_inspection_invalid", "projection inspections require unique typed paths")
        target = _workspace_target(workspace, prefix, relative)
        if kind == "investigation_block":
            try:
                raw, _ = _read_regular(workspace, target)
            except FileNotFoundError as exc:
                raise ProjectionError("workspace_target_missing", "investigation page must exist before projection") from exc
            parsed = _parse_block(raw.decode("utf-8"), owner)
            prepared[relative] = {"expected_version": _bytes_sha(raw), "expected_outside_sha256": parsed["outside_sha256"]}
            if parsed["managed_sha256"]:
                prepared[relative]["expected_managed_sha256"] = parsed["managed_sha256"]
        elif kind == "story_page":
            try:
                raw, _ = _read_regular(workspace, target)
            except FileNotFoundError:
                prepared[relative] = {"expected_version": "absent", "expected_outside_sha256": EMPTY_SHA256}
            else:
                body = raw.decode("utf-8")
                _validate_page(body, owner)
                prepared[relative] = {"expected_version": _bytes_sha(raw), "expected_outside_sha256": EMPTY_SHA256, "expected_managed_sha256": _bytes_sha(raw)}
        else:
            raise ProjectionError("workspace_inspection_invalid", "projection inspection kind is unsupported")
    prior = None
    if previous_binding:
        receipt_id = previous_binding.get("workspace_receipt_ref")
        receipt_sha = previous_binding.get("workspace_receipt_sha256")
        if not isinstance(receipt_id, str) or not SHA_RE.fullmatch(str(receipt_sha)):
            raise ProjectionError("workspace_receipt_unbound", "previous receipt binding is invalid")
        path = _receipt_path(workspace, receipt_id)
        try:
            raw, _ = _read_regular(workspace, path)
            prior = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionError("workspace_receipt_required", "bound previous workspace receipt is unavailable") from exc
        verify_previous_receipt(prior, prior.get("destination_id", ""), previous_binding)
    return {"workspace": workspace, "prefix": prefix, "preconditions": prepared, "previous_receipt": prior}


def recorded_local_preconditions(root: Path, idempotency_key: str, inspections: list[dict[str, str]]) -> dict[str, Any] | None:
    """Recover the exact CAS inputs for an interrupted idempotent package.

    The journal deliberately stores only hashes, paths, and ownership metadata;
    rendered Markdown remains reconstructible from the graph snapshot.
    """
    if not SHA_RE.fullmatch(idempotency_key):
        raise ProjectionError("workspace_journal_invalid", "projection recovery key is invalid")
    path = _journal_path(root, idempotency_key)
    _reject_symlinks(root, path)
    try:
        raw, _ = _read_regular(root, path)
    except FileNotFoundError:
        return None
    try:
        journal = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectionError("workspace_journal_invalid", "projection recovery journal is unreadable") from exc
    if journal.get("schema_version") != JOURNAL_SCHEMA or journal.get("idempotency_key") != idempotency_key:
        raise ProjectionError("workspace_journal_invalid", "projection recovery journal binding is invalid")
    expected = {
        item["path"]: (item["owner_id"], item["kind"])
        for item in inspections
        if item.get("kind") in {"investigation_block", "story_page"}
    }
    recorded_operations = {
        item.get("path"): (item.get("owner_id"), item.get("kind"))
        for item in journal.get("operations", [])
        if item.get("kind") == "managed_block_upsert"
    }
    # Journal operations use workspace operation names while inspections use
    # renderer names. Both must bind every currently rendered path and owner.
    normalized = {path: (owner, "story_page" if path.startswith("stories/") else "investigation_block") for path, (owner, _kind) in recorded_operations.items()}
    if normalized != expected:
        raise ProjectionError("workspace_journal_conflict", "projection recovery journal ownership differs from the rendered pages")
    recovered = journal.get("prepared_preconditions")
    if not isinstance(recovered, dict) or set(recovered) != set(expected):
        raise ProjectionError("workspace_journal_invalid", "projection recovery journal lacks exact prepared preconditions")
    for relative, values in recovered.items():
        if not isinstance(values, dict):
            raise ProjectionError("workspace_journal_invalid", "projection recovery preconditions are invalid")
        version = values.get("expected_version")
        outside = values.get("expected_outside_sha256")
        managed = values.get("expected_managed_sha256")
        if (version != "absent" and not SHA_RE.fullmatch(str(version))) or not SHA_RE.fullmatch(str(outside)) or (managed is not None and not SHA_RE.fullmatch(str(managed))):
            raise ProjectionError("workspace_journal_invalid", "projection recovery precondition hashes are invalid")
        if relative.startswith("investigations/") and version == "absent":
            raise ProjectionError("workspace_journal_invalid", "investigation recovery cannot use an absent version")
    return recovered


def stage_local_projection(package: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "package_id", "idempotency_key", "case_id", "classification", "destination_id", "graph_receipt_id", "desired_projection_set_sha256", "operations"}
    desired = str(package.get("desired_projection_set_sha256", ""))
    if (
        frozenset(package) != frozenset(required)
        or package.get("schema_version") != "spotlight-workspace-projection-package/v1"
        or not SHA_RE.fullmatch(str(package.get("idempotency_key", "")))
        or package.get("idempotency_key") != desired
        or not SHA_RE.fullmatch(desired)
        or package.get("package_id") != "package:" + desired
        or not re.fullmatch(r"case:[a-z0-9][a-z0-9._:-]{0,250}", str(package.get("case_id", "")))
        or package.get("classification") not in {"shareable", "internal", "personal"}
        or not re.fullmatch(r"destination:[a-z0-9][a-z0-9._:-]{0,243}", str(package.get("destination_id", "")))
        or not re.fullmatch(r"receipt:[a-z0-9][a-z0-9._:-]{0,247}", str(package.get("graph_receipt_id", "")))
    ):
        raise ProjectionError("workspace_package_invalid", "projection package identity is invalid")
    operations = package.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ProjectionError("workspace_package_invalid", "projection package requires operations")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for operation in operations:
        operation_id, relative, owner = operation.get("operation_id"), operation.get("path"), operation.get("owner_id")
        if not isinstance(operation_id, str) or operation_id in seen_ids or not isinstance(relative, str) or relative in seen_paths or not OWNER_RE.fullmatch(str(owner)) or not safe.PROJECTION_PATH_RE.fullmatch(relative):
            raise ProjectionError("workspace_package_invalid", "projection operations require unique safe identities and paths")
        seen_ids.add(operation_id); seen_paths.add(relative)
        if operation.get("kind") == "managed_block_upsert":
            allowed = {"operation_id", "kind", "path", "owner_id", "expected_version", "expected_outside_sha256", "expected_managed_sha256", "desired_sha256", "content"}
            required_upsert = allowed - {"expected_managed_sha256"}
            content = operation.get("content")
            expected = operation.get("expected_version")
            managed = operation.get("expected_managed_sha256")
            if (
                not isinstance(content, str)
                or _bytes_sha(content.encode()) != operation.get("desired_sha256")
                or not SHA_RE.fullmatch(str(operation.get("expected_outside_sha256", "")))
                or (expected != "absent" and not SHA_RE.fullmatch(str(expected)))
                or (managed is not None and not SHA_RE.fullmatch(str(managed)))
                or (relative.startswith("investigations/") and expected == "absent")
                or not set(operation).issubset(allowed)
                or not required_upsert.issubset(operation)
            ):
                raise ProjectionError("workspace_package_invalid", "managed upsert hashes are invalid")
        elif operation.get("kind") == "managed_page_removal":
            required_removal = {"operation_id", "kind", "path", "owner_id", "expected_version", "deleted_sha256"}
            if frozenset(operation) != frozenset(required_removal) or not relative.startswith("stories/") or operation.get("deleted_sha256") != operation.get("expected_version") or not SHA_RE.fullmatch(str(operation.get("expected_version", ""))):
                raise ProjectionError("workspace_package_invalid", "managed removal identity is invalid")
        else:
            raise ProjectionError("workspace_package_invalid", "projection operation kind is unsupported")
    return {"package_sha256": digest(package), "desired_projection_set_sha256": package["desired_projection_set_sha256"], "operations": len(operations)}


def _load_or_create_journal(root: Path, package: dict[str, Any], stage: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    path = _journal_path(root, package["idempotency_key"])
    _reject_symlinks(root, path)
    if path.exists():
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionError("workspace_journal_invalid", "projection journal is unreadable") from exc
        if journal.get("schema_version") != JOURNAL_SCHEMA or journal.get("package_sha256") != stage["package_sha256"] or journal.get("desired_projection_set_sha256") != package["desired_projection_set_sha256"] or journal.get("case_id") != package["case_id"] or journal.get("destination_id") != package["destination_id"] or journal.get("graph_receipt_id") != package["graph_receipt_id"]:
            raise ProjectionError("workspace_journal_conflict", "projection idempotency key belongs to another package")
        expected = [(op["operation_id"], digest(op)) for op in package["operations"]]
        actual = [(op.get("operation_id"), op.get("operation_sha256")) for op in journal.get("operations", [])]
        if actual != expected:
            raise ProjectionError("workspace_journal_conflict", "projection journal operations differ from the package")
        return path, journal
    now = _now()
    journal = {
        "schema_version": JOURNAL_SCHEMA, "idempotency_key": package["idempotency_key"],
        "package_sha256": stage["package_sha256"], "desired_projection_set_sha256": package["desired_projection_set_sha256"],
        "case_id": package["case_id"], "classification": package["classification"], "destination_id": package["destination_id"],
        "graph_receipt_id": package["graph_receipt_id"], "state": "staged", "created_at": now, "updated_at": now,
        "prepared_preconditions": {
            op["path"]: {
                key: op[key]
                for key in ("expected_version", "expected_outside_sha256", "expected_managed_sha256")
                if key in op
            }
            for op in package["operations"]
            if op["kind"] == "managed_block_upsert"
        },
        "operations": [
            {
                "operation_id": op["operation_id"], "operation_sha256": digest(op),
                "path": op["path"], "owner_id": op["owner_id"], "kind": op["kind"],
                "state": "pending",
            }
            for op in package["operations"]
        ],
    }
    _atomic_json(root, path, journal)
    return path, journal


def _save_journal(root: Path, path: Path, journal: dict[str, Any], state: str) -> None:
    journal["state"], journal["updated_at"] = state, _now()
    _atomic_json(root, path, journal)


def _commit_upsert(root: Path, prefix: str, journal_path: Path, journal: dict[str, Any], entry: dict[str, Any], operation: dict[str, Any]) -> None:
    target = _workspace_target(root, prefix, operation["path"])
    is_story = operation["path"].startswith("stories/")
    marker_kind = "page" if is_story else "block"
    desired_managed = _render_managed(marker_kind, operation["owner_id"], operation["content"])
    try:
        current, mode = _read_regular(root, target)
    except FileNotFoundError:
        current, mode = None, 0o600
    if is_story:
        desired = desired_managed.encode()
        if current == desired:
            entry.update(state="complete", result_version=_bytes_sha(desired))
            _save_journal(root, journal_path, journal, "reconciling")
            return
        if entry["state"] == "pending":
            if current is None:
                if operation["expected_version"] != "absent" or operation.get("expected_managed_sha256") or operation["expected_outside_sha256"] != EMPTY_SHA256:
                    raise ProjectionError("workspace_conflict", "story creation no longer matches the absent checkpoint")
                entry["before_version"] = "absent"
            else:
                _validate_page(current.decode("utf-8"), operation["owner_id"])
                version = _bytes_sha(current)
                if version != operation["expected_version"] or version != operation.get("expected_managed_sha256") or operation["expected_outside_sha256"] != EMPTY_SHA256:
                    raise ProjectionError("workspace_conflict", "managed story version changed")
                entry["before_version"], entry["managed_sha256"] = version, version
            entry["outside_sha256"], entry["state"] = EMPTY_SHA256, "checkpointed"
            _save_journal(root, journal_path, journal, "checkpointed")
        if entry["state"] == "checkpointed":
            try:
                latest, mode = _read_regular(root, target)
            except FileNotFoundError:
                latest, mode = None, 0o600
            if latest == desired:
                entry.update(state="complete", result_version=_bytes_sha(desired))
                _save_journal(root, journal_path, journal, "reconciling")
                return
            if entry["before_version"] == "absent":
                if latest is not None:
                    raise ProjectionError("workspace_conflict", "foreign story appeared after checkpoint")
            elif latest is None or _bytes_sha(latest) != entry["before_version"]:
                raise ProjectionError("workspace_conflict", "managed story changed after checkpoint")
            _atomic_write(root, target, desired, mode)
            entry["state"] = "mutated"
            _save_journal(root, journal_path, journal, "mutating")
    else:
        if current is None:
            raise ProjectionError("workspace_target_missing", "investigation page must exist before projection")
        parsed = _parse_block(current.decode("utf-8"), operation["owner_id"])
        desired = (parsed["prefix"] + desired_managed + parsed["suffix"]).encode()
        if current == desired and parsed["outside_sha256"] == operation["expected_outside_sha256"]:
            entry.update(state="complete", result_version=_bytes_sha(current))
            _save_journal(root, journal_path, journal, "reconciling")
            return
        if entry["state"] == "pending":
            if _bytes_sha(current) != operation["expected_version"] or parsed["outside_sha256"] != operation["expected_outside_sha256"] or parsed["managed_sha256"] != operation.get("expected_managed_sha256", ""):
                raise ProjectionError("workspace_conflict", "investigation version, managed bytes, or outside bytes changed")
            entry.update(before_version=_bytes_sha(current), outside_sha256=parsed["outside_sha256"], managed_sha256=parsed["managed_sha256"], state="checkpointed")
            _save_journal(root, journal_path, journal, "checkpointed")
        if entry["state"] == "checkpointed":
            latest, mode = _read_regular(root, target)
            latest_parsed = _parse_block(latest.decode("utf-8"), operation["owner_id"])
            recovered = (latest_parsed["prefix"] + desired_managed + latest_parsed["suffix"]).encode()
            if latest == recovered and latest_parsed["outside_sha256"] == entry["outside_sha256"]:
                entry.update(state="complete", result_version=_bytes_sha(latest))
                _save_journal(root, journal_path, journal, "reconciling")
                return
            if _bytes_sha(latest) != entry["before_version"] or latest_parsed["outside_sha256"] != entry["outside_sha256"] or latest_parsed["managed_sha256"] != entry["managed_sha256"]:
                raise ProjectionError("workspace_conflict", "investigation changed after checkpoint")
            _atomic_write(root, target, recovered, mode)
            entry["state"] = "mutated"
            _save_journal(root, journal_path, journal, "mutating")
    final, _ = _read_regular(root, target)
    if final != desired:
        raise ProjectionError("workspace_reconciliation_failed", "managed upsert did not reconcile")
    entry.update(state="complete", result_version=_bytes_sha(final))
    _save_journal(root, journal_path, journal, "reconciling")


def _commit_removal(root: Path, prefix: str, journal_path: Path, journal: dict[str, Any], entry: dict[str, Any], operation: dict[str, Any]) -> None:
    target = _workspace_target(root, prefix, operation["path"])
    if entry["state"] == "pending":
        try:
            current, _ = _read_regular(root, target)
        except FileNotFoundError as exc:
            raise ProjectionError("workspace_conflict", "managed story disappeared before its removal checkpoint") from exc
        _validate_page(current.decode("utf-8"), operation["owner_id"])
        version = _bytes_sha(current)
        if version != operation["expected_version"] or version != operation["deleted_sha256"]:
            raise ProjectionError("workspace_conflict", "managed story changed before removal")
        entry.update(before_version=version, managed_sha256=version, state="checkpointed")
        _save_journal(root, journal_path, journal, "checkpointed")
    if entry["state"] == "checkpointed":
        try:
            current, _ = _read_regular(root, target)
        except FileNotFoundError:
            current = None
        if current is not None:
            _validate_page(current.decode("utf-8"), operation["owner_id"])
            if _bytes_sha(current) != entry["before_version"]:
                raise ProjectionError("workspace_conflict", "managed story changed after removal checkpoint")
            os.unlink(target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        entry["state"] = "mutated"
        _save_journal(root, journal_path, journal, "mutating")
    _reject_symlinks(root, target)
    if os.path.lexists(target):
        raise ProjectionError("workspace_reconciliation_failed", "managed story removal did not reconcile")
    entry.update(state="complete", removed=True)
    _save_journal(root, journal_path, journal, "reconciling")


def _operation_receipts(package: dict[str, Any], journal: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    if len(package["operations"]) != len(journal["operations"]):
        raise ProjectionError("workspace_journal_invalid", "projection operation count changed")
    for operation, entry in zip(package["operations"], journal["operations"]):
        if entry.get("state") != "complete":
            raise ProjectionError("workspace_reconciliation_failed", "projection operation is incomplete")
        result = {key: operation[key] for key in ("operation_id", "kind", "path", "owner_id")}
        if operation["kind"] == "managed_block_upsert":
            if not SHA_RE.fullmatch(str(entry.get("result_version", ""))):
                raise ProjectionError("workspace_reconciliation_failed", "upsert lacks its final version")
            result["final_version"] = entry["result_version"]
        else:
            result["removed"] = True
        results.append(result)
    return results


def commit_local_projection(root: Path, prefix: str, package: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    root, sealed_prefix = _workspace_prefix(root)
    if prefix != sealed_prefix:
        raise ProjectionError("workspace_route_invalid", "projection commit prefix differs from the sealed workspace route")
    journal_path, journal = _load_or_create_journal(root, package, stage)
    if journal.get("state") == "committed":
        receipt = journal.get("final_receipt")
        if not isinstance(receipt, dict):
            raise ProjectionError("workspace_journal_invalid", "committed projection receipt is unavailable or invalid")
        receipt_path = _receipt_path(root, str(receipt.get("receipt_id", "")))
        if str(receipt_path) != journal.get("receipt_path"):
            raise ProjectionError("workspace_journal_invalid", "committed projection receipt path is not canonical")
        _reject_symlinks(root, receipt_path)
        if not receipt_path.is_file() or verify_previous_receipt(receipt, package["destination_id"]) != receipt:
            raise ProjectionError("workspace_journal_invalid", "committed projection receipt is unavailable or invalid")
        durable = json.loads(receipt_path.read_text(encoding="utf-8"))
        if canonical_bytes(durable) != canonical_bytes(receipt):
            raise ProjectionError("workspace_journal_invalid", "durable projection receipt differs from its journal")
        return {"package_sha256": stage["package_sha256"], "journal_path": str(journal_path), "receipt_path": str(receipt_path), "status": "committed", "receipt": receipt}
    if len(package["operations"]) != len(journal["operations"]):
        raise ProjectionError("workspace_journal_invalid", "projection operation count changed")
    for operation, entry in zip(package["operations"], journal["operations"]):
        if entry.get("state") == "complete":
            continue
        if operation["kind"] == "managed_block_upsert":
            _commit_upsert(root, prefix, journal_path, journal, entry, operation)
        else:
            _commit_removal(root, prefix, journal_path, journal, entry, operation)
    receipt = journal.get("final_receipt")
    if not isinstance(receipt, dict):
        receipt = {
            "schema_version": "spotlight-workspace-final-receipt/v1", "receipt_id": "",
            "package_sha256": stage["package_sha256"], "desired_projection_set_sha256": package["desired_projection_set_sha256"],
            "case_id": package["case_id"], "classification": package["classification"],
            "destination_id": package["destination_id"], "graph_receipt_id": package["graph_receipt_id"],
            "operations": _operation_receipts(package, journal),
        }
        receipt["receipt_id"] = "receipt:" + _bytes_sha(canonical_bytes(receipt))
        journal["final_receipt"] = receipt
        journal["receipt_path"] = str(_receipt_path(root, receipt["receipt_id"]))
        _save_journal(root, journal_path, journal, "receipt_pending")
    receipt_path = _receipt_path(root, receipt["receipt_id"])
    if str(receipt_path) != journal.get("receipt_path"):
        raise ProjectionError("workspace_journal_invalid", "pending projection receipt path is not canonical")
    _atomic_json(root, receipt_path, receipt)
    _save_journal(root, journal_path, journal, "committed")
    return {"package_sha256": stage["package_sha256"], "journal_path": str(journal_path), "receipt_path": str(receipt_path), "status": "committed", "receipt": receipt}


def local_projection_status(root: Path, idempotency_key: str) -> dict[str, Any]:
    workspace, _ = _workspace_prefix(root)
    if not SHA_RE.fullmatch(idempotency_key):
        raise ProjectionError("workspace_status_invalid", "projection status requires a SHA-256 idempotency key")
    path = _journal_path(workspace, idempotency_key)
    _reject_symlinks(workspace, path)
    try:
        raw, _ = _read_regular(workspace, path)
        journal = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError("workspace_status_unavailable", "projection journal is unavailable") from exc
    if journal.get("schema_version") != JOURNAL_SCHEMA or journal.get("idempotency_key") != idempotency_key:
        raise ProjectionError("workspace_status_invalid", "projection journal binding is invalid")
    return {"idempotency_key": idempotency_key, "state": journal.get("state"), "package_sha256": journal.get("package_sha256"), "receipt_path": journal.get("receipt_path", "")}


def _check_current(database: Path, job_id: str) -> dict[str, Any]:
    conn = kd.open_existing_database(database)
    try:
        kd.verify_database(conn)
        row = conn.execute("SELECT j.* FROM projection_jobs j JOIN projection_heads h ON h.current_job_id=j.job_id WHERE j.job_id=?", (job_id,)).fetchone()
        if row is None:
            raise ProjectionError("job_superseded", "job is no longer current")
        return dict(row)
    finally:
        conn.close()


def _mark_running_failed(database: Path, job_id: str, exc: Exception) -> None:
    connection = kd.connect_database(database)
    try:
        row = connection.execute("SELECT status FROM projection_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is not None and row["status"] == "running":
            kd.fail_projection_job(connection, job_id, (getattr(exc, "code", "projection_failed") + ": " + str(exc))[:4096])
    finally:
        connection.close()


@contextmanager
def serialized_worker_lock(database: Path):
    lock_path = database.resolve().with_name(database.name + ".projection-worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProjectionError("serialized_runner_busy", "another local projection worker is active") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def validate_worker_activation(activation: dict[str, Any] | None, destination_id: str, root: Path, database: Path, prefix: str) -> None:
    if not isinstance(activation, dict):
        raise ProjectionError("local_activation_invalid", "valid local activation is required")
    required = {
        "schema_version", "status", "assurance", "destination_id", "project_id",
        "namespace", "projection_namespace", "story_namespace", "graph_database_path",
        "provider_policy_sha256", "workflow_migration_receipt",
    }
    if frozenset(activation) != frozenset(required) or (
        activation.get("schema_version") != "spotlight-knowledge-activation/v1"
        or activation.get("status") != "active"
        or activation.get("assurance") != "local_conformance"
        or activation.get("destination_id") != destination_id
        or activation.get("namespace") != prefix
        or activation.get("projection_namespace") != "investigations"
        or activation.get("story_namespace") != "stories"
        or not SHA_RE.fullmatch(str(activation.get("provider_policy_sha256", "")))
    ):
        raise ProjectionError("local_activation_invalid", "local activation does not bind this destination and every required check")
    project_id = activation.get("project_id")
    if not isinstance(project_id, str) or not project_id.startswith("local:"):
        raise ProjectionError("local_activation_invalid", "local activation project is invalid")
    try:
        if Path(project_id.removeprefix("local:")).expanduser().resolve() != root:
            raise ProjectionError("local_activation_invalid", "local activation project differs from the workspace")
        graph_path = Path(str(activation["graph_database_path"])).expanduser()
        if not graph_path.is_absolute():
            graph_path = root / graph_path
        if graph_path.resolve() != database.expanduser().resolve():
            raise ProjectionError("local_activation_invalid", "local activation graph database differs from the worker database")
        migration = activation["workflow_migration_receipt"]
        if not isinstance(migration, dict) or frozenset(migration) != {"path", "sha256"} or not SHA_RE.fullmatch(str(migration.get("sha256", ""))):
            raise ProjectionError("local_activation_invalid", "local activation workflow migration binding is invalid")
        migration_path = root / str(migration["path"])
        raw, _ = _read_regular(root, migration_path)
        if _bytes_sha(raw) != migration["sha256"]:
            raise ProjectionError("local_activation_invalid", "local activation workflow migration hash is stale")
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, ProjectionError):
            raise
        raise ProjectionError("local_activation_invalid", "local activation filesystem binding is invalid") from exc


def drain_projection_queue(database: Path, runner: Callable[[dict[str, Any]], Any]) -> list[str]:
    """Drain current pending/failed jobs and reconcile abandoned running jobs.

    Startup and the operator retry surface deliberately call this same
    serialized hook; the supplied runner performs the normal local projection.
    """
    with serialized_worker_lock(database):
        connection = kd.connect_database(database)
        try:
            rows = connection.execute(
                "SELECT j.* FROM projection_jobs j JOIN projection_heads h "
                "ON h.current_job_id=j.job_id WHERE j.status IN ('pending','failed','running') "
                "ORDER BY j.destination_id,j.generation,j.job_id"
            ).fetchall()
            normalized = []
            for row in rows:
                job = dict(row)
                if job["status"] == "running":
                    job = kd.fail_projection_job(
                        connection, job["job_id"],
                        "abandoned running job recovered by serialized queue drain",
                    )
                normalized.append(job)
        finally:
            connection.close()
    drained: list[str] = []
    for job in normalized:
        runner(job)
        drained.append(job["job_id"])
    return drained


def startup_drain(database: Path, runner: Callable[[dict[str, Any]], Any]) -> list[str]:
    return drain_projection_queue(database, runner)


def operator_retry(database: Path, runner: Callable[[dict[str, Any]], Any]) -> list[str]:
    return drain_projection_queue(database, runner)


def discover_case_directories(cases_root: Path) -> dict[str, str]:
    """Discover reviewed case batches without trusting directory names as IDs."""
    try:
        root = cases_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProjectionError("queue_cases_root_invalid", "cases root is unavailable") from exc
    if not root.is_dir():
        raise ProjectionError("queue_cases_root_invalid", "cases root must be a directory")
    discovered: dict[str, str] = {}
    try:
        entries = sorted(root.iterdir(), key=lambda value: value.name)
    except OSError as exc:
        raise ProjectionError("queue_cases_root_invalid", "cases root cannot be enumerated") from exc
    for entry in entries:
        try:
            info = os.lstat(entry)
        except OSError as exc:
            raise ProjectionError("queue_case_invalid", "case directory cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ProjectionError("queue_case_escape", "case discovery refuses symlinked entries")
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            resolved = entry.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ProjectionError("queue_case_escape", "case directory escapes the cases root") from exc
        batch_path = resolved / "data" / "knowledge-batch.json"
        if not batch_path.exists():
            continue
        _reject_symlinks(resolved, batch_path)
        try:
            batch, _snapshot_sha = kd.load_json_snapshot(batch_path)
        except kd.ContractError as exc:
            raise ProjectionError("queue_case_batch_invalid", str(exc)) from exc
        errors = kd.validate_batch(batch)
        if errors:
            raise ProjectionError("queue_case_batch_invalid", "; ".join(errors[:8]))
        project = batch["source_case"]["project"]
        case_id = kd._case_id_for_project(project)
        expected_id = kd._case_id_for_project(entry.name)
        if case_id != expected_id:
            raise ProjectionError("queue_case_mismatch", "reviewed batch project does not match its case directory")
        if case_id in discovered:
            raise ProjectionError("queue_case_duplicate", "multiple reviewed case directories resolve to the same case ID")
        discovered[case_id] = str(resolved)
    return discovered


def load_explicit_case_map(path: str) -> dict[str, str]:
    value = kd.load_json(Path(path))
    if not value or any(not isinstance(key, str) or not isinstance(directory, str) for key, directory in value.items()):
        raise ProjectionError("queue_hook_invalid", "case map must be a non-empty string-to-string JSON object")
    result: dict[str, str] = {}
    for case_id, directory in value.items():
        candidate = Path(directory).expanduser()
        try:
            info = os.lstat(candidate)
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectionError("queue_case_unmapped", "explicit case directory is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProjectionError("queue_case_escape", "explicit case directory must be a real directory")
        result[case_id] = str(resolved)
    return result


def run_worker(database: Path, job_id: str, case_dir: Path, root: Path, preconditions: dict[str, Any], previous_receipt: dict[str, Any] | None, activation: dict[str, Any] | None = None) -> dict[str, Any]:
    with serialized_worker_lock(database):
        return _run_worker(database, job_id, case_dir, root, preconditions, previous_receipt, activation)


def _run_worker(database: Path, job_id: str, case_dir: Path, root: Path, preconditions: dict[str, Any], previous_receipt: dict[str, Any] | None, activation: dict[str, Any] | None = None) -> dict[str, Any]:
    graph_lock = None
    try:
        initial = _check_current(database, job_id)
        workspace, route_prefix = _workspace_prefix(root)
        validate_worker_activation(activation, initial["destination_id"], workspace, database, route_prefix)
        if initial.get("status") == "completed":
            connection = kd.open_existing_database(database)
            try:
                binding = connection.execute(
                    "SELECT workspace_receipt_ref,workspace_receipt_sha256 "
                    "FROM projection_final_receipts WHERE job_id=?",
                    (job_id,),
                ).fetchone()
            finally:
                connection.close()
            if binding is None:
                raise ProjectionError("workspace_receipt_unbound", "completed projection lacks its final receipt")
            receipt_path = _receipt_path(workspace, binding["workspace_receipt_ref"])
            _reject_symlinks(workspace, receipt_path)
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProjectionError(
                    "workspace_receipt_invalid",
                    "completed projection receipt is unavailable or invalid",
                ) from exc
            verify_previous_receipt(receipt, initial["destination_id"], dict(binding))
            return {
                "status": "completed", "replayed": True, "job": initial,
                "manifest": None, "workspace_receipt": receipt,
            }
        snapshot = resolve_snapshot(database, job_id, case_dir)
        current = _check_current(database, job_id)
        if current["status"] in {"pending", "failed"}:
            connection = kd.connect_database(database)
            try:
                claimed = kd.claim_projection_job_exact(connection, job_id)
            finally:
                connection.close()
            if not claimed or claimed["job_id"] != job_id:
                raise ProjectionError("serialized_runner_busy", "another destination job claimed the runner")
        elif current["status"] != "running":
            raise ProjectionError("job_not_runnable", "job must be pending or running")

        graph_lock = kd.connect_database(database)
        graph_lock.execute("BEGIN IMMEDIATE")
        locked = graph_lock.execute(
            "SELECT j.* FROM projection_jobs j JOIN projection_heads h ON h.current_job_id=j.job_id WHERE j.job_id=? AND j.status='running'",
            (job_id,),
        ).fetchone()
        if locked is None:
            raise ProjectionError("job_superseded", "job lost current-head status before workspace mutation")

        with workspace_projection_lock(workspace):
            inspection = [{"path": page["path"], "owner_id": page["owner_id"], "kind": page["kind"]} for page in render_pages(snapshot)]
            prepared = prepare_local_projection(workspace, inspection, snapshot.get("previous_binding"))
            effective_preconditions = recorded_local_preconditions(
                prepared["workspace"], snapshot["job"]["desired_projection_set_sha256"], inspection,
            ) or prepared["preconditions"]
            if preconditions and canonical_bytes(preconditions) != canonical_bytes(effective_preconditions):
                raise ProjectionError("workspace_conflict", "supplied preconditions do not match the locked local workspace")
            if previous_receipt is not None and canonical_bytes(previous_receipt) != canonical_bytes(prepared["previous_receipt"]):
                raise ProjectionError("workspace_receipt_unbound", "supplied previous receipt differs from the durable local receipt")
            built = build_projection(snapshot, effective_preconditions, prepared["previous_receipt"])
            stage = stage_local_projection(built["package"])
            commit = commit_local_projection(prepared["workspace"], prepared["prefix"], built["package"], stage)

        receipt = commit.get("receipt")
        if not isinstance(receipt, dict) or commit.get("package_sha256") != stage["package_sha256"]:
            raise ProjectionError("workspace_commit_mismatch", "local workspace commit does not match the staged package")
        if receipt.get("desired_projection_set_sha256") != built["package"]["desired_projection_set_sha256"] or receipt.get("package_sha256") != commit["package_sha256"]:
            raise ProjectionError("workspace_receipt_mismatch", "workspace receipt does not bind the desired package")
        if [x.get("operation_id") for x in receipt.get("operations", [])] != [x["operation_id"] for x in built["package"]["operations"]]:
            raise ProjectionError("workspace_receipt_mismatch", "workspace receipt outcomes are not in requested order")
        verify_previous_receipt(receipt, snapshot["job"]["destination_id"])
        receipt_sha = digest(receipt)
        completed = kd.complete_projection_job(graph_lock, job_id, snapshot["job"]["desired_projection_set_sha256"], receipt["receipt_id"], receipt_sha, in_transaction=True)
        graph_lock.commit()
        graph_lock.close()
        graph_lock = None
        return {"status": "completed", "job": completed, "manifest": built["manifest"], "workspace_receipt": receipt}
    except Exception as exc:
        if graph_lock is not None:
            graph_lock.rollback()
            graph_lock.close()
            graph_lock = None
        try:
            _mark_running_failed(database, job_id, exc)
        except (OSError, kd.ContractError):
            pass
        raise
    finally:
        if graph_lock is not None:
            graph_lock.close()


def _load(path: str | None, default: Any) -> Any:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True); parser.add_argument("--job-id")
    parser.add_argument("--case-dir"); parser.add_argument("--root", required=True)
    parser.add_argument("--preconditions"); parser.add_argument("--previous-receipt"); parser.add_argument("--activation")
    parser.add_argument("--drain-mode", choices=["startup", "operator"])
    parser.add_argument("--cases-root", help="directory whose reviewed case batches are discovered for queue drains")
    parser.add_argument("--case-map", help="optional explicit JSON case map (primarily for isolated tests)")
    args = parser.parse_args(argv)
    try:
        if args.drain_mode:
            if args.job_id or args.case_dir or bool(args.cases_root) == bool(args.case_map):
                raise ProjectionError("queue_hook_invalid", "queue drain requires exactly one of --cases-root or --case-map and cannot name one job/case")
            case_map = discover_case_directories(Path(args.cases_root)) if args.cases_root else load_explicit_case_map(args.case_map)
            failures: list[dict[str, str]] = []

            def queued_runner(job: dict[str, Any]) -> None:
                try:
                    case = case_map.get(job["case_id"])
                    if not isinstance(case, str):
                        connection = kd.connect_database(Path(args.database))
                        try:
                            kd.claim_projection_job_exact(connection, job["job_id"])
                            kd.fail_projection_job(connection, job["job_id"], "current projection job has no case-directory mapping")
                        finally:
                            connection.close()
                        raise ProjectionError("queue_case_unmapped", "current projection job has no case-directory mapping")
                    run_worker(Path(args.database), job["job_id"], Path(case), Path(args.root), {}, None, _load(args.activation, None))
                except (ProjectionError, kd.ContractError, safe.SafetyError, OSError, json.JSONDecodeError) as exc:
                    failures.append({"job_id": job["job_id"], "code": getattr(exc, "code", "projection_failed"), "detail": str(exc)})

            hook = startup_drain if args.drain_mode == "startup" else operator_retry
            drained = hook(Path(args.database), queued_runner)
            print(json.dumps({"status": "blocked" if failures else "drained", "mode": args.drain_mode, "jobs": drained, "failures": failures}, sort_keys=True, separators=(",", ":")))
            return 2 if failures else 0
        if not args.job_id or not args.case_dir:
            raise ProjectionError("worker_arguments_missing", "single-job mode requires --job-id and --case-dir")
        value = run_worker(Path(args.database), args.job_id, Path(args.case_dir), Path(args.root), _load(args.preconditions, {}), _load(args.previous_receipt, None), _load(args.activation, None))
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except (ProjectionError, kd.ContractError, safe.SafetyError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "code": getattr(exc, "code", "projection_failed"), "detail": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
