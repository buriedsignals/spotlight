// End-to-end tests of the real launcher and real pi processes against a
// scripted OpenAI-compatible server on 127.0.0.1. No network, no model.
// Part 1 of 6. The parts are one suite cut for running in parallel;
// run one area across them with --test-name-pattern.
import assert from 'node:assert/strict';
import { execFile, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { chmod, copyFile, mkdir, readFile, readdir, realpath, rm, unlink, writeFile } from 'node:fs/promises';
import { cpSync, existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { dirname, join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { isWorkerRequest, startFakeOpenAI, systemText, toolNames, toolResults } from './test-support/fake-openai.ts';
import type { ChatRequest, Reply } from './test-support/fake-openai.ts';
import { baseEnv, caseAtIngest, launch, newsroomEnv, requestNewsroomIngest, settledPrompt, waitUntil, workspace } from './test-support/launch.ts';
import { DECISION_RECORDED } from './router.ts';
import { NEXT_BRIEF } from './tools/spotlight.ts';
import { reviewRefusalStop } from './extension.ts';
import { AMENDMENT_STOP, PAGE_NOT_OPENED, REVIEW_HOLD_NOTICE } from './tools/review.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { registerCapture } from './test-support/receipts.ts';
import type { Chat, Workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT, FACT_CHECK_TASK, agentSpec, loadComposition, deliverableFor } from './roles.ts';
import { withCasePassages } from './case-search.ts';
import { BRIEF_PENDING_REFUSAL, FACT_CHECKERS_SPENT, GATE_DECIDED_REFUSAL, GATE_READY_REFUSAL } from './subagents.ts';
import { ASSESSMENT_HELD, FC_BATCH_DONE, FC_BATCH_DUE, FC_BATCH_NOTICE, FC_BATCH_STOP, registerFetchedPages } from './extension.ts';
import { CONTINUE_AFTER_FOLD } from './subagents.ts';
import { blockedStop, budgetedWebResult, CALL_BUDGET_SPENT, FACT_CHECK_DUE, FACT_CHECK_DUE_NOTICE, FACT_CHECK_DUE_REPEAT, FACT_CHECK_STOP, GATE_BOUND, GATE_BOUND_REFUSAL, LOOP_NOTICE_CAP, ORCHESTRATOR_LOOP_STOP, PROPOSAL_DONE, readAlreadyShown, repeatsItself, REVIEW_OPEN_STOP, REVIEW_REFUSAL_CAP, skillAlreadyLoaded, UNPRODUCTIVE_NEXT, UNPRODUCTIVE_STOP } from './extension.ts';
import { GATE_APPROVAL_REFUSAL, NEXT_METHODOLOGY_APPROVAL, NEXT_METHODOLOGY_FAILING } from './tools/spotlight.ts';
import { TASK_AFTER_FOLD, taskAfterFold } from './checkpoint.ts';
import { maxOutputTokens } from './config.ts';
import { FORBIDDEN_TOOL, isAlive, lockRecord, resolveThenStop, lastResolution, briefApproved, caseAtInvestigation, factCheckCase, NUDGE, nudges, DEGENERATE_SAMPLES, watchdogRun, search, record, isWriteCommit, writeCalls, anomalyStandIn, fakeDecisions, SEQ7_SKILLS, LOCAL_TOOLS, CONTENT_ONLY_PLAN, caseDigests, PROPOSAL, delegateToAnalyst, atGate, holdScript, notices, shownText, settles, unrepairable, withArtifact, stopCase, standInBsig, researchFiles, userText, factCheckerTask, newsroomRoute, orchestratorScript, SEAM, PYTHON, DISPATCH_TOOLS, caseAtGate, memberApprovesGate, REPORT_DRAFT, caseAtAmendment, caseAtRoundTopics, gateConfig, resolveResults } from './launcher-cases.ts';
import type { Row } from './launcher-cases.ts';

// 12B retune: a small model repeated one call (the same list four times, the
// same read forty). The third identical call in a row is refused, not run.
test('the third identical tool call in a row is blocked with the loop reason', async (t) => {
	const fake = await startFakeOpenAI((request) =>
		// Any call whose result does not change (a resolve now may carry its phase skill once).
		toolResults(request).length < 3 ? { toolCalls: [{ name: 'read_file', arguments: { path: 'data/nothing.json' } }] } : { text: 'Stopped.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Open the case.');

	const results = toolResults(fake.requests[fake.requests.length - 1]);
	assert.equal(results.length, 3);
	assert.doesNotMatch(results[1], /same call as the last two/);
	assert.match(results[2], /This is the same call as the last two\. Act on what you already have or take the next phase step\./);
});

// Gold rerecord at db814d71: one read_file of a .raw returned 200,000
// characters, and four reads in one turn 66,000, each past the 12b's 40,960
// tokens. On the 12b (the default tier) one turn's reads share 30,000.
test('on the 12b the reads of one turn share 30,000 characters, and the next turn starts afresh', async (t) => {
	// Each page fits one read_file page on the 12b (simp-read: 11,776
	// characters at its fold); three of them pass the turn's 30,000.
	const page = 'x'.repeat(99).concat('\n').repeat(110);
	const turns: Reply[] = [
		{ toolCalls: ['a', 'b', 'c'].map((name) => ({ name: 'read_file', arguments: { path: `research/${name}.md.raw` } })) },
		{ toolCalls: [{ name: 'read_file', arguments: { path: 'research/c.md.raw' } }] },
		{ text: 'Read.' },
	];
	const fake = await startFakeOpenAI((request) => turns[toolResults(request).length === 0 ? 0 : toolResults(request).length === 3 ? 1 : 2]);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const name of ['a', 'b', 'c']) await writeFile(join(caseDir, `research/${name}.md.raw`), page);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Read the pages.');

	const reads = toolResults(fake.requests.find((request) => toolResults(request).length === 3)!);
	const next = toolResults(fake.requests.find((request) => toolResults(request).length === 4)!)[3];
	const notice = '\n[truncated: use offset and limit to read the rest]';
	const kept = reads.map((text) => (text.endsWith(notice) ? text.slice(0, -notice.length) : text));
	assert.equal(page.length, 11_000);
	assert.equal(kept.reduce((sum, text) => sum + text.length, 0), 30_000);
	assert.ok(reads.some((text) => text.endsWith(notice)), 'the read past the turn budget carries the truncation notice');
	assert.equal(next, page);
});

// r5 swiss on a4f47704: one 49,241-character http_get result right after a fold
// took an investigator's prompt from 19.4k to 38.8k tokens of the 40,960 window.
test('on the 12b a web result past the turn\'s read budget is saved whole to a case file, and the model gets its head and the path', async (t) => {
	const page = `# Registry\n${'The registry lists the fixture company. '.repeat(1250)}`;
	const fake = await startFakeOpenAI(
		orchestratorScript([{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }], (request) =>
			toolResults(request).length === 0 ? { toolCalls: [{ name: 'fetch', arguments: { url: 'https://registry.example.org/company/42' } }] } : { text: 'Fetched.' },
		),
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	const python = join(space.root, 'python-fetch');
	const envelope = JSON.stringify({ markdown: page, source_url: 'https://registry.example.org/company/42', provider: 'crawl4ai', status_code: 200 });
	await writeFile(
		python,
		`#!${process.execPath}\nconst { execFileSync } = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(${JSON.stringify(envelope)});\nelse { try { process.stdout.write(execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exitCode = error.status ?? 1; } }\n`,
		{ mode: 0o755 },
	);
	// Tor mode checks the final URL without a DNS lookup, so no network is reached.
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_ANONYMIZE_FETCH: 'true' });

	await settledPrompt(chat, 'Continue.');

	const result = toolResults(fake.requests.find((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 1)!)[0];
	const saved = /It is saved whole at (research\/tool-results\/fetch-[A-Za-z0-9_-]+\.txt):/.exec(result)?.[1];
	assert.ok(saved, result.slice(-400));
	assert.ok(result.length < 2_500, `${result.length} characters entered the context`);
	const whole = await readFile(join(caseDir, saved), 'utf8');
	assert.ok(whole.length > 30_000 && whole.startsWith(result.slice(0, 2_000)), 'the file holds the whole result the model saw the head of');
});

// The launcher test above runs fetch end to end; http_get and browse need the
// network or a browser, so each web tool is checked against the budget here.
test('every web tool\'s result shares the 12b turn budget, and one past it is saved and cut to its head', () => {
	for (const tool of ['http_get', 'fetch', 'browse']) {
		const saved: string[] = [];
		const save = (path: string) => (saved.push(path), path);
		assert.deepEqual(budgetedWebResult(tool, 'c1', 'x'.repeat(1_000), 30_000, save), { used: 1_000 }, tool);
		const over = budgetedWebResult(tool, 'c/2', 'y'.repeat(49_241), 30_000, save)!;
		assert.equal(over.used, 2_000, tool);
		assert.deepEqual(saved, [`research/tool-results/${tool}-c_2.txt`], tool);
		assert.ok(over.replacement!.startsWith('y'.repeat(2_000)) && over.replacement!.includes(`saved whole at ${saved[0]}`), tool);
		assert.equal(budgetedWebResult(tool, 'c3', 'z'.repeat(5_000), 1_500, () => undefined)!.used, 1_500, tool);
	}
	assert.equal(budgetedWebResult('search', 'c4', 'x'.repeat(49_241), 30_000, () => 'p'), undefined);
});

// Gold run 1 on 99c6567e: the orchestrator called source_expressions and got only "Tool source_expressions not found".
test('a call to a tool another role owns names the owner and the next step', async (t) => {
	const fake = await startFakeOpenAI((request) =>
		toolResults(request).length === 0 ? { toolCalls: [{ name: 'source_expressions', arguments: { action: 'fingerprint_findings' } }] } : { text: 'Stopped.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Open the case.');

	const [result] = toolResults(fake.requests[fake.requests.length - 1]);
	assert.match(result, /source_expressions is not one of your tools; it belongs to the investigator and the fact-checker\. Delegate that step with spawn_agent\./);
});

// A member's gate decision arrives as a new prompt; the resolve after it must run.
test('a new prompt resets the loop guard', async (t) => {
	let limit = 2;
	const fake = await startFakeOpenAI((request) =>
		toolResults(request).length < limit ? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] } : { text: 'Waiting.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Open the case.');
	limit = 3;
	await settledPrompt(chat, 'Approved.');

	const results = toolResults(fake.requests[fake.requests.length - 1]);
	assert.equal(results.length, 3);
	assert.doesNotMatch(results[2], /same call as the last two/);
});

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
	await briefApproved(space);
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

// Osaurus (gemma-4-12b-it-qat-mxfp4, 0.25.28) renders tools through the Gemma 4
// chat template and answered every investigator request with HTTP 500 "upper
// filter requires string": http_get's query and anomaly_run's maps carried
// maxProperties, a number the template reads as a parameter. Every role's tools,
// exactly as sent, must render (test-support/gemma4/render.py).
test('every role\'s tools, as sent, render through the Gemma 4 chat template as Osaurus does', async (t) => {
	const sent = new Map<string, ChatRequest['tools']>();
	const roleOf = (request: ChatRequest) => ['investigator', 'fact-checker', 'analyst'].find((role) => isWorkerRequest(request, role)) ?? 'orchestrator';
	const record = (request: ChatRequest) => sent.set(roleOf(request), request.tools);
	const fake = await startFakeOpenAI((request) => {
		record(request);
		if (roleOf(request) !== 'orchestrator') return { text: 'Done.' };
		const next = [
			{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } },
			{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Fact-check all claims.' } },
		][toolResults(request).length];
		return next ? { toolCalls: [next] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await factCheckCase(space, [{ id: 'F1', claim: 'Fixture filing.' }]);
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	const ingest = await startFakeOpenAI((request) => (record(request), delegateToAnalyst(() => ({ text: 'Proposed.' }))(request)));
	t.after(() => ingest.close());
	const later = await workspace(t);
	await caseAtIngest(later);
	await settledPrompt(launch(t, baseEnv(later, ingest.baseURL)), 'Continue.');

	assert.deepEqual([...sent.keys()].sort(), ['analyst', 'fact-checker', 'investigator', 'orchestrator']);
	for (const [role, tools] of sent) {
		const rendered = await new Promise<{ code: number | null; stderr: string }>((done) => {
			const child = execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'harness/pi/src/test-support/gemma4/render.py')], (error, _stdout, stderr) =>
				done({ code: error ? ((error as { code?: number }).code ?? 1) : 0, stderr }),
			);
			child.stdin!.end(JSON.stringify({ tools }));
		});
		assert.equal(rendered.code, 0, `${role}: ${rendered.stderr}`);
	}
});

// E2E D2: a fixed 8192-token output cap cut off whole-file rewrites on a
// 131072-token model.
test('the output cap follows the context window', async (t) => {
	const fake = await startFakeOpenAI(() => ({ text: 'Noted.' }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '131072' });

	await settledPrompt(chat, 'Open the case.');

	const request = fake.requests[0];
	assert.equal(request.max_tokens ?? request.max_completion_tokens, 32768);
	chat.process.stdin.end();
	await chat.exited;
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

test('an invalid fact-check batch size stops the launcher before model work', async (t) => {
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, 'http://127.0.0.1:9/v1'), SPOTLIGHT_FC_BATCH_SIZE: '2' });
	const { code } = await chat.exited;
	assert.notEqual(code, 0);
	assert.match(chat.stderr(), /SPOTLIGHT_FC_BATCH_SIZE must be 1 or 3/);
	assert.deepEqual(chat.lines, []);
});

test('a parent killed mid-task leaves no child running; a second chat is refused while the case is held; resume discards unfinished lanes and resolves the same step', async (t) => {
	// First chat: resolve and delegate; the worker's model call never returns.
	// The resumed chat only resolves.
	let delegated = false;
	let killed = false;
	const fake = await startFakeOpenAI((request) => {
		// The first chat's worker never returns; the resumed chat's planner writes nothing.
		if (isWorkerRequest(request, 'investigator')) return killed ? { text: 'Planned.' } : 'hang';
		if (toolResults(request).length > 0) return { text: 'Waiting.' };
		if (delegated) return resolveThenStop(request);
		delegated = true;
		// At Methodology the resolve itself starts the planning investigator.
		return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
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
	killed = true;
	await first.exited;
	await waitUntil(() => held.children.every((pid) => !isAlive(pid)), 10_000);

	const lanes = join(caseDir, 'research', 'swarm');
	await mkdir(join(lanes, 'ownership-registries'), { recursive: true });
	await writeFile(join(lanes, 'ownership-registries', 'lane.json'), '{"status":"completed"}');
	await mkdir(join(lanes, 'ownership-web'), { recursive: true });
	await writeFile(join(lanes, 'ownership-web', 'leads.jsonl'), '{}\n');
	// The first chat's resolve never returned (its auto-spawned planner hung).
	const before = JSON.parse(execFileSync(PYTHON, [SEAM, 'status', '--json', caseDir]).toString()) as Record<string, unknown>;

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
	const editorial = await startFakeEditorial();
	t.after(() => Promise.all([route.close(), elsewhere.close(), editorial.close()]));
	const space = await workspace(t);
	const routes = [
		{ name: 'newsroom-model', base_url: route.baseURL, model: 'stub-model', inference: 'own', key_env: 'NEWSROOM_ROUTE_TOKEN' },
	];
	const newsroom = (extra: Record<string, string>) => {
		const env = newsroomEnv(space, routes, editorial, extra);
		if (!extra.SPOTLIGHT_ROUTES) delete env.SPOTLIGHT_ROUTES;
		return env;
	};

	const noRoute = launch(t, newsroom({}));
	assert.equal((await noRoute.exited).code, 2);
	assert.match(noRoute.stderr(), /no allowed model route/);

	const offRoute = launch(t, newsroom({ SPOTLIGHT_ROUTES: JSON.stringify(routes), NEWSROOM_ROUTE_TOKEN: 'route-token-0123456789', SPOTLIGHT_MODEL_BASE_URL: elsewhere.baseURL }));
	assert.equal((await offRoute.exited).code, 2);
	assert.match(offRoute.stderr(), /not one of the newsroom's allowed routes/);

	const chat = launch(t, newsroom({ SPOTLIGHT_ROUTES: JSON.stringify(routes), NEWSROOM_ROUTE_TOKEN: 'route-token-0123456789' }));
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
	const editorial = await startFakeEditorial();
	t.after(() => Promise.all([own.close(), thirdParty.close(), editorial.close()]));
	const space = await workspace(t);
	await briefApproved(space);
	const route = (name: string, baseURL: string, inference: string) => ({ name, base_url: baseURL, model: 'stub-model', inference });
	const newsroom = (routes: object[], extra: Record<string, string> = {}) => newsroomEnv(space, routes, editorial, extra);

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

// r6 northwind on dd14e2e5: the brief went to data/brief-directions.txt and the
// case never left Brief.
test('a brief written under data/ is refused with the root path, and the brief at the root moves the case past Brief', async (t) => {
	const calls = [
		{ name: 'write_file', arguments: { path: 'data/brief-directions.txt', content: 'Who owns the fixture company?\n' } },
		{ name: 'write_file', arguments: { path: 'brief-directions.txt', content: 'Who owns the fixture company?\n' } },
		{ name: 'spotlight_resolve', arguments: {} },
	];
	const fake = await startFakeOpenAI(orchestratorScript(calls));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'That direction is fine, go ahead.');

	const results = toolResults(fake.requests[fake.requests.length - 1]);
	assert.match(results[0], /refused: the approved brief goes to brief-directions\.txt at the case root/);
	assert.equal(existsSync(join(space.casesRoot, 'case-a', 'data', 'brief-directions.txt')), false);
	const resolved = JSON.parse(results[2]) as { next_phase: string };
	assert.notEqual(resolved.next_phase, 'brief');
});

// r6 northwind on dd14e2e5: the orchestrator ended the run on a text summary
// while the case waited on it. At Investigation the first text turn gets one
// nudge and a second one ends the run; at Brief, where the orchestrator talks
// with the member, a text turn ends it at once.
test('on the 12b an orchestrator text turn at a phase only it can move gets one nudge, never two in a row; at Brief it gets none', async (t) => {
	for (const atInvestigation of [true, false]) {
		const fake = await startFakeOpenAI(() => ({ text: 'Here is the plan.' }));
		t.after(() => fake.close());
		const space = await workspace(t);
		// A member follow-up: code dispatches nothing there (the member's
		// instructions are the model's to route), so a text turn gets the nudge,
		// not a continuation.
		if (atInvestigation) execFileSync(PYTHON, [SEAM, 'request-follow-up', '--instructions', 'Re-check F1 against the 2024 filing.', caseAtGate(space)]);
		const chat = launch(t, baseEnv(space, fake.baseURL));

		await settledPrompt(chat, 'Continue.');

		const nudges = fake.requests.flatMap((request) => request.messages).filter((message) => message.role === 'user' && JSON.stringify(message.content).includes('Your turn ended with text'));
		const last = fake.requests[fake.requests.length - 1].messages;
		if (atInvestigation) {
			assert.equal(fake.requests.length, 2, 'one nudge, then the second text turn ends the run');
			assert.match(JSON.stringify(last[last.length - 1].content), /the case is at investigation \(skill: phase-investigation\)/);
		} else {
			assert.equal(fake.requests.length, 1, 'no nudge at Brief');
			assert.equal(nudges.length, 0);
		}
	}
});

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

// U3 census on 84674fd9: investigators working every direction of the plan in
// one context peaked above 30k prompt tokens (also the 12B in 34 of 237 runs).
test('an investigator artifact path alias reaches the next strict fact-check validator as a case-relative file', async (t) => {
	let findings = '';
	let factCheck = '';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/findings.json', content: findings } }] } : { text: 'Investigated.' };
		if (isWorkerRequest(request, 'fact-checker')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: factCheck } }] } : { text: 'Checked.' };
		const n = toolResults(request).length;
		return n === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate the finding.' } }] } : n === 1 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the finding.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--clean']);
	const file = join(caseDir, 'data', 'findings.json');
	const doc = JSON.parse(await readFile(file, 'utf8'));
	doc.findings[0].sources[0].local_file = '{CASE_DIR}/research/primary-record.txt';
	findings = JSON.stringify(doc);
	factCheck = await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8');
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	const result = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'investigator') && !isWorkerRequest(request, 'fact-checker')).at(-1)!).join('\n');
	assert.match(result, /validate-fact-check\.py: PASSED/);
	assert.equal(JSON.parse(await readFile(file, 'utf8')).findings[0].sources[0].local_file, 'research/primary-record.txt');
});

