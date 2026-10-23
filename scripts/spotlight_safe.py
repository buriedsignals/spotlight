#!/usr/bin/env python3
"""Spotlight's shell-safety helper.

The validation commands live in one place, the portable shell-safety skill's
`skills/shell-safety/scripts/shell_safe.py`; this checkout entry point runs the
same code.
"""

from __future__ import annotations

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
    "reject_shell_metacharacters",
    "resolve_path",
    "sha256_file",
    "validate_doi",
    "validate_slug",
    "validate_timestamp",
    "validate_url",
]


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:], "Spotlight shell-safety validation helper"))
