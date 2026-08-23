#!/usr/bin/env python3
"""Spotlight local configurator server.

Launched by install-spotlight.sh. Serves install/configure.html on 127.0.0.1,
receives the journalist's choices and API keys via POST (nothing ever leaves
the machine), live-validates the keys against their providers, then writes:

  <profile>/setup-config.env     — non-secret choice flags for the installer (0600)
  <profile>/.env                 — staged secrets, atomic write (0600)
  <profile>/getting-started.html — personalized post-install guide (0644)

Exits 0 once configuration is written, 1 on timeout/abort. Stdlib only.

Hardening beyond the Mycroft pattern source: GET requires the per-run token
(?t=<token>), artifact writes are atomic all-or-nothing with secure-at-creation
modes, the profile dir is forced to 0700 even when pre-existing, and the native
folder picker ignores client-supplied prompts.
"""

import argparse
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import string
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from engine_bridge import EngineBridge, EngineUnavailable
except ModuleNotFoundError:
    class EngineUnavailable(RuntimeError):
        pass

    class EngineBridge:  # type: ignore[no-redef]
        def __init__(self, _product: str):
            raise EngineUnavailable("Engine bridge is unavailable in the public installer")
from navigator_bridge import NavigatorBridgeError, NavigatorInstallerBridge

# Asserted by install-spotlight.sh against the literal in configure.html and
# its own copy — a mismatch means the Pages CDN is mid-propagation.
CONFIGURATOR_VERSION = "3"

SUBMIT_TIMEOUT_SECONDS = 30 * 60

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# One token set: the Engine resolver vocabulary. Every downstream surface
# derives from this table — installer tokens (the SPOTLIGHT_RUNTIME case arms
# in install-spotlight.sh), labels, Navigator contract IDs, probe coverage.
# Desktop rows carry no installer arm yet: vocabulary-only, never offered by
# the static form (the Engine-managed path offers them instead).
RUNTIMES = {
    "claude-code": {"label": "Claude Code", "installer": "claude", "bin": "claude"},
    "claude-desktop": {"label": "Claude Desktop", "installer": None, "bin": None},
    "codex-cli": {"label": "Codex", "installer": "codex", "bin": "codex"},
    "codex-desktop": {"label": "Codex Desktop", "installer": None, "bin": None},
    "opencode": {"label": "OpenCode", "installer": "opencode", "bin": None},
    "pi": {"label": "Pi", "installer": "pi", "bin": None},
}
# Tokens the legacy installer body can actually install — an RT case arm exists.
INSTALLABLE_RUNTIMES = tuple(token for token, row in RUNTIMES.items() if row["installer"])
# F2 probe coverage: exactly the runtimes with a known CLI binary.
PROBED_RUNTIMES = tuple(token for token, row in RUNTIMES.items() if row["bin"])
RUNTIME_LABELS = {token: row["label"] for token, row in RUNTIMES.items()}

NAVIGATOR_RUNTIME_IDS = {
    # Resolver tokens are Navigator's contract IDs; "local" names the
    # Flue-on-Pi harness transport. "claude"/"codex" are legacy persisted
    # choices, kept as aliases so saved configs still resolve.
    "claude-code": "claude-code",
    "codex-cli": "codex-cli",
    "pi": "pi",
    "opencode": "opencode",
    "claude": "claude-code",
    "codex": "codex-cli",
    "local": "pi-flue",
}


def navigator_runtime_id(choice):
    """Map Spotlight's persisted runtime choice to Navigator's contract ID."""
    runtime = NAVIGATOR_RUNTIME_IDS.get(str(choice or "").strip())
    if runtime is None:
        raise NavigatorBridgeError("Choose a supported Spotlight runtime before connecting Navigator")
    return runtime


class NavigatorBridgeRouter:
    """Keep auth flows bound to the runtime selected when each flow starts."""

    def __init__(self, contract_path):
        self.contract_path = contract_path
        self._bridges = {}
        self._flows = {}
        self._lock = threading.Lock()

    def _bridge(self, choice):
        runtime = navigator_runtime_id(choice)
        with self._lock:
            bridge = self._bridges.get(runtime)
            if bridge is None:
                bridge = NavigatorInstallerBridge(self.contract_path, runtime)
                self._bridges[runtime] = bridge
        return bridge

    def status(self, choice):
        return self._bridge(choice).existing_status()

    def start(self, choice, email):
        bridge = self._bridge(choice)
        result = bridge.start(email)
        flow_id = result.get("flow_id")
        if isinstance(flow_id, str) and flow_id:
            with self._lock:
                self._flows[flow_id] = bridge
        return result

    def poll(self, flow_id):
        with self._lock:
            bridge = self._flows.get(flow_id)
        if bridge is None:
            return {"status": "expired"}
        result = bridge.poll(flow_id)
        if result.get("status") != "pending":
            with self._lock:
                self._flows.pop(flow_id, None)
        return result

    def cancel(self, flow_id):
        with self._lock:
            bridge = self._flows.pop(flow_id, None)
        if bridge is None:
            return {"status": "expired"}
        return bridge.cancel(flow_id)


