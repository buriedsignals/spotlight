#!/usr/bin/env python3
"""Spotlight's shell-safety helper.

The validation commands live in one place, the portable shell-safety skill's
`skills/shell-safety/scripts/shell_safe.py`; this checkout entry point runs the
same code and adds the knowledge-projection helpers Spotlight's ingest uses.
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "shell-safety" / "scripts"))

from shell_safe import (  # noqa: E402
    SafetyError,
    main as _main,
    reject_shell_metacharacters,
    resolve_path,
    sha256_file,
    validate_doi,
    validate_slug,
    validate_timestamp,
    validate_url,
)

__all__ = [
    "SafetyError",
    "bounded_content",
    "markdown_text",
    "projection_id",
    "projection_path",
    "projection_slug",
    "reject_shell_metacharacters",
    "resolve_path",
    "sha256_file",
    "validate_doi",
    "validate_slug",
    "validate_timestamp",
    "validate_url",
]

PROJECTION_ID_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9._:-]{0,250}$")
PROJECTION_PATH_RE = re.compile(r"^(investigations|stories)/[a-z0-9][a-z0-9._/-]*\.md$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Inline values cannot begin a Markdown block because the renderer controls the
# line prefix. Escape only inline delimiters; preserve ordinary prose punctuation.
MARKDOWN_META_RE = re.compile(r"([\\`*\[\]<>])")


def projection_id(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not PROJECTION_ID_RE.fullmatch(value):
        raise SafetyError(f"invalid {label}")
    return value


def projection_slug(value: str) -> str:
    """Return a stable, collision-resistant URL/path component."""
    if not isinstance(value, str) or not value or len(value) > 256:
        raise SafetyError("invalid projection identifier")
    if CONTROL_RE.search(value):
        raise SafetyError("projection identifier contains control characters")
    stem = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:72] or "record"
    return f"{stem}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def projection_path(kind: str, identifier: str) -> str:
    if kind not in {"investigations", "stories"}:
        raise SafetyError("invalid projection path kind")
    value = f"{kind}/{projection_slug(identifier)}.md"
    if not PROJECTION_PATH_RE.fullmatch(value) or ".." in Path(value).parts:
        raise SafetyError("unsafe projection path")
    return value


def markdown_text(value: str, label: str = "text", max_length: int = 65536) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise SafetyError(f"{label} is empty or oversized")
    if CONTROL_RE.search(value):
        raise SafetyError(f"{label} contains control characters")
    # Projected fields are inline by contract. Folding line breaks prevents an
    # attacker from starting a heading, list, quote, or fenced block mid-field.
    escaped = html.escape(" ".join(value.splitlines()), quote=True)
    return MARKDOWN_META_RE.sub(r"\\\1", escaped)


def bounded_content(value: str, maximum: int = 4 * 1024 * 1024) -> str:
    if CONTROL_RE.search(value) or len(value.encode("utf-8")) > maximum:
        raise SafetyError("projection content is unsafe or oversized")
    return value


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:], "Spotlight shell-safety validation helper"))
