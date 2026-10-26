// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { registerCompaction } from './checkpoint.ts';
import { maxOutputTokens, resolverEnv } from './config.ts';
import { applyFixedRequestBody, parseFixedRequestBody } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, loadComposition, skillsFor, spawnableWorkers } from './roles.ts';
import { typedTools } from './tools/index.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { INDICATOR_FRAMEWORK_ROLES } from './tools/hosted-skill.ts';
import type { ModelTier } from './config.ts';
import { bindActiveCase, pythonAdapter, spotlightTools } from './tools/spotlight.ts';
import { requestEditorial, requestResearchRound, spawnAgentTool } from './subagents.ts';
import { editorialTools } from './tools/editorial.ts';
import { openReviewTool, reportDraftCheck } from './tools/review.ts';
import { REPEAT_REASON } from './tools/run.ts';
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
		? { caseDir: active.caseDir, dataDir: required('SPOTLIGHT_HARNESS_DATA'), env: process.env, tier: (process.env.SPOTLIGHT_MODEL_TIER || '12b') as ModelTier, sensitive }
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
		tools.push(...spotlightTools(active, runAdapter, role, requestResearchRound, () => evidenceValidates(context)));
		tools.push(spawnAgentTool(spawnableWorkers(composition)));
		tools.push(openReviewTool(active.caseDir, required('SPOTLIGHT_CASES_ROOT'), undefined, reportDraftCheck(active, runAdapter, context)));
	}
	const allowlist = new Set(allowedTools(spec, sensitive, edition));
	return tools.filter((tool) => allowlist.has(tool.name));
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
	pi.on('before_provider_request', (event, ctx) => {
		const outcome = applyFixedRequestBody(event.payload, fixedBody);
		if (outcome.blocked) {
			log('provider privacy policy could not apply to this request; it was sent without content and the run is aborted');
			ctx.abort();
		}
		return outcome.payload;
	});
	// The launcher sets the local tier's fold point in Pi's settings; the
	// frontier tier keeps Pi's default threshold and summary.
	registerCompaction(pi, fixedBody, process.env.SPOTLIGHT_MODEL_TIER !== 'frontier');
	registerLoopGuard(pi);
	registerToolOwners(pi, role);
	const tools = toolsFor(role);
	for (const tool of tools) pi.registerTool(tool);
	pi.on('session_start', () => pi.setActiveTools(tools.map((tool) => tool.name)));
}

// A small model can repeat one call over and over (12B retune: the same
// list four times, the same read forty). A call that already ran twice in a
// row and got the same result both times is refused instead of run. Keying
// on the result keeps status calls such as spotlight_resolve free to repeat
// while the state they read changes, and each new prompt starts afresh, so a
// member's gate decision between two resolves never trips it.
function registerLoopGuard(pi: ExtensionAPI): void {
	let lastCall = '';
	let lastResult = '';
	let repeats = 0;
	const key = (toolName: string, input: unknown) => JSON.stringify([toolName, input]);
	pi.on('agent_start', () => {
		lastCall = '';
		repeats = 0;
	});
	pi.on('tool_call', (event) => {
		const call = key(event.toolName, event.input);
		if (call !== lastCall) {
			lastCall = call;
			lastResult = '';
			repeats = 0;
		}
		return repeats >= 2 ? { block: true, reason: REPEAT_REASON } : undefined;
	});
	pi.on('tool_result', (event) => {
		const result = JSON.stringify(event.content);
		if (key(event.toolName, event.input) !== lastCall || result.includes(REPEAT_REASON)) return;
		repeats = result === lastResult ? repeats + 1 : 1;
		lastResult = result;
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
