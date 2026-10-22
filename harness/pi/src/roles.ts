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

The skills use abstract verbs. In this harness:
- invoke-skill(id) -> the \`invoke_skill\` tool; its description lists the skills you have.
- Spotlight durable state (orchestrator only) -> \`spotlight_resolve\` and \`spotlight_transition\`. Both are bound by the harness to the active case; never name a case path.
- spawn-agent(id, prompt) / wait-agent -> the \`spawn_agent\` tool (orchestrator only). It returns the worker's final answer.
- Any other verb (fetch, search, read-file, write-file, edit-file, list-files, grep-files, execute-shell, query-vault, vault-write) is not available in this harness version. Do not improvise around it: say which verb the step needs and stop that step.

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
