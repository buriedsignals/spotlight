// End-to-end tests of the real launcher and real pi processes against a
// scripted OpenAI-compatible server on 127.0.0.1. No network, no model.
// Part 2 of 6. The parts are one suite cut for running in parallel;
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

// codex-review-comb 2: 6 turns stopped a worker gathering sources for its next
// finding. An 8-turn gap between records is research; 12 without one stops.
test('a worker that records, gathers sources for 8 turns and records again is not stopped; 12 turns without a record are', async (t) => {
	const run = await watchdogRun(t, 'frontier', (turn) => (turn === 0 || turn === 9 ? record(turn) : turn < 30 ? search(turn) : { text: 'Investigated.' }));
	assert.equal(run.turns, 22);
	assert.deepEqual(run.receipt, { watchdog: 'progress', role: 'investigator', rule: 'no-new-record', limit: 12, turns: 22, turns_since_last_record: 12, last_record: { turn: 10, tool: 'write_file', target: 'data/findings.json' }, deferred_to: null });
	// Continuity: the stop reaches the orchestrator with the harness's checks.
	assert.match(run.answer, /\[Harness checks after the investigator finished: /);
});

test('a worker that records every third turn over 30 turns is never stopped by the progress watchdog', async (t) => {
	const run = await watchdogRun(t, 'frontier', (turn) => (turn === 30 ? { text: 'Investigated.' } : turn % 3 === 2 ? record(turn) : search(turn)));
	assert.equal(run.turns, 31);
	assert.match(run.answer, /Investigated\./);
	assert.equal(run.receipt, undefined);
});

test('resolving at Research suggestions launches the AMENDMENT investigator; its suggestions file is credited, not stopped by the progress watchdog, and the resolver reaches research_approval', async (t) => {
 // B18: on 12b, six searches without the file get the write commit step; on frontier, with the switch on, none.
 for (const tier of ['frontier', '12b'] as const) await t.test(tier, async (t) => {
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	const sample = join(space.root, 'with-suggestions');
	const fixture = join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py');
	const env = { ...process.env, SPOTLIGHT_RESEARCH_SWARMS: 'on' };
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [fixture, sample, 'research'], { env });
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [fixture, caseDir, 'research', '--no-suggestions'], { env });
	const sampled = await readFile(join(sample, 'data', 'research-suggestions.json'), 'utf8');
	// B15 (K3-2): on 12b the worker's hashes are invented; code computes them, and the Research page still opens. Frontier keeps writing its own.
	const suggestions = tier === 'frontier' ? sampled : JSON.stringify({ ...JSON.parse(sampled), inputs: { 'data/leads.json': 'a'.repeat(64), 'data/clues.json': 'b'.repeat(64), 'data/research-triage.json': 'c'.repeat(64) } });
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			// B19: a provider without tool_choice "required"; the same request with "auto" writes the file.
			if (isWriteCommit(request)) return request.tool_choice === 'required' ? { status: 400 } : { toolCalls: [{ name: 'write_file', arguments: { path: 'data/research-suggestions.json', content: suggestions } }] };
			const turn = toolResults(request).length;
			if (turn === 16) return { text: 'Suggested.' };
			return turn === 10 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/research-suggestions.json', content: suggestions } }] } : search(turn);
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_CODE_ROUTER: 'on', SPOTLIGHT_RESEARCH_SWARMS: 'on', SPOTLIGHT_CONFIG: join(CHECKOUT_ROOT, 'tests/fixtures/research/install-config.sample.json') });
	await settledPrompt(chat, 'Continue.');
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && !isWriteCommit(request));
	assert.match(userText(worker[0]), /^MODE: AMENDMENT\nTASK: SUGGEST\n/);
	assert.equal(lastResolution(chat).phase, 'research_approval', 'the suggestions count and the Research page is next');
	assert.equal(/Code fills `inputs`/.test(userText(worker[0])), tier === '12b');
	assert.ok(toolResults(worker.at(-1)!).some((text) => text.startsWith('wrote ')), `the suggestions write was accepted: ${toolResults(worker.at(-1)!)[10]}`);
	const answer = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'investigator')).at(-1)!).join('\n');
	assert.doesNotMatch(answer, /progress watchdog limit/);
	const commits = fake.requests.filter(isWriteCommit);
	// Osaurus route (the Engine does not set it): thinking is off on every local-tier request, orchestrator, worker and commit step alike; frontier sends nothing.
	for (const request of fake.requests) assert.deepEqual((request as { chat_template_kwargs?: unknown }).chat_template_kwargs, tier === 'frontier' ? undefined : { enable_thinking: false });
	if (tier === 'frontier') {
		assert.equal(commits.length, 0, 'no commit request on frontier');
		assert.ok(fake.requests.every((request) => request.tool_choice === undefined && !JSON.stringify(request.messages).includes('Commit step (from the harness')));
		assert.equal(worker.length, 17);
		assert.ok(writeCalls(worker.at(-1)!).every(([id]) => !id.startsWith('commit-')));
		return;
	}
	assert.deepEqual(commits.map((request) => request.tool_choice), ['required', 'auto'], 'one commit step, asked again with auto after the provider refused required');
	assert.deepEqual(toolNames(commits[0]), ['not_ready', 'write_file']);
	assert.deepEqual(toolNames(commits[1]), ['not_ready', 'write_file']);
	// The committed write is turn 7's real write_file, accepted; the worker's own route then runs on to its answer.
	const [committed] = writeCalls(worker.at(-1)!);
	assert.ok(committed[0].startsWith('commit-'), committed[0]);
	assert.equal(committed[1], 'data/research-suggestions.json');
	assert.match(committed[2], /wrote data\/research-suggestions\.json.*Code set inputs in data\/research-suggestions\.json/);
	assert.equal(toolResults(worker[6]).length, 7, 'the commit step answered turn 7; the next request is the worker\'s own');
	assert.equal(worker.length, 16);
 });
});

// Review W1: a write by absolute path (or ./) is the same case file.
test('a worker that records by absolute or ./ path every third turn is never stopped by the progress watchdog', async (t) => {
	const run = await watchdogRun(t, 'frontier', (turn, caseDir) => (turn === 30 ? { text: 'Investigated.' } : turn % 3 === 2 ? record(turn, turn % 2 ? join(caseDir, 'data', 'findings.json') : './data/findings.json') : search(turn)));
	assert.equal(run.turns, 31);
	assert.equal(run.receipt, undefined);
});

// Review W3: re-saving an unchanged file is not progress.
test('a worker that rewrites the same findings every third turn is stopped 12 turns after its only real record', async (t) => {
	const run = await watchdogRun(t, 'frontier', (turn) => (turn === 30 ? { text: 'Investigated.' } : turn % 3 === 2 ? record(2) : search(turn)));
	assert.equal(run.turns, 15);
	assert.deepEqual(run.receipt, { watchdog: 'progress', role: 'investigator', rule: 'no-new-record', limit: 12, turns: 15, turns_since_last_record: 12, last_record: { turn: 3, tool: 'write_file', target: 'data/findings.json' }, deferred_to: null });
});

// base213 on Osaurus (e7b81f21): the orchestrator tried spotlight_transition
// "approve" on its own methodology gate, twice, got only a schema error, and
// the case stalled. The refusal says who approves and names the real next step.
test('an orchestrator trying to approve a gate through spotlight_transition is told only the member approves, and the gate stays open', async (t) => {
	const calls = [
		{ name: 'spotlight_transition', arguments: { operation: 'approve', payload: { approvedBy: 'User', gate: 'methodology', decision: 'approved' } } },
		{ name: 'spotlight_resolve', arguments: {} },
	];
	const fake = await startFakeOpenAI(orchestratorScript(calls));
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await new Promise<void>((done, fail) =>
		execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology'], (error) => (error ? fail(error) : done())),
	);
	const before = await caseDigests(caseDir);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const last = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator')).at(-1)!;
	const [refused, resolved] = toolResults(last);
	assert.ok(refused.includes(GATE_APPROVAL_REFUSAL), refused);
	assert.ok(toolNames(last).includes('open_review'), 'the step it names is an orchestrator tool');
	assert.equal((JSON.parse(resolved) as { next_phase: string }).next_phase, 'methodology_approval');
	assert.deepEqual(await caseDigests(caseDir), before, 'nothing was approved or written');
});

// LFM3 on Osaurus (7513e421) called "invoke-skill" and "query-vault"; each
// failed as a tool that does not exist.
test('on the 12b a tool name with "-" for "_" runs the real tool, and no other near-miss is mapped', async (t) => {
	const fake = await startFakeOpenAI((request) =>
		toolResults(request).length === 0
			? { toolCalls: [{ name: 'invoke-skill', arguments: { skill: 'integrations' } }, { name: 'invokeskill', arguments: { skill: 'osint' } }] }
			: { text: 'Done.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Start.');

	const [hyphen, other] = toolResults(fake.requests.at(-1)!);
	assert.match(hyphen, /^---\nname: integrations/m);
	assert.match(other, /invokeskill/);
	assert.doesNotMatch(other, /^---\nname: osint/m);
});

test('on a local tier a PLANNING investigator\'s plan gets the Anomaly section when the decision model says yes, and the EXECUTION task names the table', async (t) => {
	for (const probability of [0.9, 0.05]) {
		const decisions = await fakeDecisions(probability);
		t.after(() => decisions.close());
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				return toolResults(request).length === 0
					? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: JSON.stringify({ questions: ['Which payments stand out?'] }) } }] }
					: { text: 'Planned.' };
			}
			return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: PLANNING. Plan the case.' } }] } : { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = join(space.casesRoot, 'case-a');
		await mkdir(join(caseDir, 'research'), { recursive: true });
		await writeFile(join(caseDir, 'brief-directions.txt'), 'Payments from the city to Acme.\nWere any payments unusually large?\n');
		await writeFile(join(caseDir, 'research', 'payments.csv'), 'id,amount\n1,12\n2,15\n3,48000\n');
		const config = join(space.root, 'spotlight-config.json');
		await writeFile(config, JSON.stringify({ integrations: { decisions: { endpoint: { url: decisions.url, api: 'openrouter-decisions' } } } }));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: config, ANOMALY_HOME: await anomalyStandIn(space.root) });

		await settledPrompt(chat, 'Continue.');

		assert.equal(decisions.states.length, 1, `${probability}: one question when the PLANNING investigator finished`);
		const methodology = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')) as { anomaly?: { domains: Array<{ domain: string }> } };
		const record = JSON.parse(await readFile(join(caseDir, 'data', 'anomaly-decision.json'), 'utf8')) as { path: string; decision: boolean };
		assert.equal(record.path, 'local-asked');
		if (probability >= 0.5) {
			assert.equal(methodology.anomaly?.domains[0].domain, 'any_table');
			const block = await new Promise<string>((done, fail) =>
				execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'anomaly-decision.py'), caseDir, '--task-block'], { env: { ...process.env, ANOMALY_HOME: join(space.root, 'anomaly') } }, (error, stdout) => (error ? fail(error) : done(stdout))),
			);
			assert.match(block, /ANOMALY: .*\n- research\/payments\.csv: columns id, amount/);
		} else {
			assert.equal(methodology.anomaly, undefined, 'a no adds nothing');
		}
	}
});