def detect_platform():
    """mac | linux | windows-wsl — the page preselects matching path defaults."""
    sysname = platform.system()
    if sysname == "Darwin":
        return "mac"
    if sysname == "Linux":
        try:
            with open("/proc/version", encoding="utf-8") as f:
                if "microsoft" in f.read().lower():
                    return "windows-wsl"
        except OSError:
            pass
        return "linux"
    return "linux"

# ── Runtime detection ─────────────────────────────────────────────────
# Mirrors the engine's desktop/src/main/remote-mcp-runtime.ts: which(1) +
# --version, last whitespace token of stdout is the version. A probe failure
# degrades to the manual list — detection never blocks an install.
RUNTIME_DETECTION_PLACEHOLDER = "__RUNTIME_DETECTION__"


def default_runtime_probe(runtime_id):
    """which + --version against one runtime's binary; uncovered ids report
    not installed rather than erroring."""
    spec = RUNTIMES.get(runtime_id)
    binary = spec and spec["bin"] and shutil.which(spec["bin"])
    if not binary:
        return {"installed": False}
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"installed": False, "error": str(error)}
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return {"installed": False,
                "error": detail or f"{runtime_id} exited with code {result.returncode}"}
    tokens = result.stdout.split()
    return {"installed": True, "version": tokens[-1]} if tokens else {"installed": True}


def detect_runtimes(probe=default_runtime_probe):
    """Aggregate probes over PROBED_RUNTIMES; never raises (mirrors
    detectRuntimes in the engine's desktop/src/shared/remote-mcp-contracts.ts)."""
    installed, failures = [], []
    for runtime_id in PROBED_RUNTIMES:
        try:
            observed = probe(runtime_id)
        except Exception as error:  # a raising probe is a failure, never a crash
            failures.append({"id": runtime_id, "reason": str(error) or "runtime probe failed"})
            continue
        if observed.get("error"):
            failures.append({"id": runtime_id, "reason": str(observed["error"])})
            continue
        if observed.get("installed"):
            row = {"id": runtime_id, "label": RUNTIMES[runtime_id]["label"]}
            if observed.get("version"):
                row["version"] = str(observed["version"])
            installed.append(row)
    return {"installed": installed, "failures": failures}


def apply_runtime_detection(page, detection):
    """Bake the startup detection payload over the page placeholder. `<` is
    escaped so a hostile --version string cannot close the inline script."""
    payload = json.dumps(detection).replace("<", "\\u003c")
    return page.replace(RUNTIME_DETECTION_PLACEHOLDER, payload)


# Fixed server-side prompts keyed by field name. The client only names the
# field; it can never inject dialog copy.
PICKER_PROMPTS = {
    "install_path": "Choose the folder where Spotlight should be installed",
    "vault_path": "Choose your Spotlight vault folder",
}


def pick_folder_natively(prompt):
    """Open a native OS folder dialog; returns (path|None, error|None).

    Runs on the install machine, so this works where a hosted page never
    could. Cancel returns (None, None).
    """
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            r = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to activate',
                 "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
                capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().rstrip("/"), None
            return None, None  # cancelled
        for cmd in (["zenity", "--file-selection", "--directory", "--title", prompt],
                    ["kdialog", "--getexistingdirectory", os.path.expanduser("~"), "--title", prompt]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                continue
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), None
            return None, None  # cancelled
        return None, "No folder picker available — type the path instead."
    except subprocess.TimeoutExpired:
        return None, None
    except Exception as e:
        return None, f"Folder picker failed: {e}"


# Choice → installer-value tables. Pins live in install-spotlight.sh only;
# this server (and configure.html) carry choices, never versions.
# Roster (2026-07-09): the Gemma-4 sovereign tiers. Repo + Q4 filename pairs are
# verified against the HF API; the 12b is the Spotlight procedure-tuned orchestrator.
MODEL_REPOS = {
    "gemma12b": "tomvaillant/gemma4-12b-spotlight-orchestrator-v5-GGUF",
    "gemma26b": "unsloth/gemma-4-26B-A4B-it-GGUF",
    "gemma31b": "unsloth/gemma-4-31B-it-GGUF",
}
MODEL_LABELS = {
    "gemma12b": "Gemma 4 12B — Spotlight orchestrator (procedure-tuned)",
    "gemma26b": "Gemma 4 26B-A4B (MoE)",
    "gemma31b": "Gemma 4 31B",
}
# Tier drives the harness compaction profile, the launcher's reasoning budget,
# and integration dismissal (12b: constrained set).
MODEL_TIERS = {"gemma12b": "12b", "gemma26b": "26b", "gemma31b": "31b"}
# The RLM (fetch distillation + compaction summarizer), served by the launcher on
# its own llama.cpp. Stock instruction-tuned e4b — verified public on HF.
RLM_REPO = "unsloth/gemma-4-E4B-it-GGUF"
RLM_GGUF = "gemma-4-E4B-it-Q4_K_M.gguf"
# Local serving is llama.cpp only (the Flue/Pi harness needs --jinja tool-calling).
SERVER_FOR_AGENT = {"flue": "llamacpp"}
CLOUD_KEY_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
}
PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "fireworks": "Fireworks AI",
}


