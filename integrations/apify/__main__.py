"""Apify actor runs without putting the token on a command line.

    python3 -m integrations.apify status
    python3 -m integrations.apify run <actor> --input <input.json> --output <items.json>

`status` prints `apify:enabled` or `apify:unavailable` and never the token.
`run` POSTs the input file to the actor's run-sync-get-dataset-items endpoint
with the token in an `Authorization: Bearer` header (Apify's recommended form,
https://docs.apify.com/api/v2), and writes the dataset items to the output
file. The token comes from integrations/_credentials.py, so it works the same
from any agent and shell, including PowerShell.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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


API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.~-]{0,127}$")
MAX_INPUT = 1 << 20
# The sync endpoint answers 408 after 300 s; allow for transfer time.
TIMEOUT_S = 330


def token() -> str:
    return credential("APIFY_API_TOKEN") or credential("APIFY_TOKEN")


def run(actor: str, input_path: Path, output_path: Path) -> int:
    if not ACTOR.match(actor):
        print(f"apify: {actor!r} is not an actor id", file=sys.stderr)
        return 2
    secret = token()
    if not secret:
        print("apify: unavailable — no APIFY_API_TOKEN is configured", file=sys.stderr)
        return 3
    body = input_path.read_bytes()
    if len(body) > MAX_INPUT:
        print("apify: the input file is larger than 1 MiB", file=sys.stderr)
        return 2
    json.loads(body)  # refuse a malformed input before spending a run
    if output_path.is_symlink():
        print("apify: the output path is a symlink", file=sys.stderr)
        return 2
    request = Request(API.format(actor=quote(actor, safe="~")), data=body, method="POST")
    request.add_header("Authorization", f"Bearer {secret}")
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            items = response.read()
    except HTTPError as exc:
        detail = redact(exc.read(2048).decode("utf-8", "replace"), [secret])
        print(f"apify: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"apify: request failed: {redact(str(exc), [secret])}", file=sys.stderr)
        return 1
    write_output(output_path, items)
    print(f"apify: wrote {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m integrations.apify")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report whether a token is configured")
    call = sub.add_parser("run", help="run an actor synchronously and save its dataset items")
    call.add_argument("actor")
    call.add_argument("--input", required=True, type=Path)
    call.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "status":
        print("apify:enabled" if token() else "apify:unavailable")
        return 0
    return run(args.actor, args.input, args.output)


if __name__ == "__main__":
    sys.exit(main())
