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
	// Optional narrowing to named case files (case-relative paths). Without
	// `reads` the role reads the whole case; without `writes` it writes anywhere
	// its write folder allows.
	reads?: string[];
	writes?: string[];
	// Optional spawn conditions: the resolver phases and the editions in which
	// the launcher starts this worker. Absent means any.
	phases?: string[];
	editions?: string[];
};
export type Composition = Record<string, AgentSpec>;

// The tools that honour a role's `reads` and `writes`. A role narrowed to named
// case files may declare only these, so no other tool (a web tool's save_as, a
// script wrapper reading a request file) reads or writes around its scope.
export const SCOPED_TOOLS = ['invoke_skill', 'read_file', 'write_file', 'edit_file', 'list_files', 'grep_files', 'hash_files', 'query_vault'];

const CASE_FILE = /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/;

function checkScope(name: string, spec: AgentSpec): void {
	if (spec.reads === undefined && spec.writes === undefined) return;
	for (const path of [...(spec.reads ?? []), ...(spec.writes ?? [])]) {
		if (!CASE_FILE.test(path) || path.split('/').some((part) => part === '..' || part === '.')) {
			throw new Error(`harness/composition.json: ${name} scope entry ${JSON.stringify(path)} is not a case-relative file path`);
		}
	}
	const unscoped = spec.tools.filter((tool) => !SCOPED_TOOLS.includes(tool));
	if (unscoped.length > 0) {
		throw new Error(`harness/composition.json: ${name} is narrowed to named case files but declares ${unscoped.join(', ')}, which do not honour that scope`);
	}
}

export function loadComposition(path = resolve(CHECKOUT_ROOT, 'harness/composition.json')): Composition {
	const parsed = JSON.parse(readFileSync(path, 'utf8')) as { agents?: Composition };
	if (!parsed.agents || typeof parsed.agents !== 'object') {
		throw new Error('harness/composition.json has no agents map');
	}
	for (const [name, spec] of Object.entries(parsed.agents)) checkScope(name, spec);
	return parsed.agents;
}

// Why the launcher will not start this worker now, or undefined when it may.
export function spawnRefusal(spec: AgentSpec, role: string, phase: string, edition: string): string | undefined {
	if (spec.editions && !spec.editions.includes(edition)) {
		return `the ${role} does not run in the ${edition} edition`;
	}
	if (spec.phases && !spec.phases.includes(phase)) {
		return `the ${role} runs only at ${spec.phases.join(', ')}; the case is at ${phase || 'an unknown phase'}`;
	}
	return undefined;
}

