// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { relative, resolve, sep } from 'node:path';
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { registerCompaction } from './checkpoint.ts';
import { maxOutputTokens, resolverEnv } from './config.ts';
import { applyFixedRequestBody, parseFixedRequestBody, withoutHistoryReasoning } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, loadComposition, nextCheckText, readCheckDocs, skillAdapterNote, skillsFor, spawnableWorkers } from './roles.ts';
import { READ_TRUNCATED, writeConfined } from './tools/case-files.ts';
import { typedTools } from './tools/index.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { INDICATOR_FRAMEWORK_ROLES, approvedIndicatorFramework } from './tools/hosted-skill.ts';
import { recordIndicatorFrameworkTool } from './tools/indicator-framework.ts';
import { bindActiveCase, nextAction, pythonAdapter, resolveCase, spotlightTools } from './tools/spotlight.ts';
import { requestEditorial, requestResearchRound, spawnAgentTool } from './subagents.ts';
import { editorialTools } from './tools/editorial.ts';
import { PAGE_NOT_OPENED, gateCheck, missingForApproval, openReviewTool } from './tools/review.ts';
import { FACT_CHECK_DUE_STOP, LOOP_STOP, REFUSAL_STOP, REFUSAL_STREAK, REPEAT_REASON } from './tools/run.ts';
import { evidenceValidates } from './validators.ts';

const log = (message: string) => process.stderr.write(`spotlight-extension: ${message}\n`);

function required(name: string): string {
	const value = process.env[name];
	if (!value) throw new Error(`Spotlight extension: ${name} is not set (start Spotlight through its launcher)`);
	return value;
}

function toolsFor(role: string): ToolDefinition[] {
	const composition = loadComposition();
	const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
	const python = process.env.SPOTLIGHT_PYTHON || 'python3';
	const spec = agentSpec(composition, role);
	const sensitive = process.env.SPOTLIGHT_SENSITIVE === 'true';
	// The Navigator-hosted Indicator Framework, cached per revision in the
	// harness data folder.
	const hosted = INDICATOR_FRAMEWORK_ROLES.includes(role)
		? { caseDir: active.caseDir, dataDir: required('SPOTLIGHT_HARNESS_DATA'), env: process.env, sensitive }
		: undefined;
	const context = {
		caseDir: active.caseDir,
		writeRoot: active.caseDir,
		python,
		configPath: process.env.SPOTLIGHT_CONFIG || `${CHECKOUT_ROOT}/.spotlight-config.json`,
		env: process.env,
		scope: { reads: spec.reads, writes: spec.writes, denyReads: spec.denyReads, denyWrites: spec.denyWrites },
	};
	const tools: ToolDefinition[] = [
		invokeSkillTool(skillsFor(composition, role), hosted, (skill) => skillAdapterNote(skill, spec.tools, role === ORCHESTRATOR)),
		...typedTools(context),
	];
	const edition = process.env.SPOTLIGHT_EDITION === 'newsroom' ? 'newsroom' : 'solo';
	// Newsroom edition: the Editorial MCP tools, served by the launcher. The
	// allowlist below keeps only the ones this role's swaps grant.
	if (edition === 'newsroom') tools.push(...editorialTools(requestEditorial));
	if (role === ORCHESTRATOR) {
		const runAdapter = pythonAdapter(python, resolverEnv(process.env, composition));
		const cycle = { spawns: 0 };
		tools.push(...spotlightTools(active, runAdapter, role, requestResearchRound, () => evidenceValidates(context), () => (cycle.spawns = 0)));
		tools.push(spawnAgentTool(spawnableWorkers(composition), cycle));
		tools.push(openReviewTool(active.caseDir, required('SPOTLIGHT_CASES_ROOT'), undefined, gateCheck(active, runAdapter, context), async () => missingForApproval(active.caseDir, await resolveCase(active, runAdapter))));
		tools.splice(0, tools.length, ...tools.map((tool) => keepGateBound(tool, active.caseDir, async () => String((await resolveCase(active, runAdapter)).phase ?? ''))));
	}
	const allowlist = new Set(allowedTools(spec, sensitive, edition));
	const allowed = tools.filter((tool) => allowlist.has(tool.name));
	// Only a case whose approved methodology turns the framework on gets the
	// tool, so every other case keeps the same tool list.
	if (hosted && approvedIndicatorFramework(active.caseDir)) allowed.push(recordIndicatorFrameworkTool(hosted, context.writeRoot));
	return allowed;
}

// LFM3 e2 kde stage 3 on Osaurus (03f008b0): at Ingest the orchestrator
// rewrote data/summary.json, which the approved Investigation gate and its
// provenance manifest bind, and the case fell back to the gate's finalization.
// Once the gate is approved the orchestrator may not write the files the gate
// and its finalization bind (the rest are in its denyWrites), nor anything in
// the folders the evidence bundle's files live in, which the gate's dependency
// digest hashes too. A follow-up returns the case to Investigation, where it
// may again. launcher.test.ts checks the list against the provenance builder's
// gate dependencies.
export const GATE_BOUND = new Set(['summary.md', 'data/summary.json', 'data/case-contract.json', 'data/provenance-manifest.json', 'review.html']);
const GATE_BOUND_FOLDERS = ['research/', 'evidence/'];
const GATE_APPROVED_PHASES = new Set(['investigation_gate_finalization', 'report', 'ingest', 'complete']);
export const GATE_BOUND_REFUSAL = 'is bound by the approved Investigation gate, so it stays as the member approved it; the file is unchanged. Work from it as it is.';

function keepGateBound(tool: ToolDefinition, caseDir: string, phase: () => Promise<string>): ToolDefinition {
	if (tool.name !== 'write_file' && tool.name !== 'edit_file') return tool;
	return {
		...tool,
		async execute(id, params, ...rest) {
			const path = String((params as { path?: unknown }).path ?? '');
			const name = relative(caseDir, resolve(caseDir, path.replace(/^\{CASE_DIR\}(?:[\\/]|$)/, ''))).split(sep).join('/').toLowerCase();
			if ((GATE_BOUND.has(name) || GATE_BOUND_FOLDERS.some((folder) => name.startsWith(folder))) && GATE_APPROVED_PHASES.has(await phase())) throw new Error(`refused: ${name} ${GATE_BOUND_REFUSAL}`);
			return tool.execute(id, params, ...rest);
		},
	};
}

