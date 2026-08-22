#!/usr/bin/env python3
"""RED contract for executable native Arbiter workflow callers.

The test uses only a mocked urllib opener. It requires the production caller to
drive ArbiterClient, preserve response fields in case-local raw files, and keep
the reviewed create gate local rather than serializing a confirmation field.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "integrations" / "arbiter" / "client.py"
WORKFLOW = ROOT / "integrations" / "arbiter" / "workflow.py"


class FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, object]):
        self._raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

class RawClient:
    """Minimal native boundary exposing the unmodified response bytes."""

    def __init__(self, responses: list[bytes]):
        self.responses = list(responses)

    def request_raw(self, method, path, *, query=None, body=None, timeout=None):
        return self.responses.pop(0)


class RecordingClient:
    """Workflow fake that records the consumer-visible request contract."""

    def __init__(self, create_payload: dict[str, object] | None = None):
        self.create_payload = create_payload or {"case_study_id": "a" * 32}
        self.calls = []

    def request_json(self, method, path, *, query=None, body=None, timeout=None):
        self.calls.append((method, path, query, body, timeout))
        if path == "/case-studies":
            return self.create_payload
        if path.endswith("/search-plan"):
            return {"plan": {"search_phrases": ["one"], "entities": ["Entity"]}}
        if path.endswith("/finalize"):
            return {"case_study_id": "a" * 32, "status": "processing"}
        raise AssertionError(f"unexpected workflow request: {method} {path}")


def public_staging_resolver(hostname, port, *_args, **_kwargs):
    """Resolve the fake staging host to a public fixture address."""
    assert hostname == "staging.example"
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))
    ]


def check_production_wiring() -> None:
    """The executable integration, not only tests, must own both collaborators."""
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "integrations").rglob("*.py")
        if path.name not in {"workflow.py", "client.py", "credentials.py"}
    )
    assert "workflow" in production and "ArbiterClient" in production, (
        "production Spotlight code must import and call the native workflow"
    )
    assert "credentials" in production and "resolve_spotlight_arbiter_key" in production, (
        "production Spotlight code must wire the credential provider"
    )
    for name in ("browse", "read", "report", "progress", "reviewed_create"):
        assert name in production, f"production caller missing workflow verb: {name}"


def check_raw_bytes_and_collisions(workflow_module) -> None:
    """Saved evidence is byte-identical and repeated timestamps do not overwrite."""
    with tempfile.TemporaryDirectory(prefix="arbiter-raw-") as raw:
        case_dir = Path(raw) / "case"
        (case_dir / "research").mkdir(parents=True)
        first = b'{  "z" : 1,\n "a":2 }'
        second = b'{"different":true}'
        client = RawClient([first, second])
        first_path = workflow_module.browse(
            client, case_dir, timestamp="2026-08-22T10-00-00Z"
        )
        second_path = workflow_module.browse(
            client, case_dir, timestamp="2026-08-22T10-00-00Z"
        )
        assert first_path != second_path, "same-second evidence filename collision"
        assert first_path.read_bytes() == first, "raw response bytes were normalized"
        assert second_path.read_bytes() == second, "raw response bytes were normalized"


def check_exact_create_contract(workflow_module) -> None:
    """Create/finalize shapes, identifier bounds, and the long plan timeout are strict."""
    body = {
        "search_query": "query",
        "platforms": ["reddit"],
        "date_range": {"from": "2026-08-01", "to": "2026-08-02"},
    }
    with tempfile.TemporaryDirectory(prefix="arbiter-contract-") as raw:
        case_dir = Path(raw) / "case"
        (case_dir / "research").mkdir(parents=True)
        for invalid_id in ("A" * 32, "a" * 31, "a" * 33, "../" + "a" * 29):
            try:
                workflow_module.read(
                    RecordingClient(), case_dir, invalid_id,
                    timestamp="2026-08-22T10-00-00Z",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid read id accepted: {invalid_id}")

        client = RecordingClient()
        workflow_module.reviewed_create(
            client, case_dir, body, search_phrases=["one"],
            final_entities=["Entity"], confirmed=True,
            timestamp="2026-08-22T10-00-00Z",
        )
        create, plan, finalize = client.calls
        assert create[:4] == ("POST", "/case-studies", None, body)
        assert plan[:4] == ("POST", "/case-studies/" + "a" * 32 + "/search-plan", None, {})
        assert finalize[:4] == (
            "POST", "/case-studies/" + "a" * 32 + "/finalize", None,
            {"search_phrases": ["one"], "final_entities": ["Entity"]},
        )
        assert plan[4] is not None and plan[4] > 800, "search-plan timeout must exceed route budget"

        for invalid_id in ("new-study", "A" * 32, "a" * 31, "a" * 33):
            invalid_client = RecordingClient({"case_study_id": invalid_id})
            try:
                workflow_module.reviewed_create(
                    invalid_client, case_dir, body, search_phrases=["one"],
                    final_entities=[], confirmed=True,
                    timestamp="2026-08-22T10-00-01Z",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid response id accepted: {invalid_id}")

        for phrases, entities in (
            ([], []),
            (["phrase"] * 51, []),
            (["x" * 201], []),
            (["phrase"], ["entity"] * 201),
            (["phrase"], ["x" * 501]),
        ):
            try:
                workflow_module.reviewed_create(
                    client, case_dir, body, search_phrases=phrases,
                    final_entities=entities, confirmed=True,
                    timestamp="2026-08-22T10-00-02Z",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("out-of-bounds create/finalize arrays accepted")


def load(path: Path, name: str):
    if not path.is_file():
        raise AssertionError(f"{path.relative_to(ROOT)} must provide the native production seam")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    client_module = load(CLIENT, "spotlight_arbiter_client")
    workflow_module = load(WORKFLOW, "spotlight_arbiter_workflow")
    required = ("browse", "read", "report", "progress", "reviewed_create")
    missing = [name for name in required if not hasattr(workflow_module, name)]
    if missing:
        raise AssertionError(f"workflow.py must expose native functions: {missing!r}")

    responses = [
        {"items": [{"id": "study-1"}], "meta": {"request_id": "req-browse", "unknown": "keep"}},
        {"items": [{"post_id": "P1"}], "next_cursor": None, "meta": {"request_id": "req-read"}},
        {"title": "Report", "top_actors": [], "meta": {"request_id": "req-report", "credits_charged": 0}},
        {"status": "processing", "updated_at": "2026-08-22T10:00:00+05:30", "meta": {"request_id": "req-progress"}},
        {"case_study_id": "a" * 32, "meta": {"request_id": "req-create", "credits_charged": 0}},
        {"search_phrases": ["one"], "meta": {"request_id": "req-plan", "credits_charged": 25}},
        {"status": "processing", "meta": {"request_id": "req-finalize", "credits_charged": 100}},
    ]
    seen: list[tuple[str, str, object | None]] = []

    def opener(request, timeout):
        assert timeout > 0
        body = None if request.data is None else json.loads(request.data.decode("utf-8"))
        seen.append((request.method, request.full_url, body))
        return FakeResponse(responses.pop(0))

    with tempfile.TemporaryDirectory(prefix="arbiter-workflow-") as raw:
        case_dir = Path(raw) / "case"
        (case_dir / "research").mkdir(parents=True)
        with patch.object(
            client_module.socket,
            "getaddrinfo",
            side_effect=public_staging_resolver,
        ):
            client = client_module.ArbiterClient.from_env(
                {
                    "ARBITER_API_KEY": "fixture-secret",
                    "ARBITER_API_BASE": "https://staging.example/api/v1",
                },
                opener=opener,
            )
            browse_file = workflow_module.browse(client, case_dir, timestamp="2026-08-22T10-00-00Z")
            read_file = workflow_module.read(client, case_dir, "a" * 32, timestamp="2026-08-22T10-00-00Z")
            report_file = workflow_module.report(client, case_dir, "a" * 32, timestamp="2026-08-22T10-00-00Z")
            progress_file = workflow_module.progress(client, case_dir, "a" * 32, timestamp="2026-08-22T10-00-00Z")
            workflow_module.reviewed_create(
                client,
                case_dir,
                {
                    "search_query": "query",
                    "platforms": ["reddit"],
                    "date_range": {"from": "2026-08-01", "to": "2026-08-02"},
                },
                search_phrases=["one"],
                final_entities=["Entity"],
                confirmed=True,
                timestamp="2026-08-22T10-00-00Z",
            )

            assert parse_qs(urlsplit(seen[0][1]).query) == {"limit": ["100"]}
            assert seen[0][0] == "GET" and seen[0][2] is None
            assert seen[1][0] == "GET" and "/topics/" + "a" * 32 + "/posts" in seen[1][1]
            assert seen[2][0] == "GET" and "/topics/" + "a" * 32 + "/report" in seen[2][1]
            assert seen[3][0] == "GET" and "/case-studies/" + "a" * 32 + "/progress" in seen[3][1]
            assert seen[4] == (
                "POST",
                "https://staging.example/api/v1/case-studies",
                {
                    "search_query": "query",
                    "platforms": ["reddit"],
                    "date_range": {"from": "2026-08-01", "to": "2026-08-02"},
                },
            )
            assert seen[5][0] == "POST" and seen[5][1].endswith("/search-plan") and seen[5][2] == {}
            assert seen[6][0] == "POST" and seen[6][1].endswith("/finalize")
            assert seen[6][2] == {"search_phrases": ["one"], "final_entities": ["Entity"]}
            assert "confirmed" not in json.dumps(seen[6][2])

            for output in (browse_file, read_file, report_file, progress_file):
                path = Path(output)
                assert path.is_file() and path.parent == case_dir / "research"
                saved = json.loads(path.read_text(encoding="utf-8"))
                assert saved.get("meta", {}).get("request_id"), path
                assert "fixture-secret" not in path.read_text(encoding="utf-8")

            symlink_case = Path(raw) / "linked-case"
            symlink_case.symlink_to(case_dir, target_is_directory=True)
            try:
                workflow_module.browse(client, symlink_case, timestamp="2026-08-22T10-00-00Z")
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError("workflow accepted a symlinked case root")

        with patch.object(client_module.socket, "getaddrinfo", side_effect=socket.gaierror("fixture DNS failure")):
            try:
                client_module.validate_api_base("https://staging.example/api/v1")
            except ValueError:
                pass
            else:
                raise AssertionError("production validation accepted an unresolved staging host")

    if responses:
        raise AssertionError(f"workflow did not consume all mocked HTTP responses: {responses!r}")
    check_production_wiring()
    check_raw_bytes_and_collisions(workflow_module)
    check_exact_create_contract(workflow_module)
    print("arbiter native workflow: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
