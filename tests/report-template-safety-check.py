#!/usr/bin/env python3
"""Regression check for hidden instructions in report HTML templates."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "skills" / "report-drafting" / "references" / "report-template.html"


def main() -> int:
    text = TEMPLATE.read_text(encoding="utf-8")
    if "<!--" in text or "-->" in text:
        raise AssertionError("report-template.html must not contain hidden HTML comments")
    print("report template safety: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
