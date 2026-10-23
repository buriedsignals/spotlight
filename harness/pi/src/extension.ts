// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { applyFixedRequestBody, parseFixedRequestBody } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { ORCHESTRATOR, agentSpec, loadComposition, skillsFor, spawnableWorkers } from './roles.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { bindActiveCase, pythonAdapter, spotlightTools } from './tools/spotlight.ts';
import { spawnAgentTool } from './subagents.ts';

const log = (message: string) => process.stderr.write(`spotlight-extension: ${message}\n`);

function required(name: string): string {
	const value = process.env[name];
	if (!value) throw new Error(`Spotlight extension: ${name} is not set (start Spotlight through its launcher)`);
	return value;
}

function toolsFor(role: string): ToolDefinition[] {
	const composition = loadComposition();
	const tools: ToolDefinition[] = [invokeSkillTool(skillsFor(composition, role))];
	if (role === ORCHESTRATOR) {
		const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
		tools.push(...spotlightTools(active, pythonAdapter(process.env.SPOTLIGHT_PYTHON || 'python3')));
		tools.push(spawnAgentTool(spawnableWorkers(composition)));
	}
	const allowlist = new Set(agentSpec(composition, role).tools);
	return tools.filter((tool) => allowlist.has(tool.name));
}

export default function spotlightExtension(pi: ExtensionAPI): void {
	const role = required('SPOTLIGHT_ROLE');
	if (process.env.SPOTLIGHT_MATERIAL === 'restricted' && process.env.SPOTLIGHT_ROUTE_INFERENCE !== 'own') {
		// The launcher never starts this combination; refuse here too.
		log('restricted material on a route the operator does not run; refusing to start');
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
	const tools = toolsFor(role);
	for (const tool of tools) pi.registerTool(tool);
	pi.on('session_start', () => pi.setActiveTools(tools.map((tool) => tool.name)));
}
