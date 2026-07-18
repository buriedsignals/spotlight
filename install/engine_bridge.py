"""Thin loopback bridge from the branded installer page to Engine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


class EngineUnavailable(RuntimeError):
    pass


class EngineBridge:
    def __init__(self, product: str):
        self.product = product
        self.binary = os.environ.get("BSIG_BINARY") or shutil.which("bsig")
        if not self.binary:
            raise EngineUnavailable("bsig is not installed")

    def _run(self, *args: str, stdin: bytes | None = None, timeout: int = 30) -> dict[str, Any]:
        try:
            result = subprocess.run([self.binary, *args], input=stdin, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise EngineUnavailable("Engine did not resolve its signed catalog in time") from error
        except OSError as error:
            raise EngineUnavailable("Engine could not be started") from error
        events = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if result.returncode or not events:
            message = "Engine rejected the configuration. Review the installer terminal for details."
            for event in reversed(events):
                if event.get("event") == "error" and event.get("message"):
                    message = str(event["message"])
                    break
            raise RuntimeError(message)
        return events[-1]

    def descriptor(self) -> dict[str, Any]:
        return self._run("configure", "describe", self.product, timeout=3)["data"]["descriptor"]

    def submit(self, request: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(request, separators=(",", ":")).encode()
        validated = self._run("configure", "validate", self.product, stdin=body)
        required = validated["data"]["normalized"].get("required_secret_ids", [])
        listed = self._run("keys", "list")
        stored = {row["id"] for row in listed["data"].get("keys", []) if row.get("stored")}
        missing = []
        for key_id in required:
            value = str(secrets.get(key_id) or "")
            if value:
                self._run("keys", "set", key_id, stdin=value.encode())
            elif key_id not in stored:
                missing.append(key_id)
        if missing:
            raise RuntimeError("Missing required credential(s): " + ", ".join(missing))
        planned = self._run("configure", "plan", self.product, stdin=body)
        return {"ok": True, "plan": planned.get("data", {}), "required_secret_ids": required}
