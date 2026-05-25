// Generator check: extracts buildExportBlock / buildOneLiner / buildCommandScript
// from setup.html and verifies the install one-liner + .command wrapper shape
// for every supported runtime/agent combo. Also validates the agent-manifest
// path (separate "let an agent install for you" zip).
//
// Run via: node tests/setup-generator-check.js
//
// Exits 0 on all pass, 1 if any config fails.

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "setup.html"), "utf8");

// --- Extract pure functions from setup.html. We rely on the file's
// consistent 2-space indentation: each top-level helper is `  function …`
// and ends with `\n  }`. This avoids needing a real JS parser for what is
// otherwise a tricky string/regex/template-literal-aware brace match. ---
function grabFn(name) {
  const re = new RegExp(`  function ${name}\\b[\\s\\S]*?\\n  \\}`);
  const m = html.match(re);
  return m ? m[0] : null;
}

const fnSources = {
  shellEscape:        grabFn("shellEscape"),
  buildExportBlock:   grabFn("buildExportBlock"),
  utf8Base64:         grabFn("utf8Base64"),
  buildOneLiner:      grabFn("buildOneLiner"),
  buildCommandScript: grabFn("buildCommandScript"),
  buildAgentManifest: grabFn("buildAgentManifest"),
  buildAgentPrompt:   grabFn("buildAgentPrompt"),
  providerEnvVars:    grabFn("providerEnvVars"),
  envValues:          grabFn("envValues"),
};
const installerUrlMatch = html.match(/const INSTALLER_URL = '[^']+';/);
const runtimesMatch     = html.match(/const RUNTIMES = \{[\s\S]*?\n  \};/);
const providersMatch    = html.match(/const OPENCODE_PROVIDERS = \{[\s\S]*?\n  \};/);

for (const [k, v] of Object.entries(fnSources)) {
  if (!v) { console.error(`✗ Could not extract function ${k} from setup.html`); process.exit(1); }
}
for (const [k, v] of Object.entries({ INSTALLER_URL: installerUrlMatch, RUNTIMES: runtimesMatch, OPENCODE_PROVIDERS: providersMatch })) {
  if (!v) { console.error(`✗ Could not extract const ${k} from setup.html`); process.exit(1); }
}

// --- Eval the extracted pieces into this scope. The function declarations
// here become live bindings on `globalThis` so we can call them below. ---
eval(
  runtimesMatch[0] + "\n" +
  providersMatch[0] + "\n" +
  installerUrlMatch[0] + "\n" +
  Object.values(fnSources).join("\n"),
);

const baseCfg = {
  firecrawl_key: "fc-test",
  nav_key: "on-test",
  install_path: "~/Documents/Spotlight",
  vault_app: "obsidian",
  vault_path: "~/Intelligence",
  int_browseruse: false,
  int_junkipedia: false,
  junkipedia_key: "",
  int_unpaywall: false,
  unpaywall_email: "",
};

const configs = [
  { label: "local/llamacpp/opencode", mode: "local", runtime: "local",
    local_model: "qwen27b", local_server: "llamacpp", agent: "opencode",
    opencode_interface: "cli", opencode_provider: null, cloud_key: "", cloud_key_var: "",
    model_repo: "tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF" },
  { label: "local/llamacpp/pi", mode: "local", runtime: "local",
    local_model: "qwen27b", local_server: "llamacpp", agent: "pi",
    opencode_interface: "cli", opencode_provider: null, cloud_key: "", cloud_key_var: "",
    model_repo: "tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF" },
  { label: "local/ollama/opencode", mode: "local", runtime: "local",
    local_model: "gemma", local_server: "ollama", agent: "opencode",
    opencode_interface: "cli", opencode_provider: null, cloud_key: "", cloud_key_var: "",
    model_repo: "unsloth/gemma-4-26B-A4B-it-GGUF" },
  { label: "local/ollama/pi", mode: "local", runtime: "local",
    local_model: "gemma", local_server: "ollama", agent: "pi",
    opencode_interface: "cli", opencode_provider: null, cloud_key: "", cloud_key_var: "",
    model_repo: "unsloth/gemma-4-26B-A4B-it-GGUF" },
  { label: "cloud/claude", mode: "cloud", runtime: "claude",
    agent: null, opencode_provider: null, cloud_key: "", cloud_key_var: "" },
  { label: "cloud/gemini", mode: "cloud", runtime: "gemini",
    agent: null, opencode_provider: null, cloud_key: "", cloud_key_var: "" },
  { label: "cloud/codex", mode: "cloud", runtime: "codex",
    agent: null, opencode_provider: null, cloud_key: "", cloud_key_var: "" },
  { label: "cloud/opencode-openrouter", mode: "cloud", runtime: "opencode",
    agent: null, opencode_provider: "openrouter", cloud_key: "sk-or-v1-x", cloud_key_var: "OPENROUTER_API_KEY" },
  { label: "cloud/opencode-fireworks", mode: "cloud", runtime: "opencode",
    agent: null, opencode_provider: "fireworks", cloud_key: "fw-x", cloud_key_var: "FIREWORKS_API_KEY" },
  { label: "cloud/opencode-together", mode: "cloud", runtime: "opencode",
    agent: null, opencode_provider: "together", cloud_key: "tg-x", cloud_key_var: "TOGETHER_API_KEY" },
];

