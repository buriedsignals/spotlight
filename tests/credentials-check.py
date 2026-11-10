#!/usr/bin/env python3
"""Offline checks for integrations/_credentials.py and the keyed helpers.

No network: HTTP is replaced by fakes. Every check builds its own checkout-like
temp directory so the real `.spotlight-config.json` and `.env` are untouched.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations import _credentials as creds  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(name)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def checkout(env_file_body: str | None = None, dotenv: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    config: dict = {}
    if env_file_body is not None:
        path = root / "credentials" / "spotlight.env"
        path.parent.mkdir()
        path.write_text(env_file_body, encoding="utf-8")
        config["env_file"] = str(path)
    (root / ".spotlight-config.json").write_text(json.dumps(config), encoding="utf-8")
    if dotenv is not None:
        (root / ".env").write_text(dotenv, encoding="utf-8")
    return root


ENGINE = (
    "# Managed by Indicator Labs. Keys are edited in Indicator Labs; changes here are overwritten.\n"
    "# managed: APIFY_API_TOKEN APIFY_TOKEN FIRECRAWL_API_KEY\n"
    "FIRECRAWL_API_KEY='fc-a'\\''b'\n"
)


def with_env(**values: str):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    return saved


def restore(saved: dict) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# 1. The configured file decides for managed names, including absence.
root = checkout(ENGINE)
saved = with_env(APIFY_API_TOKEN="stale-apify", FIRECRAWL_API_KEY="stale-fc", GEMINI_API_KEY="mine")
check("managed value from file", creds.credential("FIRECRAWL_API_KEY", root) == "fc-a'b")
check("managed absence beats a stale environment value", creds.credential("APIFY_API_TOKEN", root) == "")
check("unmanaged names fall back to the environment", creds.credential("GEMINI_API_KEY", root) == "mine")
env = creds.subprocess_env(["FIRECRAWL_API_KEY", "APIFY_API_TOKEN"], root)
check("child env carries the file value", env.get("FIRECRAWL_API_KEY") == "fc-a'b")
check("child env drops a stale managed value", "APIFY_API_TOKEN" not in env)
check("os.environ untouched", os.environ.get("FIRECRAWL_API_KEY") == "stale-fc")
restore(saved)

# 2. Rotation takes effect on the next call in the same process.
path = Path(json.loads((root / ".spotlight-config.json").read_text())["env_file"])
path.write_text(ENGINE.replace("fc-a'\\''b", "fc-rotated"), encoding="utf-8")
check("rotation seen on next call", creds.credential("FIRECRAWL_API_KEY", root) == "fc-rotated")

# 3. Without env_file, the existing order stays: environment, then .env.
root = checkout(None, dotenv="FIRECRAWL_API_KEY=from-dotenv\nJUNKIPEDIA_API_KEY='jk'\n")
saved = with_env(FIRECRAWL_API_KEY="from-env")
check("environment wins without env_file", creds.credential("FIRECRAWL_API_KEY", root) == "from-env")
check(".env used when the environment lacks the name", creds.credential("JUNKIPEDIA_API_KEY", root) == "jk")
restore(saved)

# 4. A file without a managed line wins only for names it holds.
root = checkout("FIRECRAWL_API_KEY=user-file\n")
saved = with_env(APIFY_API_TOKEN="env-apify")
check("self-install file value wins", creds.credential("FIRECRAWL_API_KEY", root) == "user-file")
check("self-install file without managed line falls back", creds.credential("APIFY_API_TOKEN", root) == "env-apify")
restore(saved)

# 5. Redaction.
check("redact replaces values", creds.redact("401 for key fc-secret-1", ["fc-secret-1"]) == "401 for key [redacted]")
check("redact ignores empty values", creds.redact("unchanged", ["", "ab"]) == "unchanged")

# 6. Arbiter resolves its key per request when built from the environment.
client_mod = load("spotlight_arbiter_client_credentials", ROOT / "integrations" / "arbiter" / "client.py")
root_env = ROOT / ".spotlight-config.json"
seen: list[str] = []


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(request, timeout):
    seen.append(request.get_header("Authorization"))
    return FakeResponse(b"{}")


client_mod._resolve_addresses = lambda host, port=443: ["203.0.113.1"]  # offline
orig_credential = client_mod.credential
keys = iter(["arb-1", "arb-1", "arb-2"])
client_mod.credential = lambda name: next(keys)
client = client_mod.ArbiterClient.from_env(opener=opener)
client.request_raw("GET", "/case-studies")
client.request_raw("GET", "/case-studies")
check("arbiter rotation applies to the next request on one client", seen == ["Bearer arb-1", "Bearer arb-2"])
client_mod.credential = orig_credential
explicit = client_mod.ArbiterClient.from_env({"ARBITER_API_KEY": "fixed"}, opener=opener)
seen.clear()
explicit.request_raw("GET", "/case-studies")
check("explicit mapping keeps its key", seen == ["Bearer fixed"])

# 7. Decision checks: the configured file decides; otherwise the old order.
signals = load("decision_signals_credentials", ROOT / "scripts" / "decision-signals.py")
root = checkout("# managed: OPENROUTER_API_KEY\n")
signals.ROOT = root
signals.credentials.ROOT = root
saved = with_env(OPENROUTER_API_KEY="stale-or")
orig_cfg = signals.credentials.config_env_file
signals.credentials.config_env_file = lambda _root=root: orig_cfg(root)
check("decision key removed in Indicator Labs stays removed", signals.resolve_key({}, None) == ("", ""))
signals.credentials.config_env_file = lambda _root=root: None
check("decision key falls back to the environment without env_file", signals.resolve_key({}, None)[0] == "stale-or")
signals.credentials.config_env_file = orig_cfg
restore(saved)

# 8. Helpers: status never prints a key; bad input is refused offline.
apify = load("spotlight_apify_main", ROOT / "integrations" / "apify" / "__main__.py")
junk = load("spotlight_junkipedia_main", ROOT / "integrations" / "junkipedia" / "__main__.py")
tmp = Path(tempfile.mkdtemp())
(tmp / "in.json").write_text("{}", encoding="utf-8")
check("apify refuses a bad actor id", apify.run("../x", tmp / "in.json", tmp / "out.json") == 2)
check("junkipedia refuses an unsupported path", junk.get("../etc", [], tmp / "out.json") == 2)
check("junkipedia refuses a malformed param", junk.get("posts", ["q"], tmp / "out.json") == 2)
captured = {}


def fake_urlopen(request, timeout):
    captured["url"] = request.full_url
    captured["auth"] = request.get_header("Authorization")
    return FakeResponse(b"[]")


apify.urlopen = fake_urlopen
apify.token = lambda: "apify-secret"
check("apify run succeeds", apify.run("61RPP7dywgiy0JPD0", tmp / "in.json", tmp / "out.json") == 0)
check("apify token goes in the header", captured.get("auth") == "Bearer apify-secret")
check("apify token stays out of the URL", "apify-secret" not in captured.get("url", ""))

# 9. An unreadable configured file fails closed, even for unmanaged names.
root = checkout(ENGINE)
Path(json.loads((root / ".spotlight-config.json").read_text())["env_file"]).unlink()
saved = with_env(FIRECRAWL_API_KEY="stale-fc", GEMINI_API_KEY="mine")
check("missing configured file fails closed", creds.credential("FIRECRAWL_API_KEY", root) == "")
check("missing configured file fails closed for unmanaged names", creds.credential("GEMINI_API_KEY", root) == "")
restore(saved)

# 10. Helpers refuse redirects, so the Authorization header never travels on.
from urllib.error import HTTPError  # noqa: E402
from urllib.request import Request  # noqa: E402
try:
    apify._NoRedirect().redirect_request(Request("https://api.apify.com/v2/x"), None, 302, "Found", {}, "http://evil.example/")
    check("apify refuses redirects", False)
except HTTPError:
    check("apify refuses redirects", True)
try:
    junk._NoRedirect().redirect_request(Request("https://api.junkipedia.org/api/v1/posts"), None, 301, "Moved", {}, "https://other.example/")
    check("junkipedia refuses redirects", False)
except HTTPError:
    check("junkipedia refuses redirects", True)

# 10b. ThinkPol and treg: bad input is refused offline, the key travels only
# in its header, only documented routes and pinned endpoints are reachable,
# and treg's balance-bearing 402 body is never echoed.
thinkpol = load("spotlight_thinkpol_main", ROOT / "integrations" / "thinkpol" / "__main__.py")
treg = load("spotlight_treg_main", ROOT / "integrations" / "treg" / "__main__.py")
thinkpol.key = lambda: "thinkpol-secret"
treg.token = lambda: "treg-secret"
sent = {}


class HeaderResponse(FakeResponse):
    headers = {"X-Treg-Cost-Micro": "450", "X-Treg-Call-Id": "call-1"}


def recording_urlopen(request, timeout):
    sent.update(url=request.full_url, method=request.get_method(), body=request.data,
                headers={k.lower(): v for k, v in request.header_items()})
    return HeaderResponse(b"{}")


thinkpol.urlopen = treg.urlopen = recording_urlopen
(tmp / "tp.json").write_text('{"terms": ["a b", "c"], "mode": "phrase"}', encoding="utf-8")
check("thinkpol refuses the stream routes", thinkpol.get("/v1/streams", tmp / "tp.json", tmp / "tp-out.json") == 2)
(tmp / "tp-user.json").write_text('{"username": "../admin"}', encoding="utf-8")
check("thinkpol refuses a path-breaking username",
      thinkpol.get("/v2/user/{username}/posts", tmp / "tp-user.json", tmp / "tp-out.json") == 2)
check("thinkpol search succeeds", thinkpol.get("/v3/search", tmp / "tp.json", tmp / "tp-out.json") == 0)
check("thinkpol repeats terms and keeps a phrase whole",
      sent["url"] == "https://api.think-pol.com/v3/search?terms=a+b&terms=c&mode=phrase")
check("thinkpol key goes in the header", sent["headers"].get("authorization") == "Bearer thinkpol-secret")
check("thinkpol key stays out of the URL", "thinkpol-secret" not in sent["url"])

(tmp / "treg.json").write_text('{"handle": "nasa", "limit": 20}', encoding="utf-8")
check("treg refuses an endpoint that is not pinned",
      treg.call("x.x.post-create", tmp / "treg.json", tmp / "treg-out.json") == 2)
(tmp / "treg-extra.json").write_text('{"handle": "nasa", "webhook": "https://evil.example"}', encoding="utf-8")
check("treg refuses a field the endpoint does not take",
      treg.call("anyapi.x.user.posts", tmp / "treg-extra.json", tmp / "treg-out.json") == 2)
check("treg refuses an unbounded cost ceiling",
      treg.call("anyapi.x.user.posts", tmp / "treg.json", tmp / "treg-out.json", max_cost=50) == 2)
check("treg call succeeds", treg.call("anyapi.x.user.posts", tmp / "treg.json", tmp / "treg-out.json") == 0)
check("treg posts the fields as a JSON body", sent["method"] == "POST" and json.loads(sent["body"]) == {"handle": "nasa", "limit": 20})
check("treg token goes in its header", sent["headers"].get("x-treg-token") == "treg-secret")
check("treg token stays out of the URL", "treg-secret" not in sent["url"])
check("treg caps the call cost", sent["headers"].get("x-treg-route-max-cost") == "0.05")
(tmp / "treg-get.json").write_text('{"url": "https://www.facebook.com/a b"}', encoding="utf-8")
check("treg GET call succeeds", treg.call("scrapecreators.x.v1-facebook-profile-posts", tmp / "treg-get.json", tmp / "treg-out.json") == 0)
check("treg sends GET fields in the query string",
      sent["method"] == "GET" and sent["body"] is None and sent["url"].endswith("?url=https%3A%2F%2Fwww.facebook.com%2Fa+b"))


def empty_balance(request, timeout):
    import io
    raise HTTPError(request.full_url, 402, "Payment Required", {}, io.BytesIO(b'{"balance_micro": 12345, "topup_url": "https://treg.to/topup/secret"}'))


treg.urlopen = empty_balance
import contextlib  # noqa: E402
import io as _io  # noqa: E402
stderr = _io.StringIO()
with contextlib.redirect_stderr(stderr):
    rc = treg.call("anyapi.x.user.posts", tmp / "treg.json", tmp / "treg-out.json")
check("treg reports an empty balance without echoing the account body",
      rc == 1 and "402" in stderr.getvalue() and "topup" not in stderr.getvalue() and "12345" not in stderr.getvalue())
for helper, origin in ((thinkpol, "https://api.think-pol.com/v3/search"), (treg, "https://treg.to/call/x")):
    try:
        helper._NoRedirect().redirect_request(Request(origin), None, 302, "Found", {}, "http://evil.example/")
        check(f"{helper.__name__} refuses redirects", False)
    except HTTPError:
        check(f"{helper.__name__} refuses redirects", True)

# 11. A provider supplied by the caller is only a fallback to configured keys.
client_mod.credential = lambda name: "arb-file"
def refuse():
    raise ValueError("not configured")
seen.clear()
client = client_mod.ArbiterClient.from_env(opener=opener, credential_provider=refuse)
client.request_raw("GET", "/case-studies")
check("file key used even when a fallback provider is given", seen == ["Bearer arb-file"])
client_mod.credential = orig_credential

# 12. The configured executable is used when PATH lacks the command.
root = checkout(None)
tool = root / "fc"
tool.write_text("#!/bin/sh\n", encoding="utf-8")
tool.chmod(0o755)
(root / ".spotlight-config.json").write_text(json.dumps({"executables": {"spotlight-no-such-cmd": str(tool)}}))
check("configured executable resolves", creds.executable("spotlight-no-such-cmd", root) == str(tool))

# 13. Output is renamed into place: a symlink swapped in is replaced, not followed.
out_dir = Path(tempfile.mkdtemp())
decoy = out_dir / "decoy"
decoy.write_text("keep", encoding="utf-8")
target = out_dir / "out.json"
target.symlink_to(decoy)
creds.write_output(target, b"[]")
check("symlink target untouched", decoy.read_text() == "keep")
check("output written in place", target.read_bytes() == b"[]" and not target.is_symlink())

# 14. SPOTLIGHT_CONFIG (absolute) names the install config instead of the checkout root's.
root = checkout(None)
elsewhere = checkout(ENGINE)
saved = with_env(SPOTLIGHT_CONFIG=str(elsewhere / ".spotlight-config.json"), FIRECRAWL_API_KEY="stale-fc")
check("SPOTLIGHT_CONFIG decides which env_file is read", creds.credential("FIRECRAWL_API_KEY", root) == "fc-a'b")
restore(saved)
saved = with_env(SPOTLIGHT_CONFIG="relative/.spotlight-config.json")
try:
    creds.credential("FIRECRAWL_API_KEY", root)
    check("a relative SPOTLIGHT_CONFIG is refused", False)
except creds.ConfigPathError:
    check("a relative SPOTLIGHT_CONFIG is refused", True)
restore(saved)

if FAILURES:
    for name in FAILURES:
        print("FAIL:", name)
    sys.exit(1)
print("credentials-check: ok")
