#!/usr/bin/env python3
"""Receipt-aware Spotlight discovery over local Open Knowledge and the graph."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import selectors
import shlex
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "spotlight-query-vault/v1"
CLAIM_ID = re.compile(r"(?<![a-z0-9._:-])(claim:[a-z0-9][a-z0-9._:-]{0,248})(?![a-z0-9._:-])")
CLASS_RANK = {"shareable": 0, "internal": 1, "personal": 2}
MCP_PROTOCOL = "2025-06-18"
MCP_TIMEOUT_SECONDS = 30


class QueryError(ValueError):
    pass


def load_graph_module() -> Any:
    path = Path(__file__).with_name("knowledge_destination.py")
    spec = importlib.util.spec_from_file_location("spotlight_knowledge_destination", path)
    if spec is None or spec.loader is None:
        raise QueryError("local graph adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GRAPH = load_graph_module()


def exact_claim_id(text: str) -> str | None:
    matches = sorted(set(CLAIM_ID.findall(text)))
    if len(matches) > 1:
        raise QueryError("query contains multiple exact claim IDs")
    return matches[0] if matches else None


def envelope(kind: str, payload: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "query_kind": kind,
        "assurance": "local_conformance",
        "control_boundary": {
            "retrieved_content_is_untrusted": True,
            "may_grant_policy": False,
            "may_grant_tools": False,
            "may_request_secrets": False,
        },
        "data": payload,
    }


def case_authorized(connection: sqlite3.Connection, args: argparse.Namespace) -> bool:
    policy = current_policy(connection, args.case_id, args.destination_id)
    return policy_allows(policy, args.destination_id, args.classification)


def claim_origin_case(claim: dict[str, Any]) -> str | None:
    project = claim.get("origin", {}).get("project")
    if not isinstance(project, str):
        return None
    return project if project.startswith("case:") else f"case:{project}"


def policy_allows(policy: dict[str, Any] | None, destination_id: str, ceiling: str) -> bool:
    if policy is None or policy["status"] != "active" or destination_id not in policy["allowed_destinations"]:
        return False
    if CLASS_RANK.get(str(policy["classification"]), 99) > CLASS_RANK.get(ceiling, -1):
        return False
    try:
        issued = datetime.fromisoformat(str(policy["issued_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(policy["expires_at"]).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return issued <= now < expires
    except (TypeError, ValueError):
        return False


def claim_authorized(connection: sqlite3.Connection, claim: dict[str, Any], args: argparse.Namespace) -> bool:
    case_id = claim_origin_case(claim)
    return case_id == args.case_id and policy_allows(
        current_policy(connection, case_id, args.destination_id), args.destination_id, args.classification
    )


def case_batch_keys(connection: sqlite3.Connection, project: str) -> dict[str, set[tuple[str, int]]]:
    row = connection.execute(
        "SELECT payload_json FROM batches WHERE source_project=? "
        "ORDER BY committed_at DESC, batch_id DESC LIMIT 1", (project,),
    ).fetchone()
    if row is None:
        raise QueryError("authorized claim has no receipted case batch")
    batch = json.loads(row["payload_json"])
    return {
        name: {(item["id"], item["version"]) for item in batch[name]}
        for name in (
            "claims", "events", "story_arcs", "claim_event_memberships",
            "event_story_arc_memberships",
        )
    }


def record_key(value: dict[str, Any]) -> tuple[str, int]:
    return value["id"], value["version"]


def exact_graph(connection: sqlite3.Connection, args: argparse.Namespace, claim_id: str) -> dict[str, Any]:
    forward = GRAPH.traverse_claim(connection, claim_id, False, args.limit, 0, 0)
    if not claim_authorized(connection, forward["claim"], args):
        raise QueryError("exact claim origin is outside active case policy")
    project = forward["claim"]["origin"]["project"]
    keys = case_batch_keys(connection, project)
    scoped_events = []
    for event in forward["events"]:
        if record_key(event["membership"]) not in keys["claim_event_memberships"] or record_key(event["event"]) not in keys["events"]:
            continue
        event["story_arcs"] = [
            story for story in event["story_arcs"]
            if record_key(story["membership"]) in keys["event_story_arc_memberships"]
            and record_key(story["story_arc"]) in keys["story_arcs"]
        ]
        event["story_arc_page"].update(total=len(event["story_arcs"]), truncated=False)
        scoped_events.append(event)
    forward["events"] = scoped_events
    forward["event_page"].update(total=len(scoped_events), truncated=False)
    reverse = []
    seen: set[str] = set()
    for event in forward["events"]:
        for story in event["story_arcs"]:
            story_id = story["story_arc"]["id"]
            if story_id not in seen:
                seen.add(story_id)
                traversal = GRAPH.traverse_story_arc(connection, story_id, False, args.limit, 0, 0)
                scoped_reverse_events = []
                for reverse_event in traversal["events"]:
                    if record_key(reverse_event["membership"]) not in keys["event_story_arc_memberships"] or record_key(reverse_event["event"]) not in keys["events"]:
                        continue
                    reverse_event["claims"] = [
                        item for item in reverse_event["claims"]
                        if record_key(item["membership"]) in keys["claim_event_memberships"]
                        and record_key(item["claim"]) in keys["claims"]
                        and claim_authorized(connection, item["claim"], args)
                    ]
                    reverse_event["claim_page"]["total"] = len(reverse_event["claims"])
                    reverse_event["claim_page"]["truncated"] = False
                    scoped_reverse_events.append(reverse_event)
                traversal["events"] = scoped_reverse_events
                traversal["event_page"].update(total=len(scoped_reverse_events), truncated=False)
                reverse.append(traversal)
    return envelope("exact_claim_graph", {
        "semantic_search_bypassed": True,
        "claim_id": claim_id,
        "claim_to_story_arcs": forward,
        "story_arcs_to_claims": reverse,
    })


def graph_workflow(connection: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if args.workflow == "prior-verdict":
        if bool(args.finding_fingerprint) == bool(args.legacy_claim_id):
            raise QueryError("prior-verdict requires exactly one exact origin key")
        result = GRAPH.prior_verdicts(connection, args.finding_fingerprint, args.legacy_claim_id, args.limit, 0)
        result["claims"] = [claim for claim in result["claims"] if claim_authorized(connection, claim, args)]
        result["page"].update(total=len(result["claims"]), truncated=False)
        return envelope("prior_verdict", {"semantic_search_bypassed": True, "graph": result})
    if not args.proposition:
        raise QueryError("dedup requires an exact proposition")
    result = GRAPH.equivalence_candidates(connection, args.proposition, args.limit, 0)
    result["claims"] = [claim for claim in result["claims"] if claim_authorized(connection, claim, args)]
    result["page"].update(total=len(result["claims"]), truncated=False)
    return envelope("dedup", {"semantic_search_bypassed": True, "graph": result})


class OpenKnowledgeMCP:
    """One bounded read-only MCP stdio session against the configured project."""

    def __init__(self, executable: str, workspace_root: Path):
        self.process = subprocess.Popen(
            [executable, "--cwd", str(workspace_root), "--log-level", "silent", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            self.close()
            raise QueryError("Open Knowledge MCP stdio pipes are unavailable")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.next_id = 0
        self.stderr: list[str] = []
        try:
            initialized = self._request("initialize", {
                "protocolVersion": MCP_PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "spotlight-query-vault", "version": "1"},
            })
        except Exception:
            self.close()
            raise
        if not isinstance(initialized, dict) or not initialized.get("protocolVersion"):
            self.close()
            raise QueryError("Open Knowledge MCP initialize response is invalid")
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _write(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise QueryError("Open Knowledge MCP closed its input") from exc

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        self.next_id += 1
        request_id = self.next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + MCP_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QueryError("Open Knowledge MCP request timed out")
            events = self.selector.select(remaining)
            if not events:
                raise QueryError("Open Knowledge MCP request timed out")
            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    if self.process.poll() is not None:
                        detail = " ".join(self.stderr)[-1000:]
                        raise QueryError("Open Knowledge MCP exited" + (f": {detail}" if detail else ""))
                    continue
                if key.data == "stderr":
                    if sum(map(len, self.stderr)) < 8192:
                        self.stderr.append(line.strip())
                    continue
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") != request_id:
                    continue
                if isinstance(response.get("error"), dict):
                    raise QueryError("Open Knowledge MCP request failed: " + str(response["error"].get("message", "unknown error")))
                if "result" not in response:
                    raise QueryError("Open Knowledge MCP response omitted result")
                return response["result"]

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": tool, "arguments": arguments})
        if not isinstance(result, dict):
            raise QueryError(f"Open Knowledge {tool} returned an invalid result")
        if result.get("isError"):
            raise QueryError(f"Open Knowledge {tool} returned a blocker")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise QueryError(f"Open Knowledge {tool} omitted structuredContent")
        if isinstance(structured.get("error"), dict):
            raise QueryError(f"Open Knowledge {tool} failed: {structured['error'].get('message', 'unknown error')}")
        return structured

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def __enter__(self) -> "OpenKnowledgeMCP":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def route_prefix(workspace_root: Path) -> str:
    routes = json.loads((workspace_root / ".knowledge-workspace" / "routes.json").read_text(encoding="utf-8"))
    prefix = routes.get("routes", {}).get("spotlight_verified") if routes.get("schema_version") == "knowledge-routes/v1" else None
    if not isinstance(prefix, str) or not prefix or prefix.startswith("/") or ".." in Path(prefix).parts:
        raise QueryError("sealed Spotlight knowledge route is invalid")
    return Path(prefix).as_posix().rstrip("/")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def current_projection_receipts(connection: sqlite3.Connection) -> set[str]:
    return {
        row["workspace_receipt_ref"]
        for row in connection.execute(
            "SELECT r.workspace_receipt_ref FROM projection_final_receipts r "
            "JOIN projection_heads h ON h.case_id=r.case_id AND h.destination_id=r.destination_id "
            "JOIN projection_jobs j ON j.job_id=h.current_job_id AND j.job_id=r.job_id "
            "WHERE j.status='completed'"
        ).fetchall()
    }


def projection_catalog(workspace_root: Path, prefix: str, current_receipts: set[str] | None = None) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    journal_dir = workspace_root / ".knowledge-workspace" / "projection-journals"
    if not journal_dir.is_dir():
        return catalog
    for journal_path in sorted(journal_dir.glob("*.json")):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            receipt = journal["final_receipt"]
            claimed = receipt["receipt_id"]
            identity = json.loads(json.dumps(receipt))
            identity["receipt_id"] = ""
            if (
                journal.get("schema_version") != "spotlight-projection-journal/v1"
                or journal.get("state") != "committed"
                or claimed != "receipt:" + canonical_sha256(identity)
                or receipt.get("package_sha256") != journal.get("package_sha256")
                or receipt.get("desired_projection_set_sha256") != journal.get("desired_projection_set_sha256")
                or receipt.get("case_id") != journal.get("case_id")
                or receipt.get("classification") != journal.get("classification")
                or receipt.get("destination_id") != journal.get("destination_id")
                or receipt.get("graph_receipt_id") != journal.get("graph_receipt_id")
                or (current_receipts is not None and claimed not in current_receipts)
            ):
                continue
            for outcome in receipt.get("operations", []):
                if outcome.get("kind") != "managed_block_upsert" or not re.fullmatch(r"[a-f0-9]{64}", str(outcome.get("final_version", ""))):
                    continue
                relative = Path(str(outcome.get("path", ""))).as_posix()
                if not (relative.startswith("investigations/") or relative.startswith("stories/")):
                    continue
                target = GRAPH.resolve_beneath(workspace_root, Path(prefix) / relative, "projected knowledge page")
                body = target.read_bytes()
                if hashlib.sha256(body).hexdigest() != outcome["final_version"]:
                    continue
                path = f"{prefix}/{relative}"
                candidate = {
                    "source": {
                        "backend": "openknowledge", "path": path,
                        "case_id": journal.get("case_id"), "classification": journal.get("classification"),
                        "destination_id": journal.get("destination_id"), "projection_receipt_id": claimed,
                    },
                    "freshness": {"current": True, "security_affecting": False},
                    "content": body.decode("utf-8"),
                    "content_sha256": outcome["final_version"],
                    "projected_at": str(journal.get("updated_at", "")),
                }
                catalog[path] = candidate
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, GRAPH.ContractError):
            continue
    return catalog


def normalize_result_path(path: Any, catalog: dict[str, dict[str, Any]]) -> str | None:
    if not isinstance(path, str):
        return None
    normalized = Path(path.lstrip("./")).as_posix()
    for candidate in (normalized, normalized + ".md"):
        if candidate in catalog:
            return candidate
    return normalized


def current_policy(connection: sqlite3.Connection, case_id: str, destination_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM case_policy_receipts
             WHERE case_id=? AND destination_id=?
             ORDER BY policy_revision DESC LIMIT 1""",
        (case_id, destination_id),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return {
        "status": row["status"], "classification": payload.get("classification"),
        "issued_at": payload.get("issued_at"), "expires_at": payload.get("expires_at"),
        "allowed_destinations": payload.get("allowed_destinations", []),
        "provider_policy": payload.get("provider_policy", {}),
    }