test('on a local tier a plan of two directions runs one investigator per direction, each with its share of calls, and the harness checks the merged findings once', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Who owns the company', direction_id: 'owners', questions: ['Who are the shareholders?'], steps: [] },
		{ direction: 'Who runs the company', direction_id: 'board', questions: [], steps: [] },
	] };
	const finding = (id: string) => ({ name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id, claim: id }] }) } });
	const fake = await startFakeOpenAI((request) => {
		const done = toolResults(request).length;
		if (isWorkerRequest(request, 'investigator') && userText(request).includes('DIRECTION 1 of 2')) {
			// A changed record every fifth call keeps the progress watchdog quiet,
			// so the direction's 40-call budget is still what this test reaches.
			if (done <= 40) return { toolCalls: [done % 5 === 4 ? { name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id: 'D1-F1', claim: `D1-F1 draft ${done}` }] }) } } : { name: 'list_files', arguments: { pattern: `research/${done}-*.md` } }] };
			return done === 41 ? { toolCalls: [finding('D1-F1')] } : { text: 'Owners done.' };
		}
		if (isWorkerRequest(request, 'investigator')) return done === 0 ? { toolCalls: [finding('D2-F1')] } : { text: 'Board done.' };
		return done === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nInvestigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, undefined, plan);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const tasks = [...new Set(fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).map(userText))];
	assert.equal(tasks.length, 2);
	assert.match(tasks[0], /DIRECTION 1 of 2 \(direction:owners\): Who owns the company\nQuestions:\n- Who are the shareholders\?/);
	assert.match(tasks[0], /every finding id starting D1-F/);
	assert.match(tasks[1], /DIRECTION 2 of 2 \(direction:board\)/);
	assert.doesNotMatch(tasks[1], /Who owns the company/);
	const owners = fake.requests.filter((request) => userText(request) === tasks[0]).at(-1)!;
	assert.equal(toolResults(owners)[40], CALL_BUDGET_SPENT);
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).findings.map((each: { id: string }) => each.id), ['D1-F1', 'D2-F1']);
	const delegated = toolResults(fake.requests.at(-1)!).join('\n');
	assert.match(delegated, /Direction D1 \(direction:owners\): Owners done\.\n\nDirection D2 \(direction:board\): Board done\./);
	assert.equal(delegated.match(/validate-case\.py/g)?.length, 1);
});

