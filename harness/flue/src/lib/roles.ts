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
const HARNESS_ROOT = resolve(AGENTS_DIR, '..');

// The Python interpreter for the `integrations.*` seams (crawl4ai / scrapling / RLM).
// MUST be the Spotlight venv's python — NOT bare `python3` (which resolves to the
// system/Homebrew python that lacks crawl4ai → the seam fails and the model falls back
// to raw `curl`). The U15 installer provisions this venv (uv: crawl4ai + scrapling +
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

/**
 * The Flue runtime adapter for AGENTS.md's abstract verbs. The shared skills call
 * verbs (execute-shell, fetch, invoke-skill, spawn-agent…); this preamble tells the
 * model how each one is performed with Flue's native tools, so the same skills work
 * here without rewriting them. Prepend to every agent's instructions.
 */
export const FLUE_VERB_ADAPTER = `## Runtime adapter (Flue) — how the skills' abstract verbs map to your tools

You are running in the Flue harness. Your **harness root** is \`${HARNESS_ROOT}\` — the \`integrations/\` Python package, \`cases/\`, \`schemas/\`, and \`.agents/skills\` all live there. Two hard path rules (breaking either silently fails the run):
- **Run every \`integrations.*\` seam as \`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.…\`.** Both parts are required: \`PYTHONPATH=${HARNESS_ROOT}\` (the package imports ONLY from the harness root — else "No module named integrations") and \`${SPOTLIGHT_PYTHON}\` (the venv interpreter that has crawl4ai/scrapling; bare \`python3\` is the system python and lacks them, so the seam fails and you'd wrongly fall back to \`curl\`).
- **Use the ABSOLUTE case dir for every case artifact.** For project slug \`<project>\`, **\`CASE_DIR = ${HARNESS_ROOT}/cases/<project>\`**. Wherever a skill references \`{CASE_DIR}/…\`, substitute this absolute path (including \`mkdir\`). NEVER use \`cases/<project>/…\` or a bare \`research/…\` path from a subagent — its cwd may already be inside the case dir, so relative paths nest as \`cases/X/cases/X/…\` and the orchestrator can't find them.

The shared skills use abstract verbs; execute them as:
- **execute-shell(cmd)** → your \`bash\` tool.
- **fetch(url, output_path)** → \`bash\`: \`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.scraping <url> --out <output_path>\` (Crawl4AI, no API key; auto-escalates to Scrapling on bot-block; give an ABSOLUTE \`output_path\` under \`${HARNESS_ROOT}/cases/<project>/research/\`). **Do NOT hand-roll \`curl\`** — the seam returns cleaned markdown; \`curl\` dumps raw HTML that balloons context. For a local PDF add \`--pdf\`. **fetch retrieves static / server-rendered content ONLY.** If it returns empty or "no information available" on a page that needs interaction — company registries (Zefix, OpenCorporates search), JS-rendered portals, forms, login-gated pages — do NOT settle for weaker secondary sources: escalate to **browse**.
- **browse(url) — browser automation for interactive pages** → activate the \`integrations\` skill and drive **dev-browser** (\`bash\`: \`dev-browser --headless --timeout 90 run <script.js>\`; Playwright-style \`page.goto/fill/click/screenshot\`; recipe in \`web-archiving/references/capture-dev-browser.md\`). Use it to fill form fields, click through multi-step flows, render JS, and capture screenshots/downloads — then record \`acquisition_method:"dev_browser"\` in the evidence bundle. If \`dev-browser\` is not on PATH, fall back to the Crawl4AI seam.
- **search(query)** → \`bash\`: \`firecrawl search "<query>"\`. For OSINT *tool* discovery use \`bash\`: \`navigator tools find "<query>" --json\`.
- **query-vault(query)** → \`bash\`: \`BUN_INSTALL="" qmd query "<query>"\`.
- **read-file / write-file / edit-file / list-files / grep-files** → your \`read\` / \`write\` / \`edit\` / \`glob\` / \`grep\` tools.
- **invoke-skill(id)** → the skill of that name is already loaded from \`.agents/skills\`; follow its instructions.
- **spawn-agent(id, prompt)** → delegate with your \`task\` tool to the named subagent (\`investigator\` or \`fact-checker\`).

## Context hygiene — RLM lead-distillation is ON BY DEFAULT in this local harness

Raw scraped sources balloon your context and cause rot on small local models — the single biggest failure mode of the local tier. Unlike frontier setups (where the \`spotlight\` skill's RLM proposal is opt-in and user-approved), **here the RLM is default-on and you run it autonomously — do NOT ask the user for approval** (that gate applies only to cloud/frontier configs).

**After each research cycle, before reasoning over the evidence:**
1. Write \`${HARNESS_ROOT}/cases/<project>/data/rlm-request.json\` = \`{project, run_id, mode:"local_gemma4_e4b", corpus_paths:[...]}\` — use the **defaults** (prefilter+hybrid on): the deterministic pass extracts structured entities and e4b supplements semantic/contradiction leads. This is the proven config (benchmark: entity_recall 1.0, schema-valid). **\`corpus_paths\` are relative to the CASE DIR (the project root \`${HARNESS_ROOT}/cases/<project>\`), NOT to the request file's folder** — e.g. \`"research/ef-about.md"\`. NO \`..\`, NO absolute: \`run_rlm\` resolves each path against the case dir and rejects \`..\` as an unsafe segment. (The request JSON itself may sit in \`data/\`; \`corpus_paths\` still resolve from the case-dir root.) (For prose-name-heavy corpora where the deterministic pass under-recalls, a stronger extractor can be configured via \`SPOTLIGHT_RLM_OPENAI_BASE_URL\`/\`_MODEL\` — off by default to stay sovereign.)
2. \`bash\`: **\`PYTHONPATH=${HARNESS_ROOT} ${SPOTLIGHT_PYTHON} -m integrations.rlm.run_rlm ${HARNESS_ROOT}/cases/<project>/data/rlm-request.json\`** (both the \`PYTHONPATH\` prefix and the \`${SPOTLIGHT_PYTHON}\` venv interpreter are REQUIRED — bare \`python3\` or the file-path form fails) → writes \`${HARNESS_ROOT}/cases/<project>/data/rlm-analysis.json\` (source-linked leads: entities, timeline events, contradictions).
3. **Reason from \`rlm-analysis.json\` (distilled leads), NOT the raw \`research/\` files.** Keep raw sources on disk as provenance/citations — do NOT load their full text back into working context. Leads are lead-only, never verified facts.

Keeps working context compact across cycles. If the RLM errors or is unavailable, note it and fall back to reading research files directly.

Work autonomously — never ask the user or wait for input. Persist evidence and findings to files as the skills direct.`;