test('after an approved Anomaly plan from the decision model, the investigator\'s EXECUTION task carries the ANOMALY block', async (t) => {
	const fake = await startFakeOpenAI(orchestratorScript([{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }], () => ({ text: 'Investigated.' })));
	t.after(() => fake.close());
	const space = await workspace(t);
	const home = await anomalyStandIn(space.root);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Were any payments unusually large?\n');
	await writeFile(join(caseDir, 'research', 'payments.csv'), 'id,amount\n1,12\n2,15\n3,48000\n');
	await writeFile(join(caseDir, 'data', 'methodology.json'), JSON.stringify({ anomaly: { domains: [{ domain: 'any_table', sources: [], why: 'test' }] } }));
	await writeFile(join(caseDir, 'data', 'anomaly-decision.json'), JSON.stringify({ path: 'local-asked', decision: true, probability: 0.9, tables: ['research/payments.csv'] }));
	await new Promise<void>((done, fail) =>
		execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'), 'approve', 'methodology', '--approved-by', 'journalist:fixture', '--approved-at', '2026-10-28T12:00:00Z', '--authorized-cases-root', space.casesRoot, caseDir], { env: { ...process.env, ANOMALY_HOME: home } }, (error) => (error ? fail(error) : done())),
	);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), ANOMALY_HOME: home });

	await settledPrompt(chat, 'Continue.');

	const task = userText(fake.requests.find((request) => isWorkerRequest(request, 'investigator'))!);
	assert.match(task, /ANOMALY: the approved methodology runs Anomaly/);
	assert.match(task, /research\/payments\.csv: columns id, amount/);
	assert.match(task, /anomaly_run \{"method": "method:numeric\.zscore_outliers@1\.1\.0", "table": "research\/payments\.csv"/);
});

// U3 census (84674fd9): both models read the same file again and again.
test('an unchanged file read again with the same range returns a short line; another range or a changed file returns the text', async (t) => {
	const calls = [
		{ name: 'write_file', arguments: { path: 'brief-directions.txt', content: 'first\nsecond\n' } },
		{ name: 'read_file', arguments: { path: 'brief-directions.txt' } },
		{ name: 'read_file', arguments: { path: 'brief-directions.txt' } },
		{ name: 'read_file', arguments: { path: 'brief-directions.txt', offset: 2, limit: 1 } },
		{ name: 'write_file', arguments: { path: 'brief-directions.txt', content: 'changed\n' } },
		{ name: 'read_file', arguments: { path: 'brief-directions.txt' } },
	];
	const fake = await startFakeOpenAI(orchestratorScript(calls));
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const results = toolResults(fake.requests.at(-1)!);
	assert.equal(results[1], 'first\nsecond\n');
	assert.equal(results[2], readAlreadyShown('brief-directions.txt'));
	assert.equal(results[3], 'second');
	assert.equal(results[5], 'changed\n');
});

// U4 candidate: a worker loading the same skill again repeats its whole text.
test('a skill loaded once is not loaded again in the same conversation', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const done = toolResults(request).length;
			return done < 2 ? { toolCalls: [{ name: 'invoke_skill', arguments: { skill: 'osint' } }] } : { text: 'Planned.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: PLANNING. Plan the case.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const results = toolResults(fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).at(-1)!);
	assert.equal(results.length, 2);
	assert.ok(results[0].length > 1000 && !results[0].startsWith('Not loaded'), 'the first call loads the skill');
	assert.equal(results[1], skillAlreadyLoaded('osint'));
});

test('on the 12b one turn loads at most 30,000 characters of skills and reads, each skill whole, and a skill left out loads in a later turn', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const done = toolResults(request).length;
			if (done === 0) return { toolCalls: SEQ7_SKILLS.map((skill) => ({ name: 'invoke_skill', arguments: { skill } })) };
			if (done === SEQ7_SKILLS.length) {
				const left = SEQ7_SKILLS.find((_skill, index) => toolResults(request)[index].startsWith('Not loaded'))!;
				return { toolCalls: [{ name: 'invoke_skill', arguments: { skill: left } }] };
			}
			return { text: 'Planned.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: PLANNING. Plan the case.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const first = toolResults(worker[1]);
	const loaded = first.filter((text) => !text.startsWith('Not loaded'));
	const refused = first.filter((text) => text.startsWith('Not loaded'));
	assert.ok(refused.length > 0, 'the 11 skills do not fit one turn');
	assert.ok(loaded.length > 0);
	assert.ok(loaded.join('').length <= 30_000, `one turn loaded ${loaded.join('').length} characters`);
	const later = toolResults(worker[2]).at(-1)!;
	assert.ok(!later.startsWith('Not loaded') && later.length > 1000, 'a skill left out loads in the next turn');
});

test('on the 12b a request left less than 512 tokens to answer is stopped with an honest line instead of sent', async (t) => {
	const fake = await startFakeOpenAI(() => ({ text: 'Should not be asked.' }));
	t.after(() => fake.close());
	const space = await workspace(t);
	// A window the system prompt and tools alone nearly fill (Pi keeps 4,096
	// back; the dispatch-only orchestrator's prompt and tools are smaller).
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '6600', SPOTLIGHT_COMPACT_AT: '5600' });

	await settledPrompt(chat, 'Start.');

	assert.ok(chat.lines.some((line) => line.includes('the conversation no longer leaves the model room to answer')));
	assert.ok(!chat.lines.some((line) => line.includes('Should not be asked.')));
});

// D5: a new case is activated by the harness, not by a model writing the
// receipt. The contract must exist before the investigator's first turn, and
// no role can write it.
test('a new case at Investigation is activated before the investigator starts, and no worker can write the activation receipt', async (t) => {
	let contractAtStart: string | undefined;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			if (toolResults(request).length > 0) return { text: 'Investigator finished.' };
			contractAtStart = existsSync(contractPath) ? readFileSync(contractPath, 'utf8') : undefined;
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/case-contract.json', content: '{"forged": true}' } }] };
		}
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	const contractPath = join(caseDir, 'data', 'case-contract.json');
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	assert.ok(contractAtStart, 'the contract existed when the investigator started');
	const events = (JSON.parse(contractAtStart) as { activation_events: Array<{ kind: string }> }).activation_events;
	assert.deepEqual(events.map((event) => event.kind), ['born']);
	assert.equal(readFileSync(contractPath, 'utf8'), contractAtStart, 'the investigator could not overwrite it');
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	assert.match(toolResults(worker[worker.length - 1]).join('\n'), /protected/);
});

// A case activate-new refuses (recorded passages without a contract) never
// starts the investigator; a legacy case (an old root findings.json) keeps
// contract 1.0 and starts without activation.
test('the investigator does not start on a case that cannot be activated, and a legacy case starts without activation', async (t) => {
	const rows = [
		{ file: 'data/source-expressions.json', content: '{"schema_version": "1.0", "project": "case-a", "expressions": [{"id": "p1"}]}', starts: false },
		{ file: 'findings.json', content: '{"project": "case-a", "findings": []}', starts: true },
	];
	for (const row of rows) {
		let investigatorRan = false;
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				investigatorRan = true;
				return { text: 'Investigator finished.' };
			}
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] }
				: { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = await caseAtInvestigation(space);
		await writeFile(join(caseDir, row.file), row.content);
		const chat = launch(t, baseEnv(space, fake.baseURL));

		await settledPrompt(chat, 'Continue.');

		assert.equal(investigatorRan, row.starts, row.file);
		assert.equal(existsSync(join(caseDir, 'data', 'case-contract.json')), false, row.file);
		if (!row.starts) {
			const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
			assert.match(toolResults(orchestrator[orchestrator.length - 1]).join('\n'), /could not be activated/);
		}
		chat.process.stdin.end();
		await chat.exited;
	}
});

