"""Refuse outgoing web requests that name a protected source.

A search query or URL that names one of the member's protected sources would
tell the search engine, the site or the scraping service who the case is
about. With Engine installed, the text goes through ``bsig wiki redact`` over
the member's wiki first; if anything would be masked, the request is refused,
because a masked query finds nothing useful and the refusal tells the
investigator why. Without Engine there is no protected-source register.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._credentials import config_path
from ._engine import bsig_path


class ProtectedSourceError(RuntimeError):
    """The request names a protected source, or could not be checked."""


def _workspace() -> Path | None:
    try:
        config = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = ((config.get("knowledge_destination") or {}) if isinstance(config, dict) else {}).get("workspace_path")
    return Path(value).expanduser() if isinstance(value, str) and value else None


def check_outgoing(*texts: str) -> None:
    """Raise ProtectedSourceError when any text names a protected source, or
    when Engine names a wiki but the check cannot run."""
    workspace = _workspace()
    if workspace is None:
        return  # no wiki in the install config: no protected-source register
    bsig = bsig_path()
    if bsig is None:
        raise ProtectedSourceError("Engine's bsig was not found, so the request cannot be checked for protected sources")
    command = [bsig, "--json", "wiki", "redact", "--workspace", str(workspace)]
    try:
        done = subprocess.run(command, input=json.dumps({"texts": list(texts)}), capture_output=True,
                              text=True, encoding="utf-8", timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProtectedSourceError(f"the protected-source check failed: {type(exc).__name__}") from None
    if done.returncode != 0:
        raise ProtectedSourceError(f"the protected-source check exited {done.returncode}")
    for line in reversed(done.stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        data = event.get("data") if isinstance(event, dict) else None
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict) and isinstance(result.get("redacted"), int):
            if result["redacted"] > 0:
                raise ProtectedSourceError("this request names a protected source; it was not sent")
            return
    raise ProtectedSourceError("the protected-source check returned no result")
