// Agent definitions for the Pi harness. Roles, skills, tools and declared keys
// come from harness/composition.json; worker personas come only from the
// checkout's agents/ folder.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// harness/pi/src -> checkout root.
export const CHECKOUT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const AGENTS_DIR = resolve(CHECKOUT_ROOT, 'agents');
const SKILLS_DIR = resolve(CHECKOUT_ROOT, 'skills');
export const ORCHESTRATOR = 'orchestrator';

export type AgentSpec = {
	role: string;
	skills: string[];
	tools: string[];
	keys?: string[];
};
export type Composition = Record<string, AgentSpec>;

export function loadComposition(path = resolve(CHECKOUT_ROOT, 'harness/composition.json')): Composition {
	const parsed = JSON.parse(readFileSync(path, 'utf8')) as { agents?: Composition };
	if (!parsed.agents || typeof parsed.agents !== 'object') {
		throw new Error('harness/composition.json has no agents map');
	}
	return parsed.agents;
}

export function agentSpec(composition: Composition, role: string): AgentSpec {
	const spec = composition[role];
	if (!spec) throw new Error(`unknown Spotlight agent: ${role}`);
	return spec;
}

function stripFrontmatter(markdown: string): string {
	return markdown.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim();
}

// A worker can be spawned only when its persona exists under agents/.
export function spawnableWorkers(composition: Composition): string[] {
	return Object.entries(composition)
		.filter(([name, spec]) => name !== ORCHESTRATOR && workerPersonaPath(spec) !== undefined)
		.map(([name]) => name);
}

function workerPersonaPath(spec: AgentSpec): string | undefined {
	const path = resolve(CHECKOUT_ROOT, spec.role);
	if (dirname(path) !== AGENTS_DIR || !existsSync(path)) return undefined;
	return path;
}

// Tools that reach a network service on the model's behalf. Sensitive mode
// removes them from every role (AGENTS.md, "Sensitive Mode").
export const EGRESS_TOOLS = ['fetch', 'search', 'http_get', 'browse', 'navigator_tools'];

// The tools a process declares and registers: its allowlist, minus egress in
// sensitive mode.
export function allowedTools(spec: AgentSpec, sensitive: boolean): string[] {
	return sensitive ? spec.tools.filter((tool) => !EGRESS_TOOLS.includes(tool)) : [...spec.tools];
}

export function skillDirectory(name: string): string {
	if (!/^[a-z0-9][a-z0-9-]*$/.test(name)) throw new Error(`invalid skill name: ${name}`);
	return resolve(SKILLS_DIR, name);
}

// Skills each process loads by explicit path: the orchestrator's own role
// skill plus its bundle; a worker's bundle.
export function skillsFor(composition: Composition, role: string): string[] {
	const spec = agentSpec(composition, role);
	const own = role === ORCHESTRATOR ? [stripSkillPath(spec.role)] : [];
	return [...new Set([...own, ...spec.skills])];
}

function stripSkillPath(rolePath: string): string {
	const match = /^skills\/([^/]+)\/SKILL\.md$/.exec(rolePath);
	if (!match) throw new Error(`orchestrator role must be a skill path, got ${rolePath}`);
	return match[1];
}

const ADAPTER = `## Runtime adapter (Pi)

The skills use abstract verbs and some shell steps. This harness has no shell: every step you may take is one of the tools in your tool list.
- invoke-skill(id) -> \`invoke_skill\`; its description lists the skills you have.
- Spotlight durable state (orchestrator only) -> \`spotlight_resolve\` and \`spotlight_transition\`. Both are bound by the harness to the active case.
- spawn-agent(id, prompt) / wait-agent -> \`spawn_agent\` (orchestrator only). It returns the worker's final answer.
- read-file, write-file, edit-file, list-files, grep-files -> \`read_file\`, \`write_file\`, \`edit_file\`, \`list_files\`, \`grep_files\`. {CASE_DIR} is the active case folder: give paths relative to it (for example \`data/findings.json\`). Files under the checkout's \`schemas/\` are readable. Writes outside your folder, through a symlink, or to resolver, review or receipt files are refused.
- fetch -> \`fetch\` (use save_as for output_path); search -> \`search\`; query-vault -> \`query_vault\`.
- Shell steps in the skills map to typed tools: a \`curl\` GET to a registry, archive or API -> \`http_get\` (query parameters in the URL); a dev-browser script -> \`browse\` (one page: rendered text, HTML or a screenshot); \`scripts/osint-tools.py find|categories\` -> \`osint_tools\`; \`navigator tools find|show\` -> \`navigator_tools\`; \`exiftool {file}\` -> \`exiftool\`; \`scripts/decision-signals.py\` -> \`decision_signals\`; \`scripts/build-provenance-manifest.py\` -> \`provenance_manifest\`; \`integrations/rlm/run_rlm.py\` -> \`run_rlm\`; \`integrations/preflight.py\` -> \`integration_status\`. \`scripts/spotlight_safe.py validate-*\` checks are not needed: each tool checks its own arguments, and the web tools reach only public http(s) addresses.
- \`scripts/validate-case.py\`, \`scripts/validate-fact-check.py\` and \`scripts/validate-methodology-navigator.py\` are run by the harness itself when a worker finishes; their outcome is appended to the \`spawn_agent\` result. Act on that outcome; do not try to run them.
- Preflight step 0 (the shell check): skipped. A \`spotlight_resolve\` that answers confirms the checkout.
- Anything else (a POST request, Apify, vault-write, the ingest scripts, a tool not in your list): do not improvise around it. Say which step needs it and stop that step.

Gate approvals, the report decision and the ingest request or decline are recorded by the review page, never by you. At a gate, present the synthesis and END YOUR TURN.`;

export function systemPrompt(composition: Composition, role: string): string {
	if (role === ORCHESTRATOR) {
		return `You are the Spotlight orchestrator. You never investigate directly: research goes to the \`investigator\` worker and verification to the \`fact-checker\` worker through \`spawn_agent\`.

Start and resume by calling \`spotlight_resolve\`. Follow the \`spotlight\` skill, invoke exactly the returned phase owner, apply only the transitions it names through \`spotlight_transition\`, then resolve again. The durable sequence is Brief -> Methodology -> Investigation -> Investigation gate -> Report -> Ingest; never infer phase completion from files.

Human gates are real. When the phase owner presents a gate, present its synthesis and decisions, then END YOUR TURN. Do not proceed, delegate, or transition until the gate is recorded.

${ADAPTER}`;
	}
	const spec = agentSpec(composition, role);
	const persona = workerPersonaPath(spec);
	if (!persona) throw new Error(`Spotlight agent ${role} has no persona in agents/`);
	return `You are the Spotlight ${role} worker. You were delegated a task by the orchestrator. You have no user and no gates: never wait for input or approval, and never delegate. Do the task yourself with your own tools and return your result.

${ADAPTER}

${stripFrontmatter(readFileSync(persona, 'utf8'))}`;
}