def normalize(payload):
    """Coerce the POSTed form payload into the canonical choice dict."""
    def s(k):
        return str(payload.get(k) or "").strip()

    def b(k):
        return bool(payload.get(k))

    def enum(k, allowed, default):
        v = s(k)
        return v if v in allowed else default

    return {
        "mode": enum("mode", ("cloud", "local"), "cloud"),
        "cloudRuntime": enum("cloudRuntime", INSTALLABLE_RUNTIMES, "claude-code"),
        "opencodeProvider": enum("opencodeProvider", tuple(CLOUD_KEY_VARS), "openrouter"),
        "localAgent": enum("localAgent", tuple(SERVER_FOR_AGENT), "flue"),
        "localModel": enum("localModel", tuple(MODEL_REPOS), "gemma12b"),
        "rlmMode": enum("rlmMode", ("lite", "local_gemma4_e4b"), "lite"),
        # No silent defaults here: an emptied path must fail validation,
        # not quietly install somewhere the user didn't choose.
        "installPath": s("installPath"),
        "vaultPath": s("vaultPath"),
        "cloudKey": s("cloudKey"),
        "firecrawlKey": s("firecrawlKey"),
        "navigatorConnected": b("navigatorConnected"),
        "junkipediaKey": s("junkipediaKey"),
        "unpaywallEmail": s("unpaywallEmail"),
        "intDevBrowser": b("intDevBrowser"),
        "intJunkipedia": b("intJunkipedia"),
        "intUnpaywall": b("intUnpaywall"),
        "intRlm": b("intRlm"),
    }


def derived(d):
    """Installer-facing values derived from the canonical choice dict.

    Mirrors setup.html's collectForm()/buildExportBlock() derivations:
    runtime, local agent/server, model repo, and
    the provider env-var name the body keys the cloud-key write on.
    """
    local = d["mode"] == "local"
    opencode_cloud = (not local) and d["cloudRuntime"] == "opencode"
    return {
        "runtime": "local" if local else RUNTIMES[d["cloudRuntime"]]["installer"],
        # One local harness: Flue on Pi over llama.cpp (docs/runtimes.md, canonical).
        "agent": "flue" if local else "",
        "localServer": "llamacpp" if local else "",
        "localModel": d["localModel"] if local else "",
        "modelRepo": MODEL_REPOS[d["localModel"]] if local else "",
        "modelTier": MODEL_TIERS[d["localModel"]] if local else "",
        "opencodeProvider": d["opencodeProvider"] if opencode_cloud else "",
        "cloudKeyVar": CLOUD_KEY_VARS[d["opencodeProvider"]] if opencode_cloud else "",
        "needsCloudKey": opencode_cloud,
    }


# ── Structural validation (field names == configure.html input ids) ──

def validate_choices(d):
    errors, warnings = [], []
    if derived(d)["needsCloudKey"] and not d["cloudKey"]:
        provider = PROVIDER_LABELS[d["opencodeProvider"]]
        errors.append({"field": "cloud_key", "message": f"{provider} API key is required while OpenCode is your agent — paste a key or pick a subscription runtime."})
    if not d["installPath"]:
        errors.append({"field": "install_path", "message": "Install path is required — Spotlight has to live somewhere."})
    if not d["vaultPath"]:
        errors.append({"field": "vault_path", "message": "Vault path is required — Spotlight has nowhere to keep verified knowledge without it."})
    if d["installPath"] and d["vaultPath"]:
        # Compare expanded/resolved COPIES only; the as-entered strings are
        # what setup-config.env carries into the installer heredocs.
        install_real = os.path.realpath(os.path.expanduser(d["installPath"]))
        vault_real = os.path.realpath(os.path.expanduser(d["vaultPath"]))
        if vault_real == install_real:
            errors.append({"field": "vault_path", "message": "Vault path must be different from the install folder — the vault is durable knowledge, the install folder is replaceable code."})
        elif vault_real.startswith(install_real + os.sep):
            errors.append({"field": "vault_path", "message": "Vault path must not live inside the install folder — updates and re-installs would put your knowledge at risk."})
    if d["intJunkipedia"] and not d["junkipediaKey"]:
        warnings.append("Junkipedia is enabled without an API key; the integration stays dormant until you add JUNKIPEDIA_API_KEY to .env.")
    if d["intUnpaywall"] and not d["unpaywallEmail"]:
        warnings.append("Unpaywall is enabled without a contact email; the integration stays dormant until you add UNPAYWALL_EMAIL to .env.")
    if not d["firecrawlKey"]:
        warnings.append("Firecrawl is not configured; Spotlight will use sovereign SearXNG search and Crawl4AI scraping without a managed fallback.")
    return errors, warnings


