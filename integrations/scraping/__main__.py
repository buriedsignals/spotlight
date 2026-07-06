"""CLI for the scraping seam so skills fetch without importing Python (U14b):

    python3 -m integrations.scraping <url>                   # markdown to stdout
    python3 -m integrations.scraping <url> --out page.md      # ... to a file
    python3 -m integrations.scraping <url> --provider firecrawl
    python3 -m integrations.scraping <url> --no-escalate      # disable Scrapling fallback
    python3 -m integrations.scraping <path.pdf> --pdf         # parse a local PDF
    python3 -m integrations.scraping <url> --json             # full ScrapeResult + hash

Default provider is Crawl4AI (open-source, no API key). Exit 0 on success; 3 on a
scrape/parse failure (unreachable, bot-blocked, needs-OCR, backend not installed),
with the error on stderr.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .scrape import scrape
from .scrape_types import ScrapeError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m integrations.scraping",
        description="Fetch a URL (or parse a local PDF) to evidence markdown via the scraping seam.",
    )
    ap.add_argument("target", help="URL to scrape, or a local .pdf path with --pdf")
    ap.add_argument("--provider", default=None, help="crawl4ai (default) | firecrawl | scrapling")
    ap.add_argument("--pdf", action="store_true", help="treat target as a local PDF path (pdftotext)")
    ap.add_argument("--out", default=None, help="write output to this path instead of stdout")
    ap.add_argument("--no-escalate", action="store_true", help="disable the Scrapling stealth escalation")
    ap.add_argument("--json", action="store_true", help="emit the full ScrapeResult (with content_sha256) as JSON")
    args = ap.parse_args(argv)

    try:
        if args.pdf:
            from .pdfparse import parse_pdf

            result = parse_pdf(args.target)
        else:
            result = scrape(args.target, provider=args.provider, escalate=not args.no_escalate)
    except ScrapeError as exc:
        print(f"scrape failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        payload = dataclasses.asdict(result)
        payload["content_sha256"] = result.content_sha256
        out = json.dumps(payload, indent=2)
    else:
        out = result.markdown

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(out)
        print(f"wrote {len(result.markdown)} chars via {result.provider} -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