export default function spotlightExtension(pi: ExtensionAPI): void {
	const role = required('SPOTLIGHT_ROLE');
	if (process.env.SPOTLIGHT_ROUTE_INFERENCE !== 'own') {
		// The launcher never starts this combination; refuse here too.
		log('case material on a route the operator does not run; refusing to start');
		process.exit(78);
	}
	const fixedBody = parseFixedRequestBody(process.env.SPOTLIGHT_PROVIDER_REQUEST_BODY);
	const contextWindow = Number(process.env.SPOTLIGHT_MODEL_CONTEXT_WINDOW || 32768);
	pi.registerProvider(PROVIDER_ID, {
		name: 'Spotlight',
		baseUrl: required('SPOTLIGHT_MODEL_BASE_URL'),
		api: 'openai-completions',
		// Pi requires a key; a local server without one ignores the placeholder.
		apiKey: process.env.SPOTLIGHT_PROVIDER_API_KEY ? '$SPOTLIGHT_PROVIDER_API_KEY' : 'spotlight-no-key',
		models: [
			{
				id: required('SPOTLIGHT_MODEL_ID'),
				name: required('SPOTLIGHT_MODEL_ID'),
				reasoning: false,
				input: ['text'],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow,
				maxTokens: maxOutputTokens(contextWindow),
			},
		],
	});
	const tier12b = (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') === '12b';
	pi.on('before_provider_request', (event, ctx) => {
		const payload = tier12b ? withoutHistoryReasoning(event.payload) : event.payload;
		const outcome = applyFixedRequestBody(payload, fixedBody);
		if (outcome.blocked) {
			log('provider privacy policy could not apply to this request; it was sent without content and the run is aborted');
			ctx.abort();
		}
		return outcome.payload;
	});
	// The launcher sets the local tier's fold point in Pi's settings; the
	// frontier tier keeps Pi's default threshold and summary.
	const caseDir = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR')).caseDir;
	const fetched = role !== ORCHESTRATOR ? registerFetchedPages(pi) : () => undefined;
	const remaining = role === 'fact-checker' ? () => nextCheckText(readCheckDocs(caseDir)) : undefined;
	registerCompaction(pi, fixedBody, process.env.SPOTLIGHT_MODEL_TIER !== 'frontier', process.env, role !== ORCHESTRATOR, (path) => existsSync(resolve(caseDir, path)), remaining, fetched);
	const guarded = registerLoopGuard(pi, tier12b && role !== ORCHESTRATOR ? `The ${role} ${LOOP_STOP}` : undefined);
	// Ending the orchestrator would end the case, so only workers are stopped.
	const streakGuarded = tier12b && role !== ORCHESTRATOR ? registerRefusalStreak(pi, `The ${role} ${REFUSAL_STOP}`) : (tool: ToolDefinition) => tool;
	if (role === ORCHESTRATOR) {
		registerReviewRefusalCap(pi);
		registerEndTurnOnOpen(pi);
		registerOrchestratorLoopBound(pi);
		registerBlockedStop(pi);
		const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
		const runAdapter = pythonAdapter(process.env.SPOTLIGHT_PYTHON || 'python3', resolverEnv(process.env, loadComposition()));
		registerUnproductiveBound(pi, async () => nextAction(await resolveCase(active, runAdapter), (relative) => existsSync(resolve(active.caseDir, relative))));
	}
	if (role === 'analyst') registerProposalEnd(pi);
	const factCheckDue = role === 'fact-checker' ? registerFactCheckDue(pi) : (tool: ToolDefinition) => tool;
	if (tier12b) {
		registerReadBudget(pi, bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR')).caseDir);
		registerCompletionFloor(pi);
		registerEmptyTurnNudge(pi);
		if (role === ORCHESTRATOR) {
			const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
			const runAdapter = pythonAdapter(process.env.SPOTLIGHT_PYTHON || 'python3', resolverEnv(process.env, loadComposition()));
			registerTextTurnNudge(pi, () => resolveCase(active, runAdapter));
		}
	}
	registerToolOwners(pi, role);
	if (process.env.SPOTLIGHT_CALL_BUDGET) registerCallBudget(pi, Number(process.env.SPOTLIGHT_CALL_BUDGET));
	const tools = toolsFor(role).map(guarded).map(streakGuarded).map(registerSkillDedup(pi)).map(registerReadDedup(pi)).map(factCheckDue);
	if (tier12b) registerHyphenNames(pi, new Set(tools.map((tool) => tool.name)));
	for (const tool of tools) pi.registerTool(tool);
	pi.on('session_start', () => pi.setActiveTools(tools.map((tool) => tool.name)));
}

// The 12b serves 40,960 tokens per slot, and Pi cannot fold a turn's own tool
// results away when they overflow it: its one compact-and-retry keeps them,
// because a tool result is never a cut point. So on the 12b all the read_file
// results of one turn together return at most 30,000 characters (tool-result
// text runs about 3.1 characters per Gemma token: about 9.6k tokens, which fits
// right after a fold), cut with read_file's own truncation notice. Pi runs a
// turn's tool calls in parallel, so the read that is cut is the one that
// finishes last, not necessarily the one called last.
const TURN_READ_CHARS_12B = 30_000;

// LFM3 on Osaurus (7513e421) wrote tool names with "-" for "_" ("invoke-skill",
// "query-vault"), and each call failed as a tool that does not exist. On the
// 12b a call whose name becomes a real tool by that one swap is renamed and
// logged; no other name is ever mapped.
function registerHyphenNames(pi: ExtensionAPI, names: Set<string>): void {
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string; name?: string }> };
		if (message.role !== 'assistant' || !Array.isArray(message.content)) return undefined;
		let renamed = false;
		const content = message.content.map((part) => {
			if (part.type !== 'toolCall' || !part.name || names.has(part.name)) return part;
			const real = part.name.replaceAll('-', '_');
			if (real === part.name || !names.has(real)) return part;
			log(`tool name ${part.name} read as ${real}`);
			renamed = true;
			return { ...part, name: real };
		});
		return renamed ? { message: { ...event.message, content } as typeof event.message } : undefined;
	});
}