// D6: the orchestrator cannot rewrite the files a worker owns around the
// checks the harness runs after that worker; its own files stay writable.
test('the orchestrator cannot write the files the workers write, and writes its own', async (t) => {
	const fake = await startFakeOpenAI((request) =>
		toolResults(request).length === 0
			? {
					toolCalls: [
						{ name: 'write_file', arguments: { path: 'data/methodology.json', content: '{"rewritten": true}' } },
						{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: '{"status": "false"}' } },
						{ name: 'write_file', arguments: { path: 'data/indicator-framework.json', content: '{"indicators": []}' } },
						{ name: 'write_file', arguments: { path: 'summary.md', content: 'Gate summary.' } },
						{ name: 'write_file', arguments: { path: 'brief-directions.txt', content: 'Approved brief.\n' } },
						{ name: 'write_file', arguments: { path: 'data/knowledge-proposal.json', content: '{}' } },
					],
				}
			: { text: 'Done.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	await writeFile(join(caseDir, 'data', 'fact-check.json'), '{"status": "verified"}');
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	assert.equal(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'), '{}\n');
	assert.equal(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8'), '{"status": "verified"}');
	// The dispatch-only orchestrator writes only the brief; the gate summary is the analyst's.
	assert.equal(existsSync(join(caseDir, 'summary.md')), false);
	assert.equal(await readFile(join(caseDir, 'brief-directions.txt'), 'utf8'), 'Approved brief.\n');
	const results = toolResults(fake.requests[fake.requests.length - 1]).join('\n');
	assert.equal(existsSync(join(caseDir, 'data', 'indicator-framework.json')), false);
	assert.equal(results.match(/another role writes/g)?.length, 4);
	assert.match(results, /summary\.md [^\n]*It is the analyst's file/);
	// fc-stall item 9: the refusal names the owner and the spawn to make.
	assert.match(results, /refused: data\/methodology\.json is a case file another role writes; this agent may not write it\. It is the investigator's file: to change it, call spawn_agent with agent "investigator" and a task starting "MODE: METHODOLOGY CORRECTION" that says what to change\./);
	assert.match(results, /data\/fact-check\.json [^\n]*It is the fact-checker's file: to change it, call spawn_agent with agent "fact-checker"/);
	// overnight-summary.md H-K2c: the analyst's proposal names its writer and the move.
	assert.match(results, /refused: data\/knowledge-proposal\.json is not a file this agent may write \([^)]*\)\. It is the analyst's file: to change it, call spawn_agent with agent "analyst" and a task at Ingest/);
	// H-K2b: the orchestrator is shown only the file tools it has.
	const orchestratorPrompt = systemText(fake.requests[0]);
	for (const tool of ['edit_file', 'list_files', 'grep_files']) assert.doesNotMatch(orchestratorPrompt, new RegExp(`\\b${tool}\\b`), tool);
	assert.match(orchestratorPrompt, /read-file, write-file -> `read_file`, `write_file`\./);
});

// D13: in an activated case the producers record passages only through
// scripts/write-source-expressions.py, which computes every hash and
// fingerprint. The investigator reaches it through source_expressions; a
// hand-written passages file is refused.
// step2-design.md section 2 (TUNE W1, W2): the investigator may instead state
// the claim and cite lines of its capture; code copies the text, lists the
// source, adds the evidence item and records the passage through the same writer.
test('a worker records a passage through the writer script, or by citing lines of its capture, and a hand write of the passages file is refused', async (t) => {
	const source = 'SearXNG is released under the AGPL-3.0 licence.\n';
	const claim = 'SearXNG is released under the AGPL-3.0 licence.';
	const finding = { project: 'case-a', schema_version: '1.1', findings: [{ id: 'F1', claim }] };
	const passage = {
		id: 'SX1',
		text: claim,
		anchor_ref: { path: 'research/licence.txt', line_start: 1, line_end: 1 },
		original_evidence_bundle_id: 'E1',
		language: 'en',
		// Codex review of fix14: only the harness sets direct_quote (quotes.ts,
		// for a quote found as written in a fetched capture); a worker's is dropped.
		direct_quote: true,
		finding_links: [{ finding_id: 'F1', relation: 'supports' }],
	};
	const steps = [
		{ name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify(finding) } },
		{ name: 'source_expressions', arguments: { action: 'fingerprint_findings' } },
		{ name: 'source_expressions', arguments: { action: 'write', cycle: 1, proposal: { expressions: [passage] } } },
		{ name: 'write_file', arguments: { path: 'data/source-expressions.json', content: '{"schema_version": "1.0", "expressions": []}' } },
		{ name: 'write_file', arguments: { path: 'data/source-positions.json', content: '{"positions": []}' } },
		// A cite outside the file is refused before any write.
		{ name: 'record_finding', arguments: { finding: { claim: 'The licence permits modification.', confidence: 'medium' }, cite: [{ path: 'research/licence-page.md', line_start: 3, line_end: 9 }] } },
		{ name: 'record_finding', arguments: { finding: { claim: 'The licence permits modification.', confidence: 'medium' }, cite: [{ path: 'research/licence-page.md', line_start: 2, line_end: 3 }] } },
		// Evidence typed beside a cite is replaced by the cited lines (Codex review of simp-review).
		{ name: 'record_finding', arguments: { finding: { claim: 'The licence permits distribution.', confidence: 'low', evidence: ['I', 'n'] }, cite: [{ path: 'research/licence-page.md', line_start: 3, line_end: 3 }] } },
		// A source the cite does not back is refused (step 2 Codex review, P1).
		{ name: 'record_finding', arguments: { finding: { claim: 'The licence is revoked.', confidence: 'low', sources: [{ url: 'https://invented.example/revoked' }] }, cite: [{ path: 'research/licence-page.md', line_start: 2, line_end: 2 }] } },
		// A corrected claim (same id, no cite) keeps the cited item's link in step (Codex review of simp-link).
		{ name: 'record_finding', arguments: { finding: { id: 'F2', claim: 'The licence permits modification of the software.', confidence: 'medium', evidence: ['You may modify the software\nand distribute modified versions.'], sources: [{ url: 'https://docs.example.org/licence', accessed: '2026-07-16T12:30:00Z', local_file: 'research/licence-page.md' }], evidence_bundle_refs: ['E2'] } } },
		// A grounding that says the lines do not suffice is linked as such, never as direct support.
		{ name: 'record_finding', arguments: { finding: { claim: 'The licence covers documentation.', confidence: 'low', grounding: { support_type: 'insufficient', source_role: 'primary', claim_elements_supported: [], missing_assumptions: ['documentation is not named'], confidence_cap: 'low', misgrounding_risk: 'The page does not mention documentation.', grounding_rationale: 'Only software is named.' } }, cite: [{ path: 'research/licence-page.md', line_start: 3, line_end: 3 }] } },
		// A file with no investigator fetch receipt, only a bundle row naming a URL,
		// cannot be cited: the row is not provenance (Codex review of simp-2, P1).
		{ name: 'record_finding', arguments: { finding: { claim: 'SearXNG is free software.', confidence: 'low' }, cite: [{ path: 'research/notes.md', line_start: 1, line_end: 1 }] } },
	];
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const step = steps[toolResults(request).length];
			return step ? { toolCalls: [step] } : { text: 'Investigator finished.' };
		}
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space);
	await mkdir(join(caseDir, 'research'));
	await writeFile(join(caseDir, 'research', 'licence.txt'), source);
	const sha256 = createHash('sha256').update(source).digest('hex');
	await writeFile(join(caseDir, 'research', 'notes.md'), 'SearXNG is free software.\n');
	// E5 lists the licence page but records another source: the cite route does not reuse it (Codex review of simp-link).
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', raw_path: 'research/licence.txt', sha256 }, { id: 'E9', raw_path: 'research/notes.md', source_url: 'https://forged.example/notes', accessed: '2026-01-01T00:00:00Z' }, { id: 'E5', raw_path: 'research/licence-page.md', source_url: 'https://elsewhere.example/licence', accessed: '2026-01-01T00:00:00Z' }] }));
	// The investigator's own fetch of the licence page (its receipt).
	await writeFile(join(caseDir, 'research', 'licence-page.md'), '# Licence\n  You may modify the software\nand distribute modified versions.\n');
	registerCapture(space.dataDir, caseDir, 'research/licence-page.md', 'https://docs.example.org/licence', 'investigator');
	const chat = launch(t, baseEnv(space, fake.baseURL));

	await settledPrompt(chat, 'Continue.');

	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const results = toolResults(worker[worker.length - 1]);
	assert.equal(results.length, steps.length, results.join('\n'));
	assert.match(results[3], /written only through the source_expressions tool/);
	assert.match(results[4], /written only through the source_expressions tool/);
	const expressions = JSON.parse(await readFile(join(caseDir, 'data', 'source-expressions.json'), 'utf8')) as {
		expressions: Array<{ id: string; created_by: string; expression_fingerprint: string; finding_links: Array<Record<string, string>> }>;
	};
	const [recorded] = expressions.expressions;
	assert.equal(recorded.id, 'SX1');
	assert.equal(recorded.created_by, 'investigator');
	assert.equal((recorded as { direct_quote?: unknown }).direct_quote, undefined);
	assert.match(results[2], /direct_quote is set by the harness only/);
	assert.equal(existsSync(join(caseDir, 'data', 'source-positions.json')), false);
	// The cited finding: refused out of range with the valid range, then recorded
	// with the copied text as its evidence, the receipt's URL as its source, a
	// new evidence item and a passage the Report may quote.
	assert.match(results[5], /lines 3-9 are outside the file \(lines 1-3\)[\s\S]*Nothing was written/);
	assert.match(results[6], /Finding id: F2\. Evidence item\(s\) E2 added[\s\S]*Passage\(s\) Q1 recorded/);
	assert.match(results[8], /sources "https:\/\/invented\.example\/revoked" are not among the cited captures \(https:\/\/docs\.example\.org\/licence\)[\s\S]*Nothing was written/);
	const copied = 'You may modify the software\nand distribute modified versions.';
	const findings = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')) as { findings: Array<Record<string, unknown>> };
	const cited = findings.findings.find((row) => row.id === 'F2')!;
	assert.deepEqual(cited.evidence, [copied], 'one entry per cite');
	assert.deepEqual(findings.findings.find((row) => row.claim === 'The licence permits distribution.')?.evidence, ['and distribute modified versions.'], 'typed evidence is replaced by the cited lines');
	assert.deepEqual(cited.sources, [{ url: 'https://docs.example.org/licence', accessed: '2026-07-16T12:30:00Z', local_file: 'research/licence-page.md' }]);
	assert.deepEqual(cited.evidence_bundle_refs, ['E2']);
	assert.ok(results[9].startsWith('wrote '), results[9]);
	assert.ok(results[10].includes('Finding id:'), results[10]);
	assert.ok(!findings.findings.some((row) => row.claim === 'The licence is revoked.'));
	assert.match(results[11], /cite works only on a capture your own fetch saved[\s\S]*Nothing was written/);
	assert.ok(!findings.findings.some((row) => row.claim === 'SearXNG is free software.'));
	const bundle = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')) as { items: Array<Record<string, unknown>> };
	assert.equal(bundle.items.find((item) => item.id === 'E2')?.source_url, 'https://docs.example.org/licence');
	const links = (item: Record<string, unknown> | undefined) => (item?.claim_links ?? []) as Array<Record<string, string>>;
	assert.equal(links(bundle.items.find((item) => item.id === 'E2')).find((link) => link.finding_id === 'F2')?.claim_text, 'The licence permits modification of the software.', 'the corrected claim is in the link');
	const documentation = findings.findings.find((row) => row.claim === 'The licence covers documentation.')!;
	assert.equal(links(bundle.items.find((item) => links(item).some((link) => link.finding_id === documentation.id))).find((link) => link.finding_id === documentation.id)?.support_type, 'insufficient');
	assert.equal(links(bundle.items.find((item) => item.id === 'E5')).length, 0, 'the item naming another source is not reused');
	const quoted = expressions.expressions.find((expression) => expression.id === 'Q1') as unknown as Record<string, unknown>;
	// The writer stores the selected lines as the file has them.
	assert.equal(quoted.text, `  ${copied}`);
	assert.equal(quoted.original_evidence_bundle_id, 'E2');
	assert.equal(quoted.direct_quote, true);
	// The case validator's own check: a verified verdict citing the passage
	// has its active supporting source expression, and every fingerprint holds.
	const link = recorded.finding_links[0];
	const factCheck = {
		schema_version: '1.1',
		claims: [
			{ finding_id: 'F1', claim_text: claim, verdict: 'verified', evidence_for: [{ source_expression_refs: [{ expression_id: 'SX1', expression_fingerprint: recorded.expression_fingerprint, finding_fingerprint: link.finding_fingerprint, link_fingerprint: link.link_fingerprint }] }] },
			// The cited finding's passage serves a verified row the same way.
			{ finding_id: 'F2', claim_text: 'The licence permits modification of the software.', verdict: 'verified', evidence_for: [{ source_expression_refs: [{ expression_id: 'Q1', expression_fingerprint: quoted.expression_fingerprint, finding_fingerprint: (quoted.finding_links as Array<Record<string, string>>)[0].finding_fingerprint, link_fingerprint: (quoted.finding_links as Array<Record<string, string>>)[0].link_fingerprint }] }] },
		],
	};
	const check = `
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("validate_case", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
case = Path(sys.argv[2])
load = lambda name: json.loads((case / "data" / name).read_text(encoding="utf-8"))
print(json.dumps(module.validate_source_expressions(case, load("source-expressions.json"), load("findings.json"), json.loads(sys.argv[3]), load("evidence-bundle.json"))))
`;
	const errors = await new Promise<string>((done, fail) =>
		execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', ['-c', check, join(CHECKOUT_ROOT, 'scripts', 'validate-case.py'), caseDir, JSON.stringify(factCheck)], (error, stdout) =>
			error ? fail(error) : done(stdout),
		),
	);
	assert.deepEqual(JSON.parse(errors), []);

	// step3c (reports/step3c-commit-design.md): an investigator that only reads
	// and never records gets a commit step on the local tiers. A cite of lines
	// it was never shown leaves no finding and reads them, nothing_found changes
	// nothing, and a cite of lines it was shown is recorded through the real tool.
	// On frontier no commit request is ever made, even with the switch forced on.
	for (const tier of ['12b', 'frontier'] as const) await t.test(`commit step (${tier})`, async (t) => {
		const answers = [
			{ name: 'record_finding', arguments: { finding: { claim: 'The page is about something else.', confidence: 'low' }, cite: [{ path: 'research/licence-page.md', line_start: 4, line_end: 4 }] } },
			{ name: 'nothing_found', arguments: { reason: 'Not sure yet.' } },
			{ name: 'record_finding', arguments: { finding: { claim: 'The licence permits modification.', confidence: 'medium' }, cite: [{ path: 'research/licence-page.md', line_start: 2, line_end: 2 }] } },
		];
		// inv-retest-def8674f.md: LFM3 answered with two record_finding calls; the first that passes is kept, the other named.
		const second = { name: 'record_finding', arguments: { finding: { claim: 'The licence allows redistribution.', confidence: 'low' }, cite: [{ path: 'research/licence-page.md', line_start: 2, line_end: 2 }] } };
		const commits: ChatRequest[] = [];
		const checks: ChatRequest[] = [];
		// The cycle's log as the investigator keeps it (the review fixture's), written on its last turn.
		const outputs = join(tmpdir(), `commit-log-${process.pid}-${tier}`);
		execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), outputs, 'investigation_gate', '--activated', '--clean']);
		const log = await readFile(join(outputs, 'data', 'investigation-log.json'), 'utf8');
		t.after(() => rm(outputs, { recursive: true, force: true }));
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				if (request.tool_choice === 'required') {
					commits.push(request);
					return { toolCalls: commits.length === 3 ? [answers[2], second] : [answers[commits.length - 1] ?? answers[2]] };
				}
				const done = toolResults(request).length;
				if (done === 0) return { toolCalls: [{ name: 'grep_files', arguments: { pattern: 'modify', path: 'research' } }] };
				// A different look each turn (the same call three times trips the loop guard).
				if (done === 13) return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/investigation-log.json', content: log } }] };
				return done < 13 ? { toolCalls: [{ name: 'list_files', arguments: { pattern: `research/*${done}*` } }] } : { text: 'Investigator finished.' };
			}
			// inv-retest-def8674f.md: the fact-checker answers on the committed finding; code verifies it from the re-fetch.
			// B20 (K4 Tor P3): first it tries record_assessment, a verified row with no passage ref,
			// then fetches a second source, which opens record_assessment, then answers judge_finding.
			if (isWorkerRequest(request, 'fact-checker')) {
				checks.push(request);
				const done = toolResults(request).length;
				return done === 0 ? { toolCalls: [{ name: 'record_assessment', arguments: { assessment: { verdict: 'verified', confidence: 'high', evidence_for: [{ description: 'The licence page says so.', source: 'https://docs.example.org/licence' }], evidence_against: [] } } }] }
					: done === 1 ? { toolCalls: [{ name: 'fetch', arguments: { url: 'https://mirror.example.net/licence-faq', save_as: 'fc-faq.md' } }] }
					: done === 2 ? { toolCalls: [{ name: 'judge_finding', arguments: { answer: 'supports', reason: 'The licence page says modification is allowed.' } }] }
					: { text: 'Checked.' };
			}
			const step = toolResults(request).length;
			return step === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] }
				: step === 1 && tier !== 'frontier' ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] } : { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = await caseAtInvestigation(space);
		await mkdir(join(caseDir, 'research'), { recursive: true });
		await writeFile(join(caseDir, 'research', 'licence-page.md'), '# Licence\n  You may modify the software\nand distribute modified versions.\nUnrelated footer.\n');
		// No bundle yet: the commit step's evidence item creates it.
		if (existsSync(join(caseDir, 'data', 'evidence-bundle.json'))) await unlink(join(caseDir, 'data', 'evidence-bundle.json'));
		registerCapture(space.dataDir, caseDir, 'research/licence-page.md', 'https://docs.example.org/licence', 'investigator');
		// The fact-checker's re-fetch of the licence page (a stand-in for the scraping seam).
		const seam = join(space.root, 'recheck-python');
		await writeFile(seam, `#!${process.execPath}\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({ markdown: '# Licence\\n  You may modify the software\\nand distribute modified versions.\\n', source_url: args.at(-1), provider: 'crawl4ai', status_code: 200 }));\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(PYTHON)}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_CODE_ROUTER: 'on', SPOTLIGHT_PYTHON: seam, SPOTLIGHT_RECHECK: 'on', SPOTLIGHT_ANONYMIZE_FETCH: 'true' });
		await settledPrompt(chat, 'Continue.');

		const findings = (existsSync(join(caseDir, 'data', 'findings.json')) ? JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')) : { findings: [] }) as { findings: Array<Record<string, unknown>> };
		if (tier === 'frontier') {
			assert.equal(commits.length, 0, 'no commit request on frontier');
			// Only the model's own requests: each ends with its task or a tool result, never an added harness turn.
			const investigator = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
			assert.ok(investigator.length >= 14 && investigator.every((request) => request.messages.at(-1)!.role === 'tool' || request.messages.filter((message) => message.role === 'user').length === 1));
			assert.ok(fake.requests.every((request) => request.tool_choice === undefined && !JSON.stringify(request.messages).includes('Commit step (from the harness')));
			assert.ok(!findings.findings.some((row) => row.claim === 'The licence permits modification.'));
			return;
		}
		assert.equal(commits.length, 3, 'three commit attempts, four turns apart');
		assert.deepEqual(toolNames(commits[0]), ['nothing_found', 'record_finding']);
		assert.match(JSON.stringify(commits[0].messages.at(-1)), /Commit step \(from the harness, not the member\)/);
		// Only the shown line is recorded; the unseen cite and nothing_found left nothing.
		assert.ok(!findings.findings.some((row) => row.claim === 'The page is about something else.'));
		// The dropped cite was not silent: the turn says so and reads the lines it named, and the worker then sees them.
		const read = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && request.tool_choice === undefined).at(-1)!.messages.flatMap((message) => (message.tool_calls ?? []) as Array<{ id: string; function: { name: string; arguments: string } }>).find((call) => call.function.name === 'read_file' && call.id.startsWith('commit-'))!;
		assert.deepEqual(JSON.parse(read.function.arguments), { path: 'research/licence-page.md', offset: 4, limit: 1 });
		assert.ok(fake.requests.some((request) => isWorkerRequest(request, 'investigator') && toolResults(request).some((text) => text.includes('Unrelated footer.'))));
		assert.ok(fake.requests.some((request) => request.messages.some((message) => message.role === 'assistant' && JSON.stringify(message.content ?? '').includes('Commit step: reading the cited lines; not recorded: the finding, because its cite names lines not shown to you'))));
		const committed = findings.findings.find((row) => row.claim === 'The licence permits modification.')!;
		assert.deepEqual(committed.evidence, ['You may modify the software'], 'a one-cite commit keeps evidence as a list of the cited text');
		// inv-retest-def8674f.md: the bundle the commit created carries its schema_version, the case validates and the fact-checker starts.
		assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).schema_version, '1.0');
		// Read from the chat: since code writes the summary, the page opens with no further model request to carry this.
		assert.ok(chat.lines.some((line) => line.includes('"type":"tool_execution_end"') && line.includes('"toolName":"spawn_agent"') && line.includes('[Harness checks after the investigator finished: all passed]') && line.includes('validate-case.py: PASSED')), 'the investigator\'s case check passed');
		// The cited item links the finding (direct), so the fact-checker's judge_finding verifies it and the fact-check validates.
		const bundle = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')) as { items: Array<{ id: string; created_by?: string; claim_links?: Array<Record<string, string>> }> };
		const citedItem = bundle.items.find((item) => item.created_by === 'investigator' && item.claim_links?.some((link) => link.finding_id === committed.id));
		assert.deepEqual(citedItem?.claim_links?.find((link) => link.finding_id === committed.id), { finding_id: committed.id, claim_text: 'The licence permits modification.', support_type: 'direct' });
		const factCheck = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8')) as { claims: Array<Record<string, unknown>> };
		assert.equal(factCheck.claims.find((claim) => claim.finding_id === committed.id)?.verdict, 'verified', JSON.stringify(factCheck.claims));
		try {
			execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir], { encoding: 'utf8' });
		} catch (error) {
			assert.fail(`validate-fact-check.py: ${String((error as { stdout?: string }).stdout ?? error)}`);
		}
		// B20: on a local tier the fact-checker is offered judge_finding and not record_assessment, its task
		// names judge_finding with no record_assessment templates, a record_assessment call is answered
		// with the route, and a fetched second source opens record_assessment.
		assert.ok(toolNames(checks[0]).includes('judge_finding') && !toolNames(checks[0]).includes('record_assessment'), toolNames(checks[0]).join(','));
		assert.match(userText(checks[0]), /judge_finding \{finding_id, answer/);
		assert.doesNotMatch(userText(checks[0]), /record_assessment argument templates/);
		assert.equal(toolResults(checks[1])[0], ASSESSMENT_HELD);
		assert.ok(toolNames(checks[2]).includes('record_assessment'), 'the second source opened record_assessment');
		const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && request.tool_choice === undefined);
		const recordCall = worker.flatMap((request) => request.messages.flatMap((message) => (message.tool_calls ?? []) as Array<{ id: string; function: { name: string; arguments: string } }>)).find((call) => call.function.name === 'record_finding')!;
		assert.ok(recordCall.id.startsWith('commit-'), recordCall.id);
		assert.deepEqual(JSON.parse(recordCall.function.arguments).cite, [{ path: 'research/licence-page.md', line_start: 2, line_end: 2 }]);
		// The second call was not recorded, and the turn names it to the model.
		assert.ok(!findings.findings.some((row) => row.claim === 'The licence allows redistribution.'));
		assert.ok(worker.some((request) => request.messages.some((message) => message.role === 'assistant' && JSON.stringify(message.content ?? '').includes('Commit step: kept the first finding; not recorded: call 2 (record_finding: one finding per commit step'))));
		// Continuity: after each fall-through the model's own request followed, and the worker finished on its own words.
		assert.ok(chat.lines.some((line) => line.includes('"type":"tool_execution_end"') && line.includes('"toolName":"spawn_agent"') && line.includes('Investigator finished.')));
	});
});

// K4 Tor P3 rerun (k4r-9ef5.md): D10 kept the case at Investigation with every
// finding claimed, and the model started another fact-checker. With no batch it
// was offered record_assessment and told to use it. On a local tier it is held
// as a batch fact-checker is, a judge with no re-fetch leaves the claim alone, and
// a second source still opens record_assessment. Frontier keeps its route.
test('a fact-checker started with no batch', async (t) => {
 for (const tier of ['12b', 'frontier'] as const) await t.test(tier, async (t) => {
	const checks: ChatRequest[] = [];
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			checks.push(request);
			const done = toolResults(request).length;
			return done === 0 ? { toolCalls: [{ name: 'record_assessment', arguments: { assessment: { finding_id: 'F1', verdict: 'verified', confidence: 'high', evidence_for: [], evidence_against: [] } } }] }
				: done === 1 ? { toolCalls: [{ name: 'judge_finding', arguments: { finding_id: 'F1', answer: 'supports', reason: 'It still reads the same.' } }] }
				: done === 2 ? { toolCalls: [{ name: 'fetch', arguments: { url: 'https://mirror.example.net/licence-notes', save_as: 'fc-notes.md' } }] }
				: { text: 'Checked again.' };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--clean', '--activated']);
	await unlink(join(caseDir, 'data', 'investigation-log.json'));
	const seam = join(space.root, 'fetch-python');
	await writeFile(seam, `#!${process.execPath}\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({ markdown: '# Notes\\n', source_url: args.at(-1), provider: 'crawl4ai', status_code: 200 }));\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(PYTHON)}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_PYTHON: seam, SPOTLIGHT_ANONYMIZE_FETCH: 'true' });

	await settledPrompt(chat, 'Continue.');

	assert.ok(checks.length >= 4, `the fact-checker ran: ${checks.length}`);
	const claim = () => (JSON.parse(readFileSync(join(caseDir, 'data', 'fact-check.json'), 'utf8')) as { claims: Array<Record<string, unknown>> }).claims.find((row) => row.finding_id === 'F1');
	if (tier === 'frontier') {
		assert.ok(toolNames(checks[0]).includes('record_assessment'), 'frontier keeps record_assessment');
		assert.match(userText(checks[0]), /Submit each complete judgment with record_assessment/);
		return;
	}
	assert.ok(toolNames(checks[0]).includes('judge_finding') && !toolNames(checks[0]).includes('record_assessment'), toolNames(checks[0]).join(','));
	assert.match(userText(checks[0]), /judge_finding \{finding_id, answer/);
	assert.doesNotMatch(userText(checks[0]), /Submit each complete judgment with record_assessment/);
	assert.equal(toolResults(checks[1])[0], ASSESSMENT_HELD);
	assert.match(toolResults(checks[2])[1], /F1 keeps its verified claim/);
	assert.equal(claim()?.verdict, 'verified', 'the judge did not downgrade the claim');
	assert.ok(toolNames(checks[3]).includes('record_assessment'), 'a second source opened record_assessment');
 });
});

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
	await briefApproved(space);
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

