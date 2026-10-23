// End-to-end tests of the real launcher and real pi processes against a
// scripted OpenAI-compatible server on 127.0.0.1. No network, no model.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { isWorkerRequest, startFakeOpenAI, toolNames, toolResults } from './test-support/fake-openai.ts';
import type { ChatRequest, Reply } from './test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, waitUntil, workspace } from './test-support/launch.ts';
import type { Chat, Workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT, loadComposition } from './roles.ts';

const FORBIDDEN_TOOL = /^(bash|read|edit|write|ls|find|grep|powershell|codemode|tool_search)$|^mcp__/;

function isAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch {
		return false;
	}
}

async function lockRecord(space: Workspace, caseId = 'case-a') {
	return JSON.parse(await readFile(join(space.dataDir, 'locks', `${caseId}.lock`), 'utf8')) as {
		pid: number;
		children: number[];
	};
}

// The orchestrator resolves on its first turn; later orchestrator turns end.
function resolveThenStop(request: ChatRequest): Reply {
	return toolResults(request).length === 0
		? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] }
		: { text: 'Waiting at the gate.' };
}

// The last spotlight_resolve result the chat reported on stdout.
function lastResolution(chat: Chat): Record<string, unknown> {
	const ends = chat.lines
		.map((line) => JSON.parse(line) as { type?: string; toolName?: string; result?: { content: Array<{ text: string }> } })
		.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'spotlight_resolve');
	assert.ok(ends.length > 0, 'the chat resolved the case');
	return JSON.parse(ends[ends.length - 1].result!.content[0].text) as Record<string, unknown>;
}

test('stdout carries only RPC records for a scripted prompt', async (t) => {
	const fake = await startFakeOpenAI(resolveThenStop);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Open the case.');
	chat.process.stdin.end();
	const { code } = await chat.exited;

	assert.equal(code, 0);
	assert.ok(chat.lines.length > 3);
	for (const line of chat.lines) {
		const record = JSON.parse(line) as Record<string, unknown>;
		assert.equal(typeof record.type, 'string', line);
	}
	assert.ok(chat.lines.some((line) => line.includes('"type":"tool_execution_end"')));
});

test('each process declares exactly its allowlist, workers cannot delegate, and every request carries the fixed privacy fields', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Investigator finished.' };
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Plan the case.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, {
		...baseEnv(space, fake.baseURL),
		SPOTLIGHT_PROVIDER_REQUEST_BODY: '{"provider":{"zdr":true}}',
	});

	await settledPrompt(chat, 'Start.');

	const composition = loadComposition();
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	assert.equal(worker.length, 1);
	assert.ok(orchestrator.length >= 2);
	assert.deepEqual(toolNames(worker[0]), [...composition.investigator.tools].sort());
	assert.ok(!toolNames(worker[0]).includes('spawn_agent'));
	for (const request of orchestrator) {
		assert.deepEqual(toolNames(request), [...composition.orchestrator.tools].sort());
	}
	for (const request of fake.requests) {
		assert.deepEqual(toolNames(request).filter((name) => FORBIDDEN_TOOL.test(name)), []);
		assert.deepEqual(request.provider, { zdr: true });
	}
	assert.match(toolResults(orchestrator[orchestrator.length - 1]).join('\n'), /Investigator finished\./);
});

test('a missing required variable stops the launcher with a plain message and no protocol output', async (t) => {
	const space = await workspace(t);
	const env = baseEnv(space, 'http://127.0.0.1:9/v1');
	delete (env as Record<string, string | undefined>).SPOTLIGHT_MODEL_ID;

	const chat = launch(t, env);
	const { code } = await chat.exited;

	assert.notEqual(code, 0);
	assert.match(chat.stderr(), /cannot start: SPOTLIGHT_MODEL_ID is not set\./);
	assert.deepEqual(chat.lines, []);
});

