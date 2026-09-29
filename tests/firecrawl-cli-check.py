#!/usr/bin/env python3
"""Firecrawl CLI robustness (Windows QA, 2026-09-29). Runs on POSIX and Windows.

- A timed-out CLI whose own child keeps the output pipe open must not hang.
  npm installs `firecrawl` as a .cmd wrapper around node on Windows, and
  CPython's subprocess.run waits on the pipes after a timeout there.
- The CLI never gets the caller's stdin, so a key prompt cannot block.
- Search output with non-Latin text prints under a cp1252 console.
- CLI messages reach the user without terminal colour codes.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations._cli import run_captured, strip_ansi  # noqa: E402

WINDOWS = sys.platform == "win32"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def script(directory: pathlib.Path, name: str, python_body: str) -> pathlib.Path:
    """A CLI shaped like npm's: a shell wrapper that runs a separate process."""
    helper = directory / f"{name}.py"
    helper.write_text(python_body, encoding="utf-8")
    if WINDOWS:
        path = directory / f"{name}.cmd"
        path.write_text(f'@"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8")
    else:
        path = directory / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# 1. Timeout kills the whole tree even when a grandchild holds stdout open.
with tempfile.TemporaryDirectory() as tmp:
    marker = pathlib.Path(tmp) / "grandchild.pid"
    grandchild = f"import os, pathlib, time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
    wrapper = script(pathlib.Path(tmp), "slowcli",
                     f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {grandchild!r}]).wait()\n")
    started = time.monotonic()
    try:
        run_captured([str(wrapper)], timeout=2)
        check("timeout raises", False, "returned instead of raising")
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        check("timeout returns promptly with a pipe-holding grandchild", elapsed < 15, f"{elapsed:.1f}s")
    time.sleep(0.5)
    pid = int(marker.read_text().strip()) if marker.exists() else 0
    check("grandchild is killed on timeout", pid > 0 and not alive(pid), f"pid {pid}")

# 2. No stdin: a prompting CLI sees EOF instead of waiting.
started = time.monotonic()
proc = run_captured([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10)
check("child stdin is empty", proc.stdout.strip() == "''" and time.monotonic() - started < 10, proc.stdout)

# 3. UTF-8 decoding regardless of locale or console code page.
proc = run_captured([sys.executable, "-c", "import sys; sys.stdout.buffer.write('東京 القاهرة'.encode())"], timeout=10)
check("child output decodes as UTF-8", proc.stdout == "東京 القاهرة", repr(proc.stdout))

# 4. Colour codes are stripped.
check("strip_ansi", strip_ansi("\x1b[31mError:\x1b[0m no key \x1b[?25h") == "Error: no key ")

# 5. The search CLI prints non-Latin hits under a cp1252 stdout.
snippet = textwrap.dedent("""
    import sys
    sys.path.insert(0, %r)
    import integrations.search.__main__ as cli
    from integrations.search.search_types import SearchHit
    cli.search = lambda *a, **k: [SearchHit(url="https://ja.wikipedia.org/wiki/x", title="東京都", snippet="日本の首都", date=None, engine="firecrawl")]
    sys.exit(cli.main(["tokyo"]))
""") % str(ROOT)
env = dict(os.environ, PYTHONIOENCODING="cp1252")
proc = subprocess.run([sys.executable, "-c", snippet], capture_output=True, env=env, stdin=subprocess.DEVNULL)
check("search CLI prints non-Latin text under cp1252", proc.returncode == 0 and "東京都".encode() in proc.stdout,
      proc.stderr.decode("utf-8", "replace")[-300:])

# 6. The Firecrawl search provider reports a key-less CLI cleanly.
import integrations.search.firecrawl_provider as fsearch  # noqa: E402
from integrations.search.search_types import SearchError  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    script(pathlib.Path(tmp), "firecrawl", "print('\\x1b[31mError:\\x1b[0m No API key found')\n")
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = tmp + os.pathsep + old_path
    try:
        fsearch.search("anything")
        check("key-less CLI raises SearchError", False, "returned hits")
    except SearchError as exc:
        message = str(exc)
        check("key-less CLI raises a clean SearchError",
              "No API key found" in message and "\x1b" not in message, repr(message))
    finally:
        os.environ["PATH"] = old_path

sys.exit(1 if failures else 0)
