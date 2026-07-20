#!/usr/bin/env python3
"""Guard the Engine-owned dependency and public/member installer boundary."""

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
    "bootstrap_engine || exit 1",
    'minisign -Vm "$archive" -P "$ENGINE_PUBLIC_KEY"',
    '"$ENGINE_BINARY" apply "$ENGINE_PLAN_PATH"',
]:
    require(token, installer, "public installer")

for forbidden in [
    'QMD_VERSION=',
    'OPEN_KNOWLEDGE_VERSION=',
    'CRAWL4AI_VERSION=',
    'ensure_npm_global_exact',
    'navigator_bridge.py',
    'navigator-cli==',
    'OSINT_NAV_API_KEY:?',
]:
    if forbidden in installer:
        print(f"FAIL: public installer contains Engine-owned or legacy token {forbidden!r}", file=sys.stderr)
        raise SystemExit(1)

require("Connect Navigator", configure, "configurator")
require("Continue without Navigator", configure, "configurator")
print("Engine-owned public installer dependency and entitlement policy ok")