// 12B final arm on b479466b: the pi processes never got SPOTLIGHT_BSIG, so a
// worker's search checked protected sources with the installed bsig (no
// `wiki` verb) and every search failed. The stand-in interpreter records the
// environment the search seam is started with.
test('a worker\'s search seam gets the launcher\'s SPOTLIGHT_BSIG and XDG_*', async (t) => {
	const fake = await startFakeOpenAI(
		orchestratorScript([{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }], (request) =>
			toolResults(request).length === 0 ? { toolCalls: [{ name: 'search', arguments: { query: 'Acme' } }] } : { text: 'Investigator finished.' },
		),
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const seen = join(space.root, 'search-env.json');
	const python = join(space.root, 'python-recording-search');
	await writeFile(
		python,
		`#!${process.execPath}\nconst { execFileSync } = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.search')) { require('node:fs').writeFileSync(${JSON.stringify(seen)}, JSON.stringify(process.env)); process.stdout.write('[]'); }\nelse { try { process.stdout.write(execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exitCode = error.status ?? 1; } }\n`,
		{ mode: 0o755 },
	);
	const bsig = join(space.root, 'engine', 'bsig');
	const xdg = join(space.root, 'xdg');
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_BSIG: bsig, XDG_CONFIG_HOME: xdg, XDG_DATA_HOME: join(xdg, 'data') });

	await settledPrompt(chat, 'Continue.');

	const env = JSON.parse(await readFile(seen, 'utf8')) as Record<string, string>;
	assert.equal(env.SPOTLIGHT_BSIG, bsig);
	assert.equal(env.XDG_CONFIG_HOME, xdg);
	assert.equal(env.XDG_DATA_HOME, join(xdg, 'data'));
});

