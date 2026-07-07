import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// harness/flue/src/lib -> repo root is four up; agents/ + role .md files live there.
// Resolved from the source file (absolute), so it holds regardless of `flue run` cwd,
// in dev and in the sparse-fetched install (which ships harness/flue + agents together).
const AGENTS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../agents');

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

You are running in the Flue harness. The shared skills use abstract verbs; execute them as:
- **execute-shell(cmd)** → your \`bash\` tool.
- **fetch(url, output_path)** → \`bash\`: \`python3 -m integrations.scraping <url> --out <output_path>\` (Crawl4AI, no API key; auto-escalates to Scrapling on bot-block). For a local PDF add \`--pdf\`.
- **search(query)** → \`bash\`: \`firecrawl search "<query>"\`. For OSINT *tool* discovery use \`bash\`: \`navigator tools find "<query>" --json\`.
- **query-vault(query)** → \`bash\`: \`BUN_INSTALL="" qmd query "<query>"\`.
- **read-file / write-file / edit-file / list-files / grep-files** → your \`read\` / \`write\` / \`edit\` / \`glob\` / \`grep\` tools.
- **invoke-skill(id)** → the skill of that name is already loaded from \`.agents/skills\`; follow its instructions.
- **spawn-agent(id, prompt)** → delegate with your \`task\` tool to the named subagent (\`investigator\` or \`fact-checker\`).

Work autonomously — never ask the user or wait for input. Persist evidence and findings to files as the skills direct.`;
