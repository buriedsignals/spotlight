#!/usr/bin/env python3
"""Contract tests for the local configurator (install/setup_server.py).
Covers: page serving + token injection, the GET token gate, CSRF token
rejection on both POST endpoints, structural validation (paths, cloud key,
vault/install nesting), the retired Obsidian/Tolaria vault-app contract
(stale clients are ignored end to end), key-validation routing with a
stubbed prober, artifact writing (content, modes, atomicity, secret
hygiene), the getting-started guide, and the installer env naming
contract. Live key validation is skipped (--skip-key-validation) — its
routing is unit-tested directly against validate_keys.
"""

import importlib.util
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
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "install"))
import setup_server as srv  # noqa: E402
import engine_bridge as engine  # noqa: E402

BASE = {
    "mode": "cloud", "cloudRuntime": "claude-code", "opencodeProvider": "openrouter",
    "cloudKey": "", "localAgent": "flue", "localModel": "gemma12b",
    "firecrawlKey": "fc-secret-test", "navigatorConnected": True,
    # Retired F1 field: a stale client may still post it; the server must
    # drop it instead of honoring it (test_vault_app_submission_is_ignored).
    "vaultApp": "obsidian",
    "installPath": "~/Documents/Spotlight", "vaultPath": "~/Intelligence",
    "intDevBrowser": True,
    "intJunkipedia": True, "junkipediaKey": "jk-secret-test",
    "intUnpaywall": True, "unpaywallEmail": "reporter@example.com",
    "intRlm": True, "rlmMode": "local_gemma4_e4b",
}
OPENCODE = {**BASE, "cloudRuntime": "opencode", "cloudKey": "sk-or-cloud-secret"}
PI = {**BASE, "cloudRuntime": "pi"}
LOCAL = {**BASE, "mode": "local", "intJunkipedia": False, "junkipediaKey": ""}
SECRETS = ["fc-secret-test", "sk-or-cloud-secret", "jk-secret-test"]


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
    def test_engine_bridge_keeps_secret_values_off_argv(self):
        bridge = engine.EngineBridge.__new__(engine.EngineBridge)
        bridge.product = "spotlight"
        bridge.binary = "/fake/bsig"
        replies = iter([
            {"event": "result", "data": {"normalized": {"required_secret_ids": ["OPENROUTER_API_KEY"]}}},
            {"event": "result", "data": {"keys": []}},
            {"event": "result", "data": {}},
            {"event": "result", "data": {"plan_path": "/tmp/plan.json"}},
        ])
        calls = []
        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs.get("input")))
            event = next(replies)
            return subprocess.CompletedProcess(argv, 0, (json.dumps(event) + "\n").encode(), b"")
        with mock.patch.object(engine.subprocess, "run", side_effect=fake_run):
            result = bridge.submit({"schema_version": "bsig-configure/v1"}, {"OPENROUTER_API_KEY": "newsroom-secret"})
        self.assertTrue(result["ok"])
        self.assertTrue(any(argv[-3:] == ["keys", "set", "OPENROUTER_API_KEY"] and body == b"newsroom-secret" for argv, body in calls))
        self.assertFalse(any("newsroom-secret" in " ".join(argv) for argv, _ in calls))

    def test_navigator_runtime_mapping_and_flow_routing(self):
        expected = {
            "claude": "claude-code",
            "codex": "codex-cli",
            "pi": "pi",
            "opencode": "opencode",
            "local": "pi-flue",
        }
        for choice, runtime in expected.items():
            self.assertEqual(srv.navigator_runtime_id(choice), runtime)
        with self.assertRaises(srv.NavigatorBridgeError):
            srv.navigator_runtime_id("")
        with self.assertRaises(srv.NavigatorBridgeError):
            srv.navigator_runtime_id("unknown")

        instances = []

        class FakeBridge:
            def __init__(self, contract_path, runtime):
                self.contract_path = contract_path
                self.runtime = runtime
                instances.append(self)

            def existing_status(self):
                return {"status": "connected", "runtime": self.runtime}

            def start(self, _email):
                return {"status": "pending", "flow_id": "flow-" + self.runtime}

            def poll(self, _flow_id):
                return {"status": "connected", "runtime": self.runtime}

            def cancel(self, _flow_id):
                return {"status": "cancelled", "runtime": self.runtime}

        with mock.patch.object(srv, "NavigatorInstallerBridge", FakeBridge):
            router = srv.NavigatorBridgeRouter("matrix.json")
            self.assertEqual(router.status("codex")["runtime"], "codex-cli")
            started = router.start("local", "reporter@example.com")
            self.assertEqual(started["flow_id"], "flow-pi-flue")
            self.assertEqual(router.poll(started["flow_id"])["runtime"], "pi-flue")
            self.assertEqual(router.poll(started["flow_id"])["status"], "expired")
            cancelled = router.start("opencode", "reporter@example.com")
            self.assertEqual(router.cancel(cancelled["flow_id"])["runtime"], "opencode")
        self.assertEqual([bridge.runtime for bridge in instances], ["codex-cli", "pi-flue", "opencode"])

    def test_navigator_reconnect_uses_saved_or_explicit_runtime(self):
        script = os.path.join(ROOT, "scripts", "navigator-connect")
        loader = SourceFileLoader("spotlight_navigator_connect", script)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as root:
            module.ROOT = Path(root)
            Path(root, ".spotlight-config.json").write_text(
                json.dumps({"runtime": "codex"}), encoding="utf-8"
            )
            self.assertEqual(module.selected_runtime(None), "codex-cli")
            self.assertEqual(module.selected_runtime("opencode"), "opencode")
            with mock.patch.dict(os.environ, {"SPOTLIGHT_RUNTIME": "pi"}):
                self.assertEqual(module.selected_runtime(None), "pi")
            Path(root, ".spotlight-config.json").unlink()
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(srv.NavigatorBridgeError):
                    module.selected_runtime(None)

    def test_structural_validation(self):
        self.assertEqual(errs(BASE), [])
        cases = [
            ({"vaultPath": "   "}, "vault_path"),
            ({"installPath": ""}, "install_path"),
        ]
        for overrides, field in cases:
            errors = errs({**BASE, **overrides})
            self.assertTrue(any(e["field"] == field for e in errors), field)
        # Firecrawl is an optional fallback; the sovereign stack needs no key.
        self.assertEqual(errs({**BASE, "firecrawlKey": ""}), [])

    def test_cloud_key_requirement(self):
        # opencode without a cloud key errors; with one it passes
        self.assertTrue(any(e["field"] == "cloud_key" for e in errs({**OPENCODE, "cloudKey": ""})))
        self.assertEqual(errs(OPENCODE), [])
        # Subscription runtimes never need a cloud key.
        self.assertEqual(errs(BASE), [])
        self.assertEqual(errs(PI), [])
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

    def test_vault_app_submission_is_ignored(self):
        # F1 retirement: OpenKnowledge is the sole knowledge runtime, so the
        # vault-app question is gone. A stale client posting vaultApp has it
        # dropped — never defaulted in, never validated, never warned about.
        payload = {**BASE, "vaultApp": "tolaria"}
        self.assertNotIn("vaultApp", srv.normalize(payload))
        self.assertNotIn("vaultApp", srv.derived(srv.normalize(payload)))
        self.assertEqual(errs(payload), [])
        self.assertFalse(any(e["field"] == "vault_app" for e in errs({**BASE, "vaultApp": "obsidian"})))
        self.assertFalse(any("Tolaria" in w or "Obsidian" in w for w in warns(payload)))

    def test_junkipedia_without_key_warns(self):
        payload = {**BASE, "junkipediaKey": ""}
        self.assertEqual(errs(payload), [])
        self.assertTrue(any("Junkipedia" in w for w in warns(payload)))

    def test_normalize_defaults(self):
        d = srv.normalize({})
        # enum choices may default…
        self.assertEqual(d["mode"], "cloud")
        self.assertEqual(d["cloudRuntime"], "claude-code")
        # AC3/PRD F5: default follows the catalog recommendation (26b is
        # 'default'; 12b is 'advanced') — see task notes.md deviation record.
        self.assertEqual(d["localModel"], "gemma26b")
        # The vault-app choice is retired; normalize never reintroduces it.
        self.assertNotIn("vaultApp", d)
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
                             {"firecrawl_key", "cloud_key"})
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
            seen = []
            srv.probe = lambda url, headers: seen.append(url) or "ok"
            srv.validate_keys(srv.normalize({**BASE, "firecrawlKey": ""}))
            self.assertFalse(any("firecrawl" in url for url in seen))
        finally:
            srv.probe = orig

    def test_cloud_key_probe_targets_provider(self):
        seen = []
        orig = srv.probe
        try:
            srv.probe = lambda url, headers: seen.append(url) or "ok"
            for provider, fragment in [("openrouter", "openrouter.ai/api/v1/key"),
                                       ("fireworks", "api.fireworks.ai")]:
                seen.clear()
                srv.validate_keys(srv.normalize({**OPENCODE, "opencodeProvider": provider}))
                self.assertTrue(any(fragment in u for u in seen), provider)
            # claude payload probes no cloud endpoint at all
            seen.clear()
            srv.validate_keys(srv.normalize(BASE))
            self.assertFalse(any("openrouter" in u or "fireworks" in u for u in seen))
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
                       "SPOTLIGHT_INT_DEVBROWSER=true", "SPOTLIGHT_INT_JUNKIPEDIA=true",
                       "SPOTLIGHT_INT_UNPAYWALL=true", "UNPAYWALL_EMAIL=reporter@example.com",
                       "SPOTLIGHT_INT_RLM=true", "SPOTLIGHT_RLM_MODE=local_gemma4_e4b",
                       "SPOTLIGHT_NAVIGATOR_CONNECTION=connected",
                       "SPOTLIGHT_RLM_MODEL=''", "SPOTLIGHT_RLM_REPO=''",
                       "SPOTLIGHT_RLM_GGUF=''"]:
            self.assertIn(needle, cfg)
        self.assertNotIn("OSINT_NAVIGATOR", cfg)
        self.assertNotIn("SPOTLIGHT_VAULT_APP", cfg)
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
        self.assertNotIn("OSINT_NAV_API_KEY", env)
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
                       "Unpaywall",
                       "SearXNG + Crawl4AI (sovereign web research)",
                       "Firecrawl (optional hosted fallback)",
                       "Navigator (OSINT tool discovery)"]:
            self.assertIn(needle, guide)
        # F1 retirement: the guide no longer coaches an Obsidian CLI toggle
        # and never names either retired vault app.
        for stale in ["Obsidian", "Tolaria", "Command Line Interface"]:
            self.assertNotIn(stale, guide)
        for secret in SECRETS:
            self.assertNotIn(secret, guide)
        pi = srv.build_getting_started(srv.normalize(PI))
        self.assertIn("Pi", pi)
        self.assertIn("/login", pi)
        self.assertNotIn("Flue", pi)
        local = srv.build_getting_started(srv.normalize(LOCAL))
        self.assertIn("Gemma 4 12B", local)
        self.assertIn("llama.cpp", local)
        locked = srv.build_getting_started(srv.normalize({**BASE, "firecrawlKey": "", "navigatorConnected": False}))
        self.assertIn("Navigator skill (locked; no credential or CLI)", locked)
        self.assertNotIn("Firecrawl (optional hosted fallback)", locked)

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