# ── Live key validation ──
# Strict checks reject only on 401/403 — an unreachable or moved endpoint
# must never block an install. Lenient checks warn but never reject.

def probe(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8):
            return "ok"
    except urllib.error.HTTPError as e:
        return "rejected" if e.code in (401, 403) else "ok"
    except Exception:
        return "unreachable"


def validate_keys(d, skip=False):
    errors, warnings = [], []
    if skip:
        return errors, warnings
    der = derived(d)
    # Unpaywall identifies callers by email — format check only, no probe.
    if d["intUnpaywall"] and d["unpaywallEmail"] and not EMAIL_RE.match(d["unpaywallEmail"]):
        errors.append({"field": "unpaywall_email", "message": "Unpaywall contact email doesn't look like an email address — check it."})
    checks = []
    if d["firecrawlKey"]:
        checks.append(("firecrawl_key", "FIRECRAWL_API_KEY", True,
                       "https://api.firecrawl.dev/v1/team/credit-usage",
                       {"Authorization": "Bearer " + d["firecrawlKey"]}))
    if der["needsCloudKey"] and d["cloudKey"]:
        provider_probe_urls = {
            # OpenRouter's /models is public and returns 200 unauthenticated;
            # /api/v1/key is the endpoint that genuinely 401s on a bad key.
            "openrouter": "https://openrouter.ai/api/v1/key",
            "fireworks": "https://api.fireworks.ai/inference/v1/models",
        }
        checks.append(("cloud_key", der["cloudKeyVar"], True,
                       provider_probe_urls[d["opencodeProvider"]],
                       {"Authorization": "Bearer " + d["cloudKey"]}))
    if d["intJunkipedia"] and d["junkipediaKey"]:
        checks.append(("junkipedia_key", "JUNKIPEDIA_API_KEY", False,
                       "https://api.junkipedia.org/api/v1/issues",
                       {"Authorization": "Bearer " + d["junkipediaKey"]}))
    for field, name, strict, url, headers in checks:
        result = probe(url, headers)
        if result == "rejected" and strict:
            errors.append({"field": field, "message": f"{name} was rejected by the provider (401/403) — check the key and try again."})
        elif result == "rejected":
            warnings.append(f"{name} could not be verified (provider returned 401/403); continuing anyway.")
        elif result == "unreachable":
            warnings.append(f"{name} could not be verified (provider unreachable); continuing anyway.")
    return errors, warnings


# ── Generated artifacts ──

def build_env_lines(d):
    """Staged secrets. The installer body maps SPOTLIGHT_CLOUD_KEY onto
    $SPOTLIGHT_CLOUD_KEY_VAR when it writes the final $SPOTLIGHT_DIR/.env."""
    der = derived(d)
    lines = [
        "# Spotlight secrets — generated by the local configurator",
        "FIRECRAWL_API_KEY=" + shlex.quote(d["firecrawlKey"]),
    ]
    if der["needsCloudKey"] and d["cloudKey"]:
        lines.append("SPOTLIGHT_CLOUD_KEY=" + shlex.quote(d["cloudKey"]))
    if d["junkipediaKey"]:
        lines.append("JUNKIPEDIA_API_KEY=" + shlex.quote(d["junkipediaKey"]))
    return "\n".join(lines) + "\n"


def build_setup_config(d):
    """Choice flags — the full env-var contract the installer body consumes.

    Field-for-field mirror of setup.html's retired buildExportBlock(),
    minus the secrets (which live in the staged .env).
    """
    der = derived(d)
    local = d["mode"] == "local"
    # Local tier: the RLM is runtime-auto (fetch distillation + compaction summarizer),
    # served by the launcher on its own llama.cpp from a verified public GGUF.
    rlm_local = local and d["intRlm"]
    fields = [
        ("SPOTLIGHT_MODE", d["mode"]),
        ("SPOTLIGHT_RUNTIME", der["runtime"]),
        ("SPOTLIGHT_LOCAL_SERVER", der["localServer"]),
        ("SPOTLIGHT_LOCAL_MODEL", der["localModel"]),
        ("SPOTLIGHT_MODEL_TIER", der["modelTier"]),
        ("SPOTLIGHT_AGENT", der["agent"]),
        ("SPOTLIGHT_OPENCODE_INTERFACE", "cli"),
        ("SPOTLIGHT_OPENCODE_PROVIDER", der["opencodeProvider"]),
        ("SPOTLIGHT_CLOUD_KEY_VAR", der["cloudKeyVar"]),
        # Carried exactly as entered — the doctor/updater/launcher heredocs
        # bake these literals in; the body's expand_path handles ~ at runtime.
        ("SPOTLIGHT_DIR_INPUT", d["installPath"]),
        ("SPOTLIGHT_VAULT_INPUT", d["vaultPath"]),
        ("SPOTLIGHT_MODEL_REPO", der["modelRepo"]),
        ("SPOTLIGHT_INT_DEVBROWSER", "true" if d["intDevBrowser"] else "false"),
        ("SPOTLIGHT_INT_JUNKIPEDIA", "true" if d["intJunkipedia"] else "false"),
        ("SPOTLIGHT_INT_UNPAYWALL", "true" if d["intUnpaywall"] else "false"),
        ("UNPAYWALL_EMAIL", d["unpaywallEmail"]),
        ("SPOTLIGHT_INT_RLM", "true" if d["intRlm"] else "false"),
        ("SPOTLIGHT_NAVIGATOR_CONNECTION", "connected" if d["navigatorConnected"] else "locked"),
        ("SPOTLIGHT_RLM_MODE", "local_llamacpp_e4b" if rlm_local else ((d["rlmMode"] or "lite") if d["intRlm"] else "off")),
        ("SPOTLIGHT_RLM_MODEL", "rlm-e4b" if rlm_local else ""),
        ("SPOTLIGHT_RLM_REPO", RLM_REPO if rlm_local else ""),
        ("SPOTLIGHT_RLM_GGUF", RLM_GGUF if rlm_local else ""),
    ]
    lines = ["# Spotlight setup choices — generated by the local configurator (no secrets)"]
    lines += [f"{name}={shlex.quote(value)}" for name, value in fields]
    return "\n".join(lines) + "\n"


