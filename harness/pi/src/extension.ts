// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { registerCompaction } from './checkpoint.ts';
import { applyFixedRequestBody, parseFixedRequestBody } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, loadComposition, skillsFor, spawnableWorkers } from './roles.ts';
import { typedTools } from './tools/index.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { bindActiveCase, pythonAdapter, spotlightTools } from './tools/spotlight.ts';
import { requestEditorial, requestResearchRound, spawnAgentTool } from './subagents.ts';
import { editorialTools } from './tools/editorial.ts';
import { openReviewTool } from './tools/review.ts';

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
	const tools: ToolDefinition[] = [
		invokeSkillTool(skillsFor(composition, role)),
		...typedTools({
			caseDir: active.caseDir,
			writeRoot: active.caseDir,
			python,
			configPath: process.env.SPOTLIGHT_CONFIG || `${CHECKOUT_ROOT}/.spotlight-config.json`,
			env: process.env,
			scope: { reads: spec.reads, writes: spec.writes, denyReads: spec.denyReads },
		}),
	];
	const edition = process.env.SPOTLIGHT_EDITION === 'newsroom' ? 'newsroom' : 'solo';
	// Newsroom edition: the Editorial MCP tools, served by the launcher. The
	// allowlist below keeps only the ones this role's swaps grant.
	if (edition === 'newsroom') tools.push(...editorialTools(requestEditorial));
	if (role === ORCHESTRATOR) {
		tools.push(...spotlightTools(active, pythonAdapter(python), role, requestResearchRound));
		tools.push(spawnAgentTool(spawnableWorkers(composition)));
		tools.push(openReviewTool(active.caseDir, required('SPOTLIGHT_CASES_ROOT')));
	}
	const allowlist = new Set(allowedTools(spec, process.env.SPOTLIGHT_SENSITIVE === 'true', edition));
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
				contextWindow: Number(process.env.SPOTLIGHT_MODEL_CONTEXT_WINDOW || 32768),
				maxTokens: 8192,
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
	const tools = toolsFor(role);
	for (const tool of tools) pi.registerTool(tool);
	pi.on('session_start', () => pi.setActiveTools(tools.map((tool) => tool.name)));
}