test('a parent killed mid-task leaves no child running; a second chat is refused while the case is held; resume discards unfinished lanes and resolves the same step', async (t) => {
	// First chat: resolve and delegate; the worker's model call never returns.
	// The resumed chat only resolves.
	let delegated = false;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return 'hang';
		if (toolResults(request).length > 0) return { text: 'Waiting.' };
		if (delegated) return resolveThenStop(request);
		delegated = true;
		return {
			toolCalls: [
				{ name: 'spotlight_resolve', arguments: {} },
				{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Plan the case.' } },
			],
		};
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
	const env = baseEnv(space, fake.baseURL);

	const first = launch(t, env);
	first.send({ id: 'p', type: 'prompt', message: 'Continue.' });
	await waitUntil(() => fake.requests.some((request) => isWorkerRequest(request, 'investigator')), 30_000);
	const held = await lockRecord(space);
	assert.equal(held.children.length, 2);

	const second = launch(t, env);
	assert.equal((await second.exited).code, 3);
	assert.match(second.stderr(), /already open in another Spotlight chat/);

	first.process.kill('SIGKILL');
	await first.exited;
	await waitUntil(() => held.children.every((pid) => !isAlive(pid)), 10_000);

	const lanes = join(caseDir, 'research', 'swarm');
	await mkdir(join(lanes, 'ownership-registries'), { recursive: true });
	await writeFile(join(lanes, 'ownership-registries', 'lane.json'), '{"status":"completed"}');
	await mkdir(join(lanes, 'ownership-web'), { recursive: true });
	await writeFile(join(lanes, 'ownership-web', 'leads.jsonl'), '{}\n');
	const before = lastResolution(first);

	const resumed = launch(t, env);
	await settledPrompt(resumed, 'Continue.');

	assert.deepEqual(await readdir(lanes), ['ownership-registries']);
	const after = lastResolution(resumed);
	assert.equal(before.phase, 'methodology');
	assert.deepEqual(
		{ phase: after.phase, owner: after.owner, missing: after.missing },
		{ phase: before.phase, owner: before.owner, missing: before.missing },
	);
});

test('closing stdin shuts down in order and a fresh case resumes at the same next step', async (t) => {
	const fake = await startFakeOpenAI(resolveThenStop);
	t.after(() => fake.close());
	const space = await workspace(t);
	const env = baseEnv(space, fake.baseURL);

	const first = launch(t, env);
	await settledPrompt(first, 'Start a case.');
	const children = (await lockRecord(space)).children;
	first.process.stdin.end();
	assert.equal((await first.exited).code, 0);
	assert.ok(children.every((pid) => !isAlive(pid)));
	await assert.rejects(lockRecord(space));
	const before = lastResolution(first);

	const resumed = launch(t, env);
	await settledPrompt(resumed, 'Continue.');
	const after = lastResolution(resumed);

	assert.equal(before.owner, 'phase-methodology');
	assert.deepEqual([after.phase, after.owner], [before.phase, before.owner]);
});

test('SIGTERM stops the harness, its children and its lock', async (t) => {
	const fake = await startFakeOpenAI(resolveThenStop);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Start.');
	const children = (await lockRecord(space)).children;

	chat.process.kill('SIGTERM');
	await chat.exited;

	assert.ok(children.length > 0 && children.every((pid) => !isAlive(pid)));
	await assert.rejects(lockRecord(space));
});

test('newsroom edition: no allowed route refuses to start; with a route, requests go only there', async (t) => {
	const route = await startFakeOpenAI(resolveThenStop);
	const elsewhere = await startFakeOpenAI(resolveThenStop);
	t.after(() => Promise.all([route.close(), elsewhere.close()]));
	const space = await workspace(t);
	const newsroom = (extra: Record<string, string>) => {
		const env: Record<string, string> = { ...baseEnv(space, ''), SPOTLIGHT_EDITION: 'newsroom', ...extra };
		if (!extra.SPOTLIGHT_MODEL_BASE_URL) delete env.SPOTLIGHT_MODEL_BASE_URL;
		return env;
	};
	const routes = JSON.stringify([
		{ name: 'newsroom-model', base_url: route.baseURL, model: 'stub-model', inference: 'own', key_env: 'NEWSROOM_ROUTE_TOKEN' },
	]);

	const noRoute = launch(t, newsroom({}));
	assert.equal((await noRoute.exited).code, 2);
	assert.match(noRoute.stderr(), /no allowed model route/);

	const offRoute = launch(t, newsroom({ SPOTLIGHT_ROUTES: routes, NEWSROOM_ROUTE_TOKEN: 'tok', SPOTLIGHT_MODEL_BASE_URL: elsewhere.baseURL }));
	assert.equal((await offRoute.exited).code, 2);
	assert.match(offRoute.stderr(), /not one of the newsroom's allowed routes/);

	const chat = launch(t, newsroom({ SPOTLIGHT_ROUTES: routes, NEWSROOM_ROUTE_TOKEN: 'tok' }));
	await settledPrompt(chat, 'Start.');

	assert.ok(route.requests.length >= 2);
	assert.equal(elsewhere.requests.length, 0);
});

test('case material reaches only a route the operator runs: refused at start without one, every process on it with one', async (t) => {
	const own = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Investigator finished.' };
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Plan the case.' } }] }
			: { text: 'Done.' };
	});
	const thirdParty = await startFakeOpenAI(resolveThenStop);
	t.after(() => Promise.all([own.close(), thirdParty.close()]));
	const space = await workspace(t);
	const route = (name: string, baseURL: string, inference: string) => ({ name, base_url: baseURL, model: 'stub-model', inference });
	const newsroom = (routes: object[], extra: Record<string, string> = {}) => {
		const env: Record<string, string> = {
			...baseEnv(space, ''),
			SPOTLIGHT_EDITION: 'newsroom',
			SPOTLIGHT_ROUTES: JSON.stringify(routes),
			...extra,
		};
		delete env.SPOTLIGHT_MODEL_BASE_URL;
		return env;
	};

	const refused = launch(t, newsroom([route('cloud', thirdParty.baseURL, 'third-party')]));
	refused.send({ id: 'p', type: 'prompt', message: 'Start.' });
	await waitUntil(() => refused.process.exitCode !== null || thirdParty.requests.length > 0, 30_000);
	assert.equal(thirdParty.requests.length, 0);
	assert.equal((await refused.exited).code, 2);
	assert.match(refused.stderr(), /cannot start: .*route you run yourself/);
	assert.deepEqual(refused.lines, []);

	// The third-party route comes first, so it is the pipeline route.
	const chat = launch(t, newsroom([route('cloud', thirdParty.baseURL, 'third-party'), route('own', own.baseURL, 'own')]));
	await settledPrompt(chat, 'Start.');
	chat.process.stdin.end();
	await chat.exited;

	assert.equal(thirdParty.requests.length, 0);
	assert.ok(own.requests.some((request) => isWorkerRequest(request, 'investigator')));
	assert.ok(own.requests.some((request) => !isWorkerRequest(request, 'investigator')));
});

