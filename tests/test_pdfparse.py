"""U16 deterministic-PDF tests — run against the REAL poppler pdftotext.

A minimal single-page PDF is generated in-process (correct xref offsets) so the
parser is exercised end-to-end without a fixture. The Gemini fallback is not
tested here (needs a key + network); it is a labelled, lazy path.

    python3 tests/test_pdfparse.py
    python3 -m pytest tests/test_pdfparse.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from integrations.scraping.pdfparse import (  # noqa: E402
    NeedsOcrError,
    _density_ok,
    _page_count,
    parse_pdf,
)


def _make_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 800 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 18 Tf 20 100 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
    return out


def _write(text: str) -> str:
    p = tempfile.mktemp(suffix=".pdf")
    with open(p, "wb") as fh:
        fh.write(_make_pdf(text))
    return p


def test_page_count():
    assert _page_count("") == 0
    assert _page_count("one page no ff") == 1
    assert _page_count("p1\f") == 1               # trailing FF (pdftotext shape)
    assert _page_count("p1\fp2\f") == 2
    assert _page_count("p1\fp2") == 2             # no trailing FF


def test_density_guard():
    assert _density_ok(100, 1) is True
    assert _density_ok(5, 1) is False
    assert _density_ok(0, 0) is False


def test_parse_real_pdf_deterministic():
    p = _write("Ownership chain: ACME HOLDING AS org 998877665")
    r = parse_pdf(p)
    assert "ACME HOLDING AS" in r.markdown and "998877665" in r.markdown
    assert r.metadata["parser"] == "pdftotext" and r.metadata["pages"] == 1
    assert r.provider == "pdftotext" and r.metadata["kind"] == "pdf"
    assert r.content_sha256 == parse_pdf(p).content_sha256, "must be reproducible for evidence dedup"


def test_scanned_pdf_needs_ocr_without_key():
    # A 1-char page trips the density guard; no key -> structured NeedsOcrError.
    p = _write("x")
    try:
        parse_pdf(p, gemini_key=None)
    except NeedsOcrError as e:
        assert e.pages == 1 and e.chars < 20
        return
    raise AssertionError("expected NeedsOcrError for a text-thin PDF with no Gemini key")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