// A frontier orchestrator sent one explicitly scoped execution task per direction.
test('parallel explicitly scoped tasks run only their assigned direction and preserve each other’s findings', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
	] };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const id = userText(request).includes('direction_id "owners"') ? 'D1-F1' : 'D2-F1';
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id, claim: id }] }) } }] }
				: { text: 'Done.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: ['owners', 'board'].map((id) => ({
			name: 'spawn_agent', arguments: { agent: 'investigator', task: `MODE: EXECUTION\nTHIS TASK — ONE METHODOLOGY DIRECTION ONLY: direction_id "${id}"` },
		})) } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, undefined, plan);
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');

	const tasks = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
	assert.equal(tasks.length, 2, 'one worker per explicit direction');
	for (const [id, tag] of [['owners', '1'], ['board', '2']]) {
		const task = tasks.find((text) => text.includes(`direction_id "${id}"`));
		assert.ok(task, `worker for ${id}`);
		assert.match(task, new RegExp(`DIRECTION ${tag} of 2 \\(direction:${id}\\)`));
		assert.doesNotMatch(task, new RegExp(`DIRECTION ${tag === '1' ? '2' : '1'} of 2`));
	}
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).findings.map((each: { id: string }) => each.id).sort(), ['D1-F1', 'D2-F1']);
});

test('frontier execution honors an explicit direction and preserves the other partition', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
	] };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0
			? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id: 'D2-F1', claim: 'board' }] }) } }] }
			: { text: 'Done.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nDIRECTION_ID: board' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, undefined, plan);
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ project: 'case-a', findings: [{ id: 'D1-F1', claim: 'owners' }] }));
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	const requests = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0);
	assert.equal(requests.length, 1);
	assert.match(userText(requests[0]), /DIRECTION 2 of 2 \(direction:board\)/);
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).findings.map((each: { id: string }) => each.id).sort(), ['D1-F1', 'D2-F1']);
});

