#!/usr/bin/env python3
"""Guard reviewed pins and the public/member installer boundary."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT / "install-spotlight.sh").read_text()
configure = (ROOT / "install" / "configure.html").read_text()


def require(token: str, body: str, where: str) -> None:
    if token not in body:
        print(f"FAIL: {where} is missing {token!r}", file=sys.stderr)
        raise SystemExit(1)


for token in [
    'OPEN_KNOWLEDGE_VERSION="0.54.3"',
    'CRAWL4AI_VERSION="0.9.0"',
    'ensure_npm_global_exact open-knowledge @inkeep/open-knowledge',
]:
    require(token, installer, "public installer")

for forbidden in ["bootstrap_engine", "minisign -Vm", "navigator-cli==", "OSINT_NAV_API_KEY:?", "@tobilu/qmd", "qmd collection"]:
    if forbidden in installer:
        print(f"FAIL: public installer contains forbidden member/legacy token {forbidden!r}", file=sys.stderr)
        raise SystemExit(1)

require("Yes, authenticate", configure, "configurator")
require("Continue without Navigator", configure, "configurator")
print("public installer dependency and entitlement policy ok")