class RuntimeTokenChecks(unittest.TestCase):
    """F3: one token set — the Engine resolver vocabulary — mapped once in
    setup_server.py; every page-offered runtime resolves into it."""

    RESOLVER_VOCABULARY = {
        "claude-code", "claude-desktop", "codex-cli", "codex-desktop",
        "opencode", "pi",
    }

    def test_runtime_map_covers_resolver_vocabulary_exactly(self):
        self.assertEqual(set(srv.RUNTIMES), self.RESOLVER_VOCABULARY)

    def test_every_page_offered_runtime_exists_in_resolver_vocabulary(self):
        source = read(os.path.join(ROOT, "install", "configure.html"))
        offered = set(re.findall(r'name="cloud_runtime" value="([^"]+)"', source))
        self.assertTrue(offered, "no cloud_runtime options found in configure.html")
        self.assertLessEqual(offered, set(srv.RUNTIMES))

    def test_probe_coverage_is_exactly_claude_code_and_codex_cli(self):
        probed = {token for token, spec in srv.RUNTIMES.items() if spec.get("bin")}
        self.assertEqual(probed, {"claude-code", "codex-cli"})

    def test_normalize_keeps_resolver_tokens_as_posted(self):
        for token in ("claude-code", "codex-cli", "pi", "opencode"):
            self.assertEqual(srv.normalize({"cloudRuntime": token})["cloudRuntime"], token)

    def test_derived_maps_resolver_tokens_to_installer_tokens(self):
        # The installer-facing SPOTLIGHT_RUNTIME token must keep working:
        # setup-config.env still carries claude/codex/pi/opencode.
        expected = {"claude-code": "claude", "codex-cli": "codex",
                    "pi": "pi", "opencode": "opencode"}
        for resolver, installer in expected.items():
            derived = srv.derived(srv.normalize({"cloudRuntime": resolver}))
            self.assertEqual(derived["runtime"], installer, resolver)