// Inside Osaurus, Engine runs the scraping seam on its runtime pack's Python:
// Crawl4AI finds the pack's Chromium and its cache only through these two
// variables, so both must survive the launcher's child environment and the
// fetch tool's seam allowlist. Tor mode keeps the harness off the network.
test('a worker\'s fetch seam gets the launcher\'s runtime pack Chromium and Crawl4AI cache folders', async (t) => {
	const fake = await startFakeOpenAI(
		orchestratorScript([{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }], (request) =>
			toolResults(request).length === 0 ? { toolCalls: [{ name: 'fetch', arguments: { url: 'https://registry.example.org/company/42' } }] } : { text: 'Investigator finished.' },
		),
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const seen = join(space.root, 'fetch-env.json');
	const python = join(space.root, 'python-recording-fetch');
	const envelope = JSON.stringify({ markdown: '# Company 42', source_url: 'https://registry.example.org/company/42', provider: 'crawl4ai', status_code: 200 });
	await writeFile(
		python,
		`#!${process.execPath}\nconst { execFileSync } = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) { require('node:fs').writeFileSync(${JSON.stringify(seen)}, JSON.stringify(process.env)); process.stdout.write(${JSON.stringify(envelope)}); }\nelse { try { process.stdout.write(execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exitCode = error.status ?? 1; } }\n`,
		{ mode: 0o755 },
	);
	const pack = { PLAYWRIGHT_BROWSERS_PATH: join(space.root, 'pack', 'playwright'), CRAWL4_AI_BASE_DIRECTORY: join(space.root, 'runtime-data', 'crawl4ai') };
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_ANONYMIZE_FETCH: 'true', ...pack });

	await settledPrompt(chat, 'Continue.');

	const env = JSON.parse(await readFile(seen, 'utf8')) as Record<string, string>;
	assert.equal(env.PLAYWRIGHT_BROWSERS_PATH, pack.PLAYWRIGHT_BROWSERS_PATH);
	assert.equal(env.CRAWL4_AI_BASE_DIRECTORY, pack.CRAWL4_AI_BASE_DIRECTORY);
});

