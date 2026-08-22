"""Small, stdlib-only request boundary for the Arbiter API.

The boundary owns deployment URL validation, in-process credential handling,
request serialization, sensitive-mode egress blocking, and case research path
containment.  Callers receive decoded response objects without normalizing or
discarding upstream fields.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_API_BASE = "https://arbiter.simppl.org/api/v1"
DEFAULT_TIMEOUT = 60.0

_NUMERIC_HOST_RE = re.compile(r"^[0-9.]+$")
_BLOCKED_DNS_SUFFIXES = (
    ".internal",
    ".intranet",
    ".localhost",
    ".local",
    ".home.arpa",
    ".nip.io",
    ".sslip.io",
    ".xip.io",
    ".localtest.me",
)


def _safe_hostname(hostname: str) -> bool:
    """Reject local, reserved, numeric, and wildcard DNS targets."""
    lowered = hostname.rstrip(".").lower()
    labels = lowered.split(".")
    numeric_tail = len(labels) >= 4 and all(label.isdecimal() for label in labels[-4:])
    if (
        not lowered
        or any(not label or len(label) > 63 for label in labels)
        or any(ord(char) > 127 for char in lowered)
        or lowered in {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}
        or lowered.endswith(_BLOCKED_DNS_SUFFIXES)
        or _NUMERIC_HOST_RE.fullmatch(lowered)
        or numeric_tail
    ):
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return "." in lowered
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def validate_api_base(value: str) -> str:
    """Validate the exact HTTPS ``/api/v1`` deployment base used by the client."""
    if not isinstance(value, str) or not value:
        raise ValueError("ARBITER_API_BASE must be a non-empty HTTPS URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ARBITER_API_BASE is malformed") from exc
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("ARBITER_API_BASE must use HTTPS without userinfo")
    if parsed.query or parsed.fragment or port not in (None, 443):
        raise ValueError("ARBITER_API_BASE must not include query, fragment, or a non-default port")
    if parsed.path != "/api/v1":
        raise ValueError("ARBITER_API_BASE must end at /api/v1")
    if not _safe_hostname(hostname):
        raise ValueError("ARBITER_API_BASE host is not an allowed deployment host")
    return urlunsplit(("https", hostname.lower(), "/api/v1", "", ""))


def _validate_request_path(path: str) -> str:
    """Validate an API-relative path without accepting encoded traversal."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("request path must be an absolute API path")
    if "?" in path or "#" in path:
        raise ValueError("request path must not include query or fragment")
    decoded = path
    for _ in range(4):
        decoded = unquote(decoded)
    if any(ord(char) < 32 for char in decoded) or "?" in decoded or "#" in decoded:
        raise ValueError("request path contains unsafe characters")
    if "\\" in decoded or any(segment in {".", ".."} for segment in decoded.split("/")):
        raise ValueError("request path must not contain traversal segments")
    return path


def _research_root(case_dir: Path | str) -> tuple[Path, Path]:
    """Resolve a real case and research directory without following a root link."""
    case_path = Path(case_dir)
    if case_path.is_symlink() or not case_path.is_dir():
        raise ValueError("case research directory is missing or unsafe")
    root = case_path.resolve(strict=True)
    research = case_path / "research"
    if research.is_symlink() or not research.is_dir():
        raise ValueError("case research directory is missing or unsafe")
    resolved_research = research.resolve(strict=True)
    if resolved_research != root / "research":
        raise ValueError("case research directory is missing or unsafe")
    return root, resolved_research



class ArbiterClient:
    """Authenticated JSON client with a testable opener dependency."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        *,
        sensitive: bool = False,
        opener: Callable[..., Any] = urlopen,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_base = validate_api_base(api_base)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("ARBITER_API_KEY must be configured")
        if timeout <= 0:
            raise ValueError("request timeout must be positive")
        self._api_key = api_key
        self._sensitive = sensitive
        self._opener = opener
        self._timeout = timeout

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        sensitive: bool = False,
        opener: Callable[..., Any] = urlopen,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "ArbiterClient":
        values = os.environ if env is None else env
        api_key = values.get("ARBITER_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("ARBITER_API_KEY is required")
        base = values.get("ARBITER_API_BASE", DEFAULT_API_BASE)
        return cls(base, api_key, sensitive=sensitive, opener=opener, timeout=timeout)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Issue one JSON request, blocking sensitive mode before opening it."""
        if self._sensitive:
            raise PermissionError("Arbiter requests are unavailable in sensitive mode")
        verb = method.upper()
        if verb not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        _validate_request_path(path)
        if verb == "GET" and body is not None:
            raise ValueError("GET requests must use query parameters, not a JSON body")
        encoded_query = urlencode(list(query.items()), doseq=True) if query else ""
        url = self.api_base + path + ("?" + encoded_query if encoded_query else "")
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=data, method=verb)
        request.add_header("Authorization", f"Bearer {self._api_key}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError:
            raise
        return payload


def safe_research_path(case_dir: Path | str, filename: str) -> Path:
    """Return one regular research artifact path beneath a real research dir."""
    _root, resolved_research = _research_root(case_dir)
    if not isinstance(filename, str) or not filename or filename.startswith("-"):
        raise ValueError("research filename must be a non-empty ordinary filename")
    candidate_name = Path(filename)
    if candidate_name.name != filename or filename in {".", ".."}:
        raise ValueError("research filename must not contain path separators")
    candidate = Path(case_dir) / "research" / candidate_name
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise ValueError("research path must be a regular file")
    if candidate.resolve(strict=False).parent != resolved_research:
        raise ValueError("research path escapes case directory")
    return candidate
