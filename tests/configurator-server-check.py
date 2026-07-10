#!/usr/bin/env python3
"""Contract tests for the local configurator (install/setup_server.py).

Covers: page serving + token injection, the GET token gate, CSRF token
rejection on both POST endpoints, structural validation (paths, cloud key,
Tolaria, vault/install nesting), key-validation routing with a stubbed
prober, artifact writing (content, modes, atomicity, secret hygiene), the
getting-started guide, and the installer env naming contract. Live key
validation is skipped (--skip-key-validation) — its routing is unit-tested
directly against validate_keys.
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "install"))
import setup_server as srv  # noqa: E402

BASE = {
    "mode": "cloud", "cloudRuntime": "claude", "opencodeProvider": "openrouter",
    "cloudKey": "", "localAgent": "flue", "localModel": "gemma12b",
    "firecrawlKey": "fc-secret-test", "navKey": "on-secret-test",
    "vaultApp": "obsidian",
    "installPath": "~/Documents/Spotlight", "vaultPath": "~/Intelligence",
    "intDevBrowser": True,
    "intJunkipedia": True, "junkipediaKey": "jk-secret-test",
    "intUnpaywall": True, "unpaywallEmail": "reporter@example.com",
    "intRlm": True, "rlmMode": "local_gemma4_e4b",
}
OPENCODE = {**BASE, "cloudRuntime": "opencode", "cloudKey": "sk-or-cloud-secret"}
LOCAL = {**BASE, "mode": "local", "intJunkipedia": False, "junkipediaKey": ""}
SECRETS = ["fc-secret-test", "on-secret-test", "sk-or-cloud-secret", "jk-secret-test"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def errs(payload):
    errors, _ = srv.validate_choices(srv.normalize(payload))
    return errors


def warns(payload):
    _, warnings = srv.validate_choices(srv.normalize(payload))
    return warnings


class UnitChecks(unittest.TestCase):
    def test_structural_validation(self):
        self.assertEqual(errs(BASE), [])
        cases = [
            ({"firecrawlKey": ""}, "firecrawl_key"),
            ({"navKey": ""}, "nav_key"),
            ({"vaultPath": "   "}, "vault_path"),
            ({"installPath": ""}, "install_path"),
        ]
        for overrides, field in cases:
            errors = errs({**BASE, **overrides})
            self.assertTrue(any(e["field"] == field for e in errors), field)

    def test_cloud_key_requirement(self):
        # opencode without a cloud key errors; with one it passes
        self.assertTrue(any(e["field"] == "cloud_key" for e in errs({**OPENCODE, "cloudKey": ""})))
        self.assertEqual(errs(OPENCODE), [])
        # claude (subscription login) never needs a cloud key
        self.assertEqual(errs(BASE), [])
        # local mode never needs a cloud key either
        self.assertEqual(errs({**LOCAL, "cloudKey": ""}), [])

    def test_vault_install_separation(self):
        # equal (same tilde form)
        errors = errs({**BASE, "vaultPath": "~/Documents/Spotlight"})
        self.assertTrue(any(e["field"] == "vault_path" for e in errors))
        # nested (same form)
        errors = errs({**BASE, "vaultPath": "~/Documents/Spotlight/vault"})
        self.assertTrue(any(e["field"] == "vault_path" for e in errors))
        # mixed form: tilde install path vs absolute nested vault path
        abs_nested = os.path.join(os.path.expanduser("~"), "Documents", "Spotlight", "deep", "vault")
        errors = errs({**BASE, "vaultPath": abs_nested})
        self.assertTrue(any(e["field"] == "vault_path" for e in errors))
        # mixed form: equal
        abs_equal = os.path.join(os.path.expanduser("~"), "Documents", "Spotlight")
        errors = errs({**BASE, "vaultPath": abs_equal})
        self.assertTrue(any(e["field"] == "vault_path" for e in errors))
        # sibling with a shared prefix is NOT nested
        self.assertEqual(errs({**BASE, "vaultPath": "~/Documents/Spotlight-Vault"}), [])
        # validation must not mutate the as-entered strings
        d = srv.normalize({**BASE, "vaultPath": "~/Intelligence"})
        srv.validate_choices(d)
        self.assertEqual(d["installPath"], "~/Documents/Spotlight")
        self.assertEqual(d["vaultPath"], "~/Intelligence")

    def test_tolaria_presence(self):
        orig_platform, orig_tolaria = srv.detect_platform, srv.tolaria_installed
        try:
            srv.detect_platform = lambda: "mac"
            srv.tolaria_installed = lambda: False
            errors = errs({**BASE, "vaultApp": "tolaria"})
            self.assertTrue(any(e["field"] == "vault_app" for e in errors))
            # obsidian never checks for Tolaria
            srv.tolaria_installed = lambda: (_ for _ in ()).throw(AssertionError("checked"))
            self.assertEqual(errs(BASE), [])
            # tolaria present on mac passes
            srv.tolaria_installed = lambda: True
            self.assertEqual(errs({**BASE, "vaultApp": "tolaria"}), [])
            # on linux: warn only, never error
            srv.detect_platform = lambda: "linux"
            srv.tolaria_installed = lambda: False
            payload = {**BASE, "vaultApp": "tolaria"}
            self.assertEqual(errs(payload), [])
            self.assertTrue(any("Tolaria" in w for w in warns(payload)))
        finally:
            srv.detect_platform, srv.tolaria_installed = orig_platform, orig_tolaria

    def test_junkipedia_without_key_warns(self):
        payload = {**BASE, "junkipediaKey": ""}
        self.assertEqual(errs(payload), [])
        self.assertTrue(any("Junkipedia" in w for w in warns(payload)))

    def test_normalize_defaults(self):
        d = srv.normalize({})
        # enum choices may default…
        self.assertEqual(d["mode"], "cloud")
        self.assertEqual(d["cloudRuntime"], "claude")
        self.assertEqual(d["localModel"], "gemma12b")
        self.assertEqual(d["vaultApp"], "obsidian")
        # …but emptied paths must NOT be silently defaulted
        self.assertEqual(d["installPath"], "")
        self.assertEqual(d["vaultPath"], "")
        # bogus enum values fall back instead of passing through
        self.assertEqual(srv.normalize({"mode": "bogus"})["mode"], "cloud")

    def test_key_validation_routing(self):
        d = srv.normalize(OPENCODE)
        orig = srv.probe
        try:
            srv.probe = lambda url, headers: "rejected"
            errors, warnings = srv.validate_keys(d)
            # strict fields error; junkipedia is lenient (warn only)
            self.assertEqual({e["field"] for e in errors},
                             {"firecrawl_key", "nav_key", "cloud_key"})
            self.assertTrue(any("JUNKIPEDIA" in w for w in warnings))
            srv.probe = lambda url, headers: "unreachable"
            errors, warnings = srv.validate_keys(d)
            self.assertEqual(errors, [])
            self.assertTrue(warnings)
            srv.probe = lambda url, headers: "ok"
            self.assertEqual(srv.validate_keys(d), ([], []))
            # --skip-key-validation bypasses every probe
            srv.probe = lambda url, headers: (_ for _ in ()).throw(AssertionError("probed"))
            self.assertEqual(srv.validate_keys(d, skip=True), ([], []))
        finally:
            srv.probe = orig

    def test_cloud_key_probe_targets_provider(self):
        seen = []
        orig = srv.probe
        try:
            srv.probe = lambda url, headers: seen.append(url) or "ok"
            for provider, fragment in [("openrouter", "openrouter.ai/api/v1/key"),
                                       ("fireworks", "api.fireworks.ai"),
                                       ("together", "api.together.xyz")]:
                seen.clear()
                srv.validate_keys(srv.normalize({**OPENCODE, "opencodeProvider": provider}))
                self.assertTrue(any(fragment in u for u in seen), provider)
            # claude payload probes no cloud endpoint at all
            seen.clear()
            srv.validate_keys(srv.normalize(BASE))
            self.assertFalse(any("openrouter" in u or "fireworks" in u or "together" in u for u in seen))
        finally:
            srv.probe = orig

    def test_unpaywall_email_format_check(self):
        orig = srv.probe
        try:
            srv.probe = lambda url, headers: "ok"
            errors, _ = srv.validate_keys(srv.normalize({**BASE, "unpaywallEmail": "not-an-email"}))
            self.assertTrue(any(e["field"] == "unpaywall_email" for e in errors))
            errors, _ = srv.validate_keys(srv.normalize(BASE))
            self.assertEqual(errors, [])
        finally:
            srv.probe = orig

    def test_setup_config_cloud(self):
        cfg = srv.build_setup_config(srv.normalize(BASE))
        for needle in ["SPOTLIGHT_MODE=cloud", "SPOTLIGHT_RUNTIME=claude",
                       "SPOTLIGHT_OPENCODE_INTERFACE=cli",
                       "SPOTLIGHT_CLOUD_KEY_VAR=''", "SPOTLIGHT_OPENCODE_PROVIDER=''",
                       "SPOTLIGHT_LOCAL_SERVER=''", "SPOTLIGHT_LOCAL_MODEL=''",
                       "SPOTLIGHT_AGENT=''", "SPOTLIGHT_MODEL_REPO=''",
                       "SPOTLIGHT_DIR_INPUT='~/Documents/Spotlight'",
                       "SPOTLIGHT_VAULT_INPUT='~/Intelligence'",
                       "SPOTLIGHT_VAULT_APP=obsidian",
                       "SPOTLIGHT_INT_DEVBROWSER=true", "SPOTLIGHT_INT_JUNKIPEDIA=true",
                       "SPOTLIGHT_INT_UNPAYWALL=true", "UNPAYWALL_EMAIL=reporter@example.com",
                       "SPOTLIGHT_INT_RLM=true", "SPOTLIGHT_RLM_MODE=local_gemma4_e4b",
                       "SPOTLIGHT_RLM_MODEL=''", "SPOTLIGHT_RLM_REPO=''",
                       "SPOTLIGHT_RLM_GGUF=''"]:
            self.assertIn(needle, cfg)
        self.assertNotIn("OSINT_NAVIGATOR", cfg)
        for secret in SECRETS:
            self.assertNotIn(secret, cfg)

    def test_setup_config_opencode(self):
        cfg = srv.build_setup_config(srv.normalize(OPENCODE))
        self.assertIn("SPOTLIGHT_RUNTIME=opencode", cfg)
        self.assertIn("SPOTLIGHT_OPENCODE_PROVIDER=openrouter", cfg)
        self.assertIn("SPOTLIGHT_CLOUD_KEY_VAR=OPENROUTER_API_KEY", cfg)
        self.assertNotIn("sk-or-cloud-secret", cfg)
        fw = srv.build_setup_config(srv.normalize({**OPENCODE, "opencodeProvider": "fireworks"}))
        self.assertIn("SPOTLIGHT_CLOUD_KEY_VAR=FIREWORKS_API_KEY", fw)
        tg = srv.build_setup_config(srv.normalize({**OPENCODE, "opencodeProvider": "together"}))
        self.assertIn("SPOTLIGHT_CLOUD_KEY_VAR=TOGETHER_API_KEY", tg)
        # cloud key var derives ONLY when mode=cloud AND runtime=opencode
        local = srv.build_setup_config(srv.normalize({**OPENCODE, "mode": "local"}))
        self.assertIn("SPOTLIGHT_CLOUD_KEY_VAR=''", local)

    def test_setup_config_local(self):
        cfg = srv.build_setup_config(srv.normalize(LOCAL))
        for needle in ["SPOTLIGHT_MODE=local", "SPOTLIGHT_RUNTIME=local",
                       "SPOTLIGHT_AGENT=flue", "SPOTLIGHT_LOCAL_SERVER=llamacpp",
                       "SPOTLIGHT_LOCAL_MODEL=gemma12b", "SPOTLIGHT_MODEL_TIER=12b",
                       "SPOTLIGHT_MODEL_REPO=tomvaillant/gemma4-12b-spotlight-orchestrator-v5-GGUF",
                       "SPOTLIGHT_RLM_MODE=local_llamacpp_e4b", "SPOTLIGHT_RLM_MODEL=rlm-e4b"]:
            self.assertIn(needle, cfg)
        quality = srv.build_setup_config(srv.normalize({**LOCAL, "localModel": "gemma31b"}))
        self.assertIn("SPOTLIGHT_AGENT=flue", quality)
        self.assertIn("SPOTLIGHT_MODEL_TIER=31b", quality)
        self.assertIn("SPOTLIGHT_MODEL_REPO=unsloth/gemma-4-31B-it-GGUF", quality)

    def test_setup_config_rlm_variants(self):
        off = srv.build_setup_config(srv.normalize({**BASE, "intRlm": False}))
        self.assertIn("SPOTLIGHT_RLM_MODE=off", off)
        self.assertIn("SPOTLIGHT_RLM_MODEL=''", off)
        self.assertIn("SPOTLIGHT_RLM_REPO=''", off)
        self.assertIn("SPOTLIGHT_RLM_GGUF=''", off)
        lite = srv.build_setup_config(srv.normalize({**BASE, "rlmMode": "lite"}))
        self.assertIn("SPOTLIGHT_RLM_MODE=lite", lite)
        self.assertIn("SPOTLIGHT_RLM_MODEL=''", lite)

    def test_env_lines(self):
        env = srv.build_env_lines(srv.normalize(OPENCODE))
        self.assertIn("FIRECRAWL_API_KEY=fc-secret-test", env)
        self.assertIn("OSINT_NAV_API_KEY=on-secret-test", env)
        self.assertIn("SPOTLIGHT_CLOUD_KEY=sk-or-cloud-secret", env)
        self.assertIn("JUNKIPEDIA_API_KEY=jk-secret-test", env)
        self.assertNotIn("OSINT_NAVIGATOR", env)
        # subscription runtimes stage no cloud key
        claude = srv.build_env_lines(srv.normalize(BASE))
        self.assertNotIn("SPOTLIGHT_CLOUD_KEY", claude)
        # secrets with shell metacharacters are shlex-quoted
        spicy = srv.build_env_lines(srv.normalize({**BASE, "firecrawlKey": "fc-it's $weird"}))
        self.assertIn("""FIRECRAWL_API_KEY='fc-it'"'"'s $weird'""", spicy)

    def test_getting_started(self):
        guide = srv.build_getting_started(srv.normalize(BASE))
        for needle in ["~/Documents/Spotlight", "~/Intelligence", "spotlight doctor",
                       "spotlight update", "Claude Code", "dev-browser", "Junkipedia",
                       "Unpaywall", "Command Line Interface"]:
            self.assertIn(needle, guide)
        for secret in SECRETS:
            self.assertNotIn(secret, guide)
        local = srv.build_getting_started(srv.normalize({**LOCAL, "vaultApp": "obsidian"}))
        self.assertIn("Gemma 4 12B", local)
        self.assertIn("llama.cpp", local)
        tolaria = srv.build_getting_started(srv.normalize({**BASE, "vaultApp": "tolaria"}))
        self.assertIn("Tolaria", tolaria)
        self.assertNotIn("Command Line Interface: ON", tolaria)

    def test_artifacts(self):
        tmp = tempfile.mkdtemp()
        os.chmod(tmp, 0o755)  # pre-existing dir gets forced back to 0700
        srv.write_artifacts(srv.normalize(OPENCODE), tmp)
        self.assertEqual(stat.S_IMODE(os.stat(tmp).st_mode), 0o700)
        expected = {".env": 0o600, "setup-config.env": 0o600, "getting-started.html": 0o644}
        self.assertEqual(set(os.listdir(tmp)), set(expected))  # no temps, no skill registry
        for name, mode in expected.items():
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(tmp, name)).st_mode), mode, name)
        for name in ("setup-config.env", "getting-started.html"):
            content = read(os.path.join(tmp, name))
            for secret in SECRETS:
                self.assertNotIn(secret, content, name)
        env = read(os.path.join(tmp, ".env"))
        self.assertIn("SPOTLIGHT_CLOUD_KEY=sk-or-cloud-secret", env)

    def test_artifacts_atomic_failure_leaves_nothing(self):
        tmp = tempfile.mkdtemp()
        orig = os.replace
        def boom(src, dst):
            raise OSError("disk full")
        os.replace = boom
        try:
            with self.assertRaises(OSError):
                srv.write_artifacts(srv.normalize(BASE), tmp)
        finally:
            os.replace = orig
        self.assertEqual(os.listdir(tmp), [])  # no artifacts, no temp files

    def test_configurator_version_in_page(self):
        page = read(os.path.join(ROOT, "install", "configure.html"))
        self.assertIn(f'<meta name="configurator-version" content="{srv.CONFIGURATOR_VERSION}">', page)


class ServerChecks(unittest.TestCase):
    PORT = 0

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "install", "setup_server.py"),
             "--profile-dir", cls.tmp, "--repo-dir", ROOT,
             "--port", "0", "--no-browser", "--skip-key-validation"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # GET is token-gated, so the token comes from the printed URL.
        cls.token = None
        deadline = time.time() + 10
        while time.time() < deadline:
            line = cls.proc.stdout.readline()
            if not line:
                break
            m = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=([A-Za-z0-9_-]+)", line)
            if m:
                cls.PORT = int(m.group(1))
                cls.token = m.group(2)
                break
        if not cls.token:
            remainder = cls.proc.stdout.read()
            raise RuntimeError(f"configurator never printed its token URL: {remainder}")
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                cls.page = urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.PORT}/?t={cls.token}", timeout=2).read().decode()
                break
            except urllib.error.HTTPError:
                raise
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.proc.stdout.close()

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_flow(self):
        # 1. page is the configurator with token + platform injected
        self.assertIn("Configure your", self.page)
        self.assertNotIn("__SETUP_TOKEN__", self.page)
        self.assertNotIn("__PLATFORM__", self.page)
        self.assertIn("configurator-version", self.page)
        for needle in ["firecrawl_key", "nav_key", "install_path", "vault_path",
                       "int_devbrowser", "rlm_mode"]:
            self.assertIn(needle, self.page)

        # 2. GET without (or with a bad) token is rejected
        for url in (f"http://127.0.0.1:{self.PORT}/",
                    f"http://127.0.0.1:{self.PORT}/?t=wrong"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(ctx.exception.code, 403, url)

        # 3. bad token is rejected on both POST endpoints
        for path in ("/submit", "/pick-folder"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post(path, {**BASE, "token": "wrong", "field": "vault_path"})
            self.assertEqual(ctx.exception.code, 403, path)

        # 4. structural validation blocks with field errors
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/submit", {**BASE, "token": self.token, "firecrawlKey": ""})
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertTrue(any(e["field"] == "firecrawl_key" for e in body["errors"]))

        # 5. good submit writes the three artifacts and exits 0
        resp = self.post("/submit", {**BASE, "token": self.token})
        self.assertTrue(resp["ok"])
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp).st_mode), 0o700)
        expected = {".env": 0o600, "setup-config.env": 0o600, "getting-started.html": 0o644}
        self.assertEqual(set(os.listdir(self.tmp)), set(expected))
        for name, mode in expected.items():
            path = os.path.join(self.tmp, name)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode, name)
        guide = read(os.path.join(self.tmp, "getting-started.html"))
        cfg = read(os.path.join(self.tmp, "setup-config.env"))
        for secret in SECRETS:
            self.assertNotIn(secret, guide)
            self.assertNotIn(secret, cfg)
        self.assertIn("SPOTLIGHT_DIR_INPUT='~/Documents/Spotlight'", cfg)
        self.assertIn("SPOTLIGHT_VAULT_INPUT='~/Intelligence'", cfg)


if __name__ == "__main__":
    unittest.main(verbosity=1)
