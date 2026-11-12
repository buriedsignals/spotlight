"""Protected cases folders: where material no cloud service may receive is kept.

A cases folder is protected when it is named `cases_protected` or holds a
`.spotlight-protected` file, so the rule travels with the folder. The Pi
harness applies the same rule when it starts (harness/pi/src/config.ts,
protectedCases): a case there always runs in sensitive mode.
"""

from __future__ import annotations

from pathlib import Path

FOLDER = "cases_protected"
MARKER = ".spotlight-protected"
WIKI = "wiki"


def protected_case(case: Path) -> bool:
    root = case.resolve().parent
    return root.name == FOLDER or (root / MARKER).exists()


def protected_wiki(case: Path) -> Path:
    """The wiki of a protected cases folder: inside it, never synced or shared."""
    return case.resolve().parent / WIKI