// local-gemma12-extractive-20261101-resume1: four investigator spawns were
// refused for an unknown DIRECTION_ID and the case stalled. Scoping is code's.
test('an unknown or ambiguous DIRECTION_ID gets the next open approved direction, and the result says the harness assigned it', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
	] };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const tag = /DIRECTION (\d) of 2/.exec(userText(request))?.[1];
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id: `D${tag}-F1`, claim: 'x' }] }) } }] }
				: { text: 'Done.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [
			'MODE: EXECUTION\nDIRECTION_ID: missing',
			'MODE: EXECUTION\nDIRECTION_ID: owners\nDIRECTION_ID: board',
		].map((task) => ({ name: 'spawn_agent', arguments: { agent: 'investigator', task } })) } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space, undefined, plan);
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	// The two spawns may run side by side: each gets its own open direction.
	const first = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
	assert.deepEqual(first.map((text) => /DIRECTION (\d) of 2 \(direction:(\w+)\)/.exec(text)?.slice(1).join(' ')).sort(), ['1 owners', '2 board']);
	for (const text of first) assert.doesNotMatch(text, /DIRECTION_ID: (missing|owners\nDIRECTION_ID: board)/);
	const results = toolResults(fake.requests.at(-1)!).join('\n');
	assert.match(results, /harness assigned the next open one: D1 \(direction:owners\)/);
	// The ambiguous pair names Board, the one still open once Owners has its finding.
	assert.match(results, /(next open one|open direction it names): D2 \(direction:board\)/);
});

test('overlapping scoped tasks do not run twice and can be retried after the worker finishes', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
	] };
	const task = 'MODE: EXECUTION\nDIRECTION_ID: owners';
	const spawn = { name: 'spawn_agent', arguments: { agent: 'investigator', task } };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Finished.' };
		const count = toolResults(request).length;
		return count === 0 ? { toolCalls: [spawn, spawn] } : count === 2 ? { toolCalls: [spawn] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space, undefined, plan);
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	const tasks = fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).map(userText);
	assert.equal(tasks.length, 2, 'one initial worker and one later retry');
	for (const worker of tasks) {
		assert.match(worker, /DIRECTION 1 of 2 \(direction:owners\)/);
		assert.doesNotMatch(worker, /DIRECTION 2 of 2/);
	}
	const results = toolResults(fake.requests.at(-1)!);
	assert.equal(results.length, 3);
	assert.equal(results.filter((result) => /refus|already|running|overlap/i.test(result)).length, 1);
});

