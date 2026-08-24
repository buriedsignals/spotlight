import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import {
	mkdir,
	mkdtemp,
	readFile,
	rm,
	symlink,
	writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import spotlightAgent from '../agents/spotlight.ts';
import { HARNESS_ROOT } from './roles.ts';
import { createSpotlightTools } from './spotlight-tools.ts';

type RegisteredTool = {
	name: string;
	run(context: { input: Record<string, unknown>; signal?: AbortSignal }): unknown | Promise<unknown>;
};

type AgentConfig = {
	tools?: unknown[];
	instructions?: string;
};

const FIXED_APPROVAL = {
	gate: 'methodology',
	approvedBy: 'journalist:fixture',
	approvedAt: '2026-08-23T12:00:00Z',
};

async function makeCasePath(t: TestContext) {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-flue-tools-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	return { caseDir: join(root, 'offline-case'), root };
}

async function makeCase(t: TestContext) {
	const fixture = await makeCasePath(t);
	await mkdir(join(fixture.caseDir, 'data'), { recursive: true });
	return fixture;
}

function runPythonAdapter(arguments_: string[], signal?: AbortSignal): Promise<string> {
	const adapter = join(HARNESS_ROOT, 'scripts', 'spotlight-orchestration.py');
	const python = process.env.SPOTLIGHT_PYTHON ?? process.env.PYTHON ?? 'python3';
	const { promise, resolve, reject } = Promise.withResolvers<string>();
	execFile(python, [adapter, ...arguments_], { encoding: 'utf8', signal }, (error, stdout, stderr) => {
		if (error) {
			reject(new Error(stderr.trim() || stdout.trim() || error.message));
			return;
		}
		resolve(stdout);
	});
	return promise;
}

async function initializeAgent(activeCaseDir: string, casesRoot: string): Promise<AgentConfig> {
	const priorActiveCase = process.env.SPOTLIGHT_ACTIVE_CASE;
	const priorCasesRoot = process.env.SPOTLIGHT_CASES_ROOT;
	process.env.SPOTLIGHT_ACTIVE_CASE = activeCaseDir;
	process.env.SPOTLIGHT_CASES_ROOT = casesRoot;
	try {
		return (await spotlightAgent.initialize({
			id: 'spotlight-tools-test',
			env: {},
		})) as AgentConfig;
	} finally {
		if (priorActiveCase === undefined) delete process.env.SPOTLIGHT_ACTIVE_CASE;
		else process.env.SPOTLIGHT_ACTIVE_CASE = priorActiveCase;
		if (priorCasesRoot === undefined) delete process.env.SPOTLIGHT_CASES_ROOT;
		else process.env.SPOTLIGHT_CASES_ROOT = priorCasesRoot;
	}
}

function registeredTool(config: AgentConfig, name: string): RegisteredTool {
	const tool = (config.tools as RegisteredTool[]).find((candidate) => candidate.name === name);
	assert.ok(tool, `Spotlight agent must register ${name}`);
	return tool;
}

test('Spotlight agent registers an executable resolver backed by the product module', async (t) => {
	const { caseDir, root } = await makeCase(t);
	const config = await initializeAgent(caseDir, root);
	const resolve = registeredTool(config, 'spotlight_resolve');

	const resolution = (await resolve.run({ input: { caseDir } })) as Record<string, unknown>;

	assert.equal(resolution.phase, 'brief');
	assert.equal(resolution.status, 'pending');
	assert.equal(resolution.owner, 'phase-methodology');
	assert.ok(Array.isArray(resolution.missing));
	assert.equal(typeof resolution.attempts, 'object');
	assert.ok('resume' in resolution);
});

test('fresh launcher slug is bootstrapped before the first registered resolve', async (t) => {
	const { caseDir, root } = await makeCasePath(t);
	const config = await initializeAgent(caseDir, root);
	const resolve = registeredTool(config, 'spotlight_resolve');

	const resolution = (await resolve.run({ input: {} })) as Record<string, unknown>;

	assert.equal(resolution.phase, 'brief');
	assert.equal(resolution.owner, 'phase-methodology');
});

test('pre-existing symlinked active case is rejected before registration', async (t) => {
	const { caseDir, root } = await makeCasePath(t);
	const realCase = join(root, 'real-case');
	await mkdir(join(realCase, 'data'), { recursive: true });
	await symlink(realCase, caseDir, 'dir');

	assert.throws(() => createSpotlightTools({ activeCaseDir: caseDir, casesRoot: root }));
});

test('Spotlight agent transition tool changes state through the product module', async (t) => {
	const { caseDir, root } = await makeCase(t);
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Verify the offline fixture.\n');
	await writeFile(join(caseDir, 'data', 'methodology.json'), '{}\n');
	const config = await initializeAgent(caseDir, root);
	const transition = registeredTool(config, 'spotlight_transition');
	const resolve = registeredTool(config, 'spotlight_resolve');

	await transition.run({
		input: {
			operation: 'approve',
			caseDir,
			payload: FIXED_APPROVAL,
		},
	});
	const resolution = (await resolve.run({ input: { caseDir } })) as Record<string, unknown>;

	assert.equal(resolution.phase, 'execution');
});

test('registered tools are a capability for the active case, not a model-selected sibling', async (t) => {
	const { caseDir, root } = await makeCase(t);
	const sibling = join(root, 'sibling-case');
	await mkdir(join(sibling, 'data'), { recursive: true });
	await writeFile(join(sibling, 'brief-directions.txt'), 'Sibling brief.\n');
	await writeFile(join(sibling, 'data', 'methodology.json'), '{}\n');
	const config = await initializeAgent(caseDir, root);
	const resolve = registeredTool(config, 'spotlight_resolve');

	const resolution = (await resolve.run({ input: { caseDir: sibling } })) as Record<string, unknown>;

	assert.equal(resolution.phase, 'brief');
});

test('registered transitions cannot mutate a model-selected sibling case', async (t) => {
	const { caseDir, root } = await makeCase(t);
	const sibling = join(root, 'sibling-case');
	await mkdir(join(sibling, 'data'), { recursive: true });
	for (const directory of [caseDir, sibling]) {
		await writeFile(join(directory, 'brief-directions.txt'), 'Fixture brief.\n');
		await writeFile(join(directory, 'data', 'methodology.json'), '{}\n');
	}
	const config = await initializeAgent(caseDir, root);
	const transition = registeredTool(config, 'spotlight_transition');
	const resolve = registeredTool(config, 'spotlight_resolve');

	await transition.run({
		input: {
			operation: 'approve',
			caseDir: sibling,
			payload: FIXED_APPROVAL,
		},
	});
	const resolution = (await resolve.run({ input: { caseDir } })) as Record<string, unknown>;

	assert.equal(resolution.phase, 'execution');
	await assert.rejects(readFile(join(sibling, 'data', 'orchestration.json')));
});

test('configured active case outside the cases root is rejected', async (t) => {
	const { root } = await makeCasePath(t);
	const outside = await mkdtemp(join(tmpdir(), 'spotlight-flue-outside-'));
	t.after(() => rm(outside, { recursive: true, force: true }));
	await mkdir(join(outside, 'data'));

	assert.throws(() => createSpotlightTools({ activeCaseDir: outside, casesRoot: root }));
});

test('real Flue instructions defer all phases through resolver operations', async (t) => {
	const { caseDir, root } = await makeCase(t);
	const config = await initializeAgent(caseDir, root);
	const instructions = String(config.instructions);

	assert.match(instructions, /spotlight_resolve/);
	assert.match(instructions, /Report.*Ingest/s);
	assert.doesNotMatch(instructions, /finalize-report\.py/);
	assert.match(instructions, /phase-preflight.*spotlight_resolve/s);
});

test('an in-flight transition reaches one durable outcome after caller abort', async (t) => {
	assert.equal(typeof createSpotlightTools, 'function');
	const { caseDir, root } = await makeCase(t);
	const started = Promise.withResolvers<void>();
	const release = Promise.withResolvers<void>();
	let invocation = 0;
	const runAdapter = async (_arguments: string[], signal?: AbortSignal): Promise<string> => {
		invocation += 1;
		if (invocation === 1) {
			started.resolve();
			const aborted = new Promise<never>((_resolve, reject) => {
				signal?.addEventListener('abort', () => reject(new Error('adapter orphaned')), {
					once: true,
				});
			});
			await Promise.race([release.promise, aborted]);
			return '';
		}
		return JSON.stringify({
			phase: 'execution',
			status: 'active',
			owner: 'phase-execution',
			missing: [],
			attempts: {},
			resume: {},
		});
	};
	const tools = createSpotlightTools({
		activeCaseDir: caseDir,
		casesRoot: root,
		runAdapter,
	});
	const transition = tools.find((tool: RegisteredTool) => tool.name === 'spotlight_transition');
	assert.ok(transition);
	const controller = new AbortController();

	const pending = transition.run({
		input: { operation: 'approve', payload: FIXED_APPROVAL },
		signal: controller.signal,
	});
	await started.promise;
	controller.abort();
	release.resolve();

	const resolution = (await pending) as Record<string, unknown>;
	assert.equal(resolution.phase, 'execution');
});
