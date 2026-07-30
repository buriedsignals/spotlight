#!/usr/bin/env python3
"""Regression check for the Arbiter post-id validator (run_id.py).

Arbiter post ids are platform-native, not Spotlight slugs: `scripts/spotlight_safe.py
validate-slug` rejects every real YouTube id, because its charset exists for
Spotlight's own lowercase filename slugs. `run_id.py` is the post-id counterpart,
so this check pins both halves of its contract — that genuine ids are accepted,
and that everything shell- or URL-risky is refused with a usable reason.

Asserts:
  1. real sampled Arbiter post ids validate, including mixed case, `_`, `-`, and
     a leading capital;
  2. the 512-character contract boundary is accepted and 513 is not;
  3. an empty id, a leading `-`, and a leading `.` are rejected;
  4. whitespace, quotes, shell metacharacters, path and URL separators, control
     characters, and non-ASCII characters are all rejected;
  5. every rejection exits 2 with a one-line reason on stderr and prints nothing
     on stdout, with or without a `--` separator before the id;
  6. `validate-slug` really does reject these ids, so the validator is not
     redundant with the helper it exists to replace;
  7. no acceptance is ever phrased as `verified` / `confirmed` / `publishable`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "integrations" / "arbiter" / "run_id.py"

# Statuses Spotlight reserves for its own editorial pipeline. A charset check is
# not a verification, so it must never borrow that vocabulary.
EDITORIAL_STATUSES = ("verified", "confirmed", "publishable")

# Sampled from real Arbiter responses: mixed case, underscore, hyphen, and a
# leading capital are all normal, and every one of these fails validate-slug.
REAL_IDS = ("IDfIYCNsmMI", "dZj9yXtff_U", "S-VgRXOzibQ")

ACCEPTED = (
    *REAL_IDS,
    "a",
    "0",
    "Z",
    "abc123",
    "post.id_with~all-marks",
    "3%2Fencoded",  # percent-encoding is accepted by the API contract
    "A" * 512,      # the contract's upper boundary
)

REJECTED = (
    ("", "empty id"),
    ("A" * 513, "one character past the 512 boundary"),
    ("-leading-dash", "could be read as a command-line option"),
    (".leading-dot", "must start with a letter or digit"),
    ("_leading-underscore", "must start with a letter or digit"),
    ("has space", "whitespace"),
    ("has\ttab", "a tab"),
    ("has\nnewline", "a newline"),
    ("id;whoami", "a command separator"),
    ("id$(whoami)", "a command substitution"),
    ("id`whoami`", "a backtick substitution"),
    ("id&background", "a background operator"),
    ("id|pipe", "a pipe"),
    ("id>redirect", "a redirect"),
    ("id<redirect", "an input redirect"),
    ('id"quote', "a double quote"),
    ("id'quote", "a single quote"),
    ("../escape", "path traversal"),
    ("path/separator", "a path separator"),
    ("back\\slash", "a backslash"),
    ("id?query=1", "a query separator"),
    ("id#fragment", "a fragment separator"),
    ("idé", "a non-ASCII letter"),
    ("id sep", "a Unicode line separator"),
    ("id​zero", "a zero-width space"),
)


def validate(post_id: str) -> subprocess.CompletedProcess[str]:
    """Run the validator on one id without invoking a shell."""
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "validate", "--", post_id],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )


def check_accepted() -> None:
    """Verify every legitimate platform-native id validates cleanly."""
    for post_id in ACCEPTED:
        proc = validate(post_id)
        assert proc.returncode == 0, (post_id[:40], proc.returncode, proc.stderr)
        assert proc.stderr == "", (post_id[:40], proc.stderr)
        for word in EDITORIAL_STATUSES:
            assert word not in proc.stdout.lower(), (post_id[:40], proc.stdout)


def check_rejected() -> None:
    """Verify every risky id is refused with exit 2 and a one-line reason."""
    for post_id, reason in REJECTED:
        proc = validate(post_id)
        assert proc.returncode == 2, (reason, proc.returncode, proc.stdout)
        assert proc.stdout == "", (reason, proc.stdout)
        message = proc.stderr.strip()
        assert message.startswith("error: "), (reason, proc.stderr)
        assert "\n" not in message, (reason, proc.stderr)


def check_slug_gap_is_real() -> None:
    """Verify the bug this validator fixes: validate-slug rejects real ids.

    Without this control the suite could pass against a validator that merely
    delegated to `validate-slug`, which is exactly the behaviour being replaced.
    """
    safe = ROOT / "scripts" / "spotlight_safe.py"
    for post_id in REAL_IDS:
        proc = subprocess.run(
            [sys.executable, str(safe), "validate-slug", post_id],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        assert proc.returncode != 0, (
            f"validate-slug unexpectedly accepted {post_id!r}; run_id.py may be redundant"
        )
        assert validate(post_id).returncode == 0, post_id


def check_leading_dash_without_separator() -> None:
    """Verify a leading-dash id is refused on its own reason, not argparse's.

    Callers write `run_id.py validate "$id"` without a `--`, so an id starting
    with `-` must still reach the validator and come back with the one-line
    charset reason rather than an argparse usage error.
    """
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "validate", "-leading-dash"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr.strip().startswith("error: post id must start with"), proc.stderr
    assert "usage:" not in proc.stderr, proc.stderr

    # The rewriting must not swallow the subcommand's own help.
    helped = subprocess.run(
        [sys.executable, str(VALIDATOR), "validate", "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert helped.returncode == 0 and "usage:" in helped.stdout, helped.stderr


def check_embedded_control_chars() -> None:
    """Verify control characters are rejected by the function itself.

    A NUL byte cannot survive `argv`, so `subprocess` refuses to launch with one
    at all. The rule is still worth pinning, so it is exercised in process — a
    caller importing the validator has no such protection.
    """
    spec = importlib.util.spec_from_file_location("arbiter_run_id", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for post_id in ("id\x00null", "id\x1bescape", "id\x07bell", "id\x7fdel"):
        try:
            module.validate_post_id(post_id)
        except ValueError:
            continue
        raise AssertionError(f"control character accepted in {post_id!r}")
    assert module.validate_post_id("IDfIYCNsmMI") == "IDfIYCNsmMI", (
        "the validator must return the id unchanged"
    )


def check_boundary_is_exact() -> None:
    """Verify the length limit is enforced at exactly 512 characters."""
    assert validate("A" * 511).returncode == 0
    assert validate("A" * 512).returncode == 0
    assert validate("A" * 513).returncode == 2
    assert "512" in validate("A" * 513).stderr, validate("A" * 513).stderr


def main() -> int:
    """Run the Arbiter post-id validator regression suite."""
    check_accepted()
    check_rejected()
    check_leading_dash_without_separator()
    check_embedded_control_chars()
    check_boundary_is_exact()
    check_slug_gap_is_real()
    print("arbiter id: OK - platform-native ids accepted, shell and URL "
          "metacharacters refused, 512-character boundary exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
