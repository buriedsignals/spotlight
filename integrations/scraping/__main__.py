"""CLI for the scraping seam so skills fetch without importing Python (U14b):

    python3 -m integrations.scraping <url>                   # markdown to stdout
    python3 -m integrations.scraping <url> --out page.md      # ... to a file
    python3 -m integrations.scraping <url> --provider firecrawl
    python3 -m integrations.scraping <url> --no-escalate      # disable Firecrawl fallback
    python3 -m integrations.scraping <url> --tor              # route via Tor (U7, anonymize)
    python3 -m integrations.scraping <path.pdf> --pdf         # parse a local PDF
    python3 -m integrations.scraping <url> --json             # full ScrapeResult + hash

Default provider is Crawl4AI (open-source, no API key); on a bot-block it escalates
to Firecrawl only when FIRECRAWL_API_KEY is set. Exit 0 on success; 3 on a
scrape/parse failure (unreachable, bot-blocked, needs-OCR, backend not installed),
with the error on stderr.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import urllib.request

from .scrape import scrape
from .scrape_types import ScrapeError


def _rlm_distill(markdown: str, source: str) -> str | None:
    """Runtime-auto RLM: distill a scraped page → compact source-linked leads via the local e4b
    (llama.cpp OpenAI endpoint). Keeps the orchestrator context small — the #1 local-tier failure mode
    is raw pages ballooning context. Returns None on any failure so the caller falls back to raw."""
    base = os.environ.get("SPOTLIGHT_RLM_OPENAI_BASE_URL")
    model = os.environ.get("SPOTLIGHT_RLM_OPENAI_MODEL", "rlm-e4b")
    if not base or not markdown.strip():
        return None
    # cap the input so a huge page doesn't blow the RLM's own context
    text = markdown[:24000]
    prompt = (
        "You are a lead-extractor. From the SOURCE below, output a COMPACT bullet list of only the "
        "investigation-relevant leads: named people (+roles), organizations, dates/events, "
        "relationships, locations, and URLs. No prose, no preamble, no full sentences copied verbatim. "
        "Leads are unverified pointers, not facts. "
        # The e4b serves with --reasoning-budget 0, so any planning it does leaks into the
        # answer; forbid it explicitly (grounded 2026-07-09: output opened with a restatement
        # of these instructions before the bullets).
        "Begin your reply DIRECTLY with the first bullet — do not restate these instructions, "
        "do not describe what you are about to do.\n\nSOURCE:\n" + text
    )
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 900, "temperature": 0}).encode()
    url = base.rstrip("/") + ("/chat/completions" if base.rstrip("/").endswith("/v1") else "/v1/chat/completions")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        content = ((data.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        return content.strip() or None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m integrations.scraping",
        description="Fetch a URL (or parse a local PDF) to evidence markdown via the scraping seam.",
    )
    ap.add_argument("target", help="URL to scrape, or a local .pdf path with --pdf")
    ap.add_argument("--provider", default=None, help="crawl4ai (default) | firecrawl")
    ap.add_argument("--pdf", action="store_true", help="treat target as a local PDF path (pdftotext)")
    ap.add_argument("--out", default=None, help="write output to this path instead of stdout")
    ap.add_argument("--no-escalate", action="store_true", help="disable the Firecrawl bot-block escalation")
    # Tor (U7): --tor anonymizes this fetch; --no-tor forces direct even when
    # SPOTLIGHT_ANONYMIZE_FETCH is set for the run. Neither = defer to the env.
    tor = ap.add_mutually_exclusive_group()
    tor.add_argument("--tor", dest="anonymize", action="store_true", default=None,
                     help="route this fetch through Tor SOCKS (hide operator IP)")
    tor.add_argument("--no-tor", dest="anonymize", action="store_false",
                     help="force a direct fetch even if SPOTLIGHT_ANONYMIZE_FETCH is set")
    ap.add_argument("--json", action="store_true", help="emit the full ScrapeResult (with content_sha256) as JSON")
    ap.add_argument("--rlm", action="store_true",
                    help="runtime-auto RLM: distill the page to compact leads (local tier); raw kept as <out>.raw")
    args = ap.parse_args(argv)

    try:
        if args.pdf:
            from .pdfparse import parse_pdf

            result = parse_pdf(args.target)
        else:
            result = scrape(args.target, provider=args.provider,
                            escalate=not args.no_escalate, anonymize=args.anonymize)
    except ScrapeError as exc:
        print(f"scrape failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        payload = dataclasses.asdict(result)
        payload["content_sha256"] = result.content_sha256
        out = json.dumps(payload, indent=2)
    else:
        out = result.markdown

    # Runtime-auto RLM: the model reads compact leads (--out); raw stays as <out>.raw for provenance.
    leads = _rlm_distill(result.markdown, args.target) if (args.rlm and not args.json and not args.pdf) else None

    if args.out:
        if leads is not None:
            with open(args.out + ".raw", "w") as fh:
                fh.write(out)
            with open(args.out, "w") as fh:
                fh.write(f"# RLM-distilled leads from {args.target}\n"
                         f"# (compact; raw source: {os.path.basename(args.out)}.raw)\n\n" + leads)
            print(f"distilled {len(result.markdown)}->{len(leads)} chars via {result.provider}+RLM -> {args.out} (raw sidecar)", file=sys.stderr)
        else:
            with open(args.out, "w") as fh:
                fh.write(out)
            note = " (RLM off/failed — raw)" if args.rlm else ""
            print(f"wrote {len(result.markdown)} chars via {result.provider} -> {args.out}{note}", file=sys.stderr)
    else:
        sys.stdout.write(leads if leads is not None else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