def receipt_is_current(connection: sqlite3.Connection, source: dict[str, Any]) -> bool:
    receipt_id = source.get("projection_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        return False
    row = connection.execute(
        """SELECT receipt.workspace_receipt_ref
             FROM projection_final_receipts AS receipt
             JOIN projection_jobs AS job ON job.job_id=receipt.job_id
             JOIN projection_heads AS head
               ON head.case_id=receipt.case_id AND head.destination_id=receipt.destination_id
            WHERE receipt.workspace_receipt_ref=? AND job.status='completed'
              AND head.current_job_id=job.job_id AND receipt.case_id=?
              AND receipt.destination_id=?""",
        (receipt_id, source.get("case_id"), source.get("destination_id")),
    ).fetchone()
    return row is not None


def claim_index_for_content(connection: sqlite3.Connection, args: argparse.Namespace, content: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for claim_id in sorted(set(CLAIM_ID.findall(content))):
        try:
            claim = GRAPH.claim_record(connection, claim_id)["claim"]
        except GRAPH.ContractError:
            continue
        if not claim_authorized(connection, claim, args):
            continue
        claims.append({
            "id": claim["id"], "version": claim["version"],
            "proposition": claim["proposition"], "origin": claim["origin"],
        })
    return claims


def openknowledge_envelope(projection: dict[str, Any], claim_index: list[dict[str, Any]]) -> dict[str, Any]:
    # Search output can lag a local edit. Use Open Knowledge only to rank the
    # receipt-bound path; never surface its potentially stale cached snippet.
    snippet = projection["content"][:1000]
    return {
        "schema_version": "knowledge-retrieval-envelope/v1",
        "source": projection["source"],
        "freshness": projection["freshness"],
        "data": {"media_type": "text/plain", "content": snippet},
        "claim_index": claim_index,
    }


def _legacy_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = Path(value.lstrip("./"))
    if candidate.is_absolute() or candidate.suffix.lower() != ".md" or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    if candidate.parts[0] in {".git", ".ok", ".knowledge-workspace"}:
        return None
    return candidate.as_posix()


def legacy_envelope(item: dict[str, Any], path: str) -> dict[str, Any]:
    snippet = item.get("snippet")
    if not isinstance(snippet, str):
        snippet = ""
    return {
        "schema_version": "knowledge-retrieval-envelope/v1",
        "source": {"path": path, "kind": "legacy_openknowledge_page", "managed": False},
        "freshness": {"current": None, "security_affecting": False, "receipt_bound": False},
        "data": {"media_type": "text/plain", "content": snippet[:1000], "untrusted": True},
        "claim_index": [],
    }


def filter_discovery(
    connection: sqlite3.Connection, args: argparse.Namespace,
    results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    policy = current_policy(connection, args.case_id, args.destination_id)
    authorized = policy_allows(policy, args.destination_id, args.classification)
    accepted: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict) or item.get("kind") != "page":
            continue
        path = normalize_result_path(item.get("path"), catalog)
        projection = catalog.get(path or "")
        if projection is None:
            legacy_path = _legacy_path(item.get("path"))
            if legacy_path is None:
                continue
            score = item.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                score = 0.0
            accepted.append({
                "rank_class": 1, "legacy": True, "managed_current": False,
                "score": score, "envelope": legacy_envelope(item, legacy_path),
            })
            continue
        if not authorized:
            continue
        source, freshness = projection["source"], projection["freshness"]
        if source.get("case_id") != args.case_id or source.get("destination_id") != args.destination_id:
            continue
        if CLASS_RANK.get(str(source.get("classification")), 99) > CLASS_RANK[args.classification]:
            continue
        if freshness.get("security_affecting") and not freshness.get("current"):
            continue
        if not receipt_is_current(connection, source):
            continue
        claim_index = claim_index_for_content(connection, args, projection["content"])
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            score = 0.0
        accepted.append({
            "rank_class": 0, "legacy": False, "managed_current": True,
            "score": score,
            "envelope": openknowledge_envelope(projection, claim_index),
        })
    accepted.sort(key=lambda value: (value["rank_class"], -float(value["score"] or 0), value["envelope"]["source"]["path"]))
    return accepted[:args.limit]


def search_openknowledge(client: OpenKnowledgeMCP, args: argparse.Namespace, policy: dict[str, Any] | None) -> dict[str, Any]:
    request = {
        "query": args.query, "intent": "full_text", "scopes": ["page"],
        # Filter after receipt/case validation, so ask Open Knowledge for its
        # bounded maximum rather than starving valid case hits behind other pages.
        "limit": 100, "cwd": str(args.workspace_root),
    }
    allowed_modes = policy.get("provider_policy", {}).get("allowed_modes", []) if policy else []
    semantic_allowed = "semantic" in allowed_modes
    if policy is not None and not semantic_allowed:
        request["semantic"] = False
    result = client.call("search", request)
    if result.get("ready") is False:
        raise QueryError("Open Knowledge index is not ready; retry after indexing completes")
    if not isinstance(result.get("results"), list):
        raise QueryError("Open Knowledge search omitted results")
    semantic = result.get("semantic")
    if not isinstance(semantic, dict):
        semantic = {"capable": False, "applied": False, "coverage": {"embedded": 0, "total": 0}}
    if not semantic_allowed and semantic.get("applied") is True:
        raise QueryError("Open Knowledge applied semantic ranking outside the signed case provider policy")
    return {"results": result["results"], "semantic": semantic}


def normalize_exec_content(value: str, path: str) -> str:
    header = f"==> {path} <==\n"
    if value.startswith(header):
        value = value[len(header):]
    enrichment = "\n\n### Referenced files\n\n- **"
    marker = value.rfind(enrichment)
    if marker >= 0:
        value = value[:marker]
    return value


def selected_page_read(
    connection: sqlite3.Connection, args: argparse.Namespace,
    catalog: dict[str, dict[str, Any]], client: OpenKnowledgeMCP,
) -> dict[str, Any]:
    path = normalize_result_path(args.read_path, catalog)
    projection = catalog.get(path or "")
    if projection is None:
        raise QueryError("selected page is not a current receipt-bound Spotlight projection")
    source = projection["source"]
    policy = current_policy(connection, args.case_id, args.destination_id)
    if (
        not policy_allows(policy, args.destination_id, args.classification)
        or source.get("case_id") != args.case_id
        or source.get("destination_id") != args.destination_id
        or CLASS_RANK.get(str(source.get("classification")), 99) > CLASS_RANK[args.classification]
        or not receipt_is_current(connection, source)
    ):
        raise QueryError("selected page is outside the current case policy or projection receipt")
    result = client.call("exec", {"command": "cat -- " + shlex.quote(path), "cwd": str(args.workspace_root)})
    content = result.get("text")
    if not isinstance(content, str):
        raise QueryError("Open Knowledge selected-page read omitted text")
    content = normalize_exec_content(content, path)
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != projection["content_sha256"]:
        raise QueryError("selected page changed after its projection receipt was validated")
    return envelope("selected_page_read", {
        "source": source, "freshness": projection["freshness"],
        "claim_index": claim_index_for_content(connection, args, projection["content"]),
        "retrieved": {"media_type": "text/markdown", "content": content, "untrusted": True},
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="installed Spotlight config; use with --case-dir")
    parser.add_argument("--case-dir", type=Path, help="current case directory for installed runtime discovery")
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--classification", choices=tuple(CLASS_RANK))
    parser.add_argument("--destination-id")
    parser.add_argument("--open-knowledge", default="open-knowledge", help="installed Open Knowledge CLI")
    parser.add_argument("--read-path", help="read one selected current projection through Open Knowledge")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--workflow", choices=("auto", "prior-verdict", "dedup"), default="auto")
    parser.add_argument("--finding-fingerprint")
    parser.add_argument("--legacy-claim-id")
    parser.add_argument("--proposition")
    parser.add_argument("query", nargs="?", default="")
    return parser


def resolve_installed_context(args: argparse.Namespace) -> None:
    if args.config is not None:
        if args.case_dir is None:
            raise QueryError("--config requires --case-dir")
        config = json.loads(args.config.expanduser().resolve().read_text(encoding="utf-8"))
        destination = config["knowledge_destination"]
        args.workspace_root = Path(destination["workspace_path"])
        args.db = Path(destination["graph_database_path"])
        args.destination_id = destination["destination_id"]
        case_dir = args.case_dir.expanduser().resolve()
        batch_path = case_dir / "data" / "knowledge-batch.json"
        project = case_dir.name
        if batch_path.is_file():
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            project = batch.get("source_case", {}).get("project", project)
        args.case_id = GRAPH._case_id_for_project(project)
        database = args.db if args.db.is_absolute() else args.workspace_root / args.db
        connection = GRAPH.open_existing_database(database)
        try:
            policy = current_policy(connection, args.case_id, args.destination_id)
        finally:
            connection.close()
        args.classification = policy.get("classification") if policy else "personal"
    missing = [name for name in ("workspace_root", "db", "case_id", "classification", "destination_id") if getattr(args, name) in (None, "")]
    if missing:
        raise QueryError("missing query context: " + ", ".join(missing))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        resolve_installed_context(args)
        args.workspace_root = GRAPH.resolve_root(args.workspace_root, "workspace root")
        args.db = GRAPH.resolve_beneath(args.workspace_root, args.db, "graph database")
        if not 1 <= args.limit <= 100:
            raise QueryError("limit must be 1..100")
        connection = GRAPH.open_existing_database(args.db)
        try:
            GRAPH.verify_database(connection)
            if len(args.query) > 4096 or "\x00" in args.query:
                raise QueryError("query is oversized or contains control data")
            if args.read_path and (args.workflow != "auto" or args.query):
                raise QueryError("selected-page read cannot be combined with a query or graph workflow")
            if args.workflow != "auto":
                output = graph_workflow(connection, args)
            elif (claim_id := exact_claim_id(args.query)) is not None:
                output = exact_graph(connection, args, claim_id)
            else:
                if not args.read_path and not args.query.strip():
                    raise QueryError("broad discovery requires a query")
                prefix = route_prefix(args.workspace_root)
                catalog = projection_catalog(args.workspace_root, prefix, current_projection_receipts(connection))
                policy = current_policy(connection, args.case_id, args.destination_id)
                if policy is not None and not case_authorized(connection, args):
                    if args.read_path:
                        raise QueryError("selected page is outside the current case policy")
                    output = envelope("broad_discovery", {
                        "retrieval_mode": "not_run", "semantic_search_used": False,
                        "semantic": {"capable": False, "applied": False, "coverage": {"embedded": 0, "total": 0}},
                        "claim_identity_manufactured": False, "results": [],
                    })
                else:
                    with OpenKnowledgeMCP(args.open_knowledge, args.workspace_root) as client:
                        if args.read_path:
                            output = selected_page_read(connection, args, catalog, client)
                        else:
                            searched = search_openknowledge(client, args, policy)
                            results = filter_discovery(connection, args, searched["results"], catalog)
                            semantic = searched["semantic"]
                            output = envelope("broad_discovery", {
                                "retrieval_mode": "semantic" if semantic.get("applied") is True else "full_text",
                                "semantic_search_used": semantic.get("applied") is True,
                                "semantic": semantic,
                                "claim_identity_manufactured": False,
                                "results": results,
                            })
        finally:
            connection.close()
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except (QueryError, GRAPH.ContractError, json.JSONDecodeError, sqlite3.Error, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "error": {"code": "query_blocked", "detail": str(exc)}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
