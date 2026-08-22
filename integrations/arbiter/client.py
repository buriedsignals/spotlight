"""Small, stdlib-only request boundary for the Arbiter API.

The boundary owns deployment URL validation, in-process credential handling,
request serialization, sensitive-mode egress blocking, and case research path
containment.  Callers receive decoded response objects without normalizing or
discarding upstream fields.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Revalidate same-origin redirects and isolate the Authorization bearer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urlsplit(req.full_url)
        target = urlsplit(newurl)
        source_port = source.port or (443 if source.scheme == "https" else None)
        target_port = target.port or (443 if target.scheme == "https" else None)
        target_path = target.path
        for _ in range(4):
            target_path = unquote(target_path)
        if (
            source.scheme != "https"
            or target.scheme != "https"
            or source.hostname != target.hostname
            or source_port != target_port
            or target.username
            or target.password
            or not target.hostname
            or not target_path.startswith("/api/v1/")
            or "\\" in target_path
            or any(segment in {".", ".."} for segment in target_path.split("/"))
        ):
            raise ValueError("Arbiter redirect changed origin, scheme, or API path")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one validated address while retaining hostname TLS checks."""

    def __init__(self, host, *, address_info, tls_hostname, **kwargs):
        self._address_family, self._address = address_info
        self._tls_hostname = tls_hostname
        super().__init__(host, **kwargs)

    def connect(self):
        sock = socket.socket(self._address_family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self._address)
            if self._tunnel_host:
                self.sock = sock
                self._tunnel()
            self.sock = self._context.wrap_socket(sock, server_hostname=self._tls_hostname)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPSHandler(HTTPSHandler):
    """HTTPS handler whose connection uses the address validated by the client."""

    def __init__(self, address_info):
        super().__init__()
        self._address_info = address_info

    def https_open(self, req):
        tls_hostname = urlsplit(req.full_url).hostname

        def connection_factory(host, **kwargs):
            return _PinnedHTTPSConnection(
                host,
                address_info=self._address_info,
                tls_hostname=tls_hostname,
                **kwargs,
            )

        return self.do_open(
            connection_factory,
            req,
            context=self._context,
            check_hostname=self._check_hostname,
        )


def _build_pinned_opener(address_info):
    return build_opener(
        SameOriginRedirectHandler(),
        _PinnedHTTPSHandler(address_info),
    ).open

DEFAULT_API_BASE = "https://arbiter.simppl.org/api/v1"
DEFAULT_TIMEOUT = 60.0

_NUMERIC_HOST_RE = re.compile(r"^[0-9.]+$")
_NUMERIC_LABEL_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)$")
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


def _blocked_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    return bool(
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_unspecified
        or not parsed.is_global
    )


def _numeric_alias(hostname: str) -> bool:
    labels = hostname.rstrip(".").split(".")
    if any(label.lower().startswith("0x") and _NUMERIC_LABEL_RE.fullmatch(label) for label in labels):
        return True
    numeric = 0
    for label in labels:
        if _NUMERIC_LABEL_RE.fullmatch(label):
            numeric += 1
        else:
            numeric = 0
        if numeric >= 2:
            return True
    return False


def _resolve_addresses(hostname: str, port: int = 443):
    """Resolve and validate every address before an authenticated connection."""
    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Arbiter API host could not be resolved") from exc
    addresses = []
    for result in results:
        try:
            family = result[0]
            sockaddr = result[4]
            address = sockaddr[0]
        except (IndexError, TypeError) as exc:
            raise ValueError("Arbiter API resolver returned an invalid address") from exc
        if family not in {socket.AF_INET, socket.AF_INET6} or _blocked_address(address):
            raise ValueError("Arbiter API host resolved to a blocked address")
        address_info = (family, sockaddr)
        if address_info not in addresses:
            addresses.append(address_info)
    if not addresses:
        raise ValueError("Arbiter API host has no usable addresses")
    return tuple(addresses)


def _safe_hostname(hostname: str) -> bool:
    """Reject local, reserved, numeric, and unsafe DNS targets."""
    lowered = hostname.rstrip(".").lower()
    labels = lowered.split(".")
    if (
        not lowered
        or any(not label or len(label) > 63 for label in labels)
        or any(ord(char) > 127 for char in lowered)
        or any(char in lowered for char in "%[]")
        or lowered in {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}
        or lowered.endswith(_BLOCKED_DNS_SUFFIXES)
        or _NUMERIC_HOST_RE.fullmatch(lowered)
        or _numeric_alias(lowered)
    ):
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        address = None
    if address is not None:
        return not _blocked_address(str(address))
    try:
        _resolve_addresses(lowered)
    except (OSError, ValueError):
        return False
    return True


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
        opener: Callable[..., Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_base = validate_api_base(api_base)
        parsed_base = urlsplit(self.api_base)
        self._hostname = parsed_base.hostname
        self._port = parsed_base.port or 443
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
        opener: Callable[..., Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        credential_provider: Callable[[], str] | None = None,
    ) -> "ArbiterClient":
        values = os.environ if env is None else env
        api_key = values.get("ARBITER_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            if credential_provider is None:
                raise ValueError("ARBITER_API_KEY is required")
            api_key = credential_provider()
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("ARBITER_API_KEY is required")
        base = values.get("ARBITER_API_BASE", DEFAULT_API_BASE)
        return cls(base, api_key, sensitive=sensitive, opener=opener, timeout=timeout)

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        """Issue one request and return response bytes without normalization."""
        if self._sensitive:
            raise PermissionError("Arbiter requests are unavailable in sensitive mode")
        verb = method.upper()
        if verb not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        _validate_request_path(path)
        if verb == "GET" and body is not None:
            raise ValueError("GET requests must use query parameters, not a JSON body")
        resolved_addresses = _resolve_addresses(self._hostname, self._port)
        encoded_query = urlencode(list(query.items()), doseq=True) if query else ""
        url = self.api_base + path + ("?" + encoded_query if encoded_query else "")
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=data, method=verb)
        request.add_header("Authorization", f"Bearer {self._api_key}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        request_timeout = self._timeout if timeout is None else timeout
        if request_timeout <= 0:
            raise ValueError("request timeout must be positive")
        opener = self._opener
        if opener is None:
            opener = _build_pinned_opener(resolved_addresses[0])
        with opener(request, timeout=request_timeout) as response:
            return response.read()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Issue one JSON request, blocking sensitive mode before opening it."""
        raw = self.request_raw(method, path, query=query, body=body, timeout=timeout)
        return json.loads(raw.decode("utf-8"))


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


def _canonical_system_root_alias(path: Path) -> Path:
    """Normalize macOS's stable ``/var`` alias before descriptor walking."""
    if path.is_absolute() and path.parts[1:2] == ("var",):
        return Path("/private", "var", *path.parts[2:])
    return path


def _open_directory_no_follow(path: Path) -> int:
    """Open every directory component without following replacement links."""
    components = path.parts
    if path.is_absolute():
        descriptor = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        components = components[1:]
    else:
        descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in components:
            if component in {"", ".", ".."}:
                raise OSError("unsafe research directory")
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_research_file(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open one research entry through no-follow directory descriptors."""
    path = _canonical_system_root_alias(path)
    case_fd = _open_directory_no_follow(path.parent.parent)
    try:
        research_fd = os.open(
            "research", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=case_fd
        )
    finally:
        os.close(case_fd)
    try:
        return os.open(path.name, flags | os.O_NOFOLLOW, mode, dir_fd=research_fd)
    finally:
        os.close(research_fd)