// fc-stall scan (incr-test2 sp98): after a fold, fact-checker threads fetched
// the same Companies House pages again (3x). The pages a worker saved this run,
// by URL, for the text that comes back after a fold.
const FETCHED_SHOWN = 12;
export function registerFetchedPages(pi: ExtensionAPI): () => string | undefined {
	const saved = new Map<string, string>();
	const started = new Map<string, { url?: unknown; save_as?: unknown }>();
	pi.on('tool_execution_start', (event) => {
		if (event.toolName === 'fetch') started.set(event.toolCallId, (event.args ?? {}) as { url?: unknown; save_as?: unknown });
	});
	pi.on('tool_execution_end', (event) => {
		const args = started.get(event.toolCallId);
		started.delete(event.toolCallId);
		if (!args || event.isError || typeof args.url !== 'string' || typeof args.save_as !== 'string') return;
		saved.delete(args.url);
		saved.set(args.url, args.save_as);
	});
	return () => {
		if (saved.size === 0) return undefined;
		const pages = [...saved].slice(-FETCHED_SHOWN).map(([url, path]) => `${url} -> ${path}`);
		return `Already fetched in this task (read the saved file; do not fetch again): ${pages.join('; ')}.`;
	};
}

// Skill bodies share the budget (LFM3 e1 on Osaurus, 7513e421: a planning
// investigator loaded 11 skills in one turn, 99,184 characters; Pi cannot fold
// inside one turn's results, so the next request was 33,177 tokens with a
// 1-token completion budget). A skill is never cut: one that does not fit
// whole is not loaded, and the model can load it in a later turn.
export const skillNotLoaded = (name: string) =>
	`Not loaded: this turn's skills and reads already use the ${TURN_READ_CHARS_12B.toLocaleString('en-US')} characters one turn may load. Call invoke_skill for ${name} again in a later turn if you still need it.`;

// A skill loaded once stays in the conversation until a fold, and loading it
// again repeats its whole text (U4 candidate: repeated skill loads). Until the
// next fold a repeat call returns this line instead; a skill the read budget
// did not load counts as not loaded.
export const skillAlreadyLoaded = (name: string) => `${name} is already loaded above in this conversation; follow those instructions.`;

function registerSkillDedup(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	const loaded = new Set<string>();
	pi.on('session_compact', () => {
		loaded.clear();
	});
	const requested = new Map<string, unknown>();
	pi.on('tool_execution_start', (event) => {
		if (event.toolName === 'invoke_skill') requested.set(event.toolCallId, (event.args as { skill?: unknown } | undefined)?.skill);
	});
	pi.on('tool_execution_end', (event) => {
		const skill = requested.get(event.toolCallId);
		requested.delete(event.toolCallId);
		if (event.toolName !== 'invoke_skill' || event.isError || typeof skill !== 'string') return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		if (text !== '' && !text.startsWith('Not loaded:') && text !== skillAlreadyLoaded(skill)) loaded.add(skill);
	});
	return (tool) =>
		tool.name !== 'invoke_skill'
			? tool
			: {
					...tool,
					async execute(id, params, ...rest) {
						const skill = (params as { skill?: unknown }).skill;
						if (typeof skill === 'string' && loaded.has(skill)) return { content: [{ type: 'text', text: skillAlreadyLoaded(skill) }], details: undefined };
						return tool.execute(id, params, ...rest);
					},
				};
}

// U3 census (84674fd9): both models read the same file again and again (r5's
// orchestrator 61 to 167 times per case, base's investigator 56 to 129). A
// read of the same path and range whose text is unchanged since an earlier
// read in this conversation returns this line instead; a changed file, another
// range, a read the budget cut short, and any read after a fold return the text.
export const readAlreadyShown = (path: string) => `${path} is unchanged since you read it above in this conversation; use that text.`;

function registerReadDedup(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	// Read key (path and range) -> hash of the text it returned in full.
	const shown = new Map<string, string>();
	const pending = new Map<string, { key: string; hash: string }>();
	const readKey = (params: unknown) => {
		const { path, offset, limit, json_pointer } = params as { path?: unknown; offset?: unknown; limit?: unknown; json_pointer?: unknown };
		return JSON.stringify([path, offset ?? null, limit ?? null, json_pointer ?? null]);
	};
	const textOf = (result: { content?: Array<{ type: string; text?: string }> }) => (result.content ?? []).map((part) => (part.type === 'text' ? (part.text ?? '') : '')).join('');
	pi.on('session_compact', () => {
		shown.clear();
	});
	pi.on('tool_execution_end', (event) => {
		const read = pending.get(event.toolCallId);
		pending.delete(event.toolCallId);
		if (!read || event.isError) return;
		const text = textOf(event.result ?? {});
		if (!text.includes(READ_TRUNCATED) && !text.endsWith(' is unchanged since you read it above in this conversation; use that text.')) shown.set(read.key, read.hash);
	});
	return (tool) =>
		tool.name !== 'read_file'
			? tool
			: {
					...tool,
					async execute(id, params, ...rest) {
						const result = await tool.execute(id, params, ...rest);
						const key = readKey(params);
						const hash = createHash('sha256').update(textOf(result as { content?: Array<{ type: string; text?: string }> })).digest('hex');
						if (shown.get(key) === hash) return { content: [{ type: 'text', text: readAlreadyShown(String((params as { path?: unknown }).path)) }], details: undefined };
						pending.set(id, { key, hash });
						return result;
					},
				};
}

// The web tools' results share the same budget. r5 swiss on a4f47704: one
// 49,241-character http_get result right after a fold took an investigator's
// prompt from 19.4k to 38.8k tokens of the 40,960 window (about 2.3 characters
// per LFM3 token). A web result that does not fit is saved whole to a case
// file, and the model gets its head and the path.
const WEB_RESULTS = new Set(['http_get', 'fetch', 'browse']);
const WEB_HEAD_CHARS = 2_000;

