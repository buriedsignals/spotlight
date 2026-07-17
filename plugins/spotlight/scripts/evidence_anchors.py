"""Shared, stdlib-only validation for case-local text evidence anchors."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


RLM_LEAD_MARKER = "# RLM-distilled leads from"


def normalized(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_evidence_path(case_dir: Path, value: Any) -> Path | None:
    """Resolve a regular artifact only within this case's research/evidence roots."""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    case_real = case_dir.resolve()
    roots = [(case_dir / name).resolve() for name in ("research", "evidence")]
    supplied = Path(raw).expanduser()
    candidate = supplied if supplied.is_absolute() else case_dir / supplied
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_file() or resolved.is_symlink():
        return None
    try:
        resolved.relative_to(case_real)
    except ValueError:
        return None
    return resolved if any(is_relative_to(resolved, root) for root in roots) else None


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def rlm_lead_failure(path: Path) -> str | None:
    """RLM summaries locate evidence; only their stored raw sidecars may anchor it."""
    try:
        with path.open(errors="replace") as handle:
            opening_lines = [handle.readline() for _ in range(8)]
    except OSError as exc:
        return f"cannot inspect {path.name}: {exc}"
    if any(
        line.lstrip("\ufeff").strip().startswith(RLM_LEAD_MARKER)
        for line in opening_lines
    ):
        raw = path.with_name(path.name + ".raw")
        guidance = f"; cite {raw.name} instead" if raw.is_file() else ""
        return f"{path.name} is an RLM-distilled lead file, not source evidence{guidance}"
    return None


def json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("must start with '/'")
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError(f"does not resolve at {token!r}")
    return current


def selected_source_text(path: Path, source_ref: dict[str, Any]) -> tuple[str | None, str | None]:
    pointer = source_ref.get("json_pointer")
    if isinstance(pointer, str):
        try:
            document = json.loads(path.read_text())
            value = json_pointer(document, pointer)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
            return None, f"JSON pointer {pointer!r} in {path.name} {exc}"
        if isinstance(value, str):
            return value, None
        return json.dumps(value, ensure_ascii=False, sort_keys=True), None

    start = source_ref.get("line_start")
    end = source_ref.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return None, "source_ref needs a valid line_start/line_end or json_pointer"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return None, f"cannot read {path.name}: {exc}"
    if end > len(lines):
        return None, f"source_ref lines {start}-{end} exceed {path.name} ({len(lines)} lines)"
    return "\n".join(lines[start - 1:end]), None


def validate_source_ref(
    case_dir: Path,
    source_ref: Any,
    quote: Any,
) -> tuple[set[Path], list[str]]:
    if not isinstance(source_ref, dict):
        return set(), ["source_ref must be an object"]
    path = case_evidence_path(case_dir, source_ref.get("path"))
    if path is None:
        return set(), [
            f"source_ref path {source_ref.get('path')!r} does not resolve to a case-local file"
        ]
    lead_failure = rlm_lead_failure(path)
    if lead_failure:
        return {path}, [lead_failure]
    selected, error = selected_source_text(path, source_ref)
    if error:
        return {path}, [error]
    wanted = normalized(quote)
    if wanted and wanted not in normalized(selected):
        return {path}, [f"exact quote is not present in the selected source_ref from {path.name}"]
    return {path}, []
