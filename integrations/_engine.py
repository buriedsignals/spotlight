"""Where Engine's `bsig` is on this computer.

Engine does not put `bsig` on PATH: on macOS and Linux it links the
`spotlight` command in ~/.local/bin to its own binary. That link, then
`$SPOTLIGHT_BSIG`, then `bsig` on PATH, in that order; None without Engine.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def bsig_path() -> str | None:
    override = os.environ.get("SPOTLIGHT_BSIG", "").strip()
    if override:
        return override if os.path.isabs(override) and os.access(override, os.X_OK) else None
    launcher = Path.home() / ".local" / "bin" / "spotlight"
    if launcher.is_symlink():
        target = launcher.resolve()
        if target.name in ("bsig", "bsig.exe") and os.access(target, os.X_OK):
            return str(target)
    return shutil.which("bsig")