// D9: the fact-checker verifies findings independently of the research that
// led to them. A task that points it at the research leads, the clues, the
// lane folders or a review page is refused before any process starts.
const FACT_CHECKER_FORBIDDEN: Array<[RegExp, string]> = [
	[/\bresearch\/swarm\b/i, 'research/swarm/'],
	[/\bleads\.jsonl?\b/i, 'the research leads'],
	[/\bclues\.json\b/i, 'the research clues'],
	[/\bresearch-(?:triage|review|suggestions)\.json\b/i, 'the research triage or review'],
	[/(?<![\w.-])(?:https?:\/\/)?(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?(?![\w.-])/i, 'a review page'],
];

export function isolationRefusal(role: string, task: string): string | undefined {
	if (role !== 'fact-checker') return undefined;
	const text = task.replaceAll('\\', '/');
	const named = FACT_CHECKER_FORBIDDEN.filter(([pattern]) => pattern.test(text)).map(([, label]) => label);
	return named.length ? `the fact-checker works without the research phase's material; the task names ${named.join(', ')}` : undefined;
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

// Tools that can reach a network service on the model's behalf. Sensitive
// mode removes them from every role (AGENTS.md, "Sensitive Mode"). run_rlm can
// send case text to SPOTLIGHT_RLM_OPENAI_BASE_URL, and integration_status runs
// the integration preflight, which can probe endpoints; neither is proven
// local, so both go. decision_signals and provenance_manifest stay: they get
// SPOTLIGHT_SENSITIVE and refuse their remote step themselves.
export const EGRESS_TOOLS = ['fetch', 'search', 'http_get', 'browse', 'navigator_tools', 'run_rlm', 'integration_status'];

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
- read-file, write-file, edit-file, list-files, grep-files -> \`read_file\`, \`write_file\`, \`edit_file\`, \`list_files\`, \`grep_files\`. {CASE_DIR} is the active case folder: give paths relative to it, keeping every folder the skill names and adding none (\`{CASE_DIR}/brief-directions.txt\` -> \`brief-directions.txt\`, \`{CASE_DIR}/data/methodology.json\` -> \`data/methodology.json\`). Files under the checkout's \`schemas/\` are readable. Writes outside your folder, through a symlink, or to resolver, review or receipt files are refused.
- fetch -> \`fetch\` (use save_as for output_path); search -> \`search\`; query-vault -> \`query_vault\`.
- Context hygiene: when the harness has a local RLM, \`fetch\` distills every page itself. You do not run the RLM. The result says \`distilled: true\` and gives you compact leads; save_as holds those leads and \`raw_path\` (save_as + \`.raw\`) the page as fetched, for citations and provenance. Read the leads. Read a section of a \`.raw\` file only when the leads look thin or you need exact wording for a citation: find it with \`grep_files\`, then \`read_file\` with offset/limit. Never load a raw page whole. Leads are unverified pointers, never facts, and never evidence: a \`source_ref\` must point at the \`.raw\` file, not the lead file (the validators refuse lead files). If the result says the RLM failed, the page is only at \`raw_path\`; treat it the same way.
- Shell steps in the skills map to typed tools: a \`curl\` GET to a registry, archive or API -> \`http_get\` (\`--data-urlencode\` values go in its \`query\` object, unencoded); a dev-browser script -> \`browse\` (one page: rendered text, HTML or a screenshot); \`scripts/osint-tools.py find|categories\` -> \`osint_tools\`; \`navigator tools find|show\` -> \`navigator_tools\`; \`exiftool {file}\` -> \`exiftool\`; \`scripts/decision-signals.py\` -> \`decision_signals\`; \`scripts/build-provenance-manifest.py\` -> \`provenance_manifest\`; \`integrations/rlm/run_rlm.py\` -> \`run_rlm\`; \`integrations/preflight.py\` -> \`integration_status\`; \`scripts/build-review-html.py\` -> \`build_review_html\`; \`spotlight_safe.py sha256\` or any SHA-256 of a case file -> \`hash_files\`; \`grep -n\`, \`sed -n\` or Python to find a source_ref line range or JSON Pointer -> \`grep_files\` and \`read_file\` (offset/limit or json_pointer), which count and select exactly as the validators do. \`scripts/spotlight_safe.py validate-*\` checks are not needed: each tool checks its own arguments, and the web tools reach only public http(s) addresses.
- \`scripts/validate-case.py\`, \`scripts/validate-fact-check.py\` and \`scripts/validate-methodology-navigator.py\` are run by the harness itself when a worker finishes; their outcome is appended to the \`spawn_agent\` result. Act on that outcome; do not try to run them.
- Preflight step 0 (the shell check): skipped. A \`spotlight_resolve\` that answers confirms the checkout.
- Ingest, the analyst proposal (orchestrator only): \`spawn_agent\` with agent \`analyst\`, at Ingest in the solo edition. It reads the approved case files and writes only \`data/knowledge-proposal.json\`. When it finishes, the harness runs \`scripts/build-knowledge-batch.py --review\` itself with the producers' model ids and appends the outcome to the result; do not try to run it. A refusal names what to fix.
- Anything else (a POST request, git clone, Apify, vault-write, the other ingest scripts, a tool not in your list): do not improvise around it. Say which step needs it and stop that step.

Gate approvals, the report decision and the ingest request or decline are recorded by the review page, never by you. At a gate, present the synthesis, call \`open_review\` so the member can decide on the page, and END YOUR TURN. When the member says they are done, call \`spotlight_resolve\` again.`;

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
