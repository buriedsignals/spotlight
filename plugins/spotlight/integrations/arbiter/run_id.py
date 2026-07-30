#!/usr/bin/env python3
"""Validate an Arbiter post id before it is interpolated into a request.

Arbiter post ids are platform-native, not Spotlight slugs: real YouTube ids look
like `IDfIYCNsmMI`, `dZj9yXtff_U` or `S-VgRXOzibQ` — mixed case, underscores,
hyphens, often a leading capital — and the API contract allows 1..512 characters
with percent-encoding accepted. `scripts/spotlight_safe.py validate-slug` rejects
every one of those, because its charset (`^[a-z0-9][a-z0-9._-]{0,127}$`) exists
for Spotlight's own lowercase filename slugs. This validator is the post-id
counterpart, so a `GET /posts/{id}` call can be checked instead of waved through.
Topic ids are 32-character lowercase Convex ids and keep using validate-slug.

Charset rationale: `A-Za-z0-9` plus `.`, `_`, `~`, `-` are the unreserved URL
characters every platform id is built from, and `%` is allowed for the
percent-encoding the contract accepts. Everything shell- or URL-risky is
therefore excluded by construction rather than by blocklist — no whitespace or
control characters, no quotes, no `/` or `\\` (path traversal), no `?`, `#` or
`&` (query and fragment splitting), and no `` ` ``, `$`, `;`, `|`, `<` or `>`
(shell metacharacters). The curls that consume the id double-quote it; this check
means a hostile id never reaches that quoting in the first place. A leading
character may not be `-`, so the id can never be read as a command-line option.

Offline, stdlib-only, and read-only: nothing is fetched and nothing is written.

Usage:
  python3 integrations/arbiter/run_id.py validate "IDfIYCNsmMI"
"""

from __future__ import annotations

import argparse
import re
import sys


MAX_ID_LENGTH = 512
POST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~%-]{0,511}$")


def validate_post_id(value: str) -> str:
    """Check one Arbiter post id against the platform-native id charset.

    Args:
        value: The candidate post id, exactly as it will be sent to Arbiter.

    Returns:
        The id unchanged, so a caller can use the result directly.

    Raises:
        ValueError: When the id is empty, longer than ``MAX_ID_LENGTH``, starts
            with a character other than a letter or digit, or contains any
            character outside the unreserved-plus-percent set.
    """
    if not value:
        raise ValueError("post id must not be empty")
    if len(value) > MAX_ID_LENGTH:
        raise ValueError(f"post id must contain at most {MAX_ID_LENGTH} characters")
    if not POST_ID_RE.match(value):
        raise ValueError(
            "post id must start with a letter or digit and contain only "
            "letters, digits, and the characters . _ ~ - %"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the parser for the post-id validation helper."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate one Arbiter post id")
    validate.add_argument("post_id", help="the post id to check")
    return parser


def main() -> int:
    """Validate one post id and report the outcome on a single line.

    Returns:
        ``0`` when the id is usable, ``2`` with a one-line reason on stderr when
        it is not.
    """
    argv = sys.argv[1:]
    # An id beginning with "-" is exactly one of the cases this validator exists
    # to reject, so the value is separated from the flags before parsing: argparse
    # would otherwise read it as an unknown option and answer with a usage error
    # instead of the one-line reason a caller can act on.
    if (
        len(argv) >= 2
        and argv[0] == "validate"
        and argv[1] not in {"-h", "--help"}
        and "--" not in argv
    ):
        argv = ["validate", "--", *argv[1:]]
    args = build_parser().parse_args(argv)
    try:
        print(f"ok {validate_post_id(args.post_id)}")
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