// A case whose methodology is approved, so the next worker task is Investigation.
async function caseAtInvestigation(space: Workspace): Promise<string> {
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
	await writeFile(join(caseDir, 'data', 'methodology.json'), '{}\n');
	await new Promise<void>((done, fail) =>
		execFile(
			process.env.SPOTLIGHT_PYTHON ?? 'python3',
			[
				join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'),
				'approve', 'methodology',
				'--approved-by', 'journalist:fixture',
				'--approved-at', '2026-08-23T12:00:00Z',
				'--authorized-cases-root', space.casesRoot,
				caseDir,
			],
			(error) => (error ? fail(error) : done()),
		),
	);
	return caseDir;
}

test('a worker writes through its typed tools and the harness validates its output when it finishes', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/findings.json', content: '{"project": "case-a", "findings": []}' } }] }
				: { text: 'Investigator finished.' };
		}
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).project, 'case-a');
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const delegated = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	assert.match(delegated, /Investigator finished\./);
	assert.match(delegated, /validate-case\.py: FAILED/);
	assert.match(delegated, /no_qualifying_findings/);
});

// Tools whose effects stay on this machine (case files, the local vault and
// tool index, the resolver, the loopback review page), plus the two checks
// that refuse their remote step themselves when SPOTLIGHT_SENSITIVE is set.
// Any other tool a sensitive process declares is a possible way out.
const LOCAL_TOOLS = new Set([
	'spotlight_resolve',
	'spotlight_transition',
	'spawn_agent',
	'invoke_skill',
	'open_review',
	'read_file',
	'write_file',
	'edit_file',
	'list_files',
	'grep_files',
	'hash_files',
	'build_review_html',
	'query_vault',
	'osint_tools',
	'exiftool',
	'decision_signals',
	'provenance_manifest',
]);

