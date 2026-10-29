// Agent definitions for the Pi harness. Roles, skills, tools and declared keys
// come from harness/composition.json; worker personas come only from the
// checkout's agents/ folder.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Edition, ModelTier } from './config.ts';

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
	// Optional case files hidden from the role's file tools (and exiftool), the
	// rest of the case staying readable: an entry ending in "/" is a folder,
	// "*" matches within one path segment. Unlike `reads`, it leaves the role's
	// other tools as they are.
	denyReads?: string[];
	// Optional case files the role's write_file and edit_file refuse, same
	// syntax: the files a worker owns and the harness validates after that
	// worker (the orchestrator may not rewrite them around the checks).
	denyWrites?: string[];
	// Optional spawn conditions: the resolver phases and the editions in which
	// the launcher starts this worker. Absent means any.
	phases?: string[];
	editions?: string[];
	// Set when code runs the role itself (the researcher: the research lane
	// runner, lanes.ts). Such a role is never a spawn_agent worker.
	runner?: string;
};
export type Composition = Record<string, AgentSpec>;

// The tools that honour a role's `reads` and `writes`. A role narrowed to named
// case files may declare only these, so no other tool (a web tool's save_as, a
// script wrapper reading a request file) reads or writes around its scope.
export const SCOPED_TOOLS = ['invoke_skill', 'read_file', 'write_file', 'edit_file', 'list_files', 'grep_files', 'hash_files', 'query_vault'];

const CASE_FILE = /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/;
const DENY_ENTRY = /^[A-Za-z0-9_.*-]+(?:\/[A-Za-z0-9_.*-]+)*\/?$/;

function plainSegments(path: string): boolean {
	return !path.replace(/\/$/, '').split('/').some((part) => part === '..' || part === '.');
}