function webResultSaved(head: string, length: number, path: string | undefined): string {
	return path
		? `${head}\n[This result is ${length} characters, more than this turn's reads leave room for. It is saved whole at ${path}: find what you need with grep_files and read only those lines with read_file offset/limit.]`
		: `${head}\n[This result is ${length} characters and was cut here; the rest could not be saved. Call it again with save_as to keep it whole.]`;
}

// A web tool's result against what is left of the turn's budget: undefined
// when the tool is not a web tool, otherwise the characters it uses and, when
// it does not fit, the text the model gets instead, after `save` stored it whole.
export function budgetedWebResult(tool: string, callId: string, text: string, left: number, save: (path: string, text: string) => string | undefined): { used: number; replacement?: string } | undefined {
	if (!WEB_RESULTS.has(tool)) return undefined;
	if (text.length <= left) return { used: text.length };
	const saved = save(`research/tool-results/${tool}-${callId.replace(/[^A-Za-z0-9_-]/g, '_')}.txt`, text);
	const head = text.slice(0, Math.min(WEB_HEAD_CHARS, left));
	return { used: head.length, replacement: webResultSaved(head, text.length, saved) };
}

function registerReadBudget(pi: ExtensionAPI, caseDir: string): void {
	let left = TURN_READ_CHARS_12B;
	pi.on('turn_start', () => {
		left = TURN_READ_CHARS_12B;
	});
	pi.on('tool_result', (event) => {
		if ((event.toolName !== 'read_file' && event.toolName !== 'invoke_skill' && !WEB_RESULTS.has(event.toolName)) || event.isError) return;
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('');
		const budgeted = budgetedWebResult(event.toolName, event.toolCallId, text, left, (path, whole) => {
			try {
				return writeConfined(caseDir, caseDir, path, whole);
			} catch (error) {
				log(`a ${event.toolName} result could not be saved: ${(error as Error).message}`);
				return undefined;
			}
		});
		if (budgeted) {
			left -= budgeted.used;
			return budgeted.replacement ? { content: [{ type: 'text' as const, text: budgeted.replacement }] } : undefined;
		}
		if (event.toolName === 'invoke_skill') {
			if (text.length <= left) {
				left -= text.length;
				return;
			}
			const name = String((event.input as { skill?: unknown }).skill ?? 'that skill');
			return { content: [{ type: 'text' as const, text: skillNotLoaded(name) }] };
		}
		const kept = Math.min(text.length, left);
		left -= kept;
		if (kept === text.length) return;
		return { content: [{ type: 'text' as const, text: `${text.slice(0, kept)}\n${READ_TRUNCATED}` }] };
	});
}

// Pi sizes each request's completion budget as the window minus its prompt
// estimate minus 4,096, and sends as little as 1 token (pi-ai
// clampMaxTokensToContext): the model can then only end its turn. Below this
// many tokens the 12b turn is stopped with an honest line instead; a fold
// cannot help, since Pi never cuts inside the turn that filled the window.
const MIN_COMPLETION_TOKENS = 512;
export const NO_ROOM_STOP = 'Spotlight stopped this turn because the conversation no longer leaves the model room to answer; nothing in it was used. The results before it stand.';

function registerCompletionFloor(pi: ExtensionAPI): void {
	let starved = false;
	pi.on('turn_start', () => {
		starved = false;
	});
	pi.on('before_provider_request', (event, ctx) => {
		const payload = event.payload as { max_completion_tokens?: unknown; max_tokens?: unknown } | undefined;
		const budget = payload?.max_completion_tokens ?? payload?.max_tokens;
		if (typeof budget !== 'number' || budget >= MIN_COMPLETION_TOKENS) return undefined;
		starved = true;
		log(`a turn with a ${budget}-token completion budget was stopped`);
		ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!starved || (event.message as { role?: string }).role !== 'assistant') return undefined;
		return { message: { ...event.message, content: [{ type: 'text', text: NO_ROOM_STOP }] } as typeof event.message };
	});
}

// The 12b can end a turn inside its own reasoning, with no text and no tool
// call (12B r3 A2 cc seq 4), and Pi then ends the run: the member sees nothing,
// and a worker's empty answer reaches the orchestrator as "(no output)". Such a
// turn gets this one follow-up, at most once per prompt. Every Spotlight process
// loads this extension, so workers get it too. The count resets on a user message
// other than the nudge, not on agent_start: a nudge sent after the run ended
// starts a new run, and resetting there could nudge forever.
export const EMPTY_TURN_NUDGE = 'Your last turn was empty. Call the next tool, or give your answer.';
// A turn that ran to the output cap with no tool call gets the same one follow-up.
export const RUNAWAY_TURN_NUDGE = 'Your last turn ran on without a tool call and was cut off. Call the next tool, or give your answer in a few sentences.';
const NUDGES = [EMPTY_TURN_NUDGE, RUNAWAY_TURN_NUDGE];

// Base 12B with no reasoning budget (step-eval, 2026-10-28) ran two turns to the
// 10,240-token cap: the orchestrator repeating "<channel|><|channel>thought"
// for 71,680 characters, a fact-checker cycling three sentences for 31,538.
// The guard stops a turn once the last TAIL_CHARS of its text or reasoning are
// one unit of at most MAX_PERIOD characters repeated exactly. Tool-call
// arguments are not watched: a long JSON write (r4arm cc:114, 34 KB) repeats its
// keys but never a whole 2,048-character stretch.
const TAIL_CHARS = 2048;
const MAX_PERIOD = 512;
const CHECK_EVERY = 256;
// base213 (Anomaly, 5ce09322): an investigator turn ran 10,240 tokens with no
// text, reasoning or tool call left after the server stripped its special
// tokens. Each streamed chunk is about one token, so this many chunks in a row
// with nothing visible stop the turn too. Prompt processing sends no chunks.
const INVISIBLE_CHUNKS = 1024;
// A server may send nothing at all for stripped tokens while the stream stays
// open, so a turn with nothing visible for this long after its first token
// chunk is stopped as well. The clock starts at the first chunk after the role
// chunk, so prompt processing (20-40 s for a 16k prompt on a Mac) never counts.
const INVISIBLE_MS = 60_000;
// A turn cut at the cap after this few tokens is a full context slot, not a
// ramble: a nudge would only overflow again.
const MIN_CUT_TOKENS = 10;

