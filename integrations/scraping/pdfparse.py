"""Deterministic PDF -> text for evidence (loop U16 / KTD7, ported from
tools/scoutpost-scrape/scrape-service/app/pdfparse.py).

Primary parser is poppler `pdftotext -layout` — embedded-text extraction, never
OCR (behavioral parity with Firecrawl's pdfMode:"fast"). It is deterministic, so
evidence dedup can key on ScrapeResult.content_sha256. A density guard measures
the scanned-document share: a bitmap-only PDF yields near-zero chars/page and
surfaces as a structured NeedsOcrError instead of silently-empty text. Gemini
native-PDF transcription is a LABELLED fallback (parser="gemini"), used only
when GEMINI_API_KEY is set — never the default (an LLM parser isn't reproducible).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone

from .scrape_types import ScrapeError, ScrapeResult

MIN_CHARS_PER_PAGE = 20  # below this average -> treat as scanned (needs OCR)


class NeedsOcrError(ScrapeError):
    """The PDF is (mostly) scanned images — no key-free deterministic text."""

    def __init__(self, pages: int, chars: int) -> None:
        super().__init__(f"needs_ocr: {chars} chars over {pages} page(s)")
        self.pages = pages
        self.chars = chars


def _page_count(text: str) -> int:
    # pdftotext appends a form-feed AFTER each page, so N pages -> N form-feeds
    # (a trailing one). Count deterministically without over-counting the last.
    if not text:
        return 0
    return text.count("\f") + (0 if text.endswith("\f") else 1)


def _density_ok(chars: int, pages: int, min_per_page: int = MIN_CHARS_PER_PAGE) -> bool:
    if pages <= 0:
        return False
    return chars / pages >= min_per_page


def _pdftotext(path: str) -> str:
    if shutil.which("pdftotext") is None:
        raise ScrapeError("pdftotext (poppler-utils) is not installed; run the U15 installer")
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScrapeError(f"pdftotext timed out on {path}") from exc
    if proc.returncode != 0:
        raise ScrapeError(f"pdftotext failed on {path}: {proc.stderr.strip()}")
    return proc.stdout


def parse_pdf(path: str, *, gemini_key: str | None = None) -> ScrapeResult:
    """Parse a local PDF to a ScrapeResult (markdown = extracted text). Raises
    NeedsOcrError for a scanned PDF when no Gemini key is available."""
    gemini_key = gemini_key if gemini_key is not None else os.environ.get("GEMINI_API_KEY")
    text = _pdftotext(path)
    pages = _page_count(text)
    chars = len(text.strip())

    if _density_ok(chars, pages):
        return _result(path, text, parser="pdftotext", pages=pages)

    # Low density -> scanned. Deterministic path is exhausted; escalate only if
    # a key is present, and LABEL it so evidence provenance stays honest.
    if gemini_key:
        from . import gemini_pdf  # lazy: only imported when a key is present

        transcript = gemini_pdf.transcribe(path, gemini_key)
        return _result(path, transcript, parser="gemini", pages=pages)

    raise NeedsOcrError(pages=pages, chars=chars)


def _result(path: str, text: str, *, parser: str, pages: int) -> ScrapeResult:
    return ScrapeResult(
        markdown=text,
        requested_url=path,
        source_url=path,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        provider=parser,
        title=None,
        metadata={"parser": parser, "pages": pages, "kind": "pdf"},
    )
