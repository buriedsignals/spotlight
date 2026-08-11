"""Shared execution helpers and closed dispatcher for Spotlight integrations.

Wrappers use this module to keep path containment, request parsing, run
manifests, atomic JSON writes, and subprocess invocation consistent.

    python3 integrations/_runner.py maigret request.json [--dry-run]
    python3 integrations/_runner.py rlm request.json
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UNSAFE_CHARS_RE = re.compile(r"[\x00\r\n`$;&|<>]")
FORBIDDEN_STATUSES = {"verified", "confirmed", "publishable"}
INTEGRATION_ENTRYPOINTS = {
    "maigret": "maigret.run_maigret",
    "rlm": "rlm.run_rlm",
}


class IntegrationError(ValueError):
    """Raised when an integration request is invalid or unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def case_workspace_root(root: Path | None = None) -> Path:
    if root is not None:
        return (root / "cases").resolve()
    configured = os.environ.get("SPOTLIGHT_CASES_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (repo_root() / "cases").resolve()


def reject_shell_metacharacters(value: str, label: str) -> None:
    if UNSAFE_CHARS_RE.search(value):
        raise IntegrationError(f"{label} contains shell metacharacters or control characters")


def validate_slug(value: Any, label: str = "slug") -> str:
    if not isinstance(value, str):
        raise IntegrationError(f"{label} must be a string")
    reject_shell_metacharacters(value, label)
    if not SLUG_RE.match(value) or ".." in value:
        raise IntegrationError(f"invalid {label}")
    return value


def validate_run_id(value: Any) -> str:
    if not isinstance(value, str):
        raise IntegrationError("run_id must be a string")
    reject_shell_metacharacters(value, "run_id")
    if not RUN_ID_RE.match(value) or ".." in value:
        raise IntegrationError("invalid run_id")
    return value


def load_request(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).expanduser().resolve()
    with open(request_path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise IntegrationError("request must be a JSON object")
    return data


def resolve_within(base: Path, candidate: str | Path) -> Path:
    raw = Path(candidate)
    if raw.is_absolute():
        raise IntegrationError("absolute paths are not allowed")
    for part in raw.parts:
        if part in {"..", ""} or part.startswith("-"):
            raise IntegrationError("path contains unsafe segment")
    base_path = base.resolve()
    resolved = (base_path / raw).resolve(strict=False)
    if base_path != resolved and base_path not in resolved.parents:
        raise IntegrationError(f"path escapes allowed base: {resolved}")
    return resolved


def case_dir(project: str, root: Path | None = None) -> Path:
    project_slug = validate_slug(project, "project")
    return resolve_within(case_workspace_root(root), project_slug)


def integration_run_dir(project: str, integration_id: str, run_id: str, root: Path | None = None) -> Path:
    validate_slug(integration_id, "integration_id")
    run = validate_run_id(run_id)
    return case_dir(project, root) / "research" / integration_id / run


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def forbid_verified_statuses(items: list[dict[str, Any]], label: str) -> None:
    for index, item in enumerate(items):
        status = str(item.get("verification_status", item.get("status", ""))).lower()
        if status in FORBIDDEN_STATUSES:
            raise IntegrationError(f"{label}[{index}] uses forbidden status {status!r}")


def run_subprocess(args: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    if not args or not all(isinstance(arg, str) and arg for arg in args):
        raise IntegrationError("subprocess args must be a non-empty list of strings")
    return subprocess.run(args, cwd=cwd, timeout=timeout, text=True, capture_output=True, check=False)


@dataclass
class RunManifest:
    integration_id: str
    project: str
    run_id: str
    request: dict[str, Any]
    started_at: str = field(default_factory=utc_now)
    status: str = "started"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    command: list[str] | None = None
    exit_code: int | None = None
    finished_at: str | None = None

    def finish(self, status: str, *, exit_code: int | None = None, error: str | None = None) -> dict[str, Any]:
        self.status = status
        self.exit_code = exit_code
        self.finished_at = utc_now()
        if error:
            self.errors.append(error)
        return self.as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {
            "integration_id": self.integration_id,
            "project": self.project,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "warnings": self.warnings,
            "errors": self.errors,
            "command": self.command,
            "exit_code": self.exit_code,
            "request": self.request,
        }


def dispatch_integration(argv: list[str] | None = None) -> int:
    """Run one request-based integration through a closed module registry.

    The Engine calls this file directly so the integration id is never turned
    into a path or shell command. Each registered module retains its own
    request validation and the shared containment/status helpers above.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        names = "|".join(sorted(INTEGRATION_ENTRYPOINTS))
        print(f"usage: _runner.py <{names}> <request.json> [integration options]")
        return 0 if args else 2

    integration_id = validate_slug(args[0], "integration_id")
    module_name = INTEGRATION_ENTRYPOINTS.get(integration_id)
    if module_name is None:
        raise IntegrationError(f"unsupported integration_id {integration_id!r}")

    integration_root = Path(__file__).resolve().parent
    if str(integration_root) not in sys.path:
        # Engine launches Python with -I -S so ambient PYTHONPATH, user/system
        # site packages, and sitecustomize cannot run before this reviewed
        # dispatcher. Add only this checkout's integration root afterward.
        sys.path.insert(0, str(integration_root))
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise IntegrationError(f"integration {integration_id!r} has no runnable entry point")

    previous = sys.argv
    sys.argv = [f"_runner.py {integration_id}", *args[1:]]
    try:
        result = entrypoint()
    finally:
        sys.argv = previous
    return int(result or 0)


def main() -> int:
    try:
        return dispatch_integration()
    except (ImportError, IntegrationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
