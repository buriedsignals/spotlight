"""Junkipedia reads without putting the key on a command line.

    python3 -m integrations.junkipedia status
    python3 -m integrations.junkipedia get posts --param q=<query> --param limit=50 --output <file>
    python3 -m integrations.junkipedia get issues --output <file>
    python3 -m integrations.junkipedia get issues/<issue_id>/posts --param limit=100 --output <file>

`status` prints `junkipedia:enabled` or `junkipedia:unavailable` and never the
key. `get` sends the key as an `Authorization: Bearer` header to one of the
paths above under https://api.junkipedia.org/api/v1 and writes the JSON
response to the output file. Parameters are URL-encoded here, so query text is
never interpolated into a shell command.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

_CHECKOUT = Path(__file__).resolve().parents[2]
if str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))
from integrations._credentials import credential, redact, write_output  # noqa: E402


class _NoRedirect(HTTPRedirectHandler):
    """Never follow a redirect: the Authorization header must not travel to
    another origin or over plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HTTPError(req.full_url, code, f"refused redirect to {newurl}", headers, fp)


urlopen = build_opener(_NoRedirect).open


API = "https://api.junkipedia.org/api/v1/"
PATHS = re.compile(r"^(posts|issues|issues/[A-Za-z0-9_-]{1,64}/posts)$")
PARAM = re.compile(r"^[a-z_]{1,32}$")
TIMEOUT_S = 60


def get(path: str, params: list[str], output_path: Path) -> int:
    if not PATHS.match(path):
        print(f"junkipedia: {path!r} is not a supported path (posts, issues, issues/<id>/posts)", file=sys.stderr)
        return 2
    query: list[tuple[str, str]] = []
    for item in params:
        name, sep, value = item.partition("=")
        if not sep or not PARAM.match(name):
            print(f"junkipedia: {item!r} is not name=value", file=sys.stderr)
            return 2
        query.append((name, value))
    secret = credential("JUNKIPEDIA_API_KEY")
    if not secret:
        print("junkipedia: unavailable — no JUNKIPEDIA_API_KEY is configured", file=sys.stderr)
        return 3
    if output_path.is_symlink():
        print("junkipedia: the output path is a symlink", file=sys.stderr)
        return 2
    url = API + path + ("?" + urlencode(query) if query else "")
    request = Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {secret}")
    request.add_header("Accept", "application/json")
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read()
    except HTTPError as exc:
        detail = redact(exc.read(2048).decode("utf-8", "replace"), [secret])
        print(f"junkipedia: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"junkipedia: request failed: {redact(str(exc), [secret])}", file=sys.stderr)
        return 1
    write_output(output_path, body)
    print(f"junkipedia: wrote {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m integrations.junkipedia")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report whether a key is configured")
    read = sub.add_parser("get", help="read one supported API path into a file")
    read.add_argument("path")
    read.add_argument("--param", action="append", default=[])
    read.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "status":
        print("junkipedia:enabled" if credential("JUNKIPEDIA_API_KEY") else "junkipedia:unavailable")
        return 0
    return get(args.path, args.param, args.output)


if __name__ == "__main__":
    sys.exit(main())