test('after a planning task the harness runs the methodology check, not the findings check', async (t) => {
 // B18 (k2-tor.md P1: LFM3 planned by reading and never wrote): on 12b a PLANNING
 // investigator that only searches gets the write commit step; not_ready leaves
 // it on its own route, and the next step four requests later writes the plan.
 for (const route of ['own', 'committed'] as const) await t.test(route, async (t) => {
	const plan = { name: 'write_file', arguments: { path: 'data/methodology.json', content: '{"project": "case-a"}' } };
	let commits = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			if (isWriteCommit(request)) return ++commits === 1 ? { toolCalls: [{ name: 'not_ready', arguments: { reason: 'Still reading.' } }] } : { toolCalls: [plan] };
			const done = toolResults(request).length;
			if (route === 'own') return done === 0 ? { toolCalls: [plan] } : { text: 'Plan written.' };
			return writeCalls(request).length ? { text: 'Plan written.' } : search(done);
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
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), ...(route === 'committed' ? { SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_CODE_ROUTER: 'on' } : {}) });

	await settledPrompt(chat, 'Continue.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const delegated = route === 'own'
		? toolResults(orchestrator[orchestrator.length - 1]).join('\n')
		: chat.lines.filter((line) => line.includes('"type":"tool_execution_end"') && line.includes('"toolName":"spawn_agent"')).join('\n');
	assert.match(delegated, /validate-methodology-navigator\.py: FAILED/);
	assert.doesNotMatch(delegated, /validate-case\.py/);
	if (route === 'own') return;
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && !isWriteCommit(request));
	assert.equal(commits, 2);
	// not_ready: turn 7's request is the worker's own (six results); the second step answers turn 11.
	assert.equal(toolResults(worker[6]).length, 6);
	const [written] = writeCalls(worker.at(-1)!);
	assert.ok(written[0].startsWith('commit-') && written[1] === 'data/methodology.json', JSON.stringify(written));
	assert.match(written[2], /wrote data\/methodology\.json/);
	assert.equal(toolResults(worker.at(-1)!).length, 11);
 });

 // B28: on 12b with the router on, once the plan is written and passes its
 // check the worker's turn ends, so its next write never runs; an invalid write
 // before it leaves the worker on its route. Frontier is unchanged.
 for (const tier of ['12b', 'frontier'] as const) await t.test(`a valid plan, then another write (${tier})`, async (t) => {
	const space = await workspace(t);
	const sample = join(space.root, 'planned');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), sample, 'methodology']);
	const valid = JSON.parse(await readFile(join(sample, 'data', 'methodology.json'), 'utf8'));
	const contents = ['{"project": "case-a"}', JSON.stringify(valid), JSON.stringify({ ...valid, tools_required: [...(valid.tools_required ?? []), 'Another registry'] })];
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const done = toolResults(request).length;
			return done < 3 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: contents[done] } }] } : { text: 'Plan written.' };
		}
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: PLANNING' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_CODE_ROUTER: 'on', SPOTLIGHT_CONFIG: await gateConfig(space) });

	await settledPrompt(chat, 'Continue.');

	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	const results = toolResults(worker.at(-1)!);
	// The invalid first write was accepted as today, and the worker wrote again.
	assert.match(results[0], /wrote data\/methodology\.json/);
	assert.match(results[1], /wrote data\/methodology\.json/);
	const tools = (JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')).tools_required ?? []) as string[];
	const delegated = chat.lines.filter((line) => line.includes('"type":"tool_execution_end"') && line.includes('"toolName":"spawn_agent"')).join('\n');
	assert.match(delegated, /validate-methodology-navigator\.py: PASSED/);
	if (tier === 'frontier') {
		assert.equal(worker.length, 4, 'the third write ran and the worker answered');
		assert.ok(tools.includes('Another registry'));
		return;
	}
	assert.equal(worker.length, 3, 'the turn ended on the valid plan; the third write never ran');
	assert.ok(!tools.includes('Another registry'), 'the valid plan stands');
	assert.match(delegated, /wrote data\/methodology\.json, which passes its check, and was stopped there/);
 });
});

// k2-tor.md P1: code started PLANNING 11 times after identical progress
// watchdog stops. After two such dispatches with no change to the case's
// records, resolve starts no third and names the member's options; a member
// turn lets code start it again.
test('a PLANNING investigator stopped twice by the progress watchdog with no change is not started a third time; resolve names the member\'s options, and a member turn retries', async (t) => {
	let wanted = 3;
	let resolves = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return search(toolResults(request).length);
		if (resolves < wanted) { resolves += 1; return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] }; }
		return { text: 'The planner made no progress.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
	// 12b: the tier with the text-turn nudge, which must not follow the stop.
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: '12b' });
	const threads = () => fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0 && /MODE: PLANNING/.test(userText(request))).length;

	await settledPrompt(chat, 'Continue.', 120_000);

	assert.equal(threads(), 2, 'two PLANNING dispatches, no third');
	const held = lastResolution(chat) as { worker_stalled?: unknown; next_action?: unknown; fixed_steps?: unknown };
	assert.deepEqual(held.worker_stalled, { step: 'investigator (investigator PLANNING)', attempts: 2 });
	assert.equal(held.fixed_steps, undefined, 'the held resolve starts nothing');
	assert.ok(!fake.requests.some((request) => JSON.stringify(request.messages).includes('Your turn ended with text')), 'no text-turn nudge after the stop (Codex review of simp-redispatch, P2)');
	assert.match(String(held.next_action), /2 times in a row, and each time the progress watchdog stopped it with no change to the case files, so code does not start it again\. Tell the member [^]*amend the brief[^]*retry/);

	// Continuity: the member's turn lets code start the planner again.
	wanted = resolves + 1;
	await settledPrompt(chat, 'Please retry.', 120_000);
	assert.equal(threads(), 3, 'the member turn retried the planner');
});

test('with the Research switch off, the methodology check refuses a research plan; with it on, the plan is checked as usual, and a plan of content only gets its paperwork from code', async (t) => {
	const sample = await readFile(join(CHECKOUT_ROOT, 'tests/fixtures/research/methodology-research.sample.json'), 'utf8');
	for (const research of ['off', 'on', 'content-only', 'content-navigator']) {
		const methodology = research === 'content-only' ? CONTENT_ONLY_PLAN : sample;
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				const done = toolResults(request).length;
				if (research !== 'content-navigator') return done ? { text: 'Plan written.' } : { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: methodology } }] };
				// The planner picks a tool from the lookup code ran, by its receipt.
				// K2 Eurostat P1 (TUNE K2-10): it also writes code-owned fields wrong,
				// status "used", a query with no receipt fields and a query with no
				// receipt at all, then an edit_file correction sets status "used" again.
				const receipt = /research\/navigator\/[0-9a-f-]{36}-receipt\.json/.exec(userText(request))?.[0];
				const plan = { ...JSON.parse(CONTENT_ONLY_PLAN), navigator: { required: false, used: true, status: 'used', queries: [{ receipt_path: receipt, selected_tools: ['WhiteIntel'], rejected_tools: [{ tool: 'Other', reason: 'Paid only.' }] }, { direction: 'Eurostat table lookup', interface: 'cli', query: 'Eurostat unemployment', selected_tools: ['Invented tool'] }] } };
				if (done === 0) return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: JSON.stringify(plan) } }] };
				if (done === 1) return { toolCalls: [{ name: 'edit_file', arguments: { path: 'data/methodology.json', old_string: '"outcome": "used"', new_string: '"status": "used"' } }] };
				return { text: 'Plan written.' };
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
		// The install config says the opposite, so only the harness's switch can decide.
		const config = join(space.root, 'spotlight-config.json');
		await writeFile(config, JSON.stringify({ model_tier: '12b', integrations: { research_swarms: { enabled: research === 'off' } } }));
		// content-only: the Navigator lookup runs, against a navigator that is not set up.
		const stubs = join(space.root, 'stub-bin');
		if (research.startsWith('content')) {
			await mkdir(stubs, { recursive: true });
			await writeFile(join(stubs, 'navigator'), research === 'content-only'
				? '#!/bin/sh\necho "navigator: not signed in" >&2\nexit 1\n'
				: '#!/bin/sh\necho \'{"tools": [{"tool_name": "WhiteIntel", "category": "companies"}]}\'\n', { mode: 0o755 });
		}
		const env = baseEnv(space, fake.baseURL);
		const content = research.startsWith('content');
		const chat = launch(t, { ...env, SPOTLIGHT_CONFIG: content ? join(CHECKOUT_ROOT, 'tests/fixtures/research/install-config.sample.json') : config, SPOTLIGHT_RESEARCH_SWARMS: research === 'off' ? 'off' : 'on', ...(content ? { SPOTLIGHT_NAVIGATOR_PRESTEP: 'on', PATH: `${stubs}:${env.PATH}` } : {}) });

		await settledPrompt(chat, 'Continue.');

		const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
		const delegated = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
		if (research === 'content-navigator') {
			const task = userText(fake.requests.find((request) => isWorkerRequest(request, 'investigator'))!);
			assert.match(task, /NAVIGATOR \(code looked up each brief direction before planning[\s\S]*WhiteIntel/);
			const written = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'));
			assert.equal(written.navigator.used, true);
			assert.match(written.navigator.queries[0].response_sha256, /^[0-9a-f]{64}$/);
			// Code wrote the block from the receipts, after the write and after the edit.
			assert.deepEqual(Object.keys(written.navigator).sort(), ['outcome', 'queries', 'required', 'used']);
			assert.equal(written.navigator.outcome, 'used');
			assert.ok(written.navigator.queries.every((query: { receipt_path?: string; retrieved_at?: string }) => query.receipt_path && query.retrieved_at), 'every query is a receipt\'s');
			assert.deepEqual(written.navigator.queries[0].selected_tools, ['WhiteIntel']);
			assert.deepEqual(written.navigator.queries[0].rejected_tools, [{ tool: 'Other', reason: 'Paid only.' }]);
			assert.ok(!JSON.stringify(written.navigator).includes('Invented tool'), 'a query with no receipt is dropped');
			assert.deepEqual(written.tools_required, ['fetch', 'WhiteIntel']);
			assert.match(delegated, /validate-methodology-navigator\.py: PASSED/, delegated);
			continue;
		}
		if (research === 'content-only') {
			const task = userText(fake.requests.find((request) => isWorkerRequest(request, 'investigator'))!);
			assert.match(task, /NAVIGATOR: code tried to look up the brief's directions and Navigator was not available/);
			const written = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'));
			assert.deepEqual(written.brief_directions, ['Who owns the fixture company?']);
			assert.equal(written.investigation_plan[0].direction_id, 'd1');
			assert.deepEqual(written.tools_required, ['fetch']);
			assert.deepEqual([written.navigator.used, written.navigator.outcome, typeof written.navigator.fallback_reason], [false, 'unavailable', 'string']);
			const plan = written.research_plan;
			assert.deepEqual([plan.topics[0].topic_id, plan.topics[0].token_budget, plan.mode, plan.round_budget_usd, plan.case_budget_usd], ['d1-web', 800000, 'openrouter_swarm', 1, 1]);
			assert.deepEqual(plan.egress, [{ topic_id: 'd1-web', destination: 'public_web' }]);
			assert.deepEqual(plan.keys, [{ name: 'FIRECRAWL_API_KEY', topics: ['d1-web'] }]);
			assert.deepEqual(plan.consent, { swarm: true });
			// Continuity: the plan passes its check, so the Methodology gate is next.
			assert.match(delegated, /validate-methodology-navigator\.py: PASSED/, delegated);
			continue;
		}
		assert.match(delegated, /validate-methodology-navigator\.py/, research);
		if (research === 'off') assert.match(delegated, /Research is off; turn on Research in the app to add a research plan/);
		else assert.doesNotMatch(delegated, /Research is off/);
		// Shakedown on 26683b3c: no prompt said the switch was on, so no plan was written.
		const planner = fake.requests.find((request) => isWorkerRequest(request, 'investigator'));
		assert.match(systemText(planner!), new RegExp(`Research is ${research} for this case`), research);
	}
});

