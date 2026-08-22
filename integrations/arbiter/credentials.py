"""Injected, local-only Arbiter credential migration helpers.

The callbacks are supplied by the host's approved secret boundary.  This module
never knows how to access a keyring and deliberately does not return secret
values, log them, or pass them to a subprocess.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


SecretReader = Callable[[], str | None]
SecretWriter = Callable[[str], Any]


def migrate_navigator_arbiter_key(
    *, read_legacy: SecretReader, write_spotlight: SecretWriter
) -> dict[str, bool]:
    """Copy an existing secret through injected callbacks without exposing it."""
    if not callable(read_legacy) or not callable(write_spotlight):
        raise TypeError("credential migration requires callable secret providers")
    secret = read_legacy()
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("the legacy Arbiter key is not configured")
    write_spotlight(secret)
    return {"migrated": True}


def resolve_spotlight_arbiter_key(read_spotlight: SecretReader) -> str:
    """Resolve a key from Spotlight-owned storage for in-process client setup."""
    if not callable(read_spotlight):
        raise TypeError("Spotlight secret provider must be callable")
    secret = read_spotlight()
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("Spotlight Arbiter key is not configured")
    return secret
