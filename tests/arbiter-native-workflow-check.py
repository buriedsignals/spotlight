#!/usr/bin/env python3
"""RED contract for executable native Arbiter workflow callers.

The test uses only a mocked urllib opener. It requires the production caller to
drive ArbiterClient, preserve response fields in case-local raw files, and keep
the reviewed create gate local rather than serializing a confirmation field.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
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
        {"case_study_id": "new-study", "meta": {"request_id": "req-create", "credits_charged": 0}},
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

    if responses:
        raise AssertionError(f"workflow did not consume all mocked HTTP responses: {responses!r}")
    print("arbiter native workflow: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