// LFM3 r3 kde stage 3 on Osaurus (03f008b0): the analyst's proposal failed
// interface 1, which it never saw, and it read and grepped until stopped.
test('an analyst proposal that fails interface 1 is refused when written, and after a valid one the analyst\'s turn ends and the batch is built', async (t) => {
 // committed (B18, k2-eurostat.md P5: LFM3 read and never proposed): on 12b the
 // analyst only reads; the write commit step writes the invalid proposal, its
 // refusal reaches the analyst, and the analyst's own route writes the valid one.
 // B21 (K4 Tor P5): reply, the analyst puts the proposal in its reply as JSON instead of writing it.
 // Codex review of simp-stack: reply-after, a valid reply after an accepted write replaces nothing.
 for (const route of ['own', 'committed', 'reply', 'reply-after'] as const) await t.test(route, async (t) => {
	const invalid = { ...PROPOSAL, claims: [{ ...PROPOSAL.claims[0], statement: { ...PROPOSAL.claims[0].statement, object: { text: 7 } } }] };
	const another = { ...PROPOSAL, claims: [{ ...PROPOSAL.claims[0], statement: { ...PROPOSAL.claims[0].statement, object: { text: 'A different object' } } }] };
	const write = (proposal: unknown) => ({ toolCalls: [{ name: 'write_file', arguments: { path: 'data/knowledge-proposal.json', content: JSON.stringify(proposal) } }] });
	const fake = await startFakeOpenAI(
		delegateToAnalyst((request) => {
			if (isWriteCommit(request)) return write(invalid);
			const done = toolResults(request).length;
			if (route === 'reply-after') return done === 0 ? write(PROPOSAL) : { text: `Here is the proposal again.\n\n\`\`\`json\n${JSON.stringify(another, null, 2)}\n\`\`\`` };
			if (route === 'reply') return { text: `Here is the proposal.\n\n\`\`\`json\n${JSON.stringify(JSON.stringify(request.messages).includes('Your reply held JSON') ? PROPOSAL : invalid, null, 2)}\n\`\`\`` };
			const first = route === 'own' ? 0 : 6;
			if (route === 'committed' && done < first) return { toolCalls: [{ name: 'read_file', arguments: { path: 'data/findings.json', offset: done + 1, limit: 1 } }] };
			if (route === 'own' && done === 0) return write(invalid);
			if (done === first + 1) return write(PROPOSAL);
			return { toolCalls: [{ name: 'read_file', arguments: { path: 'data/findings.json' } }] };
		}),
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtIngest(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), ...(route === 'committed' ? { SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_CODE_ROUTER: 'on' } : {}) });

	await settledPrompt(chat, 'Continue.');

	const analyst = fake.requests.filter((request) => isWorkerRequest(request, 'analyst') && !isWriteCommit(request));
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'analyst'));
	if (route === 'reply-after') {
		const kept = JSON.parse(await readFile(join(caseDir, 'data', 'knowledge-proposal.json'), 'utf8'));
		assert.deepEqual(kept.claims, PROPOSAL.claims, 'the accepted proposal stands');
		assert.doesNotMatch(toolResults(orchestrator.at(-1)!).join('\n'), /\[Harness: the JSON in this reply was written/);
		assert.ok(existsSync(join(caseDir, 'data', 'knowledge-batch.json')), 'the harness built the batch after the analyst ended');
		return;
	}
	if (route === 'reply') {
		// The invalid object is refused with the validator's text as the next prompt; the valid one is written as it was sent.
		assert.equal(analyst.length, 2);
		assert.match(JSON.stringify(analyst[1].messages.at(-1)), /Your reply held JSON for data\/knowledge-proposal\.json, not a write\. The harness offered it to write_file, which refused it: refused: data\/knowledge-proposal\.json does not match interface 1: \$\.claims\[0\]\.statement\.object\.text: must be string/);
		const written = JSON.parse(await readFile(join(caseDir, 'data', 'knowledge-proposal.json'), 'utf8'));
		assert.deepEqual(written.claims, PROPOSAL.claims, 'no field added to or changed in the model\'s claims');
		// Continuity: the analyst's answer says what code did and the batch is built.
		assert.ok(toolResults(orchestrator.at(-1)!).join('\n').includes('[Harness: the JSON in this reply was written to data/knowledge-proposal.json through write_file, which accepted it.]'));
		assert.ok(existsSync(join(caseDir, 'data', 'knowledge-batch.json')), 'the harness built the batch after the analyst ended');
		return;
	}
	const first = route === 'own' ? 0 : 6;
	assert.equal(analyst.length, route === 'own' ? 3 : 8, 'no analyst request after the one that went on past a valid proposal');
	const results = toolResults(analyst[analyst.length - 1]);
	assert.match(results[first], /^refused: data\/knowledge-proposal\.json does not match interface 1: \$\.claims\[0\]\.statement\.object\.text: must be string/);
	assert.match(results[first + 1], /wrote data\/knowledge-proposal\.json/);
	const delegated = chat.lines.filter((line) => line.includes('"type":"tool_execution_end"') && line.includes('"toolName":"spawn_agent"')).join('\n');
	assert.ok(delegated.includes(JSON.stringify(PROPOSAL_DONE).slice(1, 40)), 'the orchestrator gets the stop line as the analyst\'s answer');
	assert.ok(existsSync(join(caseDir, 'data', 'knowledge-batch.json')), 'the harness built the batch after the analyst ended');
	const writes = writeCalls(analyst.at(-1)!);
	assert.deepEqual(writes.map(([id]) => id.startsWith('commit-')), route === 'own' ? [false, false] : [true, false], 'the refused write was the commit step\'s; the valid one the analyst\'s own');
 });
});

// P6: the notice must reach the RPC client as a notify request and stay out of what the model reads.
test('on a local tier a worker\'s start and end reach the chat client as notices, outside the transcript; frontier sends none', async (t) => {
	for (const tier of ['12b', 'frontier']) {
		const fake = await startFakeOpenAI(delegateToAnalyst(() => ({ text: 'Stopped.' })));
		t.after(() => fake.close());
		const space = await workspace(t);
		await caseAtIngest(space);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });
		await settledPrompt(chat, 'Continue.');
		const notices = chat.lines.map((line) => JSON.parse(line) as { type?: string; method?: string; message?: string }).filter((record) => record.type === 'extension_ui_request' && record.method === 'notify').map((record) => record.message);
		assert.deepEqual(notices, tier === 'frontier' ? [] : ['The analyst started.', 'The analyst finished.'], tier);
		assert.doesNotMatch(JSON.stringify(fake.requests.at(-1)!.messages), /The analyst started/);
	}
});

test('on a local tier open_review holds the turn; a decision on the record carries the case on in the same chat turn, as a member turn', { skip: process.platform === 'linux' && 'the review child does not start on Linux CI' }, async (t) => {
	const fake = await startFakeOpenAI(holdScript);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = atGate(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_REVIEW_HOLD_MS: '60000', SPOTLIGHT_CODE_ROUTER: 'on' });

	const turn = settledPrompt(chat, 'Continue.', 30_000);
	await waitUntil(() => notices(chat).includes(REVIEW_HOLD_NOTICE), 15_000);
	assert.equal(settles(chat), 0, 'the turn stays open while the member decides');
	assert.match(REVIEW_HOLD_NOTICE, /press Stop/);
	memberApprovesGate(caseDir);
	await turn;

	assert.equal(settles(chat), 1, 'one chat turn from the page to the next step');
	assert.equal(shownText(chat).split(DECISION_RECORDED).length - 1, 1, 'Osaurus shows text deltas only, so the line must stream, once');
	assert.ok(chat.lines.filter((line) => line.includes('"type":"agent_start"')).length >= 2, 'a fresh run, so per-run limits start over');
	assert.match(chat.stderr(), /the decision on the page counts as the member's turn/, 'and the member-turn resets, which a fresh run does not fire');
	assert.notEqual(lastResolution(chat).phase, 'investigation_gate_approval', 'code resolved past the gate');
	assert.ok(!chat.lines.some((line) => line.includes(REVIEW_OPEN_STOP)));
});

// Real launch dfdad41e: Ingest finished and the last line in the chat was still the hold notice.
test('a decision that completes the case ends the held turn on code\'s closing line, as text the chat shows', { skip: process.platform === 'linux' && 'the review child does not start on Linux CI' }, async (t) => {
	const fake = await startFakeOpenAI(holdScript);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'ingest', '--activated', '--clean', '--proposal']);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_REVIEW_HOLD_MS: '60000', SPOTLIGHT_CODE_ROUTER: 'on' });

	const turn = settledPrompt(chat, 'Continue.', 40_000);
	await waitUntil(() => notices(chat).includes(REVIEW_HOLD_NOTICE), 20_000);
	execFileSync(PYTHON, [SEAM, 'decide-ingest', 'declined', '--decided-by', 'journalist:fixture', '--decided-at', '2026-08-23T12:00:00Z', caseDir]);
	await turn;

	const shown = shownText(chat);
	assert.match(shown, /Case complete: \d+ findings?/, shown);
	assert.ok(shown.lastIndexOf('Case complete') > shown.lastIndexOf(DECISION_RECORDED), 'the closing line is the last thing said');
	assert.equal(settles(chat), 1);
});

test('past the limit, the turn ends as it always did; Stop ends the hold at once; frontier never holds', { skip: process.platform === 'linux' && 'the review child does not start on Linux CI' }, async (t) => {
	for (const [name, env] of [['limit', { SPOTLIGHT_REVIEW_HOLD_MS: '300' }], ['frontier', { SPOTLIGHT_REVIEW_HOLD_MS: '60000', SPOTLIGHT_MODEL_TIER: 'frontier' }]] as const) {
		const fake = await startFakeOpenAI(holdScript);
		t.after(() => fake.close());
		const space = await workspace(t);
		atGate(space);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), ...env });
		await settledPrompt(chat, 'Continue.', 20_000);
		assert.equal(fake.requests.length, 1, name);
		assert.equal(notices(chat).includes(REVIEW_HOLD_NOTICE), name === 'limit', name);
		assert.ok(chat.lines.some((line) => line.includes(REVIEW_OPEN_STOP)), `${name}: the fallback line`);
		if (name === 'limit') assert.ok(notices(chat).includes(REVIEW_OPEN_STOP), 'a replaced line reaches Osaurus as a notice');
	}

	const fake = await startFakeOpenAI(holdScript);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = atGate(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_REVIEW_HOLD_MS: '60000' });
	const turn = settledPrompt(chat, 'Continue.', 10_000 + 20_000);
	await waitUntil(() => notices(chat).includes(REVIEW_HOLD_NOTICE), 15_000);
	const stopped = Date.now();
	chat.send({ type: 'abort' });
	await turn;
	assert.ok(Date.now() - stopped < 8_000, 'inside Osaurus\'s 8 s Stop grace');
	// A decision after Stop keeps option B: the member's next message moves the case.
	memberApprovesGate(caseDir);
	await settledPrompt(chat, 'I decided.', 20_000);
	assert.equal(settles(chat), 2, 'the chat is free again after Stop');
});