test('sensitive mode leaves every process only local tools, and a tool that can send case text out does not run', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Investigator finished.' };
		return toolResults(request).length === 0
			? {
					toolCalls: [
						{ name: 'run_rlm', arguments: { request: 'data/rlm-request.json' } },
						{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Plan the case.' } },
					],
				}
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'notes.txt'), 'Contact source@example.org on 2024-03-01.\n');
	await writeFile(
		join(caseDir, 'data', 'rlm-request.json'),
		JSON.stringify({ project: 'case-a', run_id: 'r1', mode: 'lite', corpus_paths: ['notes.txt'] }),
	);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_SENSITIVE: 'true' });

	await settledPrompt(chat, 'Start.');

	await assert.rejects(readFile(join(caseDir, 'data', 'rlm-analysis.json')), { code: 'ENOENT' });
	const worker = fake.requests.find((request) => isWorkerRequest(request, 'investigator'));
	assert.ok(worker, 'the investigator ran');
	assert.ok(toolNames(worker).includes('read_file'));
	for (const request of fake.requests) {
		assert.deepEqual(toolNames(request).filter((name) => !LOCAL_TOOLS.has(name)), []);
	}
});

test('after a planning task the harness runs the methodology check, not the findings check', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: '{"project": "case-a"}' } }] }
				: { text: 'Plan written.' };
		}
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: PLANNING' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const delegated = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	assert.match(delegated, /validate-methodology-navigator\.py: FAILED/);
	assert.doesNotMatch(delegated, /validate-case\.py/);
});

// An activated case paused at the Ingest decision, built by the review-page
// fixture (tests/fixtures/review_case.py) through the resolver's own seam.
async function caseAtIngest(space: Workspace): Promise<string> {
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(space.casesRoot, { recursive: true });
	await new Promise<void>((done, fail) =>
		execFile(
			process.env.SPOTLIGHT_PYTHON ?? 'python3',
			[join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'ingest', '--activated', '--clean'],
			(error, _stdout, stderr) => (error ? fail(new Error(stderr || error.message)) : done()),
		),
	);
	return caseDir;
}

async function caseDigests(caseDir: string): Promise<Map<string, string>> {
	const digests = new Map<string, string>();
	for (const entry of await readdir(caseDir, { recursive: true, withFileTypes: true })) {
		if (!entry.isFile()) continue;
		const path = join(entry.parentPath, entry.name);
		digests.set(path.slice(caseDir.length + 1), createHash('sha256').update(await readFile(path)).digest('hex'));
	}
	return digests;
}

const PROPOSAL = {
	interface_version: '1',
	model: 'stub-model',
	claims: [
		{
			key: 'c1',
			finding_id: 'F1',
			origin_expression_id: 'SX1',
			proposition: 'Acme paid Doe.',
			language: 'en',
			attributed: false,
			statement: { subject: { text: 'Acme' }, predicate: 'paid', object: { text: 'Doe' } },
		},
	],
};

// The orchestrator delegates once to the analyst, then stops.
function delegateToAnalyst(analyst: (request: ChatRequest) => Reply) {
	return (request: ChatRequest): Reply => {
		if (isWorkerRequest(request, 'analyst')) return analyst(request);
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task: 'Propose the knowledge batch.' } }] }
			: { text: 'Done.' };
	};
}