GETTING_STARTED_TEMPLATE = string.Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spotlight — Getting started</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23c16a34'><path d='M6 2h4v4h-4zM10 2h4v4h-4zM14 2h4v4h-4zM6 6h4v4h-4zM6 10h4v4h-4zM14 10h4v4h-4zM14 14h4v4h-4zM6 18h4v4h-4zM10 18h4v4h-4zM14 18h4v4h-4z'/></svg>">
<style>
  /* Spotlight DA (DESIGN.md) — local font stacks only; this page must work offline. */
  :root {
    --ink: #07070a;
    --ink-2: #0c0c10;
    --paper: #ede8dc;
    --paper-2: #e3ddce;
    --paper-fg: #17140e;
    --muted-dark: rgba(237, 232, 220, 0.55);
    --muted-faint-dark: rgba(237, 232, 220, 0.18);
    --muted-light: rgba(23, 20, 14, 0.55);
    --muted-faint-light: rgba(23, 20, 14, 0.15);
    --accent-warm: #c16a34;
    --accent-warm-14: rgba(193, 106, 52, 0.14);
    --green: #4a7d3f;
    --amber: #8a6212;
    --red: #a83838;
    --hairline-light: 1px solid var(--muted-faint-light);
    --hairline-dark: 1px solid var(--muted-faint-dark);
    --display: "Fraunces", Georgia, serif;
    --mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--paper-fg); font: 400 14px/1.65 var(--mono); -webkit-font-smoothing: antialiased; }
  .shell { max-width: 860px; margin: 0 auto; padding: clamp(36px, 6vw, 88px) clamp(20px, 4vw, 48px) 96px; }
  .brand { display: flex; align-items: center; gap: 10px; font-family: var(--display); font-variation-settings: "opsz" 48, "wght" 500; font-weight: 500; font-size: 17px; letter-spacing: -0.01em; margin: 0; }
  .brand svg { width: 18px; height: 18px; }
  .brand em { color: var(--accent-warm); font-style: normal; }
  h1 { font-family: var(--display); font-variation-settings: "opsz" 144, "wght" 500; font-weight: 500; font-size: clamp(44px, 7vw, 72px); line-height: 0.98; letter-spacing: -0.025em; margin: 28px 0 16px; }
  h1 em { font-style: italic; font-variation-settings: "opsz" 144, "wght" 400; font-weight: 400; color: var(--accent-warm); }
  .lede { font-size: 14px; line-height: 1.7; color: var(--muted-light); max-width: 58ch; }
  .num { font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.2em; text-transform: uppercase; color: var(--paper-fg); opacity: 0.55; margin: 72px 0 0; }
  h2 { font-family: var(--display); font-variation-settings: "opsz" 96, "wght" 500; font-weight: 500; font-size: clamp(26px, 3.4vw, 38px); line-height: 1.05; letter-spacing: -0.02em; margin: 10px 0 20px; padding-bottom: 16px; border-bottom: var(--hairline-light); }
  h2 em { font-style: italic; font-variation-settings: "opsz" 96, "wght" 400; font-weight: 400; color: var(--accent-warm); }
  p { line-height: 1.7; }
  code { font-family: var(--mono); font-size: 0.92em; background: var(--paper-2); border: var(--hairline-light); padding: 1px 5px; }
  .card { background: var(--paper-2); border: var(--hairline-light); padding: 20px 22px; margin: 0 0 8px; }
  .card .k { font-family: var(--mono); font-size: 11px; font-weight: 500; letter-spacing: 0.15em; text-transform: uppercase; color: var(--paper-fg); opacity: 0.55; margin: 0 0 10px; }
  .card code { background: var(--paper); border: var(--hairline-light); font-size: 13px; }
  .card pre { font-family: var(--mono); font-size: 13px; line-height: 1.6; color: var(--paper); background: var(--ink); border: var(--hairline-dark); padding: 14px 16px; margin: 0; white-space: pre-wrap; word-break: break-word; }
  table { width: 100%; border-collapse: collapse; background: var(--paper-2); border: var(--hairline-light); }
  th, td { text-align: left; padding: 12px 16px; border-bottom: var(--hairline-light); font-size: 13px; line-height: 1.6; vertical-align: top; }
  th { font-family: var(--mono); font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--muted-light); font-weight: 500; white-space: nowrap; }
  td code { background: var(--paper); font-size: 12px; word-break: break-all; }
  .callout { background: var(--paper-2); border: var(--hairline-light); border-left: 3px solid var(--accent-warm); padding: 18px 22px; margin: 16px 0; font-size: 13px; line-height: 1.6; }
  .callout.urgent { border-left-color: var(--red); }
  ol, ul { padding-left: 1.3em; } li { margin: 6px 0; }
  a { color: inherit; text-decoration: none; border-bottom: 1px solid currentColor; transition: color 0.15s ease; }
  a:hover { color: var(--accent-warm); }
  .foot { margin-top: 96px; padding-top: 24px; border-top: var(--hairline-light); font-size: 12px; line-height: 1.7; color: var(--muted-light); }
