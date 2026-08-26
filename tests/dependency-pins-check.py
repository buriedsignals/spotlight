#!/usr/bin/env python3
"""Guard reviewed pins and the Indicator Labs / landing boundary."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT / "install-spotlight.sh").read_text()
validated = (ROOT / "VALIDATED_DEPENDENCIES.md").read_text()


def require(token: str, body: str, where: str) -> None:
    if token not in body:
        print(f"FAIL: {where} is missing {token!r}", file=sys.stderr)
        raise SystemExit(1)


require("https://buriedsignals.com/join", installer, "install pointer")
require("Indicator Labs", installer, "install pointer")
require("There is no localhost configure.html server", installer, "install pointer")

for token in [
    "0.54.3",
    "0.9.0",
    "@inkeep/open-knowledge",
]:
    require(token, validated, "VALIDATED_DEPENDENCIES.md")

for forbidden in ["bootstrap_engine", "minisign -Vm", "navigator-cli==", "OSINT_NAV_API_KEY:?", "@tobilu/qmd", "qmd collection"]:
    if forbidden in installer:
        print(f"FAIL: install pointer contains forbidden member/legacy token {forbidden!r}", file=sys.stderr)
        raise SystemExit(1)

print("install pointer and reviewed dependency pins ok")
