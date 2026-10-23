import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT, ORCHESTRATOR } from '../roles.ts';
import { bindActiveCase, pythonAdapter, spotlightTools } from './spotlight.ts';
import type { AdapterRunner } from './spotlight.ts';

const PYTHON = process.env.SPOTLIGHT_PYTHON ?? 'python3';

async function casePath(t: TestContext) {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-tools-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	return { root, caseDir: join(root, 'offline-case') };
}

async function caseAwaitingInvestigation(t: TestContext) {
	const fixture = await casePath(t);
	await mkdir(join(fixture.caseDir, 'data'), { recursive: true });
	await writeFile(join(fixture.caseDir, 'brief-directions.txt'), 'Verify the offline fixture.\n');
	await writeFile(join(fixture.caseDir, 'data', 'methodology.json'), '{}\n');
	// The methodology approval is the review page's to record; the fixture
	// records it through the product CLI directly.
	await new Promise<void>((done, fail) =>
		execFile(
			PYTHON,
			[
				join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'),
				'approve', 'methodology',
				'--approved-by', 'journalist:fixture',
				'--approved-at', '2026-08-23T12:00:00Z',
				'--authorized-cases-root', fixture.root,
				fixture.caseDir,
			],
			(error) => (error ? fail(error) : done()),
		),
	);
	return fixture;
}

function tools(caseDir: string, root: string, runAdapter: AdapterRunner = pythonAdapter(PYTHON)) {
	const [resolveTool, transitionTool] = spotlightTools(bindActiveCase(root, caseDir), runAdapter, ORCHESTRATOR);
	const call = async (tool: typeof resolveTool, params: Record<string, unknown>, signal?: AbortSignal) => {
		const result = await tool.execute('call', params, signal, undefined, undefined as never);
		const block = result.content[0];
		assert.equal(block.type, 'text');
		return JSON.parse((block as { text: string }).text) as Record<string, unknown>;
	};
	return {
		resolve: (params: Record<string, unknown> = {}) => call(resolveTool, params),
		transition: (params: Record<string, unknown>, signal?: AbortSignal) => call(transitionTool, params, signal),
	};
}

test('a fresh launcher case is created and resolves through the product module', async (t) => {
	const { caseDir, root } = await casePath(t);

	const resolution = await tools(caseDir, root).resolve();

	assert.equal(resolution.phase, 'brief');
	assert.equal(resolution.owner, 'phase-methodology');
	assert.deepEqual(resolution.missing, ['brief-directions.txt']);
});

test('an active case that is a symlink, or outside the cases root, is refused', async (t) => {
	const { caseDir, root } = await casePath(t);
	const realCase = join(root, 'real-case');
	await mkdir(join(realCase, 'data'), { recursive: true });
	await symlink(realCase, caseDir, 'dir');
	const outside = await mkdtemp(join(tmpdir(), 'spotlight-pi-outside-'));
	t.after(() => rm(outside, { recursive: true, force: true }));

	assert.throws(() => bindActiveCase(root, caseDir), /must be a directory/);
	assert.throws(() => bindActiveCase(root, outside), /child of the configured cases root/);
});

test('an allowed transition changes durable state through the product module', async (t) => {
	const { caseDir, root } = await caseAwaitingInvestigation(t);

	const resolution = await tools(caseDir, root).transition({
		operation: 'recordAttempt',
		payload: { kind: 'investigation-cycle', gap: 'registry record not yet found' },
	});

	assert.equal(resolution.phase, 'investigation');
	assert.deepEqual(resolution.attempts, { 'investigation-cycle': 1 });
});

test('a model-named sibling case is ignored: the tools act only on the bound case', async (t) => {
	const { caseDir, root } = await caseAwaitingInvestigation(t);
	const sibling = join(root, 'sibling-case');
	await mkdir(join(sibling, 'data'), { recursive: true });

	await tools(caseDir, root).transition({
		operation: 'recordAttempt',
		caseDir: sibling,
		payload: { kind: 'investigation-cycle', gap: 'gap' },
	});

	await assert.rejects(readFile(join(sibling, 'data', 'orchestration.json')));
	const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.deepEqual(state.attempts, { 'investigation-cycle': 1 });
});