</style>
</head>
<body>
<div class="shell">
  <p class="brand"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 2h4v4h-4zM10 2h4v4h-4zM14 2h4v4h-4zM6 6h4v4h-4zM6 10h4v4h-4zM14 10h4v4h-4zM14 14h4v4h-4zM6 18h4v4h-4zM10 18h4v4h-4zM14 18h4v4h-4z"/></svg>Spotlight<em>.</em></p>
  <h1>Case <em>open.</em></h1>
  <p class="lede">Spotlight is installed and wired into your agent runtime. This page is your map for the first hour — what landed on your machine, how to open your first case, and where to look when something breaks.</p>

  <p class="num">01 — Your install</p>
  <h2>What landed <em>where.</em></h2>
  <table>
    <tr><th>Mode</th><td>$mode_label</td></tr>
    <tr><th>Install folder</th><td><code>$install_path</code> — skills, agents, and active casework under <code>cases/</code></td></tr>
    <tr><th>Vault</th><td><code>$vault_path</code> — your durable investigative memory (standard Markdown with YAML frontmatter)</td></tr>
    <tr><th>Integrations</th><td>$integrations</td></tr>
  </table>

  <p class="num">$n_first — First case</p>
  <h2>Open a terminal, say <em>the word.</em></h2>
  <div class="card"><p class="k">In a new terminal window</p><pre>spotlight</pre></div>
  <p>$launch_note</p>
$prompt_cards
  <p class="num">$n_doctor — When something breaks</p>
  <h2>Doctor first, <em>then docs.</em></h2>
  <div class="card"><p class="k">In a new terminal</p><pre>spotlight doctor    # checks every install path, command, and key
spotlight update    # fast-forward to the latest reviewed release</pre></div>
  <p>Still stuck? <a href="https://github.com/buriedsignals/spotlight#readme">Read the docs</a> · <a href="https://github.com/buriedsignals/spotlight/issues">Open an issue</a></p>

  <p class="foot">Spotlight · One agent reports, one agent checks, you stay the editor. Built by <a href="https://buriedsignals.com/">Buried Signals</a>. This guide lives at <code>~/.config/spotlight/getting-started.html</code>.</p>
