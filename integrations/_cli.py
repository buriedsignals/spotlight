"""Run an external CLI and capture its text output, on every platform.

``subprocess.run(..., timeout=)`` is not enough on Windows: a CLI installed by
npm is a ``.cmd`` wrapper around node, and on timeout only the wrapper is
killed. Node keeps the output pipes open, so the call never returns (seen with
``firecrawl`` in Windows QA, 2026-09-29). This helper kills the whole process
tree, never lets the child prompt on the caller's console, and decodes output
as UTF-8 regardless of the console code page.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove terminal colour and cursor codes a CLI adds to its messages."""
    return _ANSI.sub("", text)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run_captured(args: Sequence[str], *, env: Mapping[str, str] | None = None,
                 cwd: str | os.PathLike[str] | None = None,
                 timeout: float | None) -> subprocess.CompletedProcess[str]:
    """Run ``args`` with no stdin and return its UTF-8 output.

    Raises ``subprocess.TimeoutExpired`` after killing the child and everything
    it started when ``timeout`` seconds pass (``None`` waits indefinitely).
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=None if env is None else dict(env),
        cwd=cwd,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise subprocess.TimeoutExpired(list(args), timeout) from None
    return subprocess.CompletedProcess(list(args), proc.returncode, stdout, stderr)


def utf8_stdio() -> None:
    """Make stdout and stderr write UTF-8. A Windows console or pipe defaults to
    cp1252, which cannot encode most page titles and snippets."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
