// Generator check: extracts the buildScript() function + RUNTIMES + helpers
// from setup.html and synthesizes install scripts for all runtime+provider
// configs, verifying each produces syntactically valid bash.
//
// Run via: node tests/setup-generator-check.js
//
// Exits 0 on all pass, 1 if any config fails bash -n.

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'setup.html'), 'utf8');

const runtimesMatch = html.match(/const RUNTIMES = \{[\s\S]*?\n  \};/);
const providersMatch = html.match(/const OPENCODE_PROVIDERS = \{[\s\S]*?\n  \};/);
const shellEscapeMatch = html.match(/function shellEscape[\s\S]*?\n  \}/);
const buildScriptMatch = html.match(/function buildScript\(cfg\)[\s\S]*?return lines\.join\('\\n'\) \+ '\\n';\n  \}/);

if (!runtimesMatch || !providersMatch || !shellEscapeMatch || !buildScriptMatch) {
  console.error('✗ Could not extract required JS blocks from setup.html');
  console.error('  runtimes:', !!runtimesMatch);
  console.error('  providers:', !!providersMatch);
  console.error('  shellEscape:', !!shellEscapeMatch);
  console.error('  buildScript:', !!buildScriptMatch);
  process.exit(1);
}

eval(runtimesMatch[0] + '\n' + providersMatch[0] + '\n' + shellEscapeMatch[0] + '\n' + buildScriptMatch[0]);

const baseCfg = {
  firecrawl_key: 'fc-test',
  contact_email: 'test@example.com',
  nav_key: 'on-test',
  vault_path: '~/Intelligence',
  monitoring_acled: false,
  int_browseruse: false,
  int_junkipedia: false,
};

const configs = [
  { label: 'local', mode: 'local', runtime: 'local', opencode_provider: null, cloud_key: '', cloud_key_var: '', model_repo: 'tomvaillant/qwen3-8b-journalist' },
  { label: 'claude', mode: 'cloud', runtime: 'claude', opencode_provider: null, cloud_key: 'sk-ant-x', cloud_key_var: 'ANTHROPIC_API_KEY' },
  { label: 'gemini', mode: 'cloud', runtime: 'gemini', opencode_provider: null, cloud_key: 'AIzaX', cloud_key_var: 'GEMINI_API_KEY' },
  { label: 'codex', mode: 'cloud', runtime: 'codex', opencode_provider: null, cloud_key: 'sk-x', cloud_key_var: 'OPENAI_API_KEY' },
  { label: 'opencode/openrouter', mode: 'cloud', runtime: 'opencode', opencode_provider: 'openrouter', cloud_key: 'sk-or-v1-x', cloud_key_var: 'OPENROUTER_API_KEY' },
  { label: 'opencode/fireworks', mode: 'cloud', runtime: 'opencode', opencode_provider: 'fireworks', cloud_key: 'fw-x', cloud_key_var: 'FIREWORKS_API_KEY' },
  { label: 'opencode/together', mode: 'cloud', runtime: 'opencode', opencode_provider: 'together', cloud_key: 'tg-x', cloud_key_var: 'TOGETHER_API_KEY' },
];

let pass = 0, fail = 0;
for (const c of configs) {
  const cfg = { ...baseCfg, ...c };
  const script = buildScript(cfg);
  const tmp = `/tmp/setup-gen-${c.label.replace(/\//g, '-')}.sh`;
  fs.writeFileSync(tmp, script);
  try {
    execSync(`bash -n "${tmp}"`, { stdio: 'pipe' });
    console.log(`✓ ${c.label.padEnd(24)} ${script.length} bytes`);
    pass++;
  } catch (e) {
    console.log(`✗ ${c.label.padEnd(24)} ${(e.stderr || e).toString().split('\n')[0]}`);
    fail++;
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