// tools-da on b0d862e2: a fold replaced the investigator's task, and with it
// SOURCE_EXPRESSION_MODE, so it re-read the case files to rebuild it.
// The 12b, where every Osaurus model runs, at its own fold point and window;
// its reads are cut at 30,000 characters a turn, so it folds after two.
for (const [tier, env] of [
	['12b', { SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960' }],
	['26b', { SPOTLIGHT_MODEL_CONTEXT_WINDOW: '65536', SPOTLIGHT_COMPACT_AT: '20000', SPOTLIGHT_COMPACT_KEEP: '2000' }],
	['frontier', { SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40000' }],
] as const) {
test(`on the ${tier} after a fold a worker\'s next request carries its task word for word, after the checkpoint`, async (t) => {
	const task = 'MODE: EXECUTION\nSOURCE_EXPRESSION_MODE: activated\nTASK-MARKER: read the long filing.';
	const checkpoint = '## Goal\nRead the filing.\n## Progress\nCHECKPOINT-SENTINEL\n## Decisions\nNone.\n## Next\nWrite findings.\n## Open gate\nNone.';
	const fake = await startFakeOpenAI((request) => {
		if ((request.tools ?? []).length === 0) return { text: checkpoint };
		if (isWorkerRequest(request, 'investigator')) {
			const read = toolResults(request).length;
			return read < 3 && !JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL')
				? { toolCalls: [{ name: 'read_file', arguments: { path: `research/filing-${read}.md` } }] }
				: { text: 'Read it.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const n of [0, 1, 2]) await writeFile(join(caseDir, 'research', `filing-${n}.md`), `${n} ${'The registry lists the fixture company. '.repeat(tier === 'frontier' ? 2000 : 1500)}`);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, ...env });

	await settledPrompt(chat, 'Continue.');

	const folded = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL'));
	assert.ok(folded.length > 0, 'the investigator folded');
	const text = JSON.stringify(folded[0].messages);
	const checkpointMessage = folded[0].messages.find(message => JSON.stringify(message.content).includes('CHECKPOINT-SENTINEL'))!;
	const checkpointText = JSON.stringify(checkpointMessage.content).split(TASK_AFTER_FOLD)[0];
	assert.equal(toolNames(folded[0]).includes('spotlight_resolve'), false, 'the investigator has no orchestrator resolver tool');
	assert.doesNotMatch(checkpointText, /spotlight_resolve/, 'the checkpoint authority note must not direct the investigator to an unavailable tool');
	assert.ok(text.indexOf('CHECKPOINT-SENTINEL') < text.indexOf(JSON.stringify(TASK_AFTER_FOLD).slice(1, -1)), text.slice(0, 2000));
	assert.ok(text.includes(JSON.stringify(`${TASK_AFTER_FOLD}\n\n${task}`).slice(1, -1)), 'the task follows the checkpoint word for word');
	// On a local tier, with the task, the request after the fold leaves room
	// for a full turn (the output cap) at about 3.1 characters per token. The
	// frontier tier keeps Pi's 20,000 recent tokens, here the reads themselves.
	if (tier !== 'frontier') assert.ok(text.length / 3.1 < Number(env.SPOTLIGHT_MODEL_CONTEXT_WINDOW) - maxOutputTokens(Number(env.SPOTLIGHT_MODEL_CONTEXT_WINDOW)), `${Math.round(text.length / 3.1)} tokens`);
});
}

// r5 norway on cc8710fd: the task inside the previous checkpoint reached the
// next checkpoint request, and r5 answered it with read_file calls, so the fold
// was skipped. Only the worker's own requests carry the task.
test('on the 12b a second fold\'s checkpoint request carries the previous checkpoint without the task', async (t) => {
	const task = 'MODE: EXECUTION\nTASK-MARKER: read every filing.';
	const checkpoint = '## Goal\nRead the filings.\n## Progress\nCHECKPOINT-SENTINEL\n## Decisions\nNone.\n## Next\nRead on.\n## Open gate\nNone.';
	let reads = 0;
	const fake = await startFakeOpenAI((request) => {
		if ((request.tools ?? []).length === 0) return { text: checkpoint };
		if (isWorkerRequest(request, 'investigator')) {
			return reads < 6 ? { toolCalls: [{ name: 'read_file', arguments: { path: `research/filing-${reads++}.md` } }] } : { text: 'Read them.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const n of [0, 1, 2, 3, 4, 5]) await writeFile(join(caseDir, 'research', `filing-${n}.md`), `${n} ${'The registry lists the fixture company. '.repeat(1500)}`);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960' });

	await settledPrompt(chat, 'Continue.');

	const checkpoints = fake.requests.filter((request) => (request.tools ?? []).length === 0).map((request) => JSON.stringify(request.messages));
	assert.ok(checkpoints.length >= 2, `${checkpoints.length} checkpoint requests`);
	for (const text of checkpoints.slice(1)) {
		assert.match(text, /<previous-checkpoint>/);
		assert.match(text, /CHECKPOINT-SENTINEL/);
		assert.doesNotMatch(text, /TASK-MARKER|Your task, word for word/);
	}
});

// train-tor on 52bf6183: a quoted STRUCTURAL CORRECTION task ran as five
// direction workers, and one dropped four findings that passages still cited.
test('a correction task on a plan of two directions runs one investigator, unsplit, even when quoted', async (t) => {
	const plan = { investigation_plan: [{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] }, { direction: 'Board', direction_id: 'board', questions: [], steps: [] }] };
	const task = '"MODE: STRUCTURAL CORRECTION — shape-only. Fix the bundle fields."';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Fixed.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space, undefined, plan);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const tasks = [...new Set(fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).map(userText))];
	assert.equal(tasks.length, 1);
	assert.doesNotMatch(tasks[0], /DIRECTION \d of \d|Your deliverable/);
});

// Engine's two local routes set the model variables and strip
// SPOTLIGHT_PROVIDER_REQUEST_BODY (run/spotlight.go); a catalog body is the only
// other shape it has held. The real processes run, and each request is read;
// the checkpoint's request is checked in the fold test below.
test('on the environment Engine passes the fact-checker turn thinks and every other request stays off', async (t) => {
 for (const [label, body] of [['no request body', undefined], ['catalog privacy body', '{"provider":{"zdr":true,"data_collection":"deny"}}']] as const) await t.test(label, async (t) => {
	let reads = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return reads < 3 ? { toolCalls: [{ name: 'read_file', arguments: { path: `research/filing-${reads++}.md` } }] } : { text: 'Checked.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check every finding.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await factCheckCase(space, [{ id: 'F1', claim: 'Acme paid Doe.' }]);
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const n of [0, 1, 2]) await writeFile(join(caseDir, 'research', `filing-${n}.md`), `${n} ${'The registry lists the fixture company. '.repeat(1500)}`);
	const engine = { SPOTLIGHT_MODEL_ID: 'default', SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960', SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_PROVIDER_API_KEY: 'agent-token', ...(body ? { SPOTLIGHT_PROVIDER_REQUEST_BODY: body } : {}) };
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), ...engine }), 'Continue.');

	const thinking = (request: object) => (request as { chat_template_kwargs?: { enable_thinking?: unknown } }).chat_template_kwargs?.enable_thinking;
	const checker = fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker'));
	const others = fake.requests.filter((request) => !isWorkerRequest(request, 'fact-checker'));
	t.diagnostic(`fact-checker ${checker.length} requests ${[...new Set(checker.map(thinking))]}; orchestrator ${others.length} ${[...new Set(others.map(thinking))]}`);
	assert.ok(checker.length > 0 && others.length > 0, 'both roles ran');
	for (const request of checker) assert.equal(thinking(request), true);
	for (const request of others) assert.equal(thinking(request), false);
	if (body) for (const request of fake.requests) assert.deepEqual((request as { provider?: unknown }).provider, JSON.parse(body).provider);
 });
});

// wave 1 on 52bf6183: the fact-checker's whole task, case passages and the
// fact-check structure included, came back after every fold (21.8k characters
// median), and its post-fold prompt was 42.7k tokens.
test('on the 12b after a fold a fact-checker gets its task\'s own lines, the structure and a pointer to its passages, never the passages', async (t) => {
 for (const correction of [false, true]) await t.test(correction ? 'assigned correction with unrelated missing finding' : 'fresh batch', async (t) => {
	const task = correction ? 'MODE: EVIDENCE CORRECTION\nRepair F1 only. TASK-MARKER: check every finding.' : 'TASK-MARKER: check every finding.';
	const checkpoint = '## Goal\nCheck the findings.\n## Progress\nCHECKPOINT-SENTINEL\n## Decisions\nNone.\n## Next\nRead on.\n## Open gate\nNone.';
	let reads = 0;
	const fake = await startFakeOpenAI((request) => {
		if ((request.tools ?? []).length === 0) return { text: checkpoint };
		if (isWorkerRequest(request, 'fact-checker')) {
			return reads < 3 && !JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL')
				? { toolCalls: [{ name: 'read_file', arguments: { path: `research/filing-${reads++}.md` } }] }
				: { text: 'Checked.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await factCheckCase(space, [{ id: 'F1', claim: 'Acme paid Doe.' }, ...(correction ? [{ id: 'F4', claim: 'Unrelated future finding.' }] : [])]);
	await mkdir(join(caseDir, 'research'), { recursive: true });
 if (correction) {
  await writeFile(join(caseDir,'data/fact-check.json'), JSON.stringify({project:'case-a',claims:[{id:'FC1',finding_id:'F1',claim_text:'Acme paid Doe.',verdict:'unverified',confidence:'medium',evidence_for:[]}]}));
 }
	for (const n of [0, 1, 2]) await writeFile(join(caseDir, 'research', `filing-${n}.md`), `${n} ${'The registry lists the fixture company. '.repeat(1500)}`);
	const passages = `CASE PASSAGES\nPASSAGE-MARKER ${'A passage from the case. '.repeat(400)}`;
	const python = join(space.root, 'python-case-search');
	await writeFile(
		python,
		`#!${process.execPath}\nconst { execFileSync } = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.case_search')) process.stdout.write(${JSON.stringify(passages)});\nelse if (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({markdown:'Acme paid Doe.',source_url:args.at(-1),provider:'crawl4ai',status_code:200}));\nelse { try { process.stdout.write(execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exitCode = error.status ?? 1; } }\n`,
		{ mode: 0o755 },
	);
	if (!correction) {
		const bundlePath = join(caseDir, 'data/evidence-bundle.json');
		const bundle = JSON.parse(await readFile(bundlePath, 'utf8'));
		bundle.items = [{ id: 'E1', source_url: 'https://source.example/record', query_or_task: 'Check the filing', acquisition_method: 'manual', accessed: '2026-08-23T11:00:00Z', extraction_confidence: 'medium', human_verification_required: true, claim_links: [{ finding_id: 'F1', claim_text: 'Acme paid Doe.', support_type: 'direct' }] }];
		await writeFile(bundlePath, JSON.stringify(bundle));
	}
	const before = await caseDigests(caseDir);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960', SPOTLIGHT_RECHECK: correction ? 'off' : 'on', SPOTLIGHT_ANONYMIZE_FETCH: 'true' });

	await settledPrompt(chat, 'Continue.');

	const first = fake.requests.find((request) => isWorkerRequest(request, 'fact-checker'))!;
	assert.match(userText(first), /PASSAGE-MARKER/, 'the fact-checker starts with its passages');
	const folded = fake.requests.find((request) => isWorkerRequest(request, 'fact-checker') && JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL'));
	assert.ok(folded, 'the fact-checker folded');
	// A checkpoint is a summary: the fact-checker's own turns think, its fold does not.
	const thinking = (request: object) => (request as { chat_template_kwargs?: { enable_thinking?: unknown } }).chat_template_kwargs?.enable_thinking;
	assert.deepEqual([...new Set(fake.requests.filter((request) => (request.tools ?? []).length === 0).map(thinking))], [false]);
	assert.equal(thinking(folded), true);
	const text = JSON.stringify(folded.messages);
	const checkpointMessage = folded.messages.find(message => JSON.stringify(message.content).includes('CHECKPOINT-SENTINEL'))!;
	const checkpointText = JSON.stringify(checkpointMessage.content).split(TASK_AFTER_FOLD)[0];
	assert.equal(toolNames(folded).includes('spotlight_resolve'), false, 'the fact-checker has no orchestrator resolver tool');
	assert.doesNotMatch(checkpointText, /spotlight_resolve/, 'the checkpoint authority note must not direct the fact-checker to an unavailable tool');
	const after = text.slice(text.indexOf('Your task, word for word'));
	// The fact-checker's task is code-built (G3b): its own line survives the fold, the orchestrator's text never arrives.
	assert.match(after, /Fact-check the findings the harness assigns below independently/);
	assert.doesNotMatch(text, /TASK-MARKER/);
	if (!correction) assert.match(after, /re-check: re-fetched/, 'source recheck outcomes survive the fold');
	if (!correction) assert.match(after, /Your deliverable: the claims for F1 in data\/fact-check\.json/);
 else { assert.match(after,/judge each named finding again with judge_finding/, 'the 12b correction line names its route (B20)'); assert.doesNotMatch(after,/- F4:/, 'the fold never assigns future work to this correction'); }
	assert.match(after, /- F1: \\"Acme paid Doe\.\\"/, 'the batch packet comes back while data/fact-check.json is not written');
	const kept = /Case passages for this task: (\/[^;]+\/work\/fact-checker\/passages\/fact-checker-[^;]+\.md); read it if you need them again\./.exec(after)?.[1];
	assert.ok(kept, after.slice(0, 2000));
	assert.doesNotMatch(text, /PASSAGE-MARKER/);
	assert.match(await readFile(kept, 'utf8'), /PASSAGE-MARKER/);
	// Passage attachments stay outside the case. Only explicit harness recheck
	// and diagnostic artifacts may be added; existing case bytes stay unchanged.
	const afterFiles = await caseDigests(caseDir);
	// A correction that changed nothing is counted, and that is the one record it may touch.
	for (const [path, digest] of before) if (!(correction && path === 'data/orchestration.json')) assert.equal(afterFiles.get(path), digest, path);
	for (const path of afterFiles.keys()) if (!before.has(path)) assert.match(path, /^(research\/fc-recheck-|data\/harness-checks\/fact-checker\/)/, 'only harness recheck/diagnostic artifacts were added');
 });
});

test('after a fold the first checklist comes back until data/fact-check.json is written, then the next findings to check', () => {
	const env = { SPOTLIGHT_ROLE: 'fact-checker', SPOTLIGHT_TASK_HEADER: 'Check.\n\nYour deliverable: data/fact-check.json', SPOTLIGHT_TASK_START: 'Next to check (3 of 3 …):\n- F1', SPOTLIGHT_TASK_PASSAGES: '/h/passages/fc.md' };
	const pointer = '\n\nCase passages for this task: /h/passages/fc.md; read it if you need them again.';
	assert.equal(taskAfterFold(env, () => false, () => 'Next to check (1 of 1 …)'), `Check.\n\nYour deliverable: data/fact-check.json\nNext to check (3 of 3 …):\n- F1${pointer}`);
	assert.equal(taskAfterFold(env, () => true, () => 'Next to check (1 of 1 …):\n- F3'), `Check.\n\nYour deliverable: data/fact-check.json\nNext to check (1 of 1 …):\n- F3${pointer}`);
	assert.equal(taskAfterFold({}, () => false), undefined);
	const analyst = { SPOTLIGHT_ROLE: 'analyst', SPOTLIGHT_TASK_HEADER: 'Normalize.', SPOTLIGHT_TASK_START: 'JOINED-ANALYST-CONTEXT' };
	assert.equal(taskAfterFold(analyst, () => true), 'Normalize.\nJOINED-ANALYST-CONTEXT', 'an existing fact-check cannot discard another role’s context');
	// fc-stall item 3: after a fold the task says what was already fetched.
	const pages = 'Already fetched in this task (read the saved file; do not fetch again): https://a.example/x -> research/x.md.';
	assert.equal(taskAfterFold(env, () => true, () => undefined, () => pages), `Check.\n\nYour deliverable: data/fact-check.json${pointer}\n\n${pages}`);
});

// fc-stall item 3 (incr-test2 sp98): fact-checker threads fetched the same pages
// again after each fold. A worker's successful fetches are listed by URL, the
// latest save per URL; a failed fetch is not.
test('the pages a worker fetched are listed for the text after a fold, failures left out', () => {
	const handlers = new Map<string, (event: Record<string, unknown>) => void>();
	const pi = { on: (name: string, handler: (event: Record<string, unknown>) => void) => handlers.set(name, handler) } as unknown as Parameters<typeof registerFetchedPages>[0];
	const fetched = registerFetchedPages(pi);
	assert.equal(fetched(), undefined);
	const run = (id: string, args: Record<string, unknown>, isError = false) => {
		handlers.get('tool_execution_start')!({ toolName: 'fetch', toolCallId: id, args });
		handlers.get('tool_execution_end')!({ toolName: 'fetch', toolCallId: id, isError });
	};
	run('1', { url: 'https://a.example/x', save_as: 'x.md' });
	run('2', { url: 'https://b.example/y', save_as: 'y.md' }, true);
	run('3', { url: 'https://a.example/x', save_as: 'x2.md' });
	assert.equal(fetched(), 'Already fetched in this task (read the saved file; do not fetch again): https://a.example/x -> x2.md.');
});

// 12B r3 A2 ia: every assistant message in the requests carried its earlier
// reasoning_content, which the Gemma 4 template renders after the last user
// message; the 12b was trained on renders without it.
for (const [tier, kept] of [['12b', false], ['26b', true]] as const) {
	test(`on the ${tier} an earlier turn's reasoning is ${kept ? 'sent back' : 'left out of the request'}`, async (t) => {
		const fake = await startFakeOpenAI((request) =>
			toolResults(request).length === 0
				? { reasoning: 'Resolve the case first.', toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] }
				: { text: 'Resolved.' },
		);
		t.after(() => fake.close());
		const space = await workspace(t);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });

		await settledPrompt(chat, 'Open the case.');

		const history = fake.requests[1].messages.filter((message) => message.role === 'assistant') as Array<Record<string, unknown>>;
		assert.equal(history.length, 1);
		assert.equal(history[0].reasoning_content, kept ? 'Resolve the case first.' : undefined);
		assert.ok(Array.isArray(history[0].tool_calls), 'the tool call itself is kept');
	});
}

test('on the 12b an empty turn gets one nudge per prompt, and a second empty turn still ends the run', async (t) => {
	let answer = '';
	const fake = await startFakeOpenAI((request) => ({ text: JSON.stringify(request.messages.at(-1)?.content ?? '').includes(NUDGE) ? answer : '' }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Open the case.');
	assert.deepEqual(fake.requests.map(nudges), [0, 1], 'one nudge, and the empty answer to it ends the run');
	answer = 'Here is where the case stands.';
	await settledPrompt(chat, 'Go on.');
	assert.deepEqual(fake.requests.map(nudges), [0, 1, 1, 2], 'the next prompt gets its own nudge');
	assert.equal(fake.requests.length, 4);
});

// The guard's threshold: both samples trip it; a long fact-check JSON (keys
// repeated on every claim, as in the 34 KB r4arm cc:114 write) does not, even
// read as text.
test('a repeated unit trips the stream guard and a long, key-repetitive JSON does not', () => {
	assert.equal(repeatsItself(DEGENERATE_SAMPLES.channel), true);
	assert.equal(repeatsItself(DEGENERATE_SAMPLES.sentences), true);
	const claims = Array.from({ length: 80 }, (_, i) => ({
		claim_id: `F${i + 1}`,
		verdict: i % 3 ? 'verified' : 'unverified',
		confidence: 'medium',
		evidence_for: [{ source_ref: { path: `research/source-${i}.md`, lines: [i + 3, i + 9] }, support_type: 'direct', claim_elements_checked: ['actor', 'role', 'organization', 'date'] }],
		note: `Checked against the source for finding F${i + 1}.`,
	}));
	const json = JSON.stringify({ schema_version: '1.1', claims }, null, 2);
	assert.ok(json.length > 30_000);
	assert.equal(repeatsItself(json), false);
});

test('on the 12b a turn repeating itself or streaming nothing visible is stopped with an honest line, and a turn that ran to the cap is nudged once', async (t) => {
	// At once: the two stalled cases each wait the real 60 s clock.
	await Promise.all(([
		['channel', { text: DEGENERATE_SAMPLES.channel }],
		['sentences', { text: DEGENERATE_SAMPLES.sentences }],
		['length', { text: 'A long summary that ran out of room', finish: 'length' as const }],
		['reasoning length', { text: '', reasoning: 'Rechecking the same source. '.repeat(20), finish: 'length' as const }],
		// base213 seq 26: 10,240 tokens the server stripped to nothing.
		['invisible', { text: '', invisible: 3000, finish: 'length' as const }],
		// The server sends one empty chunk, then nothing, with the stream open.
		['stalled', { text: '', invisible: 1, stall: true }],
		// lfm3-r5 uk-cpi (rc3): two complete tool calls, then the stream held open.
		['stalled after calls', { toolCalls: [{ name: 'list_files', arguments: { pattern: 'data/*.json' } }], stall: true }],
		// A full context slot: cut after one token. No nudge would help.
		['slot full', { text: '<channel|>', finish: 'length' as const, usage: { prompt_tokens: 40959, completion_tokens: 1, total_tokens: 40960 } }],
	] as const).map(async ([name, first]) => {
		const fake = await startFakeOpenAI((_request, index) => (index === 0 ? first : { text: 'Done.' }));
		t.after(() => fake.close());
		const space = await workspace(t);
		// A dead stream before any visible output waits out the prefill limit, shortened here.
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PREFILL_MS: '3000' });

		if (name === 'stalled' || name === 'stalled after calls') {
			// The clock stops a turn after 60 s with nothing visible, once it has shown output.
			chat.send({ id: 'p-stalled', type: 'prompt', message: 'Start.' });
			await chat.waitFor((record) => record.type === 'agent_settled', 0, 90_000);
		} else {
			await settledPrompt(chat, 'Start.');
		}
		const settled = chat.lines.filter((line) => line.includes('"type":"message_end"')).join('\n');

		if (name === 'slot full') {
			// The full slot also makes Pi compact; only the nudge is checked.
			assert.ok(!fake.requests.some((request) => JSON.stringify(request.messages).includes('ran on without a tool call and was cut off')), 'a turn cut after one token gets no nudge');
		} else if (name === 'length' || name === 'reasoning length') {
			assert.equal(fake.requests.length, 2, 'one nudge, then the answer');
			const history = fake.requests[1].messages;
			assert.match(JSON.stringify(history[history.length - 1].content), /Your last turn ran on without a tool call and was cut off/);
		} else if (name === 'stalled' || name === 'stalled after calls') {
			assert.equal(fake.requests.length, 1, `${name}: the run ends at the stop`);
			assert.match(settled, /Spotlight stopped this turn because the model stopped responding\. Ask Spotlight to continue\./, name);
			assert.ok(!chat.lines.some((line) => line.includes('"type":"tool_execution_start"')), `${name}: the dropped calls do not run`);
		} else {
			assert.equal(fake.requests.length, 1, `${name}: the run ends at the stop`);
			assert.match(settled, /Spotlight stopped this turn because it ran on without calling a tool/, name);
			const unit = name === 'channel' ? '<|channel>thought' : name === 'sentences' ? 'Wait, I\'ll start.' : '\u0000';
			assert.ok(settled.split(unit).length - 1 < 5, `${name}: the repeated text is gone from what the member sees`);
		}
	}));
});

// A stopped orchestrator must leave the case as it was: every case file
// unchanged, the lock released on exit, and the next launch resolving where
// the case stood.
test('on the 12b an orchestrator stopped for repeating itself leaves the case unchanged, and the next launch resolves it', async (t) => {
	let stopped = false;
	const fake = await startFakeOpenAI((request) => {
		if (!stopped) {
			stopped = true;
			return { text: DEGENERATE_SAMPLES.channel };
		}
		return resolveThenStop(request);
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	const before = await caseDigests(caseDir);

	const first = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(first, 'Continue.');
	assert.ok(first.lines.some((line) => line.includes('Spotlight stopped this turn because it ran on without calling a tool')), 'the guard stopped the turn');
	first.process.stdin.end();
	assert.equal((await first.exited).code, 0);
	await assert.rejects(lockRecord(space), 'the lock is released');
	assert.deepEqual(await caseDigests(caseDir), before, 'no case file was written or changed');

	const resumed = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(resumed, 'Continue.');
	assert.deepEqual([lastResolution(resumed).phase, lastResolution(resumed).next_phase], ['investigation', 'investigation']);
});

// A worker stopped for repeating itself answers the orchestrator with the stop line.
test('on the 12b a worker turn repeating itself reaches the orchestrator as the stop line', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: DEGENERATE_SAMPLES.sentences };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const answer = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	assert.match(answer, /Spotlight stopped this turn because it ran on without calling a tool/);
	assert.doesNotMatch(answer, /Actually, I'll do them all\.\n\nWait/);
});

test('a worker\'s empty final answer gets the nudge before its answer reaches the orchestrator', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: nudges(request) > 0 ? 'Investigator finished.' : '' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const delegated = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	assert.match(delegated, /Investigator finished\./);
	assert.doesNotMatch(delegated, /\(no output\)/);
});

// r1 12B: the orchestrator re-spawned the fact-checker for evidence correction
// again and again instead of recording the attempt after the one the skill allows.
test('a second evidence-correction fact-checker spawn in one cycle is refused, and a new cycle allows one again', async (t) => {
	const tasks = ['EVIDENCE CORRECTION\nreason 1', 'MODE: EVIDENCE CORRECTION\nreason 2', 'Fact-check all claims.', 'MODE: EVIDENCE CORRECTION\nreason 3'];
	// Fact-checker tasks are code-built, so each worker answers with its start order.
	let started = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: `Checked ${++started}.` };
		const done = toolResults(request).length;
		return done < tasks.length ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: tasks[done] } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await factCheckCase(space, [{ id: 'F1', claim: 'Fixture filing.' }]);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' });

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'fact-checker'));
	const results = toolResults(orchestrator[orchestrator.length - 1]);
	assert.equal(results.length, 4);
	assert.match(results[1], /The fact-checker already had its one evidence correction this cycle\. Record the failure with spotlight_transition recordAttempt \(kind fact-check-evidence-repair, gap: the validator reason\), then call spotlight_resolve\./);
	// Spawns 1, 3 and 4 started the first, second and third workers; spawn 2 started none.
	assert.deepEqual([0, 2, 3].map((index) => /Checked (\d)\./.exec(results[index])?.[1]), ['1', '2', '3']);
	assert.equal(started, 3, 'the refused correction never started a worker');
});

// r6 nordhaven on dd14e2e5: about 15 fact-checker spawns in one cycle. The
// refusal names only calls the orchestrator can make there, and a real
// recordAttempt with the named kind starts the next cycle.
test('four productive fact-check requests can continue past the allowance without a new member turn', async (t) => {
	const findings = Array.from({ length: 5 }, (_, i) => ({ id: `F${i + 1}`, claim: `Fixture claim ${i + 1}.` }));
	let starts = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			if (toolResults(request).length) return { text: 'Checked.' };
			starts++;
			if (starts % 2 === 0) return { text: 'Unable to check this finding.' };
			const id = /^- (F\d+): /m.exec(userText(request))?.[1];
			const finding = findings.find((row) => row.id === id);
			assert.ok(finding);
			// B23(a) (K5-3): the first batch's verdict is written under verdicts; code moves it and the next batch still follows.
			const row = { finding_id: id, claim_text: finding.claim, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ project: 'offline-demo', ...(starts === 1 ? { claims: [], verdicts: [row] } : { claims: [row] }) }) } }] };
		}
		const n = toolResults(request).length;
		return n < 4 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Continue pass ${n + 1}.` } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await factCheckCase(space, findings);
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_FC_BATCH_SIZE: '1' }), 'Continue.');
	const results = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'fact-checker')).at(-1)!);
	assert.equal(results.length, 4);
	assert.ok(!results[3].includes(FACT_CHECKERS_SPENT));
	const checked = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8'));
	assert.deepEqual(checked.claims.map((row: { finding_id: string }) => row.finding_id), ['F1', 'F2', 'F3', 'F4']);
	assert.equal(checked.verdicts, undefined, 'one container, as the contract allows');
});

test('a written claim that fails strict validation never earns another fact-check attempt', async (t) => {
	const findings = [{ id: 'F1', claim: 'Fixture claim one.' }, { id: 'F2', claim: 'Fixture claim two.' }];
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			if (toolResults(request).length) return { text: 'Checked.' };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ project: 'offline-demo', claims: [{ finding_id: 'F1', claim_text: findings[0].claim, verdict: 'unverified', confidence: 'low', evidence_for: [{ source: 'research/primary-record.txt' }], evidence_against: [] }] }) } }] };
		}
		const n = toolResults(request).length;
		return n < 4 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Continue pass ${n + 1}.` } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await factCheckCase(space, findings);
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_FC_BATCH_SIZE: '1' }), 'Continue.');
	const starts = fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker') && toolResults(request).length === 0);
	const results = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'fact-checker')).at(-1)!);
	assert.equal(starts.length, 3);
	assert.match(results[0], /FAILED/);
	assert.equal(results[3], FACT_CHECKERS_SPENT);
});

