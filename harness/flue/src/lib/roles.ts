import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// harness/flue/src/lib -> repo root is four up; agents/ + role .md files live there.
// Resolved from the source file (absolute), so it holds regardless of `flue run` cwd,
// in dev and in the sparse-fetched install (which ships harness/flue + agents together).
const AGENTS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../agents');

// Harness root = the spotlight repo/install root (parent of `agents/`, where the
// sparse-fetch also lays down `harness/flue`, `schemas`, and `integrations`). The
// `integrations/` Python package + `cases/` + `.agents/skills` all resolve from here.
// Injected into the adapter so `-m integrations.*` calls (PYTHONPATH) and case files
// (absolute CASE_DIR) are cwd-independent — kills the "No module named integrations"
// seam failure AND the `cases/X/cases/X` nesting when a subagent's cwd is the case dir.
export const HARNESS_ROOT = resolve(AGENTS_DIR, '..');

// The Python interpreter for the `integrations.*` seams (crawl4ai / RLM).
// MUST be the Spotlight venv's python — NOT bare `python3` (which resolves to the
// system/Homebrew python that lacks crawl4ai → the seam fails and the model falls back
// to raw `curl`). The U15 installer provisions this venv (uv: crawl4ai +
// `crawl4ai-setup` browser runtime + poppler) and MAY point SPOTLIGHT_PYTHON elsewhere
// (uv-managed / conda). Default = `<root>/.venv/bin/python` (Windows: `.venv/Scripts/python.exe`).
const SPOTLIGHT_PYTHON = process.env.SPOTLIGHT_PYTHON ?? resolve(HARNESS_ROOT, '.venv/bin/python');

/**
 * Load a role definition's body (frontmatter stripped) as agent instructions.
 * Keeps the repo's `agents/<name>.md` the single, runtime-agnostic source of truth —
 * the same files the frontier skill-based path spawns.
 */
export function roleBody(name: string): string {
	const raw = readFileSync(resolve(AGENTS_DIR, `${name}.md`), 'utf8');
	return raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim();
}

// Tier-conditional raw-source affordance: the 12b must never bulk-load raw pages
// (context rot is its #1 failure mode); the more capable local tiers (26b/31b) may
// selectively read a `.raw` when the distilled leads look thin — a longer leash on
// the same harness, not a different harness.
const MODEL_TIER = process.env.SPOTLIGHT_MODEL_TIER ?? '12b';
const RAW_AFFORDANCE =
	MODEL_TIER === '12b'
		? 'Do NOT bulk-load `.raw` files. For fact-checking, you MUST inspect only the exact source passage: grep for a distinctive locator, then read a bounded line range (or resolve a JSON Pointer) and record that range/pointer in `source_ref`.'
		: 'Prefer the distilled leads. You MAY selectively read a specific `.raw` file when the leads look thin or you need exact wording for a citation — read only the relevant section (grep/head it first), never bulk-load raw pages.';

/**
 * The Flue runtime adapter for AGENTS.md's abstract verbs. The shared skills call
 * verbs (execute-shell, fetch, invoke-skill, spawn-agent…); this preamble tells the
 * model how each one is performed with Flue's native tools, so the same skills work
 * here without rewriting them. Prepend to every agent's instructions.
 */
