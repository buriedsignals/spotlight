#!/usr/bin/env python3
"""Enforce the one-way Spotlight → Mycroft monitoring boundary.

Spotlight may recommend a monitor and describe the explicit Mycroft handoff,
but it must never ship a Scoutpost integration, credential/configuration
surface, or direct ``scout`` command.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
    raise SystemExit(1)


for rel in (
    "integrations/scoutpost",
    "plugins/spotlight/integrations/scoutpost",
    "monitoring/registry.py",
    "plugins/spotlight/monitoring/registry.py",
):
    if (ROOT / rel).exists():
        fail(f"Spotlight still ships a direct Scoutpost surface: {rel}")

for rel in (
    "install-spotlight.sh",
    "install/setup_server.py",
    "install/configure.html",
    "integrations/preflight.py",
    "skills.manifest",
):
    body = (ROOT / rel).read_text(encoding="utf-8")
    if "SCOUTPOST_" in body or re.search(r"\bscout\s+(projects|scouts|units)\b", body):
        fail(f"direct Scoutpost configuration or command remains in {rel}")

monitoring = (ROOT / "skills/monitoring/SKILL.md").read_text(encoding="utf-8")
for required in ("monitoring_recommendations[]", "Mycroft", "never installs Scoutpost"):
    if required not in monitoring:
        fail(f"monitoring skill lost the required boundary text: {required!r}")
for forbidden in ("SCOUTPOST_API_KEY", "integrations/scoutpost", "scout projects", "scout scouts", "scout units"):
    if forbidden in monitoring:
        fail(f"monitoring skill contains a direct Scoutpost operation: {forbidden!r}")

print("Scoutpost boundary: OK")
