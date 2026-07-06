"""Gemini native-PDF transcription — the labelled fallback (loop U16), ported
from tools/scoutpost-scrape/scrape-service/app/gemini_pdf.py.

Reached only when pdftotext's density guard trips AND a key is present. temperature=0
minimizes variance, but the output is NOT bit-deterministic, so results are always
tagged parser="gemini" for provenance — pdftotext stays primary for the ~90%+ of
PDFs with a real text layer. Uses stdlib urllib (no SDK dependency); the key rides
the x-goog-api-key header, never the URL (a ?key=... leaks into logs).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from .scrape_types import ScrapeError

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Gemini caps the inline request at 20 MB; base64 inflates ~4/3, so keep raw
# well under. A larger PDF should surface needs_ocr, not be transcribed inline.
GEMINI_INLINE_MAX_BYTES = 14 * 1024 * 1024
DEFAULT_MODEL = "gemini-2.5-flash"
TRANSCRIBE_PROMPT = (
    "Transcribe this document to clean Markdown. Preserve ALL text — headings, "
    "body paragraphs, tables (as Markdown tables), lists, figure and chart "
    "labels, and footnotes — in natural reading order. Do not summarize, omit, "
    "or add commentary. Output only the transcription."
)


def transcribe(path: str, api_key: str, *, model: str | None = None, timeout_s: float = 120.0) -> str:  # pragma: no cover - needs key + network
    model = model or os.environ.get("GEMINI_PDF_MODEL", DEFAULT_MODEL)
    with open(path, "rb") as fh:
        pdf_bytes = fh.read()
    if len(pdf_bytes) > GEMINI_INLINE_MAX_BYTES:
        raise ScrapeError(f"PDF too large for inline Gemini transcription ({len(pdf_bytes)} bytes)")
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode("ascii")}},
                    {"text": TRANSCRIBE_PROMPT},
                ]
            }
        ],
        "generationConfig": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise ScrapeError(f"gemini transcribe failed: {exc}") from exc
    try:
        candidate = data["candidates"][0]
        text = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScrapeError("gemini response missing text part") from exc
    finish = candidate.get("finishReason")
    if finish not in (None, "STOP"):
        raise ScrapeError(f"gemini transcription incomplete: finishReason={finish}")
    if not isinstance(text, str) or not text.strip():
        raise ScrapeError("gemini returned empty transcription")
    return text
