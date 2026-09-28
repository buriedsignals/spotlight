"""Credential lookup shared by Spotlight's keyed integrations.

Resolution happens per call and never writes into ``os.environ``, so a key
changed while a session is running takes effect on the next call.

Order:

1. When ``.spotlight-config.json`` names a top-level ``env_file``, that file
   wins for every name it holds. A ``# managed: NAME …`` line makes it
   authoritative for the listed names even when one is absent, so a key removed
   in Indicator Labs is not replaced by an older copy in the environment.
   Engine-managed installs point it at the file Indicator Labs keeps up to date;
   self-installers may point it at any private file.
2. Otherwise (and for names the file neither holds nor manages): the process
   environment, then any explicit fallback files the caller passes, then the
   checkout ``.env``.

Files are parsed, never executed. Supported line forms: ``NAME=value``,
``NAME='value'`` (with ``'\\''`` for a literal quote), ``NAME="value"`` and an
optional ``export`` prefix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = ".spotlight-config.json"
REDACTED = "[redacted]"


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "'":
            end = raw.find("'", i + 1)
            if end < 0:
                out.append(raw[i + 1:])
                break
            out.append(raw[i + 1:end])
            i = end + 1
        elif ch == "\\" and i + 1 < len(raw):
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


MANAGED_PREFIX = "# managed:"


def _read_text(path: str | os.PathLike[str] | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _managed(text: str) -> frozenset[str]:
    for line in text.splitlines():
        if line.startswith(MANAGED_PREFIX):
            return frozenset(line[len(MANAGED_PREFIX):].split())
    return frozenset()


def managed_names(path: str | os.PathLike[str] | None) -> frozenset[str]:
    """Names the file declares authority over (its ``# managed:`` line)."""
    return _managed(_read_text(path) or "")


def read_env_file(path: str | os.PathLike[str] | None) -> dict[str, str]:
    """Parse KEY=VALUE lines without exporting or echoing anything."""
    text = _read_text(path)
    return {} if text is None else _parse(text)


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, _, raw = line.partition("=")
        name = name.strip()
        if name:
            values[name] = _unquote(raw)
    return values


def config_env_file(root: Path = ROOT) -> Path | None:
    """The top-level ``env_file`` named in the checkout config, if any."""
    try:
        config = json.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = config.get("env_file") if isinstance(config, dict) else None
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return None


def from_authority(name: str, root: Path = ROOT) -> tuple[str, str] | None:
    """What the configured ``env_file`` decides for ``name``: ``(value, where)``
    when it holds or manages the name (``("", "")`` = not configured), or
    ``None`` when there is no configured file or it neither holds nor manages
    the name. An unreadable configured file decides "not configured" for every
    name (fail closed) rather than letting older copies elsewhere apply."""
    authority = config_env_file(root)
    if authority is None:
        return None
    text = _read_text(authority)
    if text is None:
        return "", ""
    value = _parse(text).get(name, "")
    if value:
        return value, str(authority)
    if name in _managed(text):
        return "", ""
    return None


def credential_with_source(name: str, root: Path = ROOT, *,
                           fallbacks: Iterable[str | os.PathLike[str] | None] = ()) -> tuple[str, str]:
    """Return ``(value, where)``; ``("", "")`` when the name is not configured."""
    decided = from_authority(name, root)
    if decided is not None:
        return decided
    if os.environ.get(name):
        return os.environ[name], "environment"
    for candidate in (*fallbacks, root / ".env"):
        if not candidate:
            continue
        value = read_env_file(candidate).get(name, "")
        if value:
            return value, str(Path(candidate).expanduser())
    return "", ""


def credential(name: str, root: Path = ROOT, *,
               fallbacks: Iterable[str | os.PathLike[str] | None] = ()) -> str:
    """The value configured for ``name``, or ``""``."""
    return credential_with_source(name, root, fallbacks=fallbacks)[0]


def subprocess_env(names: Iterable[str], root: Path = ROOT,
                   base: Mapping[str, str] | None = None) -> dict[str, str]:
    """An environment for a child process that carries exactly the resolved
    values for ``names``: inherited copies are removed first, so a stale value
    can never reach the child."""
    env = dict(os.environ if base is None else base)
    for name in names:
        env.pop(name, None)
        value = credential(name, root)
        if value:
            env[name] = value
    return env


def redact(text: str, values: Iterable[str]) -> str:
    """Replace each credential value in ``text`` before it is shown or stored."""
    for value in sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


def executable(name: str, root: Path = ROOT) -> str | None:
    """A command on PATH, else the absolute path in the config's
    ``executables.<name>`` (for agents whose shell PATH lacks it)."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    try:
        config = json.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    configured = (config.get("executables") or {}).get(name) if isinstance(config, dict) else None
    if isinstance(configured, str) and Path(configured).is_absolute() and os.access(configured, os.X_OK):
        return configured
    return None


def write_output(path: Path, body: bytes) -> None:
    """Write a response next to ``path`` and rename it into place, so a
    symlink swapped in while a request runs is replaced, never followed."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
