"""Validated case storage and a case-local portable transaction lock."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .case_writer import atomic_write_json, open_case_directory
from .contract import ATTEMPT_LIMITS, OrchestrationError, STATE_VERSION

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def resolve_case(
    value: str | Path, authorized_cases_root: str | Path | None = None
) -> Path:
    configured_case = Path(value).expanduser()
    if configured_case.is_symlink():
        raise OrchestrationError(f"case directory must not be a symlink: {value}")
    try:
        case = configured_case.resolve(strict=True)
    except OSError as exc:
        raise OrchestrationError(f"case directory not found: {value}") from exc
    if authorized_cases_root is not None:
        try:
            cases_root = Path(authorized_cases_root).expanduser().resolve(strict=True)
            case.relative_to(cases_root)
        except (OSError, ValueError) as exc:
            raise OrchestrationError(
                "case directory is outside the authorized cases root"
            ) from exc
        if case == cases_root or not cases_root.is_dir():
            raise OrchestrationError(
                "case directory is outside the authorized cases root"
            )
    data = case / "data"
    if not case.is_dir() or not data.is_dir() or data.is_symlink():
        raise OrchestrationError(f"case directory or data directory not found: {case}")
    return case


def case_path(case: Path, relative: str) -> Path:
    candidate = case / relative
    try:
        candidate.resolve(strict=False).relative_to(case)
    except (OSError, ValueError) as exc:
        raise OrchestrationError(f"case path escapes case directory: {relative}") from exc
    return candidate


def new_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_VERSION,
        "approvals": {},
        "attempts": {},
        "decisions": {},
    }


def read_data_bytes(
    case: Path, name: str, data_descriptor: int | None = None
) -> bytes | None:
    relative = f"data/{name}"
    if data_descriptor is None:
        path = case_path(case, relative)
        if path.is_symlink():
            raise OrchestrationError(f"{relative} must not be a symlink")
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            raise OrchestrationError(f"cannot read {relative}: {exc}") from exc

    try:
        metadata = os.stat(name, dir_fd=data_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OrchestrationError(f"cannot inspect {relative}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise OrchestrationError(f"{relative} must be a regular file")
    try:
        handle = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=data_descriptor)
        with os.fdopen(handle, "rb") as stream:
            return stream.read()
    except OSError as exc:
        raise OrchestrationError(f"cannot read {relative}: {exc}") from exc


def load_state(case: Path, data_descriptor: int | None = None) -> dict[str, Any]:
    content = read_data_bytes(case, "orchestration.json", data_descriptor)
    if content is None:
        return new_state()
    try:
        state = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/orchestration.json: {exc}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != STATE_VERSION:
        raise OrchestrationError("data/orchestration.json has an unsupported schema")
    for field in ("approvals", "attempts", "decisions"):
        if not isinstance(state.get(field), dict):
            raise OrchestrationError(f"data/orchestration.json field {field!r} must be an object")
    if any(
        kind not in ATTEMPT_LIMITS
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or count > ATTEMPT_LIMITS[kind]
        for kind, count in state["attempts"].items()
    ):
        raise OrchestrationError("data/orchestration.json contains invalid attempt counts")
    for field in ("blocked", "follow_up", "gate1_finalization"):
        if field in state and not isinstance(state[field], dict):
            raise OrchestrationError(f"data/orchestration.json field {field!r} must be an object")
    return state


@contextmanager
def transaction(case: Path) -> Iterator[int]:
    with open_case_directory(case, ("data",)) as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise OrchestrationError(
                f"cannot lock the case data directory: {exc}"
            ) from exc
        try:
            yield descriptor
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
