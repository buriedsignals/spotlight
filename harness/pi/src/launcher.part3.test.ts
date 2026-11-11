// End-to-end tests of the real launcher and real pi processes against a
// scripted OpenAI-compatible server on 127.0.0.1. No network, no model.
// Part 3 of 6. The parts are one suite cut for running in parallel;
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

// T9 (t9ingest): 13 of 21 analyst calls died at 79-112 s, and a replay answered at 77.8 s. Through a long
// prefill Osaurus sends a role chunk, then empty keep-alives: a live stream, not silence.
test('keep-alives through a long prefill are not silence; the turn gets its answer', async (t) => {
	const fake = await startFakeOpenAI(() => ({ text: 'The answer after a long prefill.', keepAlives: { count: 28, everyMs: 2_500 } }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));
	chat.send({ id: 'p-prefill', type: 'prompt', message: 'Start.' });
	await chat.waitFor((record) => record.type === 'agent_settled', 0, 100_000);
	const settled = chat.lines.filter((line) => line.includes('"type":"message_end"')).join('\n');
	assert.match(settled, /The answer after a long prefill\./);
	assert.doesNotMatch(settled, /Spotlight stopped this turn/);
});

// T9 on a4869840: 6 of 10 analyst calls aborted. A cold prefill sent no data for 85 s.
test('85 s with no chunk before the first visible output is a prefill, and the turn gets its answer', async (t) => {
	const fake = await startFakeOpenAI(() => ({ text: 'The answer after a cold prefill.', quietMs: 85_000 }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, baseEnv(space, fake.baseURL));
	chat.send({ id: 'p-cold', type: 'prompt', message: 'Start.' });
	await chat.waitFor((record) => record.type === 'agent_settled', 0, 110_000);
	const settled = chat.lines.filter((line) => line.includes('"type":"message_end"')).join('\n');
	assert.match(settled, /The answer after a cold prefill\./);
	assert.doesNotMatch(settled, /Spotlight stopped this turn/);
});

// Case 96f45e2d: the stream goes quiet after its keep-alives, as with a closed laptop.
test('a stream that goes silent after its keep-alives is stopped as a stall', async (t) => {
	const fake = await startFakeOpenAI(() => ({ text: '', stall: true, keepAlives: { count: 3, everyMs: 1_000 } }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PREFILL_MS: '3000' });
	chat.send({ id: 'p-silent', type: 'prompt', message: 'Start.' });
	await chat.waitFor((record) => record.type === 'agent_settled', 0, 90_000);
	assert.match(chat.lines.filter((line) => line.includes('"type":"message_end"')).join('\n'), /the model stopped responding/);
});

// Case 96f45e2d, 09:20: the first correction was cut by a closed laptop, the second repaired the
// bundle, no fact-checker had run, and the missing fact-check.json read as a failed correction.
test('a correction that leaves only the fact-checker\'s next step is not counted, clears the count and reaches the fact-checker', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Repaired.' };
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: STRUCTURAL CORRECTION\nFix the bundle.' } }] } : fake.requests.filter((seen) => !isWorkerRequest(seen, 'investigator') && !isWorkerRequest(seen, 'fact-checker')).length <= 3 ? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] } : { text: 'Stopped.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = atGate(space);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const path = join(caseDir, 'data', 'orchestration.json');
	await writeFile(path, JSON.stringify({ ...JSON.parse(await readFile(path, 'utf8')), attempts: { 'structural-correction': 1 } }));
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_CODE_ROUTER: 'off' });

	await settledPrompt(chat, 'Continue.', 60_000);

	const state = JSON.parse(await readFile(path, 'utf8'));
	assert.equal(state.blocked, undefined, 'no stop');
	assert.deepEqual(state.attempts, {}, 'a repair clears the count');
	assert.ok(fake.requests.some((request) => isWorkerRequest(request, 'fact-checker')), 'the fact-checker runs');
});