test('at Ingest the analyst reads only its inputs and writes only its proposal, and the harness builds the batch from it', async (t) => {
	const fake = await startFakeOpenAI(
		delegateToAnalyst((request) =>
			toolResults(request).length === 0
				? {
						toolCalls: [
							{ name: 'read_file', arguments: { path: 'data/findings.json' } },
							{ name: 'read_file', arguments: { path: 'research/filing.txt' } },
							{ name: 'write_file', arguments: { path: 'data/findings.json', content: '{}' } },
							{ name: 'write_file', arguments: { path: 'data/knowledge-proposal.json', content: JSON.stringify(PROPOSAL) } },
						],
					}
				: { text: 'Proposed one claim.' },
		),
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtIngest(space);
	const before = await caseDigests(caseDir);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const analyst = fake.requests.filter((request) => isWorkerRequest(request, 'analyst'));
	assert.ok(analyst.length >= 2, 'the analyst ran');
	assert.deepEqual(toolNames(analyst[0]), [...loadComposition().analyst.tools].sort());
	const results = toolResults(analyst[analyst.length - 1]);
	assert.match(results[0], /"project"/);
	assert.match(results[1], /refused: research\/filing\.txt is outside the case files this agent may read/);
	assert.match(results[2], /refused: data\/findings\.json is not a file this agent may write/);
	assert.match(results[3], /wrote data\/knowledge-proposal\.json/);

	// The analyst's one write is the proposal; the builder the harness ran
	// afterwards wrote its id map, draft and batch. Nothing else changed.
	const after = await caseDigests(caseDir);
	const changed = [...after.keys()].filter((path) => before.get(path) !== after.get(path)).sort();
	assert.deepEqual(changed, [
		'data/knowledge-batch-draft.json',
		'data/knowledge-batch.json',
		'data/knowledge-ids.json',
		'data/knowledge-proposal.json',
	]);
	assert.deepEqual([...before.keys()].filter((path) => !after.has(path)), []);
	const batch = JSON.parse(await readFile(join(caseDir, 'data', 'knowledge-batch.json'), 'utf8')) as { claims: Array<{ provenance: unknown }> };
	assert.deepEqual(batch.claims[0].provenance, { method: 'agent_candidate', model: 'stub-model' });
	const ids = JSON.parse(await readFile(join(caseDir, 'data', 'knowledge-ids.json'), 'utf8')) as { models: Record<string, string> };
	assert.deepEqual(ids.models, { investigator: 'stub-model', 'fact-checker': 'stub-model' });
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'analyst'));
	const delegated = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	assert.match(delegated, /Proposed one claim\./);
	assert.match(delegated, /build-knowledge-batch\.py: PASSED/);
});

test('the analyst is not started before Ingest or in the newsroom edition', async (t) => {
	const fake = await startFakeOpenAI(delegateToAnalyst(() => ({ text: 'Proposed.' })));
	t.after(() => fake.close());
	const solo = await workspace(t);
	await caseAtInvestigation(solo);
	const early = launch(t, baseEnv(solo, fake.baseURL));
	await settledPrompt(early, 'Continue.');
	early.process.stdin.end();
	await early.exited;

	const newsroom = await workspace(t);
	await caseAtIngest(newsroom);
	const routes = JSON.stringify([{ name: 'newsroom-model', base_url: fake.baseURL, model: 'stub-model', inference: 'own' }]);
	const env: Record<string, string> = { ...baseEnv(newsroom, ''), SPOTLIGHT_EDITION: 'newsroom', SPOTLIGHT_ROUTES: routes };
	delete env.SPOTLIGHT_MODEL_BASE_URL;
	const routed = launch(t, env);
	await settledPrompt(routed, 'Continue.');

	assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'analyst')).length, 0);
	const refusals = fake.requests
		.filter((request) => toolResults(request).length > 0)
		.map((request) => toolResults(request).join('\n'));
	assert.equal(refusals.length, 2);
	assert.match(refusals[0], /the analyst runs only at ingest; the case is at investigation/);
	assert.match(refusals[1], /the analyst does not run in the newsroom edition/);
	await assert.rejects(readFile(join(newsroom.casesRoot, 'case-a', 'data', 'knowledge-proposal.json')), { code: 'ENOENT' });
});
