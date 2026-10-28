// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { registerCompaction } from './checkpoint.ts';
import { maxOutputTokens, resolverEnv } from './config.ts';
import { applyFixedRequestBody, parseFixedRequestBody, withoutHistoryReasoning } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, loadComposition, skillsFor, spawnableWorkers } from './roles.ts';
import { READ_TRUNCATED } from './tools/case-files.ts';
import { typedTools } from './tools/index.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { INDICATOR_FRAMEWORK_ROLES, approvedIndicatorFramework } from './tools/hosted-skill.ts';
import { recordIndicatorFrameworkTool } from './tools/indicator-framework.ts';
import { bindActiveCase, pythonAdapter, spotlightTools } from './tools/spotlight.ts';
import { requestEditorial, requestResearchRound, spawnAgentTool } from './subagents.ts';
import { editorialTools } from './tools/editorial.ts';
import { openReviewTool, gateCheck } from './tools/review.ts';
import { LOOP_STOP, REPEAT_REASON } from './tools/run.ts';
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
		invokeSkillTool(skillsFor(composition, role), hosted),
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
		tools.push(openReviewTool(active.caseDir, required('SPOTLIGHT_CASES_ROOT'), undefined, gateCheck(active, runAdapter, context)));
	}
	const allowlist = new Set(allowedTools(spec, sensitive, edition));
	const allowed = tools.filter((tool) => allowlist.has(tool.name));
	// Only a case whose approved methodology turns the framework on gets the
	// tool, so every other case keeps the same tool list.
	if (hosted && approvedIndicatorFramework(active.caseDir)) allowed.push(recordIndicatorFrameworkTool(hosted, context.writeRoot));
	return allowed;
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
	registerCompaction(pi, fixedBody, process.env.SPOTLIGHT_MODEL_TIER !== 'frontier');
	const guarded = registerLoopGuard(pi, tier12b && role !== ORCHESTRATOR ? `The ${role} ${LOOP_STOP}` : undefined);
	if (tier12b) {
		registerReadBudget(pi);
		registerEmptyTurnNudge(pi);
	}
	registerToolOwners(pi, role);
	const tools = toolsFor(role).map(guarded);
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

function registerReadBudget(pi: ExtensionAPI): void {
	let left = TURN_READ_CHARS_12B;
	pi.on('turn_start', () => {
		left = TURN_READ_CHARS_12B;
	});
	pi.on('tool_result', (event) => {
		if (event.toolName !== 'read_file' || event.isError) return;
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('');
		const kept = Math.min(text.length, left);
		left -= kept;
		if (kept === text.length) return;
		return { content: [{ type: 'text' as const, text: `${text.slice(0, kept)}\n${READ_TRUNCATED}` }] };
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

function registerEmptyTurnNudge(pi: ExtensionAPI): void {
	let nudged = false;
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (message.role === 'user') {
			if (!parts.some((part) => part.type === 'text' && part.text === EMPTY_TURN_NUDGE)) nudged = false;
			return;
		}
		if (message.role !== 'assistant' || message.stopReason !== 'stop' || nudged) return;
		if (parts.some((part) => part.type === 'toolCall' || (part.type === 'text' && part.text?.trim()))) return;
		nudged = true;
		pi.sendUserMessage(EMPTY_TURN_NUDGE, { deliverAs: 'followUp' });
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
