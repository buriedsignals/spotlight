#!/usr/bin/env python3
"""Guard the public Spotlight bootstrap against independent provisioning."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install-spotlight.sh").read_text()
SETUP = (ROOT / "setup.html").read_text()
CONFIGURE = (ROOT / "install" / "configure.html").read_text()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


for token in [
    "bootstrap_engine || exit 1",
    "minisign -Vm",
    '"$ENGINE_BINARY" apply "$ENGINE_PLAN_PATH"',
    '"$ENGINE_BINARY" welcome spotlight',
]:
    if token not in INSTALLER:
        fail(f"public bootstrap is missing {token!r}")

# The bootstrap may install only the verified Engine archive. Package, model,
# and runtime pins come from its signed catalog and sealed plan, never from a
# second product-specific writer.
for token in ["npm install", "pip install", "\nbrew install", "qmd", "obsidian", "Tolaria"]:
    if token in INSTALLER:
        fail(f"public bootstrap still provisions {token!r} outside Engine")

if "@google/gemini-cli" in INSTALLER or "GEMINI_CLI_VERSION" in INSTALLER:
    fail("public bootstrap exposes the removed Gemini runtime")

pin_pattern = re.compile(r"@\d+\.\d+\.\d+|==\d+\.\d+\.\d+")
for page_name, page in [("setup.html", SETUP), ("install/configure.html", CONFIGURE)]:
    match = pin_pattern.search(page)
    if match:
        fail(f"{page_name} carries a dependency pin ({match.group(0)})")

print("Engine-owned dependency policy ok")