// Whether a raw OpenAI-style stream chunk carries anything the turn keeps.
function visibleChunk(data: unknown): boolean {
	const delta = (data as { choices?: Array<{ delta?: Record<string, unknown> }> } | undefined)?.choices?.[0]?.delta;
	if (!delta) return true;
	const text = (value: unknown) => typeof value === 'string' && value.length > 0;
	return text(delta.content) || text(delta.reasoning_content) || text(delta.reasoning) || (Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0);
}

export function repeatsItself(text: string): boolean {
	if (text.length < TAIL_CHARS + MAX_PERIOD) return false;
	const tail = text.slice(-(TAIL_CHARS + MAX_PERIOD));
	const window = tail.slice(-TAIL_CHARS);
	for (let period = 1; period <= MAX_PERIOD; period += 1) {
		if (tail.slice(-TAIL_CHARS - period, -period) === window) return true;
	}
	return false;
}

export const REPEAT_STOP = 'Spotlight stopped this turn because it ran on without calling a tool or writing anything usable; nothing in it was used. The results before it stand.';
// lfm3-r5 on uk-cpi (rc3, 52bf6183): the server streamed two complete tool
// calls, then held the stream open for 223 s. The stop dropped the calls and
// the run ended with no line for the member.
export const STALL_STOP = 'Spotlight stopped this turn because the model stopped responding. Ask Spotlight to continue.';

function registerEmptyTurnNudge(pi: ExtensionAPI): void {
	let nudged = false;
	let streamed = '';
	let unchecked = 0;
	let stopped = false;
	let stalled = false;
	let invisible = 0;
	let lastVisible = 0;
	let clock: ReturnType<typeof setInterval> | undefined;
	const stopClock = () => {
		if (clock) clearInterval(clock);
		clock = undefined;
	};
	const stopInvisible = (abort: () => void) => {
		stopClock();
		stopped = true;
		stalled = true;
		log('a turn streaming nothing visible was stopped');
		abort();
	};
	// Pi starts the assistant message after its first chunk, so each turn's
	// counters reset when the turn starts.
	pi.on('turn_start', () => {
		streamed = '';
		unchecked = 0;
		invisible = 0;
		stopped = false;
		stalled = false;
		stopClock();
	});
	pi.on('provider_stream_event', (event, ctx) => {
		if (stopped) return;
		const delta = (event.data as { choices?: Array<{ delta?: Record<string, unknown> }> } | undefined)?.choices?.[0]?.delta;
		if (delta && 'role' in delta && !visibleChunk(event.data)) return;
		if (visibleChunk(event.data)) {
			invisible = 0;
			lastVisible = Date.now();
		} else {
			invisible += 1;
		}
		if (!clock) {
			lastVisible = Date.now();
			clock = setInterval(() => {
				if (!stopped && Date.now() - lastVisible >= INVISIBLE_MS) stopInvisible(() => ctx.abort());
			}, 1000);
			clock.unref();
		}
		if (invisible >= INVISIBLE_CHUNKS) stopInvisible(() => ctx.abort());
	});
	pi.on('message_update', (event, ctx) => {
		const update = event.assistantMessageEvent;
		if (stopped || (update.type !== 'text_delta' && update.type !== 'thinking_delta')) return;
		streamed = (streamed + update.delta).slice(-(TAIL_CHARS + MAX_PERIOD));
		unchecked += update.delta.length;
		if (unchecked < CHECK_EVERY) return;
		unchecked = 0;
		if (!repeatsItself(streamed)) return;
		stopped = true;
		log('a turn repeating itself was stopped');
		ctx.abort();
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string; thinking?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (message.role === 'user') {
			if (!parts.some((part) => part.type === 'text' && NUDGES.includes(part.text ?? ''))) nudged = false;
			return;
		}
		if (message.role !== 'assistant') return undefined;
		stopClock();
		// Pi's abort ends the whole run and drops queued follow-ups, so a stopped
		// turn ends the run on an honest line instead of a nudge: the member sees
		// it, and a worker's line is its answer to the orchestrator. A turn stopped
		// after it streamed tool calls lost them in the abort.
		const calls = parts.some((part) => part.type === 'toolCall');
		if (stopped) return { message: { ...event.message, content: [{ type: 'text', text: stalled && calls ? STALL_STOP : REPEAT_STOP }] } as typeof event.message };
		if (calls) return undefined;
		const output = (event.message as { usage?: { output?: number } }).usage?.output;
		if (message.stopReason === 'length' && output && output < MIN_CUT_TOKENS) return undefined;
		const cut = message.stopReason === 'length';
		if (!cut && (message.stopReason !== 'stop' || parts.some((part) => part.type === 'text' && part.text?.trim()))) return undefined;
		if (nudged) return undefined;
		nudged = true;
		pi.sendUserMessage(cut ? RUNAWAY_TURN_NUDGE : EMPTY_TURN_NUDGE, { deliverAs: 'followUp' });
		return undefined;
	});
}

// The phases where the orchestrator does the next step itself and no gate waits
// for the member. Brief, Research and Ingest also hold conversations with the
// member, so a text turn there can be right.
const ACTING_PHASES = ['methodology', 'investigation', 'investigation_gate_finalization'];
export const textTurnNudge = (phase: string, owner: string) =>
	`Your turn ended with text, but the case is at ${phase} (skill: ${owner}) and no gate is waiting for the member, so the case moves only when you act. Take that phase's next step with your tools now; call spotlight_resolve if you are unsure what it is.`;

