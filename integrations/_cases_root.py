"""Where cases live, the one rule every Spotlight helper applies.

``$SPOTLIGHT_CASES_ROOT`` (set by ``bsig spotlight`` and the Engine wrapper),
else the install config's ``case_workspace_root`` (legacy ``cases_root``),
else ``cases/`` in the checkout. A relative value is read against the
checkout, never the working directory. Sessions not started by the launcher
(an IDE, a plain ``claude``) therefore still write into the member's root
instead of splitting cases between it and the checkout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._credentials import ROOT, ConfigPathError, config_path


class CasesRootError(ValueError):
    """The install config exists but cannot be read."""


def cases_root_path(checkout: Path = ROOT) -> Path:
    """The configured cases root as an absolute path, not resolved and not
    required to exist. Raises CasesRootError when the config is unreadable."""
    configured = os.environ.get("SPOTLIGHT_CASES_ROOT", "").strip()
    if not configured:
        try:
            path = config_path(checkout)
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError, ConfigPathError) as exc:
            raise CasesRootError("the install config cannot be read") from exc
        if not isinstance(config, dict):
            raise CasesRootError("the install config cannot be read")
        configured = str(config.get("case_workspace_root") or config.get("cases_root") or "cases").strip()
    root = Path(configured).expanduser()
    return root if root.is_absolute() else checkout / root
