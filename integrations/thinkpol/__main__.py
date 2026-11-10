"""ThinkPol Reddit-archive reads without putting the key on a command line.

    python3 -m integrations.thinkpol status
    python3 -m integrations.thinkpol quota
    python3 -m integrations.thinkpol get <route> --params <params.json> --output <result.json>

`status` prints `thinkpol:enabled` or `thinkpol:unavailable` and never the key.
`get` sends one documented read-only GET route
(https://api.think-pol.com/antechamber/docs) with the key in an
`Authorization: Bearer` header and writes the response to the output file.
Search terms, usernames and ids come from the params file, never the command
line: `{name}` parts of the route are filled from it and the rest becomes the
query string (a list repeats the parameter, as `terms` needs). The stream
routes (`/v1/streams`), which register webhooks, are not reachable from here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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

API = "https://api.think-pol.com"
ROUTES = frozenset({
    "/quota",
    "/v3/search",
    "/v3/search/popularity",
    "/v3/user/search",
    "/v3/user/{username}",
    "/v2/user/{username}/posts",
    "/v2/user/{username}/comments",
    "/v2/user/{username}/subcomments",
    "/v2/posts",
    "/v2/posts/{id}",
    "/v2/posts/{id}/comments",
    "/v2/comments/{id}",
    "/v2/subcomments/{id}",
    "/v2/subreddit/search",
    "/v2/subreddit/{subreddit_name}",
    "/v2/subreddit/{subreddit_name}/users",
})
SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PARAMS = 1 << 16
TIMEOUT_S = 60


def key() -> str:
    return credential("THINKPOL_API_KEY")


def build_url(route: str, params: dict) -> str:
    """The request URL for a documented route; ValueError names what is wrong."""
    if route not in ROUTES:
        raise ValueError(f"{route!r} is not a supported route")
    query = dict(params)
    path = route
    for name in re.findall(r"\{(\w+)\}", route):
        value = query.pop(name, None)
        if not isinstance(value, str) or not SEGMENT.match(value):
            raise ValueError(f"the params file needs a plain {name!r} for {route}")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, bool):
                item = "true" if item else "false"
            if not isinstance(item, (str, int)):
                raise ValueError(f"parameter {name!r} must be text, a number, true/false or a list of them")
            pairs.append((name, str(item)))
    return API + path + ("?" + urlencode(pairs) if pairs else "")


def get(route: str, params_path: Path | None, output_path: Path | None) -> int:
    secret = key()
    if not secret:
        print("thinkpol: unavailable — no THINKPOL_API_KEY is configured", file=sys.stderr)
        return 3
    params: object = {}
    if params_path is not None:
        raw = params_path.read_bytes()
        if len(raw) > MAX_PARAMS:
            print("thinkpol: the params file is larger than 64 KiB", file=sys.stderr)
            return 2
        params = json.loads(raw)
    if not isinstance(params, dict):
        print("thinkpol: the params file must hold a JSON object", file=sys.stderr)
        return 2
    try:
        url = build_url(route, params)
    except ValueError as exc:
        print(f"thinkpol: {exc}", file=sys.stderr)
        return 2
    if output_path is not None and output_path.is_symlink():
        print("thinkpol: the output path is a symlink", file=sys.stderr)
        return 2
    request = Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {secret}")
    request.add_header("User-Agent", "Spotlight-ThinkPol/1.0")
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read()
    except HTTPError as exc:
        detail = redact(exc.read(2048).decode("utf-8", "replace"), [secret])
        print(f"thinkpol: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"thinkpol: request failed: {redact(str(exc), [secret])}", file=sys.stderr)
        return 1
    if output_path is None:
        print(f"thinkpol: {body.decode('utf-8', 'replace').strip()}")
        return 0
    write_output(output_path, body)
    print(f"thinkpol: wrote {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m integrations.thinkpol")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report whether a key is configured")
    sub.add_parser("quota", help="print the queries left on the key")
    call = sub.add_parser("get", help="send one documented GET route and save the response")
    call.add_argument("route")
    call.add_argument("--params", type=Path)
    call.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "status":
        print("thinkpol:enabled" if key() else "thinkpol:unavailable")
        return 0
    if args.command == "quota":
        return get("/quota", None, None)
    return get(args.route, args.params, args.output)


if __name__ == "__main__":
    sys.exit(main())