</div>
</body>
</html>
""")


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_getting_started(d):
    der = derived(d)
    if d["mode"] == "local":
        mode_label = f"Local · {MODEL_LABELS[d['localModel']]} via llama.cpp (Flue/Pi harness) · runs on your machine"
    elif der["needsCloudKey"]:
        mode_label = f"Frontier · OpenCode via {PROVIDER_LABELS[d['opencodeProvider']]} (pay per token)"
    else:
        mode_label = f"Frontier · {RUNTIME_LABELS[d['cloudRuntime']]} (covered by your subscription)"

    integrations = ["SearXNG + Crawl4AI (sovereign web research)"]
    if d["firecrawlKey"]:
        integrations.append("Firecrawl (optional hosted fallback)")
    if d["navigatorConnected"]:
        integrations.append("Navigator (OSINT tool discovery)")
    else:
        integrations.append("Navigator skill (locked; no credential or CLI)")
    if d["intDevBrowser"]:
        integrations.append("dev-browser (browser automation)")
    if d["intJunkipedia"]:
        integrations.append("Junkipedia (narrative tracking)")
    if d["intUnpaywall"]:
        integrations.append("Unpaywall (open-access lookup)")
    if d["intRlm"]:
        if d["mode"] == "local":
            integrations.append("RLM (Gemma4 E4B): automatic scrape distillation + conversation compaction")
        else:
            integrations.append(f"Case-corpus lead extraction ({'Gemma4 E4B' if d['rlmMode'] == 'local_gemma4_e4b' else 'Lite'} mode)")

    if d["mode"] == "local":
        launch_note = ("The <code>spotlight</code> command starts your local inference server, loads the "
                       f"{esc(MODEL_LABELS[d['localModel']])}, and opens the harness inside your Spotlight folder with every skill loaded. "
                       "Nothing leaves your machine.")
    elif d["cloudRuntime"] == "opencode":
        launch_note = ("The <code>spotlight</code> command opens OpenCode inside your Spotlight folder with every skill loaded. "
                       "First time: type <code>/model</code> and pick a strong default for your provider.")
    else:
        runtime = RUNTIME_LABELS[d["cloudRuntime"]]
        login_cmd = {"claude-code": "claude login", "codex-cli": "codex login", "pi": "pi  # then /login"}[d["cloudRuntime"]]
        launch_note = (f"The <code>spotlight</code> command opens {esc(runtime)} inside your Spotlight folder with every skill loaded. "
                       f"First time only: run <code>{esc(login_cmd)}</code> and sign in with your subscription account — no API key needed.")

    prompts = [
        ("Open a case", "Start a Spotlight investigation on [your lead]."),
        ("Resume a case", "Resume the [case name] investigation."),
        ("Ask your vault", "What do we know about [person, company, or topic]?"),
        ("Ingest findings", "Ingest the approved findings from [case name] into the vault."),
    ]
    if d["intRlm"]:
        prompts.append(("Mine the case corpus", "Run case-corpus lead extraction on [case name] and fold the leads into the plan."))
    prompt_cards = "".join(
        f'      <div class="card">\n        <p class="k">{i + 1:02d} — {esc(label)}</p>\n        <code>{esc(text)}</code>\n      </div>\n'
        for i, (label, text) in enumerate(prompts)
    )

    base = 2  # First case directly follows "Your install"
    return GETTING_STARTED_TEMPLATE.substitute(
        mode_label=esc(mode_label),
        install_path=esc(d["installPath"]),
        vault_path=esc(d["vaultPath"]),
        integrations=esc(" · ".join(integrations)),
        n_first=f"0{base}",
        launch_note=launch_note,
        prompt_cards=prompt_cards,
        n_doctor=f"0{base + 1}",
    )


def write_artifacts(d, profile_dir):
    """Atomic, all-or-nothing: every artifact is written to an O_EXCL temp
    file with its final mode at creation, then the set is renamed into place.
    Any failure removes the temps and leaves the profile dir untouched."""
    os.makedirs(profile_dir, mode=0o700, exist_ok=True)
    os.chmod(profile_dir, 0o700)  # even when the dir pre-existed

    artifacts = [
        (".env", build_env_lines(d), 0o600),
        ("setup-config.env", build_setup_config(d), 0o600),
        ("getting-started.html", build_getting_started(d), 0o644),
    ]
    suffix = ".tmp-" + secrets.token_hex(4)
    staged = []  # (tmp_path, final_path)
    try:
        for name, content, mode in artifacts:
            final = os.path.join(profile_dir, name)
            tmp = final + suffix
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            staged.append((tmp, final))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, mode)  # exact mode regardless of umask
        for tmp, final in staged:
            os.replace(tmp, final)
    except Exception:
        for tmp, _ in staged:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-key-validation", action="store_true",
                        help="Skip live provider key checks (tests, offline installs)")
    parser.add_argument("--legacy-only", action="store_true",
                        help="Use the public product writer without Engine")
    args = parser.parse_args()

    page_path = os.path.join(args.repo_dir, "install", "configure.html")
    try:
        page = open(page_path, encoding="utf-8").read()
    except OSError:
        print(f"configure.html not found at {page_path}", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(16)
    page = page.replace("__SETUP_TOKEN__", token)
    page = page.replace("__PLATFORM__", detect_platform())
    page = apply_runtime_detection(page, detect_runtimes())
    done = threading.Event()
    result = {"written": False}
    engine_bridge = None
    engine_descriptor = None
    if not args.legacy_only:
        try:
            engine_bridge = EngineBridge("spotlight")
            engine_descriptor = engine_bridge.descriptor()
        except (EngineUnavailable, RuntimeError, KeyError):
            pass
    navigator_router = NavigatorBridgeRouter(
        os.path.join(args.repo_dir, "install", "navigator-transport-matrix.json")
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _same_origin(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin == "http://" + self.headers.get("Host", "")

        def do_GET(self):
            # Token-gated GET: only the URL printed in the terminal (which
            # carries ?t=<token>) can read the page — any other local process
            # gets a 403, never the token-baked HTML.
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("t", [""])[0] != token:
                self._send(403, "forbidden — open the exact URL printed in the terminal", "text/plain")
                return
            if parsed.path == "/":
                self._send(200, page, "text/html")
            elif parsed.path == "/engine-descriptor" and engine_descriptor is not None:
                self._send(200, json.dumps(engine_descriptor))
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path not in ("/submit", "/pick-folder", "/engine-submit", "/navigator/start", "/navigator/poll", "/navigator/cancel", "/navigator/status"):
                self._send(404, "not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._send(400, json.dumps({"errors": [{"field": "", "message": "Malformed request."}]}))
                return
            if payload.get("token") != token:
                self._send(403, json.dumps({"errors": [{"field": "", "message": "Bad token — reload the page from the terminal URL."}]}))
                return
            if not self._same_origin():
                self._send(403, json.dumps({"errors": [{"field": "", "message": "Cross-origin setup request rejected."}]}))
                return
            if parsed_path == "/navigator/status":
                try:
                    self._send(200, json.dumps(navigator_router.status(payload.get("runtime"))))
                except NavigatorBridgeError as error:
                    self._send(400, json.dumps({"status": "locked-only", "detail": str(error)}))
                return
            if parsed_path == "/navigator/start":
                try:
                    self._send(200, json.dumps(navigator_router.start(
                        payload.get("runtime"), str(payload.get("email") or "").strip()
                    )))
                except NavigatorBridgeError as error:
                    self._send(503, json.dumps({"status": "offline", "detail": str(error)}))
                return
            if parsed_path == "/navigator/poll":
                try:
                    self._send(200, json.dumps(navigator_router.poll(str(payload.get("flow_id") or ""))))
                except NavigatorBridgeError as error:
                    self._send(503, json.dumps({"status": "failed", "detail": str(error)}))
                return
            if parsed_path == "/navigator/cancel":
                try:
                    self._send(200, json.dumps(navigator_router.cancel(str(payload.get("flow_id") or ""))))
                except NavigatorBridgeError as error:
                    self._send(503, json.dumps({"status": "failed", "detail": str(error)}))
                return
            if parsed_path == "/pick-folder":
                # The client names a field; the prompt copy is fixed server-side.
                field = str(payload.get("field") or "")
                prompt = PICKER_PROMPTS.get(field, "Choose a folder")
                path, error = pick_folder_natively(prompt)
                self._send(200, json.dumps({"path": path, "error": error}))
                return
            if parsed_path == "/engine-submit":
                if engine_bridge is None:
                    self._send(404, json.dumps({"errors":[{"field":"","message":"Engine configuration is unavailable in this public install."}]}))
                    return
                try:
                    response = engine_bridge.submit(payload.get("request") or {}, payload.get("secrets") or {})
                    marker = os.path.join(args.profile_dir, "engine-plan.ready")
                    os.makedirs(args.profile_dir, mode=0o700, exist_ok=True); os.chmod(args.profile_dir, 0o700)
                    tmp = marker + ".tmp-" + secrets.token_hex(4)
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle: json.dump(response, handle)
                    os.replace(tmp, marker)
                except Exception as e:
                    self._send(400, json.dumps({"errors":[{"field":"","message":str(e)}]})); return
                result["written"] = True
                self._send(200, json.dumps(response))
                threading.Timer(0.5, done.set).start(); return
            if engine_bridge is not None and not args.legacy_only:
                self._send(410, json.dumps({"errors":[{"field":"","message":"Use the Engine-managed form shown in this session."}]}))
                return
            d = normalize(payload)
            navigator_choice = str(payload.get("navigatorChoice") or "")
            if navigator_choice not in {"connect", "skip"}:
                self._send(400, json.dumps({"errors": [{"field": "navigator", "message": "Choose Connect Navigator or Continue without Navigator."}]}))
                return
            if navigator_choice == "connect":
                try:
                    status = navigator_router.status(derived(d)["runtime"])
                except NavigatorBridgeError as error:
                    self._send(400, json.dumps({"errors": [{"field": "navigator", "message": str(error)}]}))
                    return
                if status.get("status") != "connected":
                    self._send(400, json.dumps({"errors": [{"field": "navigator", "message": "Navigator is not connected for the selected runtime. Finish sign-in or choose Continue without Navigator."}]}))
                    return
            d["navigatorConnected"] = navigator_choice == "connect"
            errors, warnings = validate_choices(d)
            if not errors:
                key_errors, key_warnings = validate_keys(d, skip=args.skip_key_validation)
                errors += key_errors
                warnings += key_warnings
            if errors:
                self._send(400, json.dumps({"errors": errors, "warnings": warnings}))
                return
            try:
                write_artifacts(d, args.profile_dir)
            except Exception as e:
                self._send(500, json.dumps({"errors": [{"field": "", "message": f"Could not write configuration: {e}"}]}))
                return
            result["written"] = True
            self._send(200, json.dumps({"ok": True, "warnings": warnings}))
            threading.Timer(0.5, done.set).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    # flush: the installer (and the test harness) may read these through a pipe
    print(f"  Configurator: {url}", flush=True)
    print("  Waiting for you to finish in the browser (Ctrl-C to abort)...", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        finished = done.wait(SUBMIT_TIMEOUT_SECONDS)
    except KeyboardInterrupt:
        finished = False
        print("\n  Aborted.")
    server.shutdown()
    if not finished or not result["written"]:
        if not result["written"]:
            print("  No configuration received; nothing was written.", file=sys.stderr)
        return 1
    print("  Configuration saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