class RuntimeDetectionChecks(unittest.TestCase):
    """F2: the probe seam mirrors engine desktop/src/main/remote-mcp-runtime.ts
    (~20 lines: which <bin> + --version), is stubbed in tests, and a probe
    failure degrades to the manual list — it never blocks install."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write_fake_binary(self, name, stdout="", exit_code=0):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/usr/bin/env python3\n"
                         "import sys\n"
                         f"print({stdout!r})\n"
                         f"sys.exit({exit_code})\n")
        os.chmod(path, 0o755)
        return path

    def test_default_probe_captures_last_version_token_of_successful_binary(self):
        binary = self.write_fake_binary("claude", stdout="claude 2.1.0")
        with mock.patch.object(srv.shutil, "which", return_value=binary):
            self.assertEqual(srv.default_runtime_probe("claude-code"),
                             {"installed": True, "version": "2.1.0"})

    def test_default_probe_omits_version_when_version_output_is_empty(self):
        binary = self.write_fake_binary("claude", stdout="")
        with mock.patch.object(srv.shutil, "which", return_value=binary):
            self.assertEqual(srv.default_runtime_probe("claude-code"),
                             {"installed": True})

    def test_default_probe_treats_missing_binary_as_not_installed(self):
        with mock.patch.object(srv.shutil, "which", return_value=None):
            self.assertEqual(srv.default_runtime_probe("codex-cli"),
                             {"installed": False})

    def test_default_probe_treats_uncovered_runtime_as_not_installed(self):
        self.assertEqual(srv.default_runtime_probe("pi"), {"installed": False})

    def test_default_probe_reports_error_for_failing_version_command(self):
        binary = self.write_fake_binary("codex", stdout="", exit_code=3)
        with mock.patch.object(srv.shutil, "which", return_value=binary):
            result = srv.default_runtime_probe("codex-cli")
        self.assertFalse(result["installed"])
        self.assertTrue(result.get("error"), "a failing probe must carry its error")

    def test_detect_runtimes_lists_installed_in_probe_order_with_versions(self):
        versions = {"claude-code": "2.1.0", "codex-cli": "0.5.0"}

        def stub(runtime_id):
            return {"installed": True, "version": versions[runtime_id]}

        observation = srv.detect_runtimes(probe=stub)
        self.assertEqual([row["id"] for row in observation["installed"]],
                         ["claude-code", "codex-cli"])
        self.assertEqual(observation["installed"][0]["version"], "2.1.0")

    def test_detect_runtimes_returns_empty_when_nothing_is_detected(self):
        observation = srv.detect_runtimes(
            probe=lambda runtime_id: {"installed": False})
        self.assertEqual(observation["installed"], [])

    def test_detect_runtimes_collects_probe_errors_without_raising(self):
        def stub(runtime_id):
            return {"installed": False, "error": f"{runtime_id} exploded"}

        observation = srv.detect_runtimes(probe=stub)
        self.assertEqual(observation["installed"], [])
        self.assertEqual(sorted(row["id"] for row in observation["failures"]),
                         ["claude-code", "codex-cli"])

    def test_detect_runtimes_survives_a_raising_probe(self):
        def stub(runtime_id):
            raise RuntimeError("probe kaboom")

        observation = srv.detect_runtimes(probe=stub)
        self.assertEqual(observation["installed"], [])
        self.assertTrue(all(row["reason"] for row in observation["failures"]))

    def test_detection_json_is_baked_over_the_page_placeholder(self):
        source = read(os.path.join(ROOT, "install", "configure.html"))
        self.assertIn(srv.RUNTIME_DETECTION_PLACEHOLDER, source)
        page = "<html>" + srv.RUNTIME_DETECTION_PLACEHOLDER + "</html>"
        baked = srv.apply_runtime_detection(page, {
            "installed": [{"id": "claude-code", "version": "2.1.0"}],
            "failures": [],
        })
        self.assertNotIn(srv.RUNTIME_DETECTION_PLACEHOLDER, baked)
        self.assertIn('"claude-code"', baked)
        self.assertIn("2.1.0", baked)


class ServerChecks(unittest.TestCase):
    PORT = 0

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fake_bsig = os.path.join(cls.tmp, "bsig")
        with open(cls.fake_bsig, "w", encoding="utf-8") as handle:
            handle.write("""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[:2] == ["configure", "describe"]:
    data = {"descriptor": {"schema_version": "bsig-configure-descriptor/v1", "product": "spotlight", "fields": []}}
