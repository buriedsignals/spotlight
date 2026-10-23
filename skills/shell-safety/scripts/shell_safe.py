#!/usr/bin/env python3
"""Portable validation helpers for the shared shell-safety skill."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
TIMESTAMP_RE = re.compile(r"^\d{4}(-\d{2}){0,2}([T ][0-9:.+-]+Z?)?$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
UNSAFE_CHARS_RE = re.compile(r"[\x00\r\n`$;&|<>]")
# A URL is passed to the shell inside double quotes ("{URL}"). There "&" is
# inert, so it is allowed in the query string, where it separates parameters.
# Quotes, backslashes, whitespace and control characters never belong in a URL.
URL_UNSAFE_RE = re.compile(r"[\x00-\x20\x7f`$;|<>\"'\\]")


class SafetyError(ValueError):
    pass


def reject_shell_metacharacters(value: str, label: str) -> None:
    if UNSAFE_CHARS_RE.search(value):
        raise SafetyError(f"{label} contains shell metacharacters or control characters")


def validate_url(value: str) -> str:
    if URL_UNSAFE_RE.search(value):
        raise SafetyError("url contains shell metacharacters, quotes, whitespace or control characters")
    parsed = urlparse(value)
    if "&" in value.split("?", 1)[0]:
        raise SafetyError("url may contain '&' only in its query string")
    if parsed.scheme not in {"http", "https"}:
        raise SafetyError("url scheme must be http or https")
    if not parsed.netloc:
        raise SafetyError("url must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise SafetyError("url must not contain credentials")
    if parsed.fragment:
        raise SafetyError("url must not contain fragments")
    return value


def validate_doi(value: str) -> str:
    reject_shell_metacharacters(value, "doi")
    if not DOI_RE.match(value):
        raise SafetyError("invalid DOI")
    return value


def validate_timestamp(value: str) -> str:
    reject_shell_metacharacters(value, "timestamp")
    if not TIMESTAMP_RE.match(value):
        raise SafetyError("invalid timestamp")
    return value


def validate_slug(value: str) -> str:
    reject_shell_metacharacters(value, "slug")
    if not SLUG_RE.match(value):
        raise SafetyError("invalid slug")
    if ".." in value:
        raise SafetyError("slug cannot contain path traversal")
    return value


def resolve_path(base: str, candidate: str) -> Path:
    reject_shell_metacharacters(candidate, "path")
    raw = Path(candidate)
    if any(part.startswith("-") for part in raw.parts):
        raise SafetyError("path contains a leading-dash segment")
    base_path = Path(base).expanduser().resolve()
    target = raw.expanduser()
    if not target.is_absolute():
        target = base_path / target
    resolved = target.resolve(strict=False)
    if base_path != resolved and base_path not in resolved.parents:
        raise SafetyError(f"path escapes allowed base: {resolved}")
    return resolved


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str], description: str = "shell-safety validation helper") -> int:
    parser = argparse.ArgumentParser(description=description)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate-url").add_argument("url")
    sub.add_parser("validate-doi").add_argument("doi")
    sub.add_parser("validate-timestamp").add_argument("timestamp")
    sub.add_parser("validate-slug").add_argument("slug")
    path = sub.add_parser("resolve-path")
    path.add_argument("--base", required=True)
    path.add_argument("--path", required=True)
    probe = sub.add_parser("destructive-probe")
    probe.add_argument("--base", required=True)
    probe.add_argument("--path", required=True)
    sub.add_parser("sha256").add_argument("path")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "validate-url":
            print(validate_url(args.url))
        elif args.cmd == "validate-doi":
            print(validate_doi(args.doi))
        elif args.cmd == "validate-timestamp":
            print(validate_timestamp(args.timestamp))
        elif args.cmd == "validate-slug":
            print(validate_slug(args.slug))
        elif args.cmd == "resolve-path":
            print(resolve_path(args.base, args.path))
        elif args.cmd == "destructive-probe":
            resolved = resolve_path(args.base, args.path)
            kind = "file" if resolved.is_file() else "directory" if resolved.is_dir() else "missing"
            print(f"resolved={resolved}\nexists={str(resolved.exists()).lower()}\ntype={kind}")
        elif args.cmd == "sha256":
            print(sha256_file(args.path))
        return 0
    except (OSError, SafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
