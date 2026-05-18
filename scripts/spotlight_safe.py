#!/usr/bin/env python3
"""Small validation helpers for Spotlight shell-safety checks."""

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


class SafetyError(ValueError):
    pass


def reject_shell_metacharacters(value: str, label: str) -> None:
    if UNSAFE_CHARS_RE.search(value):
        raise SafetyError(f"{label} contains shell metacharacters or control characters")


def validate_url(value: str) -> str:
    reject_shell_metacharacters(value, "url")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise SafetyError("url scheme must be http or https")
    if not parsed.netloc:
        raise SafetyError("url must include a host")
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
    for part in raw.parts:
        if part.startswith("-"):
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Spotlight shell-safety validation helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_url = sub.add_parser("validate-url")
    p_url.add_argument("url")

    p_doi = sub.add_parser("validate-doi")
    p_doi.add_argument("doi")

    p_ts = sub.add_parser("validate-timestamp")
    p_ts.add_argument("timestamp")

    p_slug = sub.add_parser("validate-slug")
    p_slug.add_argument("slug")

    p_path = sub.add_parser("resolve-path")
    p_path.add_argument("--base", required=True)
    p_path.add_argument("--path", required=True)

    p_probe = sub.add_parser("destructive-probe")
    p_probe.add_argument("--base", required=True)
    p_probe.add_argument("--path", required=True)

    p_hash = sub.add_parser("sha256")
    p_hash.add_argument("path")

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
            exists = resolved.exists()
            kind = "missing"
            if resolved.is_file():
                kind = "file"
            elif resolved.is_dir():
                kind = "directory"
            print(f"resolved={resolved}")
            print(f"exists={str(exists).lower()}")
            print(f"type={kind}")
        elif args.cmd == "sha256":
            print(sha256_file(args.path))
        return 0
    except (OSError, SafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
