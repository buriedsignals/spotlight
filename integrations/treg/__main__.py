"""treg catalog calls without putting the token on a command line.

    python3 -m integrations.treg status
    python3 -m integrations.treg call <endpoint> --input <input.json> --output <result.json> [--max-cost USD]

`status` prints `treg:enabled` or `treg:unavailable` and never the token.
`call` sends the input file to one endpoint pinned in endpoints.json
(`https://treg.to/call/<endpoint>`, token in the `X-Treg-Token` header:
https://treg.to/integrate.md) and writes the provider's response to the output
file. The input file is a flat JSON object of the endpoint's fields; the
helper places each in the query string or the JSON body as endpoints.json
says. Every call carries a cost ceiling (`X-Treg-Route-Max-Cost`), and the
settled cost and treg call id are printed for the collection record.
"""

from __future__ import annotations

import argparse
import json
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
    """Never follow a redirect: the token header must not travel to another
    origin or over plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HTTPError(req.full_url, code, f"refused redirect to {newurl}", headers, fp)


urlopen = build_opener(_NoRedirect).open

API = "https://treg.to/call/"
ENDPOINTS = json.loads((Path(__file__).with_name("endpoints.json")).read_text(encoding="utf-8"))["endpoints"]
MAX_INPUT = 1 << 16
DEFAULT_MAX_COST = 0.05
HARD_MAX_COST = 1.0
TIMEOUT_S = 120


def token() -> str:
    return credential("TREG_API_KEY")


def build_request(endpoint: str, fields: dict) -> tuple[str, bytes | None]:
    """URL and body for a pinned endpoint; ValueError names what is wrong."""
    spec = ENDPOINTS.get(endpoint)
    if spec is None:
        raise ValueError(f"{endpoint!r} is not a pinned endpoint (see integrations/treg/endpoints.json)")
    query_names = spec["input"].get("query", [])
    body_names = spec["input"].get("body", [])
    unknown = sorted(set(fields) - set(query_names) - set(body_names))
    if unknown:
        raise ValueError(f"{endpoint} does not take {', '.join(unknown)}")
    for name, value in fields.items():
        if isinstance(value, (dict, list)) or value is None:
            raise ValueError(f"field {name!r} must be text, a number or true/false")
    query = [(name, str(fields[name]).lower() if isinstance(fields[name], bool) else str(fields[name]))
             for name in query_names if name in fields]
    url = API + quote(endpoint, safe=".-_") + ("?" + urlencode(query) if query else "")
    body = None
    if spec["method"] == "POST":
        body = json.dumps({name: fields[name] for name in body_names if name in fields}).encode("utf-8")
    return url, body


def call(endpoint: str, input_path: Path, output_path: Path, max_cost: float = DEFAULT_MAX_COST) -> int:
    secret = token()
    if not secret:
        print("treg: unavailable — no TREG_API_KEY is configured", file=sys.stderr)
        return 3
    if not 0 < max_cost <= HARD_MAX_COST:
        print(f"treg: --max-cost must be above 0 and at most {HARD_MAX_COST} USD", file=sys.stderr)
        return 2
    raw = input_path.read_bytes()
    if len(raw) > MAX_INPUT:
        print("treg: the input file is larger than 64 KiB", file=sys.stderr)
        return 2
    fields = json.loads(raw)
    if not isinstance(fields, dict):
        print("treg: the input file must hold a JSON object", file=sys.stderr)
        return 2
    try:
        url, body = build_request(endpoint, fields)
    except ValueError as exc:
        print(f"treg: {exc}", file=sys.stderr)
        return 2
    if output_path.is_symlink():
        print("treg: the output path is a symlink", file=sys.stderr)
        return 2
    request = Request(url, data=body, method=ENDPOINTS[endpoint]["method"])
    request.add_header("X-Treg-Token", secret)
    request.add_header("X-Treg-Route-Max-Cost", f"{max_cost:g}")
    request.add_header("User-Agent", "Spotlight-treg/1.0")
    org = credential("TREG_ORG")
    if org:
        request.add_header("X-Treg-Org", org)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            result = response.read()
            cost = response.headers.get("X-Treg-Cost-Micro") or "0"
            call_id = response.headers.get("X-Treg-Call-Id") or "unknown"
    except HTTPError as exc:
        # treg's own refusals (402 empty balance, 429 daily cap) carry the
        # account's balance and a top-up link: name them, do not echo them.
        if exc.code == 402:
            print("treg: HTTP 402: the treg balance is empty or the call would pass --max-cost", file=sys.stderr)
        else:
            detail = redact(exc.read(2048).decode("utf-8", "replace"), [secret])
            print(f"treg: HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except (URLError, TimeoutError) as exc:
        print(f"treg: request failed: {redact(str(exc), [secret])}", file=sys.stderr)
        return 1
    write_output(output_path, result)
    print(f"treg: wrote {output_path} (endpoint {endpoint}, cost_micro_usd {cost}, call_id {call_id})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m integrations.treg")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="report whether a token is configured")
    run = sub.add_parser("call", help="call one pinned endpoint and save the provider's response")
    run.add_argument("endpoint")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    args = parser.parse_args(argv)
    if args.command == "status":
        print("treg:enabled" if token() else "treg:unavailable")
        return 0
    return call(args.endpoint, args.input, args.output, args.max_cost)


if __name__ == "__main__":
    sys.exit(main())