// r6 northwind on dd14e2e5: the orchestrator ended the run with a plain-text
// summary of the methodology after its gate steps were refused. A text turn
// with no tool call, in a phase only the orchestrator can move, gets one
// nudge; a second text turn in a row ends the run as before, so a model that
// is done is not held.
function registerTextTurnNudge(pi: ExtensionAPI, resolve: () => Promise<Record<string, unknown>>): void {
	let nudged = false;
	pi.on('message_end', async (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (message.role !== 'assistant') return;
		if (parts.some((part) => part.type === 'toolCall')) {
			nudged = false;
			return;
		}
		if (message.stopReason !== 'stop' || nudged || !parts.some((part) => part.type === 'text' && part.text?.trim())) return;
		let status: Record<string, unknown>;
		try {
			status = await resolve();
		} catch (error) {
			log(`text-turn check skipped: ${(error as Error).message}`);
			return;
		}
		const phase = String(status.next_phase ?? '');
		if (!ACTING_PHASES.includes(phase)) return;
		nudged = true;
		pi.sendUserMessage(textTurnNudge(phase, String(status.owner ?? '')), { deliverAs: 'followUp' });
	});
}

// A small model can repeat one call over and over (12B retune: the same
// list four times, the same read forty). A call that already ran twice in a
// row and got the same result both times is refused instead of run. Keying
// on the result keeps status calls such as spotlight_resolve free to repeat
// while the state they read changes, and each new prompt starts afresh, so a
// member's gate decision between two resolves never trips it. With `stop`
// (12b workers), the third refusal in a row ends the worker's run with that
// line instead (12B r3 A2 nordhaven: six refusals of one search in a row).
// A call whose arguments fail Pi's schema check counts the same way, though
// Pi answers it before the tool_call hook: its error is its result, it is
// refused before the check (prepareArguments runs first), and the third
// refusal ends the run when the turn ends (12B r5 fsf s0: one invalid
// source_expressions call nine times). Returns the wrapper for each tool.
function registerLoopGuard(pi: ExtensionAPI, stop?: string): (tool: ToolDefinition) => ToolDefinition {
	let lastCall = '';
	let lastResult = '';
	let repeats = 0;
	let refusals = 0;
	let lastInvalid = false;
	let stopping = false;
	const key = (toolName: string, input: unknown) => JSON.stringify([toolName, input]);
	const startedWith = new Map<string, string>();
	pi.on('agent_start', () => {
		lastCall = '';
		repeats = 0;
		refusals = 0;
		lastInvalid = false;
		stopping = false;
	});
	pi.on('tool_execution_start', (event) => {
		startedWith.set(event.toolCallId, key(event.toolName, event.args));
	});
	pi.on('tool_execution_end', (event) => {
		const call = startedWith.get(event.toolCallId);
		startedWith.delete(event.toolCallId);
		const result = JSON.stringify(event.result?.content);
		const text = event.result?.content?.[0]?.text ?? '';
		if (call === undefined || !event.isError || !text.startsWith('Validation failed for tool ')) return;
		if (call !== lastCall) {
			lastCall = call;
			lastResult = '';
			repeats = 0;
		}
		repeats = result === lastResult ? repeats + 1 : 1;
		lastResult = result;
		lastInvalid = true;
		refusals = 0;
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	pi.on('tool_call', (event) => {
		const call = key(event.toolName, event.input);
		if (call !== lastCall) {
			lastCall = call;
			lastResult = '';
			repeats = 0;
		}
		if (repeats < 2) {
			refusals = 0;
			return undefined;
		}
		refusals += 1;
		return stop && refusals >= 3 ? { block: true, reason: stop, terminate: true } : { block: true, reason: REPEAT_REASON };
	});
	pi.on('tool_result', (event) => {
		const result = JSON.stringify(event.content);
		if (key(event.toolName, event.input) !== lastCall || result.includes(REPEAT_REASON)) return;
		repeats = result === lastResult ? repeats + 1 : 1;
		lastResult = result;
		lastInvalid = false;
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (lastInvalid && repeats >= 2 && key(tool.name, args) === lastCall) {
				refusals += 1;
				const reason = stop && refusals >= 3 ? stop : REPEAT_REASON;
				stopping = reason === stop;
				throw new Error(reason);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

// The loop guard sees only one call repeated. r6 on dd14e2e5: workers were
// refused for varied calls with no end (cc investigator-12: 15 writes refused
// before activation; fsf fact-checker-45 and nordhaven investigator-7: about 9
// source_expressions refusals each). Once a worker's last REFUSAL_STREAK
// results were all errors, of any kind, its next call ends the run with
// `stop` when the turn ends. prepareArguments runs before Pi's schema check
// and the tool_call hook, so every next call meets it.
function registerRefusalStreak(pi: ExtensionAPI, stop: string): (tool: ToolDefinition) => ToolDefinition {
	let streak = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		streak = 0;
		stopping = false;
	});
	pi.on('tool_execution_end', (event) => {
		streak = event.isError ? streak + 1 : 0;
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (streak >= REFUSAL_STREAK) {
				stopping = true;
				throw new Error(stop);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

// One research direction's worker (directions.ts) has its share of the
// investigator's calls. Past it, only the calls that write its deliverable
// run, and CALL_BUDGET_GRACE calls later its run ends.
export const CALL_BUDGET_SPENT = "This direction's calls are spent. Write what you found to data/findings.json, data/evidence-bundle.json and data/investigation-log.json now, then finish.";
const CALL_BUDGET_GRACE = 10;
const BUDGET_WRITES = new Set(['write_file', 'edit_file', 'source_expressions']);
function registerCallBudget(pi: ExtensionAPI, budget: number): void {
	let calls = 0;
	pi.on('tool_call', (event) => {
		calls += 1;
		if (calls <= budget) return undefined;
		if (calls > budget + CALL_BUDGET_GRACE) return { block: true, reason: CALL_BUDGET_SPENT, terminate: true };
		return BUDGET_WRITES.has(event.toolName) ? undefined : { block: true, reason: CALL_BUDGET_SPENT };
	});
}

// LFM3 e2 kde stage 2 on Osaurus (03f008b0): the orchestrator rewrote an
// invalid report draft 23 times and open_review refused it 31 times. Writes
// came between the refusals, so no consecutive-refusal guard fired, and only
// the test's 30-minute cap ended it. On every tier the orchestrator's turn is
// stopped at the fifth refusal since the page last opened, before the next
// model request, and the member is told what failed. The count starts again
// after the stop, so a member who asks to try again gets five more attempts.
export const REVIEW_REFUSAL_CAP = 5;
const REVIEW_ERROR_LINES = 3;

// The stop line is the member's, so it names the problem in plain words and
// shows at most the validator's first three FAIL lines.
export function reviewRefusalStop(refusal: string): string {
	const report = refusal.slice(PAGE_NOT_OPENED.length).startsWith('data/report-draft.json ');
	const stop = report
		? 'Spotlight stopped this turn because the report draft was refused five times. The draft did not pass the report check, so nothing was rendered or recorded. Ask Spotlight to try again, or say what to change.'
		: 'Spotlight stopped this turn because the review page was refused five times. The case files it needed did not pass their check, so nothing was recorded. Ask Spotlight to try again, or say what to change.';
	const errors = refusal
		.split('\n')
		.filter((line) => line.startsWith('FAIL '))
		.slice(0, REVIEW_ERROR_LINES)
		.map((line) => line.replace(/^FAIL\s+/, ''));
	return errors.length > 0 ? `${stop}\nThe check found these problems.\n${errors.join('\n')}` : stop;
}

// LFM3 r3 kde stage 2 on Osaurus (03f008b0): the review page opened, and the
// orchestrator went on calling open_review in the same turn 16 times; the
// member and the test driver act only between turns. Every phase skill ends
// the turn at the page, so once it opens the harness ends the turn before the
// next model request and the member sees this line.
export const REVIEW_OPEN_STOP = 'Spotlight opened the review page in your browser and is waiting for your decision there. Say here when you are done.';

function registerEndTurnOnOpen(pi: ExtensionAPI): void {
	let opened = false;
	pi.on('agent_start', () => {
		opened = false;
	});
	pi.on('tool_execution_end', (event) => {
		if (event.toolName === 'open_review' && !event.isError && (event.result?.details as { opened?: unknown } | undefined)?.opened === true) opened = true;
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (opened) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!opened || (event.message as { role?: string }).role !== 'assistant') return undefined;
		opened = false;
		return { message: { ...event.message, content: [{ type: 'text', text: REVIEW_OPEN_STOP }] } as typeof event.message };
	});
}

// LFM3 r3 kde stage 3 on Osaurus (03f008b0): the analyst never ended its turn,
// so the builder the harness runs when it finishes never ran. Its one output is
// data/knowledge-proposal.json, refused when it fails interface 1, and then a
// short final answer (agents/analyst.md). Once a proposal is written, a text
// answer ends the run as usual; a message with more tool calls is replaced by
// this answer, with no calls, so the run ends and the builder runs.
export const PROPOSAL_DONE = 'The analyst wrote data/knowledge-proposal.json and was stopped there; its final answer and any watch candidates were not written.';

function registerProposalEnd(pi: ExtensionAPI): void {
	let written = false;
	pi.on('agent_start', () => {
		written = false;
	});
	pi.on('tool_execution_end', (event) => {
		if ((event.toolName !== 'write_file' && event.toolName !== 'edit_file') || event.isError) return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		if (/^(wrote|edited) (?:.*[\\/])?data[\\/]knowledge-proposal\.json$/.test(text.trim())) written = true;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (!written || message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		log('the analyst went on after writing a valid proposal; its turn was ended');
		return { message: { ...event.message, content: [{ type: 'text', text: PROPOSAL_DONE }] } as typeof event.message };
	});
}

// U3 census, r5 on swiss (84674fd9): the orchestrator made one osint_tools
// call 286 times in one turn and got the loop guard's notice 273 times. The
// guard stops only workers (ending the orchestrator would end the case), so
// nothing bounded it. At the fifth notice in one run the orchestrator's turn
// ends, not the case, before the next request, and the member sees this line.
export const LOOP_NOTICE_CAP = 5;
export const ORCHESTRATOR_LOOP_STOP = 'Spotlight stopped this turn because it kept repeating the same step without getting anything new. Ask Spotlight to continue, or say what to change.';

// U3 census stage 1 (0af4585b): told to spawn the fact-checker, the r5
// orchestrator read and listed case files for 60 minutes, 197 to 280 calls,
// each returning nothing new (not found, no matching files, unchanged), with
// pairs varied enough that the loop guard never fired. Such results are
// counted per run: at the fifth the next one carries the step the case is
// waiting on, and at the fifteenth the turn ends with the loop stop line.
export const UNPRODUCTIVE_NEXT = 5;
export const UNPRODUCTIVE_STOP = 15;
const unproductive = (text: string) => text.startsWith('not found: ') || text === 'no matching files' || text.endsWith(' is unchanged since you read it above in this conversation; use that text.');

function registerUnproductiveBound(pi: ExtensionAPI, next: () => Promise<string | undefined>): void {
	let count = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		count = 0;
		stopping = false;
	});
	pi.on('tool_result', async (event) => {
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('');
		if (!unproductive(text)) return undefined;
		count += 1;
		if (count >= UNPRODUCTIVE_STOP && !stopping) {
			stopping = true;
			log(`the orchestrator got ${UNPRODUCTIVE_STOP} results with nothing new; its turn was stopped`);
		}
		if (count !== UNPRODUCTIVE_NEXT) return undefined;
		const step = await next();
		return step ? { content: [{ type: 'text' as const, text: `${text}\n${step}` }] } : undefined;
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stopping) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!stopping || (event.message as { role?: string }).role !== 'assistant') return undefined;
		stopping = false;
		count = 0;
		return { message: { ...event.message, content: [{ type: 'text', text: ORCHESTRATOR_LOOP_STOP }] } as typeof event.message };
	});
}

function registerOrchestratorLoopBound(pi: ExtensionAPI): void {
	let notices = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		notices = 0;
		stopping = false;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string; text?: string }> };
		if (message.role === 'toolResult') {
			if (message.content?.some((part) => part.type === 'text' && part.text?.includes(REPEAT_REASON)) && ++notices >= LOOP_NOTICE_CAP && !stopping) {
				stopping = true;
				log(`the orchestrator got ${LOOP_NOTICE_CAP} loop notices; its turn was stopped`);
			}
			return undefined;
		}
		if (!stopping || message.role !== 'assistant') return undefined;
		stopping = false;
		notices = 0;
		return { message: { ...event.message, content: [{ type: 'text', text: ORCHESTRATOR_LOOP_STOP }] } as typeof event.message };
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stopping) ctx.abort();
		return undefined;
	});
}

