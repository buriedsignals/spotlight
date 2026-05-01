// Generator check: extracts the buildScript() function + RUNTIMES + helpers
// from setup.html and synthesizes install scripts for all runtime+provider
// configs, verifying each produces syntactically valid bash.
//
// Run via: node tests/setup-generator-check.js
//
// Exits 0 on all pass, 1 if any config fails bash -n.

const fs = require("fs");
const { execSync } = require("child_process");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "setup.html"), "utf8");

const runtimesMatch = html.match(/const RUNTIMES = \{[\s\S]*?\n  \};/);
const providersMatch = html.match(
  /const OPENCODE_PROVIDERS = \{[\s\S]*?\n  \};/,
);
const shellEscapeMatch = html.match(/function shellEscape[\s\S]*?\n  \}/);
const buildScriptMatch = html.match(
  /function buildScript\(cfg\)[\s\S]*?return lines\.join\('\\n'\) \+ '\\n';\n  \}/,
);
const providerEnvVarsMatch = html.match(/function providerEnvVars[\s\S]*?\n  \}/);
const envValuesMatch = html.match(/function envValues[\s\S]*?\n  \}/);
const buildAgentManifestMatch = html.match(
  /function buildAgentManifest\(cfg\)[\s\S]*?\n  \}/,
);
const buildAgentPromptMatch = html.match(
  /function buildAgentPrompt\(manifest\)[\s\S]*?\n  \}/,
);

if (
  !runtimesMatch ||
  !providersMatch ||
  !shellEscapeMatch ||
  !buildScriptMatch ||
  !providerEnvVarsMatch ||
  !envValuesMatch ||
  !buildAgentManifestMatch ||
  !buildAgentPromptMatch
) {
  console.error("✗ Could not extract required JS blocks from setup.html");
  console.error("  runtimes:", !!runtimesMatch);
  console.error("  providers:", !!providersMatch);
  console.error("  shellEscape:", !!shellEscapeMatch);
  console.error("  buildScript:", !!buildScriptMatch);
  console.error("  providerEnvVars:", !!providerEnvVarsMatch);
  console.error("  envValues:", !!envValuesMatch);
  console.error("  buildAgentManifest:", !!buildAgentManifestMatch);
  console.error("  buildAgentPrompt:", !!buildAgentPromptMatch);
  process.exit(1);
}

eval(
  runtimesMatch[0] +
    "\n" +
    providersMatch[0] +
    "\n" +
    shellEscapeMatch[0] +
    "\n" +
    buildScriptMatch[0] +
    "\n" +
    providerEnvVarsMatch[0] +
    "\n" +
    envValuesMatch[0] +
    "\n" +
    buildAgentManifestMatch[0] +
    "\n" +
    buildAgentPromptMatch[0],
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
  {
    label: "local/ollama",
    mode: "local",
    runtime: "local",
    local_server: "ollama",
    opencode_provider: null,
    cloud_key: "",
    cloud_key_var: "",
    model_repo: "unsloth/gemma-4-26B-A4B-it-GGUF",
  },
  {
    label: "local/llamacpp",
    mode: "local",
    runtime: "local",
    local_server: "llamacpp",
    opencode_interface: "cli",
    opencode_provider: null,
    cloud_key: "",
    cloud_key_var: "",
    model_repo: "HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Aggressive",
  },
  {
    label: "claude",
    mode: "cloud",
    runtime: "claude",
    opencode_provider: null,
    cloud_key: "",
    cloud_key_var: "",
  },
  {
    label: "gemini",
    mode: "cloud",
    runtime: "gemini",
    opencode_provider: null,
    cloud_key: "",
    cloud_key_var: "",
  },
  {
    label: "codex",
    mode: "cloud",
    runtime: "codex",
    opencode_provider: null,
    cloud_key: "",
    cloud_key_var: "",
  },
  {
    label: "opencode/openrouter",
    mode: "cloud",
    runtime: "opencode",
    opencode_provider: "openrouter",
    cloud_key: "sk-or-v1-x",
    cloud_key_var: "OPENROUTER_API_KEY",
  },
  {
    label: "opencode/fireworks",
    mode: "cloud",
    runtime: "opencode",
    opencode_provider: "fireworks",
    cloud_key: "fw-x",
    cloud_key_var: "FIREWORKS_API_KEY",
  },
  {
    label: "opencode/together",
    mode: "cloud",
    runtime: "opencode",
    opencode_provider: "together",
    cloud_key: "tg-x",
    cloud_key_var: "TOGETHER_API_KEY",
  },
];

let pass = 0,
  fail = 0;

function assertFragment(script, label, needle) {
  if (!script.includes(needle)) {
    console.log(`✗ ${label.padEnd(24)} missing ${needle}`);
    return false;
  }
  return true;
}

for (const c of configs) {
  const cfg = { ...baseCfg, ...c };
  const script = buildScript(cfg);
  const required = [
    "git fetch origin main",
    "git merge --ff-only origin/main",
    "spotlight-update",
    "spotlight-doctor",
    "qmd collection add \"$SPOTLIGHT_VAULT_PATH\" --name spotlight",
    "write_env_var FIRECRAWL_API_KEY",
    "write_env_var OSINT_NAV_API_KEY",
    "SPOTLIGHT-BEGIN",
    "awk '",
    'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"',
  ];
  let ok = true;
  for (const needle of required) ok = assertFragment(script, c.label, needle) && ok;
  if (script.includes("git pull --no-rebase --autostash origin main")) {
    console.log(`✗ ${c.label.padEnd(24)} contains unsafe git pull --autostash`);
    ok = false;
  }
  if (c.runtime === "local" && !script.includes("spotlight-local")) {
    console.log(`✗ ${c.label.padEnd(24)} local runtime does not install spotlight-local`);
    ok = false;
  }
  if (c.runtime === "opencode" && !script.includes("opencode loads Spotlight skills")) {
    console.log(`✗ ${c.label.padEnd(24)} opencode skills not linked`);
    ok = false;
  }
  if (!ok) {
    fail++;
    continue;
  }
  const tmp = `/tmp/setup-gen-${c.label.replace(/\//g, "-")}.sh`;
  fs.writeFileSync(tmp, script);
  try {
    execSync(`bash -n "${tmp}"`, { stdio: "pipe" });
    console.log(`✓ ${c.label.padEnd(24)} ${script.length} bytes`);
    pass++;
  } catch (e) {
    console.log(
      `✗ ${c.label.padEnd(24)} ${(e.stderr || e).toString().split("\n")[0]}`,
    );
    fail++;
  }
}

const manifestCfg = {
  ...baseCfg,
  mode: "cloud",
  runtime: "opencode",
  opencode_provider: "fireworks",
  cloud_key: "fw-secret-test",
  cloud_key_var: "FIREWORKS_API_KEY",
  int_junkipedia: true,
  junkipedia_key: "junk-secret-test",
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
  console.log("✓ agent manifest/prompt      values included, prompt redacted");
  pass++;
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
