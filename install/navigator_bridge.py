#!/usr/bin/env python3
"""Direct website-installer bridge for Navigator membership.

This stdlib-only module is copied verbatim into Mycroft and Spotlight release
assets. It never returns a PAT to browser code: an eligible PAT is kept in
memory only long enough to pass it on stdin to Navigator's keyring writer.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path


class NavigatorBridgeError(RuntimeError):
    """A sanitized, user-presentable connection error."""


class NavigatorInstallerBridge:
    def __init__(self, contract_path: str | os.PathLike[str], runtime: str):
        self.contract_path = Path(contract_path)
        self.contract = json.loads(self.contract_path.read_text("utf-8"))
        if self.contract.get("schema_version") != "navigator-transport-matrix/v1":
            raise NavigatorBridgeError("Navigator transport contract is incompatible")
        self.runtime = runtime
        self.transport = self._select_transport()
        self.origin = str(self.contract["auth_origin"]).rstrip("/")
        self._flows: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _host() -> tuple[str, str]:
        system = platform.system().lower()
        if system == "linux":
            try:
                if "microsoft" in Path("/proc/version").read_text("utf-8").lower():
                    system = "wsl2"
            except OSError:
                pass
        arch_value = platform.machine().lower()
        arch = "arm64" if arch_value in {"arm64", "aarch64"} else "amd64"
        return system, arch

    def _select_transport(self) -> str:
        os_name, arch = self._host()
        matches = [
            row["transport"]
            for row in self.contract.get("rows", [])
            if row.get("os") == os_name
            and row.get("arch") == arch
            and self.runtime in row.get("runtimes", [])
        ]
        if len(matches) > 1:
            raise NavigatorBridgeError("Navigator transport contract is ambiguous")
        return matches[0] if matches else "locked-only"

    def existing_status(self) -> dict:
        if self.transport == "api/mcp":
            return {
                "status": "available",
                "transport": "api/mcp",
                "mcp_url": self.contract["mcp_url"],
            }
        executable = self._navigator_executable(existing_only=True)
        if executable is None:
            return {"status": "locked", "transport": self.transport}
        result = subprocess.run(
            [str(executable), "auth", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
        try:
            body = json.loads(result.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if result.returncode == 0 and body.get("verified") and "osint_tools" in body.get("capabilities", []):
            return {
                "status": "connected",
                "transport": "cli",
                "tier": body.get("tier"),
                "capabilities": body.get("capabilities", []),
            }
        return {"status": "locked", "transport": self.transport}

    def start(self, email: str) -> dict:
        if self.transport == "locked-only":
            return {
                "status": "locked-only",
                "transport": self.transport,
                "detail": "No secure Navigator transport is available on this host yet.",
            }
        if self.transport == "api/mcp":
            return {
                "status": "oauth-required",
                "transport": self.transport,
                "mcp_url": self.contract["mcp_url"],
            }
        body = self._json_request("POST", "/auth/cli/start", {"email": email})
        remote_flow = body.get("flow_id")
        if not isinstance(remote_flow, str) or not remote_flow:
            raise NavigatorBridgeError("Navigator returned an invalid connection state")
        local_flow = secrets.token_urlsafe(18)
        with self._lock:
            self._flows[local_flow] = remote_flow
        return {
            "status": "pending",
            "flow_id": local_flow,
            "transport": self.transport,
            "poll_interval_seconds": int(body.get("poll_interval_seconds") or 3),
        }

    def poll(self, local_flow: str) -> dict:
        with self._lock:
            remote_flow = self._flows.get(local_flow)
        if remote_flow is None:
            return {"status": "expired"}
        body = self._json_request("GET", f"/auth/cli/poll/{remote_flow}", allow_status={403, 409, 410})
        status = body.get("status")
        if status == "pending":
            return {"status": "pending", "flow_id": local_flow}
        if status != "ready":
            with self._lock:
                self._flows.pop(local_flow, None)
            return {"status": status if status in {"denied_tier", "denied_key_cap", "cancelled", "expired", "consumed"} else "failed"}
        pat = body.get("api_key")
        capabilities = body.get("capabilities", [])
        if not isinstance(pat, str) or not pat.startswith("on_") or "osint_tools" not in capabilities:
            with self._lock:
                self._flows.pop(local_flow, None)
            raise NavigatorBridgeError("Navigator returned no usable member connection")
        try:
            self._adopt(pat)
        except Exception:
            self._revoke(pat)
            raise
        finally:
            pat = ""  # best-effort lifetime reduction; never persisted here
        with self._lock:
            self._flows.pop(local_flow, None)
        return {
            "status": "connected",
            "transport": "cli",
            "tier": body.get("tier"),
            "capabilities": capabilities,
        }

    def cancel(self, local_flow: str) -> dict:
        with self._lock:
            remote_flow = self._flows.pop(local_flow, None)
        if remote_flow is None:
            return {"status": "expired"}
        self._json_request("POST", f"/auth/cli/cancel/{remote_flow}", allow_status={410})
        return {"status": "cancelled"}

    def _navigator_executable(self, *, existing_only: bool) -> Path | None:
        found = shutil.which("navigator")
        if found:
            return Path(found)
        root = Path.home() / ".local" / "share" / "navigator" / "venv"
        executable = root / ("Scripts/navigator.exe" if os.name == "nt" else "bin/navigator")
        if executable.is_file() or existing_only:
            return executable if executable.is_file() else None
        root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(root)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            check=True,
        )
        pip = root / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
        package = f"{self.contract['cli_package']}=={self.contract['cli_version']}"
        subprocess.run(
            [str(pip), "install", "--disable-pip-version-check", "--no-input", package],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=600,
            check=True,
        )
        if not executable.is_file():
            raise NavigatorBridgeError("Navigator CLI installation did not produce an executable")
        if os.name != "nt":
            link = Path.home() / ".local" / "bin" / "navigator"
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists() and not link.is_symlink():
                link.symlink_to(executable)
        return executable

    def _adopt(self, pat: str) -> None:
        executable = self._navigator_executable(existing_only=False)
        if executable is None:
            raise NavigatorBridgeError("Navigator CLI is unavailable")
        result = subprocess.run(
            [str(executable), "auth", "import", "--stdin"],
            input=(pat + "\n").encode(),
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise NavigatorBridgeError("Navigator could not store the member connection securely")

    def _revoke(self, pat: str) -> None:
        try:
            request = urllib.request.Request(
                self.origin + "/auth/cli/revoke",
                method="POST",
                headers={"Authorization": "Bearer " + pat},
            )
            with urllib.request.urlopen(request, timeout=10):
                pass
        except Exception:
            pass

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        allow_status: set[int] | None = None,
    ) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(64 * 1024)
        except urllib.error.HTTPError as exc:
            if allow_status and exc.code in allow_status:
                raw = exc.read(64 * 1024)
            elif exc.code == 429:
                raise NavigatorBridgeError("Too many Navigator connection attempts; try again later") from exc
            else:
                raise NavigatorBridgeError("Navigator connection service rejected the request") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise NavigatorBridgeError("Navigator connection service is temporarily unavailable") from exc
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NavigatorBridgeError("Navigator returned an invalid connection response") from exc
        if not isinstance(value, dict):
            raise NavigatorBridgeError("Navigator returned an invalid connection response")
        return value