// Lab OptiQ leg on d3b8305f (kseeds/u9mac-od3b81): the analyst left [verdict] in summary.md and five refusals ended the turn.
test('at the gate a summary the check refuses is rebuilt by code on a local tier, and the page opens; frontier keeps the refusal', { skip: process.platform === 'linux' && 'the review child does not start on Linux CI' }, async (t) => {
	for (const tier of ['12b', 'frontier']) {
		const fake = await startFakeOpenAI(holdScript);
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = atGate(space);
		await writeFile(join(caseDir, 'summary.md'), '- Finding ID [id]: Verdict [verdict]\n');
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier });
		await settledPrompt(chat, 'Continue.', 20_000);
		const written = await readFile(join(caseDir, 'summary.md'), 'utf8');
		if (tier === 'frontier') {
			assert.equal(chat.opened().length, 0);
			assert.match(written, /\[verdict\]/);
		} else {
			assert.equal(chat.opened().length, 1, 'the page opened');
			assert.doesNotMatch(written, /\[verdict\]/);
			assert.match(execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'scripts', 'validate-case.py'), caseDir], { encoding: 'utf8' }), /PASS|valid/i);
		}
	}
});

// A failure at the gate that is not the summary: no worker may repair it there, but the page shows it and can send the case back.
test('a gate check failure with no repair route opens the page at once and ends on code\'s stop, naming it and the next step; frontier keeps the refusal', { skip: process.platform === 'linux' && 'the review child does not start on Linux CI' }, async (t) => {
	for (const tier of ['12b', 'frontier']) {
		const fake = await startFakeOpenAI(() => ({ toolCalls: [{ name: 'open_review', arguments: {} }] }));
		t.after(() => fake.close());
		const space = await workspace(t);
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), join(space.casesRoot, 'case-a'), 'investigation_gate']);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_REVIEW_HOLD_MS: '300' });
		await settledPrompt(chat, 'Continue.', 30_000);
		const said = chat.lines.filter((line) => line.includes('"type":"message_end"') && line.includes('Spotlight stopped')).join('\n');
		if (tier === 'frontier') {
			assert.equal(chat.opened().length, 0, 'frontier refuses as before');
			continue;
		}
		assert.equal(chat.opened().length, 1, 'the page opened');
		assert.equal(fake.requests.length, 1, 'no refusal loop');
		assert.match(said, /did not pass their check[\s\S]*The check found these problems\.[\s\S]*send the case back to Investigation/);
		assert.ok(!said.includes(REVIEW_OPEN_STOP));
		assert.ok(notices(chat).some((text) => text.includes('send the case back to Investigation')), 'the hold names it while the member decides');
	}
});

// D1 loads on demand: the router waits for a slow answer. A failure never locks the member out: the model takes the turn, with a notice.
test('the router waits out a cold decision model, and when it fails the model takes the turn and the member is told', async (t) => {
	for (const [name, status, delay] of [['slow', 200, 9_000], ['error', 503, 0]] as const) {
		const decision = createServer((_req, res) => {
			_req.resume();
			setTimeout(() => {
				res.writeHead(status, { 'content-type': 'application/json' });
				res.end(JSON.stringify({ answers: { intent: { choice: 'question', probabilities: { question: 0.9 } } } }));
			}, delay);
		});
		await new Promise<void>((done) => decision.listen(0, '127.0.0.1', done));
		t.after(() => decision.close());
		const fake = await startFakeOpenAI(() => ({ text: 'The model answered.' }));
		t.after(() => fake.close());
		const space = await workspace(t);
		const config = join(space.root, 'decision-config.json');
		await writeFile(config, JSON.stringify({ integrations: { decisions: { endpoint: { url: `http://127.0.0.1:${(decision.address() as AddressInfo).port}/v1/systemone`, credential_env: 'SPOTLIGHT_NO_KEY', api: 'openrouter-decisions' } } } }));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CODE_ROUTER: 'on', SPOTLIGHT_CONFIG: config });
		await settledPrompt(chat, 'What is the status?', 30_000);
		if (name === 'slow') {
			assert.match(chat.stderr(), /\[D1 question/, chat.stderr().slice(-400));
			assert.equal(fake.requests.length, 1);
			continue;
		}
		assert.equal(fake.requests.length, 1, 'the model takes the turn');
		assert.ok(chat.lines.some((line) => line.includes('The model answered.')), 'and the member gets its reply');
		assert.deepEqual(notices(chat).filter((text) => text.includes('did not answer (HTTP 503)')).length, 1, 'with one notice');
		assert.doesNotMatch(JSON.stringify(fake.requests[0].messages), /did not answer/, 'outside the transcript');
	}
});

// Case 7656d7ba: 'As of today. ... Approved, proceed' got the same Brief next_action as the first message, and the model asked again.
test('at the Brief a member\'s reply changes what resolve says, and following it writes the brief and moves the case on; frontier keeps its text', async (t) => {
	for (const tier of ['12b', 'frontier']) {
		const fake = await startFakeOpenAI((request) => {
			const last = request.messages.at(-1)!;
			if (last.role === 'user') return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
			const said = String(last.content);
			if (said.includes('Write brief-directions.txt now')) return { toolCalls: [{ name: 'write_file', arguments: { path: 'brief-directions.txt', content: 'Who runs the Tor Project, as of today.\n' } }] };
			return { text: 'Here is the direction. Do you approve?' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_CODE_ROUTER: 'off' });

		await settledPrompt(chat, 'Who runs the Tor Project?', 20_000);
		assert.equal(lastResolution(chat).next_action, NEXT_BRIEF, 'the first message gets the proposal step');
		await settledPrompt(chat, 'As of today. Core leadership only. Approved, proceed.', 20_000);
		const written = existsSync(join(space.casesRoot, 'case-a', 'brief-directions.txt'));
		assert.equal(written, tier !== 'frontier', tier);
		if (tier !== 'frontier') {
			assert.ok(chat.lines.some((line) => line.includes('"type":"tool_execution_end"') && line.includes('approved in any words')), 'the reply changed the step');
			assert.doesNotMatch(execFileSync(PYTHON, [SEAM, 'status', join(space.casesRoot, 'case-a')], { encoding: 'utf8' }), /brief/, 'the case left the Brief');
		}
	}
});

// Case 2bd8d15c: about 10 corrections in an hour with attempts {}: validate-case passed, so each read as fixed
// while validate-fact-check still named the same [investigator] fault, and the resolver sent the next one.
test('a correction that leaves validate-fact-check\'s investigator fault in place is counted, and two end in the stop; a real repair is not', async (t) => {
	for (const repaired of [false, true]) {
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				if (!repaired || toolResults(request).length) return { text: 'The structural correction has been completed.' };
				return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: withArtifact(path) }) } }] };
			}
			if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
			return fake.requests.filter((seen) => !isWorkerRequest(seen, 'investigator') && !isWorkerRequest(seen, 'fact-checker')).length <= 6 ? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] } : { text: 'Stopped.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = atGate(space);
		const path = join(caseDir, 'data', 'evidence-bundle.json');
		const document = JSON.parse(await readFile(path, 'utf8'));
		const row = document.items.find((item: { id: string }) => item.id === 'E1');
		unrepairable(document.items);
		await writeFile(path, JSON.stringify(document));
		const findingsPath = join(caseDir, 'data', 'findings.json');
		const findings = JSON.parse(await readFile(findingsPath, 'utf8'));
		findings.findings[0].evidence_bundle_refs = ['E1', 'E9'];
		await writeFile(findingsPath, JSON.stringify(findings));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_CODE_ROUTER: 'off' });

		await settledPrompt(chat, 'Continue.', 60_000);

		const corrections = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0 && /STRUCTURAL CORRECTION/.test(userText(request)));
		const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
		if (repaired) {
			assert.equal(corrections.length, 1, 'one repair, then the case moves on');
			assert.equal(state.attempts?.['structural-correction'], undefined, 'a real repair is not counted');
		} else {
			assert.equal(corrections.length, 2, 'bounded');
			assert.equal(state.attempts['structural-correction'], 2);
			assert.match(state.blocked.gap, /no stored artifact path/, 'the stop records the real fault');
		}
	}
});

// Case 2bd8d15c, 05:58 to 06:29: code sent STRUCTURAL at each resolve and the model sent the investigator
// MODE: EVIDENCE CORRECTION between them, which nothing counted. Any correction mode shares one bound.
test('corrections of any mode, from code or the model, share one bound when nothing improves', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'The correction has been completed.' };
		const turn = fake.requests.filter((seen) => !isWorkerRequest(seen, 'investigator')).length;
		if (turn > 8) return { text: 'Stopped.' };
		return { toolCalls: [turn % 2 ? { name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EVIDENCE CORRECTION\nCASE_DIR: .\nEVIDENCE VALIDATOR REASONS (evidence/data only):\nrecord:F1' } } : { name: 'spotlight_resolve', arguments: {} }] };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = atGate(space);
	const path = join(caseDir, 'data', 'evidence-bundle.json');
	const document = JSON.parse(await readFile(path, 'utf8'));
	unrepairable(document.items);
	await writeFile(path, JSON.stringify(document));
	const findingsPath = join(caseDir, 'data', 'findings.json');
	const findings = JSON.parse(await readFile(findingsPath, 'utf8'));
	findings.findings[0].evidence_bundle_refs = ['E1', 'E9'];
	await writeFile(findingsPath, JSON.stringify(findings));
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_CODE_ROUTER: 'off' });

	await settledPrompt(chat, 'Continue.', 60_000);

	const starts = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).length;
	const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.equal(state.attempts['structural-correction'], 2);
	assert.equal(starts, 2, 'the second no-progress correction ends it, whichever mode it was');
	assert.equal(state.blocked.exhausted_attempt, 'structural-correction');
});