export const FLUE_VERB_ADAPTER = `## Runtime adapter (Flue) — how the skills' abstract verbs map to your tools

You are running in the Flue harness. Your **harness root** is \`${HARNESS_ROOT}\` — the \`integrations/\` Python package, \`cases/\`, \`schemas/\`, and \`.agents/skills\` all live there. Two hard path rules (breaking either silently fails the run):
- **Run every \`integrations.*\` seam as \`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.…\`.** Both parts are required: \`PYTHONPATH=${HARNESS_ROOT}\` (the package imports ONLY from the harness root — else "No module named integrations") and \`${SPOTLIGHT_PYTHON}\` (the venv interpreter that has crawl4ai; bare \`python3\` is the system python and lacks it, so the seam fails and you'd wrongly fall back to \`curl\`).
- **Use the ABSOLUTE case dir for every case artifact.** For project slug \`<project>\`, **\`CASE_DIR = ${HARNESS_ROOT}/cases/<project>\`**. Wherever a skill references \`{CASE_DIR}/…\`, substitute this absolute path (including \`mkdir\`). NEVER use \`cases/<project>/…\` or a bare \`research/…\` path from a subagent — its cwd may already be inside the case dir, so relative paths nest as \`cases/X/cases/X/…\` and the orchestrator can't find them.

The shared skills use abstract verbs; execute them as:
- **execute-shell(cmd)** → your \`bash\` tool.
- **fetch(url, output_path)** → \`bash\`: \`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.scraping <url> --out <output_path> --rlm\` (Crawl4AI, no API key; on a bot-block escalates to Firecrawl only if FIRECRAWL_API_KEY is set, else surfaces the block; add \`--tor\` to anonymize via Tor; give an ABSOLUTE \`output_path\` under \`${HARNESS_ROOT}/cases/<project>/research/\`). **Do NOT hand-roll \`curl\`** — the seam returns cleaned markdown; \`curl\` dumps raw HTML that balloons context. For a local PDF add \`--pdf\`. **fetch retrieves static / server-rendered content ONLY.** If it returns empty or "no information available" on a page that needs interaction — company registries (Zefix, OpenCorporates search), JS-rendered portals, forms, login-gated pages — do NOT settle for weaker secondary sources: escalate to **browse**.
- **browse(url) — browser automation for interactive pages** → activate the \`integrations\` skill and drive **dev-browser** (\`bash\`: \`dev-browser --headless --timeout 90 run <script.js>\`; Playwright-style \`page.goto/fill/click/screenshot\`; recipe in \`web-archiving/references/capture-dev-browser.md\`). Use it to fill form fields, click through multi-step flows, render JS, and capture screenshots/downloads — then record \`acquisition_method:"dev_browser"\` in the evidence bundle. If \`dev-browser\` is not on PATH, fall back to the Crawl4AI seam.
- **search(query)** → \`bash\`: \`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.search "<query>"\` (SearXNG, self-hosted, no API key; paginates the long tail so obscure sources stay reachable; add \`--limit N\`, \`--union\` for exhaustive recall, or \`--categories news --time-range month\` for recency). For OSINT *tool* discovery: local / open tier → \`bash\`: \`python3 scripts/osint-tools.py find "<query>" [--category X] [--limit 8]\` (local SQLite+FTS index, 12,500 tools, offline, no entitlement); subscription tier (Navigator entitled) → \`bash\`: \`navigator tools find "<query>" --json\`.
- **query-vault(query)** → OpenKnowledge \`ok_search\` against the configured workspace. Return normalized paths, backend, readiness, and ranked results. If OpenKnowledge reports unready or unavailable, use exact read-only Markdown search and label every result \`markdown_fallback\`; never treat an unready empty result as "not found".
- **read-file / write-file / edit-file / list-files / grep-files** → your \`read\` / \`write\` / \`edit\` / \`glob\` / \`grep\` tools.
- **invoke-skill(id)** → the skill of that name is already loaded from \`.agents/skills\`; follow its instructions.
- **spawn-agent(id, prompt)** → delegate with your \`task\` tool to the named subagent (\`investigator\` or \`fact-checker\`).

## Context hygiene — RLM lead-distillation is AUTOMATIC in \`fetch\` on this local harness

Raw scraped sources balloon your context and cause rot on small local models — the single biggest failure mode of the local tier (a raw page is ~40k tokens; its distilled leads are ~600 — a ~99% saving). **You do NOT run the RLM yourself.** \`fetch(..., --rlm)\` (default on this harness) distills every page automatically via the local e4b: \`<output_path>\` receives the **compact leads** (what you read); the raw source is kept at \`<output_path>.raw\` for citations/provenance only. Requires the RLM endpoint env (\`SPOTLIGHT_RLM_OPENAI_BASE_URL\`) — set by the local launcher.

**So: just \`fetch\` and read \`<output_path>\` — it is already compact leads.** ${RAW_AFFORDANCE} Leads are unverified pointers, never facts. (The RLM proposal/gate in the \`spotlight\` skill applies only to cloud/frontier setups, where distillation is opt-in.)

Keeps working context compact across cycles. If the RLM errors or is unavailable, note it and fall back to reading research files directly.

Persist evidence and findings to files as the skills direct. **Whether you pause for the user at a gate is role-specific — follow your role instructions below** (the orchestrator gates to the user; delegated workers never do).`;