let pass = 0, fail = 0;

function decodeBlock(oneLiner) {
  const m = oneLiner.match(/SPOTLIGHT_CONFIG='([^']+)'/);
  if (!m) return null;
  return Buffer.from(m[1], "base64").toString("utf8");
}

function hasExport(block, key, value) {
  // Matches `export KEY='<value>'` exactly (value is the raw form input).
  const escaped = "'" + value.replace(/'/g, "'\\''") + "'";
  return block.includes(`export ${key}=${escaped}`);
}

for (const c of configs) {
  const cfg = { ...baseCfg, ...c };
  const oneLiner = buildOneLiner(cfg);
  const cmdScript = buildCommandScript(cfg);
  let ok = true;

  // One-liner shape
  if (!oneLiner.startsWith("curl -fsSL https://raw.githubusercontent.com/buriedsignals/spotlight/main/install-spotlight.sh")) {
    console.log(`✗ ${c.label.padEnd(28)} one-liner missing curl URL`);
    ok = false;
  }
  if (!oneLiner.includes("SPOTLIGHT_CONFIG='")) {
    console.log(`✗ ${c.label.padEnd(28)} one-liner missing SPOTLIGHT_CONFIG env`);
    ok = false;
  }
  if (!oneLiner.endsWith(" bash")) {
    console.log(`✗ ${c.label.padEnd(28)} one-liner does not end with 'bash'`);
    ok = false;
  }

  // .command wrapper shape (3-line bash that fetches install-spotlight.sh)
  const cmdLines = cmdScript.trim().split("\n");
  if (cmdLines.length !== 3 || cmdLines[0] !== "#!/usr/bin/env bash") {
    console.log(`✗ ${c.label.padEnd(28)} .command wrapper not 3 lines with shebang`);
    ok = false;
  }
  if (!cmdScript.includes("SPOTLIGHT_CONFIG='")) {
    console.log(`✗ ${c.label.padEnd(28)} .command wrapper missing config export`);
    ok = false;
  }

  // Decode + verify the export block
  const block = decodeBlock(oneLiner);
  if (!block) { console.log(`✗ ${c.label.padEnd(28)} cannot decode base64 from one-liner`); ok = false; }
  else {
    if (!hasExport(block, "SPOTLIGHT_MODE", cfg.mode)) {
      console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_MODE=${cfg.mode}`);
      ok = false;
    }
    if (!hasExport(block, "SPOTLIGHT_RUNTIME", cfg.runtime)) {
      console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_RUNTIME=${cfg.runtime}`);
      ok = false;
    }
    if (!hasExport(block, "FIRECRAWL_API_KEY", cfg.firecrawl_key)) {
      console.log(`✗ ${c.label.padEnd(28)} block missing FIRECRAWL_API_KEY`);
      ok = false;
    }
    if (!hasExport(block, "OSINT_NAV_API_KEY", cfg.nav_key)) {
      console.log(`✗ ${c.label.padEnd(28)} block missing OSINT_NAV_API_KEY`);
      ok = false;
    }
    if (cfg.mode === "local") {
      if (!hasExport(block, "SPOTLIGHT_LOCAL_SERVER", cfg.local_server)) {
        console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_LOCAL_SERVER=${cfg.local_server}`);
        ok = false;
      }
      if (!hasExport(block, "SPOTLIGHT_AGENT", cfg.agent)) {
        console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_AGENT=${cfg.agent}`);
        ok = false;
      }
      if (!hasExport(block, "SPOTLIGHT_MODEL_REPO", cfg.model_repo)) {
        console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_MODEL_REPO`);
        ok = false;
      }
    }
    if (cfg.cloud_key_var) {
      if (!hasExport(block, "SPOTLIGHT_CLOUD_KEY_VAR", cfg.cloud_key_var)) {
        console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_CLOUD_KEY_VAR=${cfg.cloud_key_var}`);
        ok = false;
      }
      if (!hasExport(block, "SPOTLIGHT_CLOUD_KEY", cfg.cloud_key)) {
        console.log(`✗ ${c.label.padEnd(28)} block missing SPOTLIGHT_CLOUD_KEY`);
        ok = false;
      }
    }
  }

  if (ok) {
    console.log(`✓ ${c.label.padEnd(28)} one-liner=${oneLiner.length}B  block=${block ? block.length : "?"}B`);
    pass++;
  } else {
    fail++;
  }
}