test('a model follow-up records the agent channel and the harness role, whatever the payload claims', async (t) => {
	const { caseDir, root } = await casePath(t);
	await new Promise<void>((done, fail) =>
		execFile(
			PYTHON,
			[join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate'],
			(error) => (error ? fail(error) : done()),
		),
	);

	const resolution = await tools(caseDir, root).transition({
		operation: 'requestFollowUp',
		payload: { instructions: 'Find a second filing.', requestedByRole: 'member', decidedBy: 'A. Member' },
	});

	assert.equal(resolution.phase, 'investigation');
	const { follow_up: followUp } = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.equal(followUp.review.channel, 'agent');
	assert.equal(followUp.requested_by_role, 'orchestrator');
	assert.equal(followUp.decided_by, undefined);
});

test('gate approvals, the report decision and an ingest request or decline are refused', async (t) => {
	const { caseDir, root } = await caseAwaitingInvestigation(t);
	const before = await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8');
	const { transition } = tools(caseDir, root);

	for (const params of [
		{ operation: 'approve', payload: { gate: 'investigation_gate', approvedBy: 'model', approvedAt: '2026-08-23T12:00:00Z' } },
		{ operation: 'decideReport', payload: { decision: 'completed' } },
		{ operation: 'decideIngest', payload: { decision: 'requested' } },
		{ operation: 'decideIngest', payload: { decision: 'declined' } },
	]) {
		await assert.rejects(transition(params), /review page/, JSON.stringify(params));
	}
	assert.equal(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'), before);
});

test('an in-flight transition reaches one durable outcome after caller abort', async (t) => {
	const { caseDir, root } = await casePath(t);
	const started = Promise.withResolvers<void>();
	const release = Promise.withResolvers<void>();
	let invocation = 0;
	const runAdapter: AdapterRunner = async (_arguments, signal) => {
		invocation += 1;
		if (invocation === 1) {
			started.resolve();
			const aborted = new Promise<never>((_resolve, reject) => {
				signal?.addEventListener('abort', () => reject(new Error('adapter orphaned')), { once: true });
			});
			await Promise.race([release.promise, aborted]);
			return '';
		}
		return JSON.stringify({ phase: 'investigation', attempts: { 'investigation-cycle': 1 } });
	};
	const controller = new AbortController();

	const pending = tools(caseDir, root, runAdapter).transition(
		{ operation: 'recordAttempt', payload: { kind: 'investigation-cycle', gap: 'gap' } },
		controller.signal,
	);
	await started.promise;
	controller.abort();
	release.resolve();

	assert.equal((await pending).phase, 'investigation');
});

test('resolve runs the research round only while lanes or their triage are owed, never at the model\'s Research steps', async (t) => {
	const { caseDir, root } = await casePath(t);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	for (const [step, runs] of [['lanes', true], ['triage', true], ['round_topics', false], ['suggestions', false], ['amendment', false]] as const) {
		let rounds = 0;
		const adapter: AdapterRunner = async () => JSON.stringify({ phase: 'research', research: { resume_at: step } });
		const [resolveTool] = spotlightTools(bindActiveCase(root, caseDir), adapter, ORCHESTRATOR, async () => {
			rounds += 1;
			return { ran: [] };
		});
		const result = await resolveTool.execute('call', {}, undefined, undefined, undefined as never);
		const resolved = JSON.parse((result.content[0] as { text: string }).text) as Record<string, unknown>;
		assert.equal(rounds, runs ? 1 : 0, step);
		assert.equal('research_round' in resolved, runs, step);
	}
});

test('a stall round and an amendment are model operations that reach their checked transitions', async (t) => {
	const { caseDir, root } = await caseAwaitingInvestigation(t);
	const { transition } = tools(caseDir, root);
	// The case is not stalled and has no Research approval: both are refused
	// by the product module, which owns D1 and D8, not by the tool.
	await assert.rejects(transition({ operation: 'startResearchRound' }), /no stalled investigation/);
	await assert.rejects(transition({ operation: 'recordAmendment' }), /not waiting for a methodology amendment/);
});
