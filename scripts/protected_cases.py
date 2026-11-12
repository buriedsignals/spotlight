"""Protected cases folders: where material no cloud service may receive is kept.

A cases folder is protected when it is named `cases_protected` or holds a
`.spotlight-protected` file, so the rule travels with the folder. The Pi
harness applies the same rule when it starts (harness/pi/src/config.ts,
protectedCases): a case there always runs in sensitive mode.
"""

from __future__ import annotations

import os
from pathlib import Path

FOLDER = "cases_protected"
MARKER = ".spotlight-protected"
WIKI = "wiki"


def protected_case(case: Path) -> bool:
    # The folder as named and the folder it really is: a link under another
    # name does not take the protection off.
    roots = {Path(os.path.abspath(case)).parent, case.resolve().parent}
    return any(root.name == FOLDER or (root / MARKER).exists() for root in roots)


def protected_wiki(case: Path) -> Path:
    """The wiki of a protected cases folder: inside it, never synced or shared."""
    return case.resolve().parent / WIKI
