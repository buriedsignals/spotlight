"""Validated case storage and a case-local portable transaction lock."""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .case_writer import atomic_write_json, open_case_directory
from .contract import (
    ATTEMPT_LIMITS,
    LEGACY_STATE_VERSION,
    OrchestrationError,
    STATE_VERSION,
)

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


_V1_ATTEMPT_KINDS = {
    "execution-cycle": "investigation-cycle",
    "fact-check-evidence-repair": "fact-check-evidence-repair",
    "structural-correction": "structural-correction",
}
_V1_FIELDS = {"schema_version", "approvals", "attempts", "decisions", "blocked", "follow_up", "gate1_finalization"}


def _upgrade_v1(state: dict[str, Any]) -> dict[str, Any]:
    """Map a v1 state onto v2 names in memory; the next transition writes v2.

    Only v1 vocabulary is mapped. A v2 name inside a v1 document is refused rather
    than silently merged, so a hand-edited or half-migrated file cannot pass.
    """
    unknown = set(state) - _V1_FIELDS
    if unknown:
        raise OrchestrationError(
            f"data/orchestration.json has unknown v1 fields: {', '.join(sorted(unknown))}"
        )
    upgraded: dict[str, Any] = {"schema_version": STATE_VERSION}
    approvals = state.get("approvals")
    if isinstance(approvals, dict):
        if set(approvals) - {"methodology", "gate1"}:
            raise OrchestrationError("data/orchestration.json has unknown v1 approvals")
        approvals = dict(approvals)
        if "gate1" in approvals:
            approvals["investigation_gate"] = approvals.pop("gate1")
    upgraded["approvals"] = approvals
    attempts = state.get("attempts")
    if isinstance(attempts, dict):
        if set(attempts) - set(_V1_ATTEMPT_KINDS):
            raise OrchestrationError("data/orchestration.json contains invalid attempt counts")
        attempts = {_V1_ATTEMPT_KINDS[kind]: count for kind, count in attempts.items()}
    upgraded["attempts"] = attempts
    upgraded["decisions"] = state.get("decisions")
    if "blocked" in state:
        blocked = state["blocked"]
        if isinstance(blocked, dict):
            blocked = dict(blocked)
            if blocked.get("phase") == "execution":
                blocked["phase"] = "investigation"
            if blocked.get("exhausted_attempt") in _V1_ATTEMPT_KINDS:
                blocked["exhausted_attempt"] = _V1_ATTEMPT_KINDS[blocked["exhausted_attempt"]]
            if isinstance(blocked.get("attempts"), dict):
                blocked["attempts"] = {
                    _V1_ATTEMPT_KINDS.get(kind, kind): count
                    for kind, count in blocked["attempts"].items()
                }
        upgraded["blocked"] = blocked
    if "follow_up" in state:
        upgraded["follow_up"] = state["follow_up"]
    if "gate1_finalization" in state:
        upgraded["investigation_gate_finalization"] = state["gate1_finalization"]
    return upgraded


def load_state(case: Path, data_descriptor: int | None = None) -> dict[str, Any]:
    content = read_data_bytes(case, "orchestration.json", data_descriptor)
    if content is None:
        return new_state()
    try:
        state = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"cannot read data/orchestration.json: {exc}") from exc
    if isinstance(state, dict) and state.get("schema_version") == LEGACY_STATE_VERSION:
        state = _upgrade_v1(state)
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
    for field in ("blocked", "follow_up", "investigation_gate_finalization"):
        if field in state and not isinstance(state[field], dict):
            raise OrchestrationError(f"data/orchestration.json field {field!r} must be an object")
    return state


@contextmanager
def transaction(case: Path) -> Iterator[int]:
    with open_case_directory(case, ("data",)) as descriptor:
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        except OSError as exc:
            raise OrchestrationError(
                f"cannot lock the case data directory: {exc}"
            ) from exc
        try:
            yield descriptor
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            else:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