elif args[:2] == ["configure", "validate"]:
    json.load(sys.stdin); data = {"normalized": {"required_secret_ids": []}}
elif args[:2] == ["keys", "list"]:
    data = {"keys": []}
elif args[:2] == ["configure", "plan"]:
    json.load(sys.stdin); data = {"plan_path": "/tmp/spotlight-install.json"}
else:
    sys.exit(4)
print(json.dumps({"event": "result", "data": data}))
""")
        os.chmod(cls.fake_bsig, 0o755)
        env = {**os.environ, "BSIG_BIN": cls.fake_bsig}
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "install", "setup_server.py"),
             "--profile-dir", cls.tmp, "--repo-dir", ROOT,
             "--port", "0", "--no-browser", "--skip-key-validation"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
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
        for needle in ["firecrawl_key", "navigatorConnect", "navigatorSkip", "install_path", "vault_path",
                       "int_devbrowser", "rlm_mode"]:
            self.assertIn(needle, self.page)
        # F1 retirement: the served page carries no vault-app choice and
        # names OpenKnowledge as the sole knowledge runtime.
        for stale in ["vault_app", "applyVaultApp", "obsidianNote", "tolariaNote",
                      "Obsidian", "Tolaria"]:
            self.assertNotIn(stale, self.page)
        self.assertIn("OpenKnowledge", self.page)

        # 2. GET without (or with a bad) token is rejected
        for url in (f"http://127.0.0.1:{self.PORT}/",
                    f"http://127.0.0.1:{self.PORT}/?t=wrong"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=5)
            self.assertEqual(ctx.exception.code, 403, url)

        # 3. bad token is rejected on both active POST endpoints
        for path in ("/engine-submit", "/pick-folder"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post(path, {**BASE, "token": "wrong", "field": "vault_path"})
            self.assertEqual(ctx.exception.code, 403, path)

        # 4. the legacy writer endpoint is retired
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/submit", {**BASE, "token": self.token, "navKey": ""})
        self.assertEqual(ctx.exception.code, 410)

        # 5. Engine submit writes only the sealed-plan marker and exits 0
        resp = self.post("/engine-submit", {
            "token": self.token,
            "request": {"schema_version": "bsig-configure/v1"},
            "secrets": {},
        })
        self.assertTrue(resp["ok"])
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp).st_mode), 0o700)
        marker = os.path.join(self.tmp, "engine-plan.ready")
        self.assertEqual(stat.S_IMODE(os.stat(marker).st_mode), 0o600)
        with open(marker, encoding="utf-8") as handle:
            marker_data = json.load(handle)
        self.assertEqual(marker_data["plan"]["plan_path"], "/tmp/spotlight-install.json")
        self.assertEqual(marker_data["engine_binary"], self.fake_bsig)
        self.assertEqual(set(os.listdir(self.tmp)), {"bsig", "engine-plan.ready"})

    def test_runtime_detection_page_contract(self):
        # F2/F3: detection payload is baked server-side; the static form
        # speaks resolver tokens; uncovered options say so honestly.
        self.assertNotIn("__RUNTIME_DETECTION__", self.page)
        offered = set(re.findall(r'name="cloud_runtime" value="([^"]+)"', self.page))
        self.assertEqual(offered, {"claude-code", "pi", "codex-cli", "opencode"})

        def card_head(token):
            for segment in self.page.split('<label class="item radio-card">')[1:]:
                head = segment.split("</label>")[0]
                if f'value="{token}"' in head:
                    return head
            return ""

        for token in ("pi", "opencode"):
            self.assertIn("not auto-detected", card_head(token), token)
        for token in ("claude-code", "codex-cli"):
            head = card_head(token)
            self.assertTrue(head, f"missing radio card for {token}")
            self.assertNotIn("not auto-detected", head, token)

        # Detected runtimes collapse §02 to a confirmation row with a change link.
        self.assertIn('id="runtimeConfirm"', self.page)
        self.assertIn('id="runtimeChange"', self.page)
        templates = [line for line in self.page.splitlines() if "using it" in line]
        self.assertEqual(len(templates), 1, "exactly one confirmation-row template")
        row_template = templates[0]
        self.assertIn("Detected", row_template)
        self.assertIn("installed", row_template)
        # Copy discipline: installation is proven, entitlement is never claimed.
        self.assertNotIn("subscription", row_template)
        self.assertNotIn("covered", row_template)


class PublicWebsiteChecks(unittest.TestCase):
    def test_skip_completes_without_engine_or_navigator_credential(self):
        with tempfile.TemporaryDirectory() as profile:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "install", "setup_server.py"),
                 "--profile-dir", profile, "--repo-dir", ROOT, "--port", "0",
                 "--no-browser", "--skip-key-validation", "--legacy-only"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                line = proc.stdout.readline()
                match = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=([A-Za-z0-9_-]+)", line)
                self.assertIsNotNone(match, line)
                port, token = int(match.group(1)), match.group(2)

                bad_origin = urllib.request.Request(
                    f"http://127.0.0.1:{port}/navigator/status",
                    data=json.dumps({"token": token}).encode(),
                    headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(bad_origin, timeout=5)
                self.assertEqual(ctx.exception.code, 403)

                payload = {**BASE, "token": token, "navigatorChoice": "skip",
                           "navigatorConnected": False}
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/submit",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json",
                             "Origin": f"http://127.0.0.1:{port}"},
                )
                response = json.loads(urllib.request.urlopen(request, timeout=15).read())
                self.assertTrue(response["ok"])
                self.assertEqual(proc.wait(timeout=10), 0)
                config = read(os.path.join(profile, "setup-config.env"))
                self.assertIn("SPOTLIGHT_NAVIGATOR_CONNECTION=locked", config)
                self.assertNotIn("OSINT_NAV_API_KEY", read(os.path.join(profile, ".env")))
                self.assertFalse(os.path.exists(os.path.join(profile, "engine-plan.ready")))
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
                proc.stdout.close()


class ModelCatalogChecks(unittest.TestCase):
    """F5: local-model cards render from one pinned table in setup_server.py
    whose numbers equal the signed engine catalog (engine/catalog/catalog.json,
    release_sequence 22). The public static install path cannot read the
    catalog, so the expected values are hardcoded here from the signed source.
    Catalog artifacts[] carry no byte counts, so download figures are the
    grounded Content-Length approximations of the pinned artifact URLs
    (response etag == catalog sha256, measured 2026-08-23).
    """

    CATALOG = {
        "gemma12b": {"catalog_id": "spotlight-gemma4-12b", "min_ram_gb": 24,
                     "artifact_id": "spotlight-gemma4-12b-q4km",
                     "download_gb": 7, "recommendation": "advanced"},
        "gemma26b": {"catalog_id": "gemma4-26b-a4b", "min_ram_gb": 32,
                     "artifact_id": "gemma4-26b-a4b-q4km",
                     "download_gb": 17, "recommendation": "default"},
        "gemma31b": {"catalog_id": "gemma4-31b", "min_ram_gb": 48,
                     "artifact_id": "gemma4-31b-q4km",
                     "download_gb": 18, "recommendation": "advanced"},
    }

    def baked_page(self):
        return srv.apply_model_cards(read(os.path.join(ROOT, "install", "configure.html")))

    def card(self, page, token):
        match = re.search(
            rf'<label class="item radio-card" id="modelCard-{re.escape(token)}">.*?</label>',
            page, re.S)
        self.assertIsNotNone(match, f"model card for {token} not rendered")
        return match.group(0)

    def test_pinned_model_table_matches_signed_catalog(self):
        self.assertEqual(set(srv.MODELS), set(self.CATALOG))
        for token, want in self.CATALOG.items():
            row = srv.MODELS[token]
            for field, value in want.items():
                self.assertEqual(row[field], value, f"{token}.{field}")

    def test_default_local_model_is_the_catalog_recommendation_default(self):
        defaults = [token for token, row in srv.MODELS.items()
                    if row["recommendation"] == "default"]
        self.assertEqual(defaults, ["gemma26b"])
        self.assertEqual(srv.DEFAULT_MODEL, "gemma26b")
        # The server-side fallback for payloads without a model follows the pin.
        self.assertEqual(srv.normalize({"mode": "local"})["localModel"], srv.DEFAULT_MODEL)

    def test_cards_render_from_the_pinned_table(self):
        raw = read(os.path.join(ROOT, "install", "configure.html"))
        self.assertEqual(
            re.findall(r'<input type="radio" name="local_model" value="([^"]+)"', raw),
            [], "cards must be server-rendered from MODELS, not hardcoded")
        page = self.baked_page()
        offered = re.findall(r'<input type="radio" name="local_model" value="([^"]+)"', page)
        self.assertEqual(sorted(offered), sorted(self.CATALOG))
        checked = re.findall(
            r'<input type="radio" name="local_model" value="([^"]+)"[^>]*checked', page)
        self.assertEqual(checked, [srv.DEFAULT_MODEL])
        for token, row in self.CATALOG.items():
            card = self.card(page, token)
            self.assertIn(f">{row['min_ram_gb']} GB</span>", card, token)
            self.assertIn(f"~{row['download_gb']} GB download", card, token)

    def test_no_stale_ram_claims_remain_on_cards_or_splash(self):
        page = self.baked_page()
        for token in self.CATALOG:
            self.assertNotIn("16 GB", self.card(page, token), token)
        # Splash Local meta states the true floor (min min_ram_gb = 24).
        self.assertNotIn("16 GB+ RAM", page)
        self.assertIn("24 GB+ RAM", page)


if __name__ == "__main__":
    unittest.main(verbosity=1)