// --- Injection-resistance check ---
const injCfg = {
  ...baseCfg, mode: "cloud", runtime: "claude", agent: null,
  opencode_provider: null, cloud_key: "", cloud_key_var: "",
  firecrawl_key: "fc-with-quote'and$dollar`backtick;rm-rf",
};
const injOneLiner = buildOneLiner(injCfg);
const injBlock = decodeBlock(injOneLiner);
if (injBlock && hasExport(injBlock, "FIRECRAWL_API_KEY", injCfg.firecrawl_key)) {
  console.log("✓ injection-resistance         single-quote and shell metas survived round-trip safely");
  pass++;
} else {
  console.log("✗ injection-resistance         malicious key did NOT survive base64+eval intact");
  console.log("  expected:", injCfg.firecrawl_key);
  console.log("  block excerpt:", injBlock ? injBlock.split("\n").find(l => l.includes("FIRECRAWL")) : "(no block)");
  fail++;
}

// --- UTF-8 round-trip check ---
const utf8Cfg = { ...baseCfg, mode: "cloud", runtime: "claude", agent: null,
  opencode_provider: null, cloud_key: "", cloud_key_var: "",
  vault_path: "/Users/x/Vaults/Schräge·Münzen" };
const utf8Block = decodeBlock(buildOneLiner(utf8Cfg));
if (utf8Block && utf8Block.includes("Schräge·Münzen")) {
  console.log("✓ utf-8 round-trip             vault path preserved through TextEncoder+base64");
  pass++;
} else {
  console.log("✗ utf-8 round-trip             unicode mangled");
  fail++;
}

// --- Agent manifest path (unchanged from previous test) ---
const manifestCfg = {
  ...baseCfg, mode: "cloud", runtime: "opencode",
  opencode_provider: "fireworks", cloud_key: "fw-secret-test",
  cloud_key_var: "FIREWORKS_API_KEY",
  int_junkipedia: true, junkipedia_key: "junk-secret-test",
};
const manifest = buildAgentManifest(manifestCfg);
const prompt = buildAgentPrompt(manifest);
if (
  manifest.env.values.FIRECRAWL_API_KEY !== "fc-test" ||
  manifest.env.values.OSINT_NAV_API_KEY !== "on-test" ||
  manifest.env.values.FIREWORKS_API_KEY !== "fw-secret-test" ||
  manifest.env.values.JUNKIPEDIA_API_KEY !== "junk-secret-test"
) {
  console.log("✗ agent manifest missing local secret values");
  fail++;
} else if (prompt.includes("fw-secret-test") || prompt.includes("junk-secret-test")) {
  console.log("✗ agent prompt printed secret values");
  fail++;
} else if (!prompt.includes("Handle the setup for the user") || !prompt.includes("env.values")) {
  console.log("✗ agent prompt does not instruct agent setup from manifest values");
  fail++;
} else {
  console.log("✓ agent manifest/prompt        values included, prompt redacted");
  pass++;
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