function checkScope(name: string, spec: AgentSpec): void {
	for (const field of ['denyReads', 'denyWrites'] as const) {
		for (const entry of spec[field] ?? []) {
			if (!DENY_ENTRY.test(entry) || !plainSegments(entry)) {
				throw new Error(`harness/composition.json: ${name} ${field} entry ${JSON.stringify(entry)} is not a case-relative file, pattern or folder/`);
			}
		}
	}
	if (spec.reads === undefined && spec.writes === undefined) return;
	for (const path of [...(spec.reads ?? []), ...(spec.writes ?? [])]) {
		if (!CASE_FILE.test(path) || !plainSegments(path)) {
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
// lane folders or a review page is refused before any process starts. The
// patterns live in harness/fact-checker-isolation.json, which case search
// (integrations/case_search.py) reads too, so a passage cannot name them either.
const FACT_CHECKER_FORBIDDEN: Array<[RegExp, string]> = (
	JSON.parse(readFileSync(resolve(CHECKOUT_ROOT, 'harness/fact-checker-isolation.json'), 'utf8')) as {
		taskNaming: Array<{ pattern: string; label: string }>;
	}
).taskNaming.map(({ pattern, label }) => [new RegExp(pattern, 'i'), label]);

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
		.filter(([name, spec]) => name !== ORCHESTRATOR && !spec.runner && workerPersonaPath(spec) !== undefined)
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

// The newsroom edition runs the same roles; only where prior knowledge,
// decision checks and Ingest go differs. Its Editorial MCP replaces the
// member wiki (query_vault) and the local decision checks (decision_signals,
// held only by the orchestrator, which also gets the Submit step). These
// tools call the newsroom's own service through the launcher; they stay in
// sensitive mode, which keeps the case away from the public web, not from
// the newsroom. Solo Ingest's local wiki write (wiki_destination) has no
// newsroom counterpart: there, Ingest is the Submit step.
export const NEWSROOM_TOOL_SWAPS: Record<string, string[]> = {
	query_vault: ['search_knowledge', 'get_record', 'traverse', 'coverage'],
	decision_signals: ['run_checks', 'newsroom_submit'],
	wiki_destination: [],
};

// The tools a process declares and registers: its allowlist, with the
// newsroom edition's swaps, minus egress in sensitive mode.
export function allowedTools(spec: AgentSpec, sensitive: boolean, edition: Edition = 'solo'): string[] {
	const tools = edition === 'newsroom' ? spec.tools.flatMap((tool) => NEWSROOM_TOOL_SWAPS[tool] ?? [tool]) : [...spec.tools];
	return sensitive ? tools.filter((tool) => !EGRESS_TOOLS.includes(tool)) : tools;
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

// How far a model may read the raw page behind the distilled leads, by tier
// (SPOTLIGHT_MODEL_TIER). The 12b degrades as its context grows, so it reads
// only the leads and touches a raw page only for an exact citation passage;
// the larger local tiers and the frontier tier may also read a section when
// the leads look thin.
const RAW_PAGE_RULE = {
	'12b': 'Read only the leads. Do not read a `.raw` file unless you need the exact source passage for a citation; then grep it first with `grep_files` for a distinctive locator, read only that bounded line range with `read_file` (offset/limit, or json_pointer) and record that range or pointer in `source_ref`. Never load a raw page whole.',
	default:
		'Read the leads. Read a section of a `.raw` file only when the leads look thin or you need exact wording for a citation: find it with `grep_files`, then `read_file` with offset/limit. Never load a raw page whole.',
};

const adapter = (tier: ModelTier) => `## Runtime adapter (Pi)

The skills use abstract verbs and some shell steps. This harness has no shell: every step you may take is one of the tools in your tool list.
- invoke-skill(id) -> \`invoke_skill\`; its description lists the skills you have.
- Spotlight durable state (orchestrator only) -> \`spotlight_resolve\` and \`spotlight_transition\`. Both are bound by the harness to the active case.
- spawn-agent(id, prompt) / wait-agent -> \`spawn_agent\` (orchestrator only). It returns the worker's final answer.
- read-file, write-file, edit-file, list-files, grep-files -> \`read_file\`, \`write_file\`, \`edit_file\`, \`list_files\`, \`grep_files\`. {CASE_DIR} is the active case folder: give paths relative to it, keeping every folder the skill names and adding none (\`{CASE_DIR}/brief-directions.txt\` -> \`brief-directions.txt\`, \`{CASE_DIR}/data/methodology.json\` -> \`data/methodology.json\`). Files under the checkout's \`schemas/\` are readable. Writes outside your folder, through a symlink, or to resolver, review or receipt files are refused.
- fetch -> \`fetch\` (use save_as for output_path); search -> \`search\`; query-vault -> \`query_vault\`.
- Context hygiene: when the harness has a local RLM, \`fetch\` distills every page itself. You do not run the RLM. The result says \`distilled: true\` and gives you compact leads; save_as holds those leads and \`raw_path\` (save_as + \`.raw\`) the page as fetched, for citations and provenance. ${RAW_PAGE_RULE[tier === '12b' ? '12b' : 'default']} Leads are unverified pointers, never facts, and never evidence: a \`source_ref\` must point at the \`.raw\` file, not the lead file (the validators refuse lead files). If the result says the RLM failed, the page is only at \`raw_path\`; treat it the same way.
- Shell steps in the skills map to typed tools: a \`curl\` GET to a registry, archive or API -> \`http_get\` (\`--data-urlencode\` values go in its \`query\` object, unencoded); a dev-browser script -> \`browse\` (one page: rendered text, HTML or a screenshot); \`scripts/osint-tools.py find|categories\` -> \`osint_tools\`; \`navigator tools find|show\` -> \`navigator_tools\`; \`exiftool {file}\` -> \`exiftool\`; \`scripts/decision-signals.py\` -> \`decision_signals\`; \`scripts/build-provenance-manifest.py\` -> \`provenance_manifest\`; \`integrations/rlm/run_rlm.py\` -> \`run_rlm\`; \`integrations/preflight.py\` -> \`integration_status\`; \`integrations/anomaly/adapter.py context [--domain D | --source ID]\` -> \`anomaly_methods\` with domain or source; \`scripts/build-review-html.py\` -> \`build_review_html\`; \`scripts/write-source-expressions.py {CASE_DIR} --actor <you> --cycle N < proposal.json\` -> \`source_expressions\` with action \`write\`, the cycle and the proposal object (the harness passes your role as the actor), and \`--fingerprint-findings\` -> \`source_expressions\` with action \`fingerprint_findings\`; \`data/source-expressions.json\` and \`data/source-positions.json\` refuse every file-tool write; \`spotlight_safe.py sha256\` or any SHA-256 of a case file -> \`hash_files\`; \`grep -n\`, \`sed -n\` or Python to find a source_ref line range or JSON Pointer -> \`grep_files\` and \`read_file\` (offset/limit or json_pointer), which count and select exactly as the validators do. \`scripts/spotlight_safe.py validate-*\` checks are not needed: each tool checks its own arguments, and the web tools reach only public http(s) addresses.
- \`scripts/validate-case.py\`, \`scripts/validate-fact-check.py\`, \`scripts/validate-indicator-framework.py\` and \`scripts/validate-methodology-navigator.py\` are run by the harness itself when a worker finishes; their outcome is appended to the \`spawn_agent\` result. Act on that outcome; do not try to run them.
- \`scripts/case-contract.py activate-new\` (orchestrator only) is run by the harness itself right before the first investigator task of a new case at Investigation; \`data/case-contract.json\` is the harness's and no tool writes it. Resolve the source-expression mode as the skill says, reading that file: a new case with no findings is activated.
- Worker files (orchestrator only): the files the workers write and the harness checks after them (\`data/methodology.json\`, the findings, the fact-check, the evidence bundle, the investigation log, the passages, the Indicator Framework files) refuse your writes. Where a skill has you change one (a research plan, an RLM block), send the change to the investigator as a METHODOLOGY CORRECTION, or to the worker that wrote the file.
- Research (orchestrator only): at the Research phase \`spotlight_resolve\` runs the approved research topics itself and returns the round as \`research_round\` (what ran, the spend, the topics left out, and \`stopped\` with the fix when the provider failed). You never start, widen or choose topics. Pass \`retry_research: true\` only when the member asks to retry a stopped round.
- Preflight step 0 (the shell check): skipped. A \`spotlight_resolve\` that answers confirms the checkout.
- Ingest, the analyst proposal (orchestrator only): \`spawn_agent\` with agent \`analyst\`, at Ingest in the solo edition. It reads the approved case files and writes only \`data/knowledge-proposal.json\`. When it finishes, the harness runs \`scripts/build-knowledge-batch.py --review\` itself with the producers' model ids and appends the outcome to the result; do not try to run it. A refusal names what to fix.
- Ingest, the wiki write (orchestrator only, solo edition): \`scripts/wiki-destination.py {CASE_DIR}\` -> \`wiki_destination\`, only once the member has requested ingestion on the page (\`resume_at\` \`ingest\`). It writes the member's wiki and the completed receipt; then resolve again and seal.
- Anything else (a POST request, git clone, Apify, the retired \`ingest\` skill's scripts, a tool not in your list): do not improvise around it. Say which step needs it and stop that step.

Gate approvals, the report decision and the ingest request or decline are recorded by the review page, never by you. At a gate, present the synthesis, call \`open_review\` so the member can decide on the page, and END YOUR TURN. When the member says they are done, call \`spotlight_resolve\` again.`;

// The newsroom edition's differences, appended to every role's adapter.
const NEWSROOM_ADAPTER = `## Newsroom edition
This case belongs to a newsroom. Prior reporting comes from the newsroom's knowledge base, not a member wiki: query-vault -> \`search_knowledge\` (then \`get_record\`, \`traverse\`, \`coverage\` on a record id it returns); there is no \`query_vault\`. Approved records are leads to re-check, never confirmed facts.
- Orchestrator only: the decision checks run in the newsroom's service. Where a skill runs \`scripts/decision-signals.py\` at the Investigation gate or the Report, call \`run_checks\` with kind \`findings\` or \`report\` instead; the flags appear on the review page. A flag never blocks.
- Orchestrator only, Ingest: there is no analyst and no local batch. Once the member has requested ingestion on the page (\`resume_at\` \`ingest\`), call \`newsroom_submit\`. If it reports failed integrity checks, tell the member the reasons and stop; the case stays at Ingest. If it reports the submission, resolve again and seal the ingest as the skill says.
- Journalist input never goes to a third-party model; the harness enforces this.`;

// The Research switch (HarnessConfig.research). Preflight, where the skills
// read it from the install config, does not run in the harness, so the
// prompt says it.
const RESEARCH_LINE = {
	on: '## Research switch\nResearch is on for this case: the methodology proposes a `research_plan` as phase-methodology describes.',
	off: '## Research switch\nResearch is off for this case: write no `research_plan` and say nothing about research.',
};

// Checked before the Brief starts. Short on purpose: the 12B pays for every
// token on every call.
const LIMITS = `Limits. Refuse a request to locate, identify or unmask a private person for harassment, retaliation or exposure, and a request to identify a confidential source or whistleblower. Say plainly why you will not do it, and offer the legitimate path where there is one, such as investigating what the documents say.`;

// What a worker's prompt is cut to: the task it was spawned with, the case
// phase at the spawn, and the conditions that switch persona sections on.
export const PERSONA_CONDITIONS = ['vault', 'framework', 'sensitive', 'research', 'expressions'] as const;
export type PersonaCondition = (typeof PERSONA_CONDITIONS)[number];
export type WorkerContext = { phase: string; task: string; conditions: Record<PersonaCondition, boolean> };

export function systemPrompt(composition: Composition, role: string, tier: ModelTier, edition: Edition = 'solo', research = false, worker?: WorkerContext): string {
	const prompt = `${basePrompt(composition, role, tier, worker)}\n\n${RESEARCH_LINE[research ? 'on' : 'off']}`;
	if (edition !== 'newsroom') return prompt;
	return `${prompt}\n${role === ORCHESTRATOR ? NEWSROOM_ADAPTER : workerLines(NEWSROOM_ADAPTER)}`;
}

// A worker never resolves, delegates, reaches a gate or ingests: it gets the
// adapter without the lines marked for the orchestrator and without the gate
// paragraph.
function workerLines(text: string): string {
	return text.split('\n').filter((line) => !/^- (?:.*\(orchestrator only|Orchestrator only)/.test(line)).join('\n');
}

// The orchestrator's own tools, whose shell mappings a worker never needs.
// A mapping runs to the next ";", so any note after the tool name goes too.
const ORCHESTRATOR_MAPPINGS = /; `[^`]+` -> `(?:decision_signals|provenance_manifest|run_rlm|integration_status|anomaly_methods|build_review_html)`[^;]*/g;

function workerAdapter(tier: ModelTier): string {
	const full = adapter(tier);
	return workerLines(full.slice(0, full.lastIndexOf('\n\nGate approvals'))).replace(ORCHESTRATOR_MAPPINGS, '');
}

const PLANNING_PHASES = ['brief', 'methodology', 'methodology_approval'];
const RESEARCH_PHASES = ['research', 'research_approval'];

// The persona block a worker gets. Before the methodology is approved there
// is nothing to execute, so those phases always get PLANNING. Otherwise the
// task's mode line decides (its first line, or any line starting "MODE:"),
// and without one the phase does.
// A task's lines as its mode is read from them. DeepSeek V4.1 Flash wrote
// task strings that open with a quote ('"MODE: STRUCTURAL CORRECTION — ...',
// train-tor on 52bf6183), so a correction read as an investigation task, was
// split into direction workers, and one of them dropped four findings.
// Leading quotes, backticks and markdown marks are not part of the mode line.
function modeLines(task: string): string[] {
	return task.split('\n').map((line) => line.trim().replace(/^["'`*#>\s]+/, ''));
}

export function workerMode(phase: string, task: string): 'PLANNING' | 'EXECUTION' | 'AMENDMENT' {
	if (PLANNING_PHASES.includes(phase)) return 'PLANNING';
	const all = modeLines(task.trim());
	const lines = [all[0] ?? '', ...all.filter((line) => line.startsWith('MODE:'))];
	for (const line of lines) {
		const mode = /^(?:MODE:\s*)?(PLANNING|EXECUTION|AMENDMENT|METHODOLOGY CORRECTION|(?:[A-Z]+ )?CORRECTION)\b/.exec(line)?.[1];
		if (mode === 'PLANNING' || mode === 'EXECUTION' || mode === 'AMENDMENT') return mode;
		if (mode === 'METHODOLOGY CORRECTION') return 'PLANNING';
		if (mode) return 'EXECUTION';
	}
	return RESEARCH_PHASES.includes(phase) ? 'AMENDMENT' : 'EXECUTION';
}

// tools-da on r4 (LFM3): the fact-checker's task said "write only
// knowledge-proposal.json" (analyst rows bleeding in) or "data/fact-check-*.json",
// and the worker obeyed the task, so data/fact-check.json was never written.
// Every spawn now ends with the role's fixed deliverable, from its
// return_contract in agents/*.md: the files it must write and the line that
// says so. A correction task names its own targets and gets none.
// `start` is a structure to begin the file from (the fact-check's), sent after the line.
export type Deliverable = { files: string[]; line: string; start?: string };
const AMENDMENT_FILES: Record<string, string> = {
	suggestions: 'data/research-suggestions.json',
	amendment: 'data/methodology-amendment.json',
	round_topics: 'data/research-round.json',
};
export function deliverableFor(role: string, phase: string, task: string, researchStep?: string): Deliverable | undefined {
	const one = (file: string, line = `Your deliverable: ${file}; write it with write_file, then finish.`) => ({ files: [file], line });
	if (role === 'fact-checker') return one('data/fact-check.json', 'Your deliverable: data/fact-check.json covering every finding in data/findings.json for this cycle; write it with write_file, then finish.');
	if (role === 'analyst') return one('data/knowledge-proposal.json');
	if (role !== 'investigator') return undefined;
	if (modeLines(task).some((line) => /^(?:MODE:\s*)?(?:[A-Z]+ )?CORRECTION\b/.test(line))) return undefined;
	const mode = workerMode(phase, task);
	if (mode === 'PLANNING') return one('data/methodology.json');
	if (mode === 'AMENDMENT') {
		const file = researchStep ? AMENDMENT_FILES[researchStep] : undefined;
		return file ? one(file) : undefined;
	}
	return {
		files: ['data/findings.json', 'data/evidence-bundle.json'],
		line: 'Your deliverable: data/findings.json and data/evidence-bundle.json, with this cycle appended to data/investigation-log.json; write them with write_file, then finish.',
	};
}

// Cuts a persona to the task: of the `## [MODE: X]` ... `## [/MODE: X]`
// blocks only the active one stays, and a `<!-- [IF: name] -->` ...
// `<!-- [/IF: name] -->` section only when its condition holds (its marker
// lines never reach the prompt).
// A `<!-- [FULL: name] -->` ... `<!-- [/FULL: name] -->` block is what every
// reader gets, skill mode included (the persona file stays its full text). On
// the 12b tier the block is replaced by agents/compact/<name>.md: the same
// rules, with any long procedure left in agents/references/ for the worker
// to read at the step (U4 prompt shrink, LFM3 at 2.3 characters a token).
const COMPACT_DIR = resolve(AGENTS_DIR, 'compact');

export function slicePersona(persona: string, mode: string, conditions: Record<PersonaCondition, boolean>, compact = false): string {
	const sized = persona.replace(/^<!-- \[FULL: ([\w-]+)\] -->\n([\s\S]*?)^<!-- \[\/FULL: \1\] -->\n/gm, (_block, name: string, body: string) =>
		compact ? readFileSync(join(COMPACT_DIR, `${name}.md`), 'utf8') : body,
	);
	const modes = sized.replace(/^## \[MODE: (\w+)\]\n[\s\S]*?^## \[\/MODE: \1\]\n/gm, (block, name: string) => (name === mode ? block : ''));
	return modes.replace(/^<!-- \[IF: (\w+)\] -->\n([\s\S]*?)^<!-- \[\/IF: \1\] -->\n/gm, (_block, name: string, body: string) => {
		if (!(PERSONA_CONDITIONS as readonly string[]).includes(name)) throw new Error(`unknown persona condition: ${name}`);
		return conditions[name as PersonaCondition] ? body : '';
	});
}

function basePrompt(composition: Composition, role: string, tier: ModelTier, worker?: WorkerContext): string {
	if (role === ORCHESTRATOR) {
		return `You are the Spotlight orchestrator. You never investigate directly: research goes to the \`investigator\` worker and verification to the \`fact-checker\` worker through \`spawn_agent\`.

Start and resume by calling \`spotlight_resolve\`. Follow the \`spotlight\` skill, invoke exactly the returned phase owner, apply only the transitions it names through \`spotlight_transition\`, then resolve again. The durable sequence is Brief -> Methodology -> Research (only when the approved methodology has a research plan) -> Investigation -> Investigation gate -> Report -> Ingest; never infer phase completion from files.

Human gates are real. When the phase owner presents a gate, present its synthesis and decisions, then END YOUR TURN. Do not proceed, delegate, or transition until the gate is recorded.

${LIMITS}

${adapter(tier)}`;
	}
	const spec = agentSpec(composition, role);
	const persona = workerPersonaPath(spec);
	if (!persona) throw new Error(`Spotlight agent ${role} has no persona in agents/`);
	if (!worker) throw new Error(`Spotlight worker ${role} needs its spawn context (phase, task, conditions)`);
	const body = slicePersona(stripFrontmatter(readFileSync(persona, 'utf8')), workerMode(worker.phase, worker.task), worker.conditions, tier === '12b');
	return `You are the Spotlight ${role} worker. You were delegated a task by the orchestrator. You have no user and no gates: never wait for input or approval, and never delegate. Do the task yourself with your own tools and return your result.

${workerAdapter(tier)}

${body}`;
}