// U3 census, r5 on uk-cpi (84674fd9): the case reached `blocked` (the cycle
// cap spent) and the orchestrator went on to about 300 calls. At `blocked`
// phase-investigation says STOP: present the gaps and wait for a revised
// methodology. Once a resolve or transition returns it, a text answer ends
// the turn as usual; a message with more tool calls is replaced by this line,
// with no calls, so the turn ends. The member's next message starts afresh.
export const blockedStop = (open: number) =>
	`Spotlight stopped the investigation because it has tried every step it is allowed and ${open > 1 ? 'some questions are' : 'one question is'} still open. To continue, change the methodology and approve it again.`;

function registerBlockedStop(pi: ExtensionAPI): void {
	let blocked = false;
	let open = 1;
	pi.on('agent_start', () => {
		blocked = false;
	});
	pi.on('tool_execution_end', (event) => {
		if ((event.toolName !== 'spotlight_resolve' && event.toolName !== 'spotlight_transition') || event.isError) return;
		try {
			const status = JSON.parse(event.result?.content?.[0]?.text ?? '') as { status?: unknown; blocked?: { gaps_report?: unknown } };
			if (status.status !== 'blocked') return;
			blocked = true;
			const gaps = status.blocked?.gaps_report;
			open = Array.isArray(gaps) && gaps.length > 1 ? gaps.length : 1;
		} catch {
			// Not a resolved state.
		}
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (!blocked || message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		log('the case is blocked and the orchestrator went on; its turn was ended');
		return { message: { ...event.message, content: [{ type: 'text', text: blockedStop(open) }] } as typeof event.message };
	});
}

// DeepSeek V4.1 Flash on wikimedia stage 0 (704dcc78): the fact-checker
// chased a real address conflict for 501 calls through three research rounds
// and never wrote data/fact-check.json; its calls kept producing something,
// so no other bound tripped. From FACT_CHECK_DUE calls without a write of the
// file, the first result past the count carries this notice, and again every
// FACT_CHECK_DUE_REPEAT calls (a parallel batch can step over the exact count,
// and a fold drops the notice); at FACT_CHECK_STOP the run stops
// as its answer, and the orchestrator re-spawns for the missing file (#245).
export const FACT_CHECK_DUE = 150;
export const FACT_CHECK_STOP = 250;
export const FACT_CHECK_DUE_REPEAT = 50;
export const FACT_CHECK_DUE_NOTICE = 'Write data/fact-check.json now with what you have. A claim whose sources conflict gets the verdict disputed or unverified, with the conflict named in its grounding_assessment.assessment.';

function registerFactCheckDue(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	let calls = 0;
	let notice = FACT_CHECK_DUE;
	let stopping = false;
	const started = new Map<string, unknown>();
	pi.on('agent_start', () => {
		calls = 0;
		notice = FACT_CHECK_DUE;
		stopping = false;
	});
	pi.on('tool_execution_start', (event) => {
		started.set(event.toolCallId, (event.args as { path?: unknown } | undefined)?.path);
	});
	pi.on('tool_execution_end', (event) => {
		const path = started.get(event.toolCallId);
		started.delete(event.toolCallId);
		calls += 1;
		if (!event.isError && (event.toolName === 'write_file' || event.toolName === 'edit_file') && typeof path === 'string' && path.replace(/^\{CASE_DIR\}[\\/]/, '') === 'data/fact-check.json') {
			calls = 0;
			notice = FACT_CHECK_DUE;
		}
	});
	pi.on('tool_result', (event) => {
		if (calls + 1 < notice) return undefined;
		notice = calls + 1 + FACT_CHECK_DUE_REPEAT;
		return { content: [...event.content, { type: 'text' as const, text: `\n${FACT_CHECK_DUE_NOTICE}` }] };
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (calls >= FACT_CHECK_STOP) {
				stopping = true;
				throw new Error(`The fact-checker ${FACT_CHECK_DUE_STOP}`);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

function registerReviewRefusalCap(pi: ExtensionAPI): void {
	let refusals = 0;
	let stop: string | undefined;
	pi.on('tool_execution_end', (event) => {
		if (event.toolName !== 'open_review') return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		if (!event.isError) refusals = 0;
		else if (text.startsWith(PAGE_NOT_OPENED) && ++refusals >= REVIEW_REFUSAL_CAP) {
			refusals = 0;
			stop = reviewRefusalStop(text);
			log(`open_review was refused ${REVIEW_REFUSAL_CAP} times; the turn was stopped`);
		}
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stop) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!stop || (event.message as { role?: string }).role !== 'assistant') return undefined;
		const text = stop;
		stop = undefined;
		return { message: { ...event.message, content: [{ type: 'text', text }] } as typeof event.message };
	});
}

// Pi answers a call to a tool the role does not have with "Tool X not found".
// When another Spotlight role owns X, say which, so the model takes the next
// step instead of retrying (gold run 1: the orchestrator called
// source_expressions twice).
function registerToolOwners(pi: ExtensionAPI, role: string): void {
	const composition = loadComposition();
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; isError?: boolean; content?: Array<{ type: string; text?: string }> };
		if (message.role !== 'toolResult' || !message.isError) return undefined;
		const name = /^Tool (\S+) not found$/.exec(message.content?.[0]?.text ?? '')?.[1];
		const owners = Object.entries(composition)
			.filter(([, spec]) => spec.tools.includes(name ?? ''))
			.map(([owner]) => owner);
		if (!name || owners.length === 0) return undefined;
		const next = role === ORCHESTRATOR ? 'Delegate that step with spawn_agent.' : 'Leave that step to them and say so in your answer.';
		return { message: { ...event.message, content: [{ type: 'text', text: `${name} is not one of your tools; it belongs to the ${owners.join(' and the ')}. ${next}` }] } as typeof event.message };
	});
}