test('a fourth fact-checker spawn in one cycle is refused, again on every retry, and a recorded attempt allows the next', async (t) => {
	const calls = [
		...[1, 2, 3, 4, 5].map((n) => ({ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Fact-check pass ${n}.` } })),
		{ name: 'spotlight_transition', arguments: { operation: 'recordAttempt', payload: { kind: 'investigation-cycle', gap: 'F1 has no stored source.' } } },
		{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Fact-check pass 6.' } },
	];
	let started = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: `Checked ${++started}.` };
		const next = calls[toolResults(request).length];
		return next ? { toolCalls: [next] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await factCheckCase(space, [{ id: 'F1', claim: 'Fixture filing.' }]);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'fact-checker'));
	const last = orchestrator[orchestrator.length - 1];
	const results = toolResults(last);
	assert.equal(results.length, calls.length);
	for (const index of [3, 4]) assert.equal(results[index].includes(FACT_CHECKERS_SPENT), true, `spawn ${index + 1} is refused with the cap line`);
	assert.doesNotMatch(results[5], /refused|error/i);
	// Spawns 1 to 3 and 6 started the four workers, in that order (tasks are code-built).
	assert.deepEqual([0, 1, 2, 6].map((index) => /Checked (\d)\./.exec(results[index])?.[1]), ['1', '2', '3', '4']);
	assert.equal(started, 4);
	// Every tool the line names is one the orchestrator has.
	for (const name of FACT_CHECKERS_SPENT.match(/\b[a-z]+(?:_[a-z]+)+\b/g) ?? []) assert.ok(toolNames(last).includes(name), `${name} is an orchestrator tool`);
	assert.match(FACT_CHECKERS_SPENT, /recordAttempt \(kind investigation-cycle/);
});

// 12B r3 A2 nordhaven on ad768b54: investigator-7 repeated one search through six
// loop-guard refusals in a row until the case was stopped.
// Every tier since constellation-20261102 review W4.
for (const tier of ['12b', 'frontier']) test(`on the ${tier} tier a worker refused three times in a row for one repeated call is stopped, and the orchestrator gets that as its answer`, async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { toolCalls: [{ name: 'list_files', arguments: { pattern: 'data/*' } }] };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });

	await settledPrompt(chat, 'Continue.');

	const investigator = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const results = toolResults(investigator[investigator.length - 1]);
	assert.equal(investigator.length, 5, 'two calls run, two are refused, and the third refusal ends the run');
	assert.deepEqual(results.slice(2).map((text) => /same call as the last two/.test(text)), [true, true]);
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	assert.match(toolResults(orchestrator[orchestrator.length - 1]).join('\n'), /The investigator repeated one call and was stopped; its last results stand\./);
});

// 12B r5 fsf s0 on 2f357029: fact-checker-71 made one source_expressions call nine times in a
// row; Pi refused its arguments each time, before the loop guard ever saw the call.
// Every tier since constellation-20261102 review W4.
for (const tier of ['12b', 'frontier']) test(`on the ${tier} tier a worker repeating one call whose arguments fail the schema is refused, then stopped`, async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { toolCalls: [{ name: 'read_file', arguments: { file: 'data/findings.json' } }] };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });

	await settledPrompt(chat, 'Continue.');

	const investigator = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const results = toolResults(investigator[investigator.length - 1]);
	assert.equal(investigator.length, 5, 'two calls fail validation, two are refused, and the third refusal ends the run');
	assert.deepEqual(results.map((text) => /^Validation failed for tool "read_file"/.test(text)), [true, true, false, false]);
	assert.deepEqual(results.slice(2).map((text) => /same call as the last two/.test(text)), [true, true]);
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	assert.match(toolResults(orchestrator[orchestrator.length - 1]).join('\n'), /The investigator repeated one call and was stopped; its last results stand\./);
});

// r6 on dd14e2e5: cc investigator-12 had 15 writes refused in a row, each a
// different call, so the loop guard never saw a repeat.
// Every tier since the progress watchdog (constellation-20261102): no recorded
// DeepSeek or Gemma4 31B worker had three refusals in a row.
for (const tier of ['12b', 'frontier']) test(`on the ${tier} tier a worker whose last six calls were all refused, each a different call, is stopped at its next call; a search with no matches and a site that does not resolve are not refusals`, async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const done = toolResults(request).length;
			if (done === 5) return { toolCalls: [{ name: 'grep_files', arguments: { pattern: 'no such text anywhere', path: 'data' } }] };
			// codex-review-comb 1: a site that does not resolve is the upstream's
			// failure, not a refusal; the harness refusing a local address is one.
			if (done === 11) return { toolCalls: [{ name: 'http_get', arguments: { url: 'http://registry.invalid/filing' } }] };
			if (done === 12) return { toolCalls: [{ name: 'fetch', arguments: { url: 'http://127.0.0.1:9/page' } }] };
			return { toolCalls: [{ name: 'read_file', arguments: { path: `research/missing-${done}.md` } }] };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });

	await settledPrompt(chat, 'Continue.');

	const investigator = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	// Five refusals, one search that finds nothing (not a refusal: the streak
	// starts again), five refusals, an unresolvable site (neither counted nor a
	// reset), a refused local fetch (the sixth refusal), then the stop.
	assert.equal(investigator.length, 14);
	const results = toolResults(investigator[investigator.length - 1]);
	assert.equal(results[5], 'no matches');
	assert.match(results[11], /ENOTFOUND|getaddrinfo/);
	assert.match(results[12], /refused: /);
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	// Continuity: the orchestrator gets the stop and the harness's checks, and
	// goes on with its own turn.
	const answer = toolResults(orchestrator.at(-1)!).join('\n');
	assert.match(answer, /The investigator had six tool calls in a row refused by the harness and was stopped; its last results stand\./);
	assert.match(answer, /\[Harness checks after the investigator finished: /);
});

// H-K3a (K3 Tor P3): at 12b with a 40,960 window (fold point 20,480), one whole
// read of a ~30k-character capture crossed the fold point and the worker read it
// again seven times. The read now returns a page of whole lines with the next
// offset and limit; the worker pages to the line it needs and records it. The
// frontier tier reads the whole file as before.
test('on a small fold point read_file pages a large capture, the worker follows the pages and records a cited finding; frontier reads it whole', async (t) => {
	for (const tier of ['12b', 'frontier'] as const) await t.test(tier, async (t) => {
		const lines = Array.from({ length: 400 }, (_, index) => index === 299 ? 'The registry names Acme Holdings as the owner of the fixture company.'.padEnd(75, '.') : `Registry page line ${index + 1} with routine filing text.`.padEnd(75, '.'));
		const capture = `${lines.join('\n')}\n`;
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				const results = toolResults(request);
				if (results.length === 0) return { toolCalls: [{ name: 'read_file', arguments: { path: 'research/registry.md' } }] };
				const next = /Read the next part with read_file offset (\d+) and limit (\d+)/.exec(results[0]);
				if (results.length === 1 && next) return { toolCalls: [{ name: 'read_file', arguments: { path: 'research/registry.md', offset: Number(next[1]), limit: Number(next[2]) } }] };
				// The caller's own range ten lines past one page: the next read stays inside it.
				if (results.length === 2 && next) return { toolCalls: [{ name: 'read_file', arguments: { path: 'research/registry.md', offset: 1, limit: Number(next[1]) - 1 + 10 } }] };
				if (!results.some((result) => /recorded|F1/.test(result) && !/^refused/.test(result)) && results.length < 5) return { toolCalls: [{ name: 'record_finding', arguments: { finding: { claim: 'The registry names Acme Holdings as the owner.', confidence: 'medium' }, cite: [{ path: 'research/registry.md', line_start: 300, line_end: 300 }] } }] };
				return { text: 'Recorded.' };
			}
			return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nWork the approved methodology.' } }] } : { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = await caseAtInvestigation(space);
		await mkdir(join(caseDir, 'research'), { recursive: true });
		await writeFile(join(caseDir, 'research', 'registry.md'), capture);
		registerCapture(space.dataDir, caseDir, 'research/registry.md', 'https://registry.example/acme', 'investigator');
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960' });

		await settledPrompt(chat, 'Continue.');

		const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
		const results = toolResults(worker.at(-1)!);
		if (tier === 'frontier') {
			assert.equal(results[0].trimEnd(), capture.trimEnd(), 'frontier reads the whole capture');
			assert.doesNotMatch(results[0], /\[page: /);
			return;
		}
		assert.ok(results[0].length < 12_500, `the first read is one page (${results[0].length} characters)`);
		assert.match(results[0], /^Registry page line 1 /);
		assert.match(results[0], /\[page: lines 1-\d+ of 400, so the read stays under this model's context fold\. Read the next part with read_file offset \d+ and limit \d+\.\]/);
		assert.match(results[1], /The registry names Acme Holdings/, 'the next page holds line 300');
		const pageEnd = Number(/offset (\d+)/.exec(results[0])![1]) - 1;
		assert.match(results[2], new RegExp(`Read the next part with read_file offset ${pageEnd + 1} and limit 10\\.`), 'the next read stays inside the caller\'s range (Codex review of simp-read)');
		const findings = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')) as { findings: Array<{ claim: string; evidence?: unknown }> };
		assert.ok(findings.findings.some((finding) => /Acme Holdings/.test(finding.claim)), JSON.stringify(results.slice(2)));
	});
});

for (const tier of ['12b', 'frontier']) test(`on the ${tier} tier a worker that searches 20 times without a record is stopped at 16 turns with a receipt`, async (t) => {
	const run = await watchdogRun(t, tier, (turn) => (turn < 20 ? search(turn) : { text: 'Investigated.' }));
	assert.equal(run.turns, 16);
	assert.match(run.answer, /The investigator made no new accepted record within the progress watchdog limit and was stopped; its last results stand\./);
	assert.deepEqual(run.receipt, { watchdog: 'progress', role: 'investigator', rule: 'no-first-record', limit: 16, turns: 16, turns_since_last_record: 16, last_record: null, deferred_to: null });
});

