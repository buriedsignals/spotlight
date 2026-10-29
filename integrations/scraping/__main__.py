"""CLI for the scraping seam so skills fetch without importing Python (U14b):

    python3 -m integrations.scraping <url>                   # markdown to stdout
    python3 -m integrations.scraping <url> --out page.md      # ... to a file
    python3 -m integrations.scraping <url> --provider firecrawl
    python3 -m integrations.scraping <url> --no-escalate      # disable Firecrawl fallback
    python3 -m integrations.scraping <url> --tor              # route via Tor (U7, anonymize)
    python3 -m integrations.scraping <path.pdf> --pdf         # parse a local PDF
    python3 -m integrations.scraping <url> --json             # full ScrapeResult + hash
    python3 -m integrations.scraping <url> --json --out page.md  # markdown to the file, JSON to stdout

Default provider is Crawl4AI (open-source, no API key); on a bot-block it escalates
to Firecrawl only when FIRECRAWL_API_KEY is set. A URL whose path ends in .pdf is
downloaded and parsed with pdftotext instead of rendered. An access-wall page
(bot check, "403: Access limited") counts as a block, never as the page. Exit 0
on success; 3 on a scrape/parse failure (unreachable, bot-blocked, needs-OCR,
backend not installed), with the error on stderr.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from integrations._cli import utf8_stdio

from .scrape import scrape
from .scrape_types import ScrapeError

# The lead-distillation request (prompt, input cap, output budget), shared with
# the Pi harness's fetch tool so both runtimes distill the same way.
_RLM_REQUEST = Path(__file__).with_name("rlm-leads.json")


def _rlm_distill(markdown: str, source: str) -> str | None:
    """Runtime-auto RLM: distill a scraped page → compact source-linked leads via the local e4b
    (llama.cpp OpenAI endpoint). Keeps the orchestrator context small — the #1 local-tier failure mode
    is raw pages ballooning context. Returns None on any failure so the caller falls back to raw."""
    base = os.environ.get("SPOTLIGHT_RLM_OPENAI_BASE_URL")
    request = json.loads(_RLM_REQUEST.read_text(encoding="utf-8"))
    model = os.environ.get("SPOTLIGHT_RLM_OPENAI_MODEL", request["default_model"])
    if not base or not markdown.strip():
        return None
    task = os.environ.get("SPOTLIGHT_TASK_HEADER", "")[:4000]
    scope = f"Task scope (not source evidence):\n{task}\n\n" if task else ""
    lines = [line[:-1] if line.endswith("\r") else line for line in markdown.split("\n")]
    if lines[-1] == "":
        lines.pop()
    excerpts: list[str] = []
    next_line = 0
    url = base.rstrip("/") + ("/chat/completions" if base.rstrip("/").endswith("/v1") else "/v1/chat/completions")
    try:
        for _ in range(3):
            start = next_line
            numbered = ""
            while next_line < len(lines):
                line = f"{next_line + 1}: {lines[next_line]}\n"
                if len(numbered) + len(line) > request["max_input_chars"]:
                    break
                numbered += line
                next_line += 1
            if next_line == start:
                break
            body = json.dumps({"model": model, "messages": [{"role": "user", "content": scope + request["prompt"] + numbered}],
                               "response_format": request["response_format"], "max_tokens": request["max_tokens"], "temperature": request["temperature"]}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            content = ((data.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
            fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", content.strip(), re.IGNORECASE)
            selected = json.loads(fenced.group(1) if fenced else content)
            if not isinstance(selected, dict) or set(selected) != {"ranges"} or not isinstance(selected["ranges"], list):
                raise ValueError("invalid line selections")
            for item in selected["ranges"]:
                if not isinstance(item, dict) or set(item) != {"start", "end"}:
                    raise ValueError("invalid line selection")
                first, last = item["start"], item["end"]
                if type(first) is not int or type(last) is not int or first < start + 1 or last > next_line or last < first:
                    raise ValueError("line selection outside inspected range")
                excerpts.append(f"[raw lines {first}-{last}]\n" + "\n".join(lines[first - 1:last]))
            if next_line == len(lines):
                break
        leads = "\n\n".join(excerpts)
        if not leads or len(leads) > 30_000:
            raise ValueError("empty or oversized line selections")
        return f"[Inspected raw lines 1-{next_line} of {len(lines)}; coverage {'complete' if next_line == len(lines) else 'incomplete'}; selected excerpts may omit relevant text]\n\n{leads}"
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m integrations.scraping",
        description="Fetch a URL (or parse a local PDF) to evidence markdown via the scraping seam.",
    )
    ap.add_argument("target", help="URL to scrape, or a local .pdf path with --pdf")
    ap.add_argument("--provider", default=None, help="crawl4ai (default) | firecrawl")
    ap.add_argument("--pdf", action="store_true", help="treat target as a local PDF path (pdftotext)")
    ap.add_argument("--out", default=None, help="write the page content (markdown) to this path instead of stdout")
    ap.add_argument("--no-escalate", action="store_true", help="disable the Firecrawl bot-block escalation")
    # Tor (U7): --tor anonymizes this fetch; --no-tor forces direct even when
    # SPOTLIGHT_ANONYMIZE_FETCH is set for the run. Neither = defer to the env.
    tor = ap.add_mutually_exclusive_group()
    tor.add_argument("--tor", dest="anonymize", action="store_true", default=None,
                     help="route this fetch through Tor SOCKS (hide operator IP)")
    tor.add_argument("--no-tor", dest="anonymize", action="store_false",
                     help="force a direct fetch even if SPOTLIGHT_ANONYMIZE_FETCH is set")
    ap.add_argument("--json", action="store_true", help="print the full ScrapeResult (with content_sha256) as JSON on stdout, also with --out")
    ap.add_argument("--rlm", action="store_true",
                    help="runtime-auto RLM: distill the page to compact leads (local tier); raw kept as <out>.raw")
    args = ap.parse_args(argv)
    utf8_stdio()

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

    # --out always receives the page content; --json always goes to stdout,
    # so `--json --out page.md` saves the markdown and prints the envelope.
    out = result.markdown
    envelope = None
    if args.json:
        payload = dataclasses.asdict(result)
        payload["content_sha256"] = result.content_sha256
        envelope = json.dumps(payload, indent=2)

    # Runtime-auto RLM: the model reads compact leads (--out); raw stays as <out>.raw for provenance.
    leads = _rlm_distill(result.markdown, args.target) if (args.rlm and not args.json and not args.pdf) else None

    if args.out:
        if leads is not None:
            with open(args.out + ".raw", "w", encoding="utf-8") as fh:
                fh.write(out)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(f"# RLM-distilled leads from {args.target}\n"
                         f"# (compact; raw source: {os.path.basename(args.out)}.raw)\n\n" + leads)
            print(f"distilled {len(result.markdown)}->{len(leads)} chars via {result.provider}+RLM -> {args.out} (raw sidecar)", file=sys.stderr)
        else:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(out)
            note = " (RLM off/failed — raw)" if args.rlm else ""
            print(f"wrote {len(result.markdown)} chars via {result.provider} -> {args.out}{note}", file=sys.stderr)
        if envelope is not None:
            sys.stdout.write(envelope)
    else:
        sys.stdout.write(envelope if envelope is not None else (leads if leads is not None else out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
