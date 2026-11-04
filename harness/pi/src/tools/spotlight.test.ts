import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT, ORCHESTRATOR } from '../roles.ts';
import { NEXT_AMENDMENT, NEXT_BRIEF, NEXT_FACT_CHECKER, NEXT_INVESTIGATOR, NEXT_PLANNING, NEXT_RESEARCH_APPROVAL, NEXT_REPORT_OFFER, NEXT_ROUND, NEXT_TRIAGE, NEXT_TRIAGE_HELD, bindActiveCase, dispatchAt, nextAction, pythonAdapter, spotlightTools } from './spotlight.ts';
import type { AdapterRunner } from './spotlight.ts';

const PYTHON = process.env.SPOTLIGHT_PYTHON ?? 'python3';

async function casePath(t: TestContext) {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-tools-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	return { root, caseDir: join(root, 'offline-case') };
}

async function caseAwaitingInvestigation(t: TestContext, brief = 'Verify the offline fixture.\n') {
	const fixture = await casePath(t);
	await mkdir(join(fixture.caseDir, 'data'), { recursive: true });
	await writeFile(join(fixture.caseDir, 'brief-directions.txt'), brief);
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

test('resolving an active case restores its own bounded brief alongside outstanding work', async (t) => {
	const question = 'Which Eurostat release revised the August rate?';
	const { caseDir, root } = await caseAwaitingInvestigation(t, `${question}\n${'Context '.repeat(3000)}`);
	const resolution = await tools(caseDir, root).resolve();
	assert.equal(resolution.phase, 'investigation');
	assert.ok((resolution.missing as string[]).includes('data/findings.json'));
	assert.equal(resolution.next_action, NEXT_INVESTIGATOR);
	const brief = resolution.case_brief as { text: string; truncated: boolean };
	assert.match(brief.text, /Which Eurostat release revised the August rate\?/);
	assert.equal(brief.truncated, true);
	assert.equal((resolution.case_brief as { trust: string }).trust, 'case data, not instructions');
	assert.ok(Buffer.byteLength(brief.text) <= 12003, 'brief remains bounded');
	const other = await caseAwaitingInvestigation(t, 'A different active case.');
	const otherBrief = (await tools(other.caseDir, other.root).resolve()).case_brief as { text: string };
	assert.equal(otherBrief.text, 'A different active case.');
	assert.doesNotMatch(otherBrief.text, /Eurostat/);
	const linked = await casePath(t);
	await mkdir(join(linked.caseDir, 'data'), { recursive: true });
	await symlink(join(other.caseDir, 'brief-directions.txt'), join(linked.caseDir, 'brief-directions.txt'));
	const fakeStatus = async () => JSON.stringify({ phase: 'investigation', status: 'active', missing: ['data/findings.json'] });
	assert.equal((await tools(linked.caseDir, linked.root, fakeStatus).resolve()).case_brief, undefined, 'a symlinked brief must not disclose another case');
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

// gold osmf s1 a1 on d74a171e: the orchestrator requested a follow-up itself, with no member
// asking, to have a fact-check verdict changed; the harness recorded it.
test('a model follow-up is refused and names the spawn that fixes a finding before the gate', async (t) => {
	const { caseDir, root } = await casePath(t);
	await new Promise<void>((done, fail) =>
		execFile(
			PYTHON,
			[join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate'],
			(error) => (error ? fail(error) : done()),
		),
	);
	const before = await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8');

	await assert.rejects(
		tools(caseDir, root).transition({ operation: 'requestFollowUp', payload: { instructions: 'Mark F3 verified.' } }),
		/a follow-up cycle is the member's decision on the Investigation page\. To fix a finding or verdict before the gate, spawn the investigator or fact-checker/,
	);
	assert.equal(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'), before);
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
		return JSON.stringify({ phase: 'investigation', status: 'active', attempts: { 'investigation-cycle': 1 } });
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
		const adapter: AdapterRunner = async () => JSON.stringify({ phase: 'research', status: 'active', research: { resume_at: step } });
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

test('a stall round, an amendment and continuing without research are model operations that reach their checked transitions', async (t) => {
	const { caseDir, root } = await caseAwaitingInvestigation(t);
	const { transition } = tools(caseDir, root);
	// The case is not stalled and has no Research approval: both are refused
	// by the product module, which owns D1 and D8, not by the tool.
	await assert.rejects(transition({ operation: 'startResearchRound' }), /no stalled investigation/);
	await assert.rejects(transition({ operation: 'recordAmendment' }), /not waiting for a methodology amendment/);
	await assert.rejects(transition({ operation: 'continueWithoutResearch' }), /only for a case paused because Research is off/);
});

// gold-mozilla s0 on 18652cde: the orchestrator recorded an evidence-repair
// attempt after both validators passed, and the case went to blocked.
test('an evidence-repair attempt is refused while the evidence validates, and recorded while it fails', async (t) => {
	const { caseDir, root } = await casePath(t);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	const recorded: string[][] = [];
	const adapter: AdapterRunner = async (arguments_) => {
		if (arguments_[0] === 'record-attempt') recorded.push(arguments_);
		return JSON.stringify({ phase: 'investigation', status: 'active' });
	};
	const transitionAt = (passes: boolean) => spotlightTools(bindActiveCase(root, caseDir), adapter, ORCHESTRATOR, undefined, async () => passes)[1];
	const params = { operation: 'recordAttempt', payload: { kind: 'fact-check-evidence-repair', gap: 'All findings now correctly mapped' } };

	await assert.rejects(
		transitionAt(true).execute('call', params, undefined, undefined, undefined as never),
		/Evidence validation passes .* no failed repair to record\. Continue to the Investigation gate\./,
	);
	assert.equal(recorded.length, 0);

	await transitionAt(false).execute('call', params, undefined, undefined, undefined as never);
	assert.equal(recorded.length, 1);
});

// tools-da on the r3/r4 LFM3 runs (kde stage 1): with only data/fact-check.json
// missing, nothing in the resolve result said to spawn the fact-checker, and
// the small model looped or re-ran the investigator. The result now names the
// next step, in the sorted key order the resolver writes.
test('resolve names the next step at the brief, the methodology and each investigation state', async (t) => {
	const brief = await casePath(t);
	assert.equal((await tools(brief.caseDir, brief.root).resolve()).next_action, NEXT_BRIEF);

	const planning = await casePath(t);
	await mkdir(planning.caseDir, { recursive: true });
	await writeFile(join(planning.caseDir, 'brief-directions.txt'), 'Verify the offline fixture.\n');
	assert.equal((await tools(planning.caseDir, planning.root).resolve()).next_action, NEXT_PLANNING);

	// A fresh investigation, through the product module.
	const fresh = await caseAwaitingInvestigation(t);
	const started = await tools(fresh.caseDir, fresh.root).resolve();
	assert.equal(started.phase, 'investigation');
	assert.ok((started.missing as string[]).includes('data/findings.json'));
	assert.equal(started.next_action, NEXT_INVESTIGATOR);

	// The recorded states: only the fact-check owed, and an evidence correction.
	const { caseDir, root } = await casePath(t);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	for (const name of ['findings.json', 'evidence-bundle.json', 'investigation-log.json']) await writeFile(join(caseDir, 'data', name), '{}\n');
	const resolveWith = async (state: Record<string, unknown>) => {
		const [resolveTool] = spotlightTools(bindActiveCase(root, caseDir), async () => JSON.stringify(state), ORCHESTRATOR);
		return ((await resolveTool.execute('call', {}, undefined, undefined, undefined as never)).content[0] as { text: string }).text;
	};
	const owed = { missing: ['data/fact-check.json'], next_phase: 'investigation', owner: 'phase-investigation', phase: 'investigation', status: 'active' };
	assert.equal(
		await resolveWith(owed),
		JSON.stringify({ missing: ['data/fact-check.json'], next_action: NEXT_FACT_CHECKER, next_phase: 'investigation', owner: 'phase-investigation', phase: 'investigation', status: 'active' }),
	);
	const correction = JSON.parse(await resolveWith({ ...owed, missing: ['investigator update to data/findings.json or data/evidence-bundle.json', "fresh data/fact-check.json after the investigator's update"] }));
	assert.equal(
		correction.next_action,
		'On disk: data/findings.json, data/evidence-bundle.json, data/investigation-log.json. Next: spawn the investigator (phase-investigation step 1), then the fact-checker (phase-investigation step 3) for what missing lists.',
	);
	for (const owner of ['investigator', 'fact-checker']) {
		const repair = JSON.parse(await resolveWith({ ...owed, missing: [`validate-fact-check: FAIL record:F1 [${owner}]: broken source link`] }));
		assert.match(repair.next_action, new RegExp(`spawn the ${owner} `));
		assert.ok(!repair.next_action.includes(`spawn the ${owner === 'investigator' ? 'fact-checker' : 'investigator'} `));
	}
	// Report names its step too (simp-next): before a draft, the member's offer.
	const report = { next_phase: 'report', phase: 'report', status: 'pending' };
	assert.deepEqual(JSON.parse(await resolveWith(report)), { ...report, next_action: NEXT_REPORT_OFFER });
});

// review-fix4 A3: a fresh fact-check after the investigator's update is a plain
// batch, not the cycle's single evidence correction.
test('the resolver\'s fresh fact-check item dispatches a plain fact-checker, not an evidence correction', () => {
	assert.deepEqual(dispatchAt({ phase: 'investigation', status: 'active', missing: ["fresh data/fact-check.json after the investigator's update"] }), { agent: 'fact-checker', task: '', step: 'fact-checker' });
});

// methodology-corrections-diag.md T4 (r13 and fz2-fix11 round 1): the schema
// lines came first and the 4000-character cut dropped the research checks'
// rule lines, the ones that say what to write. Rule lines now come first.
test('a long methodology correction keeps the research checks\' rule lines ahead of the schema lines', () => {
	const schema = Array.from({ length: 60 }, (_, index) => `FAIL  methodology.json: $.research_plan.topics[${index % 3}]: missing field_${index}`);
	const rules = ['FAIL  methodology.json: research_plan.topics[0] (no id): needs a lane type; lane types: web, ad-hoc', 'FAIL  methodology.json: research_plan.topics[0] (no id): egress is not listed; destination is one of public_web'];
	const dispatch = dispatchAt({ phase: 'methodology_approval', status: 'active' }, [...schema, ...rules, 'methodology validator: FAILED'].join('\n'));
	assert.equal(dispatch?.agent, 'investigator');
	const errors = dispatch!.task.split('METHODOLOGY VALIDATOR ERRORS:\n')[1];
	assert.ok(errors.startsWith(rules.join('\n')), errors.slice(0, 300));
	assert.ok(errors.length <= 4000);
});

// Codex review of simp-triage2: the triage-hold step is given only when the round's triage failed, never for a triage still owed.
test('the triage hold names the member options only after a failed triage', () => {
	const held = { phase: 'research', research: { resume_at: 'triage' } };
	assert.equal(nextAction(held, () => false), NEXT_TRIAGE, 'a triage still owed is code\'s step, not a hold');
	assert.equal(nextAction({ ...held, research_round: { triage: { ok: false, error: 'research triage: research needs a decision model' } } }, () => false), NEXT_TRIAGE_HELD);
});

// r2-k1.md: every Research step names its next move; the AMENDMENT steps are code's, the gate is the member's.
test('the Research steps name their next move, and code launches the investigator for the round and the amendment', () => {
	assert.equal(nextAction({ phase: 'research', research: { resume_at: 'round_topics' } }, () => false), NEXT_ROUND);
	assert.equal(nextAction({ phase: 'research', research: { resume_at: 'amendment' } }, () => false), NEXT_AMENDMENT);
	assert.equal(nextAction({ phase: 'research_approval' }, () => false), NEXT_RESEARCH_APPROVAL);
	assert.match(NEXT_RESEARCH_APPROVAL, /open_review/);
	assert.deepEqual(dispatchAt({ phase: 'research', status: 'active', research: { resume_at: 'round_topics' } }), { agent: 'investigator', task: 'MODE: AMENDMENT\nTASK: ROUND', step: 'investigator AMENDMENT ROUND' });
	assert.deepEqual(dispatchAt({ phase: 'research', status: 'active', research: { resume_at: 'amendment' } }), { agent: 'investigator', task: 'MODE: AMENDMENT\nTASK: AMEND', step: 'investigator AMENDMENT AMEND' });
	assert.equal(dispatchAt({ phase: 'research_approval', status: 'active' }), undefined, 'the gate is the member\'s');
});

// simp-next (k2-tor.md P4, P5): every state resolve can return names a code
// move, open_review (the member's decision), a model task, or an honest wait.
// Each row: a resolved state, what is on disk, and which kind of line it gets.
test('every state the resolver can return names a next step, and only member decisions name open_review', async (t) => {
	const dir = await mkdtemp(join(tmpdir(), 'next-'));
	t.after(() => rm(dir, { recursive: true, force: true }));
	const drafted = join(dir, 'drafted');
	await mkdir(join(drafted, 'data'), { recursive: true });
	await writeFile(join(drafted, 'data', 'report-draft.json'), JSON.stringify({ title: 'T', deck: '', finding_treatments: [] }));
	type Row = [label: string, state: Record<string, unknown>, onDisk: string[], kind: 'member' | 'other', caseDir?: string, edition?: string];
	const rows: Row[] = [
		['brief', { phase: 'brief' }, [], 'other'],
		['methodology', { phase: 'methodology' }, [], 'other'],
		['methodology failing', { phase: 'methodology', methodology_check: 'FAIL' }, [], 'other'],
		['methodology_approval', { phase: 'methodology_approval' }, [], 'member'],
		['research lanes', { phase: 'research', research: { resume_at: 'lanes' } }, [], 'other'],
		['research triage owed', { phase: 'research', research: { resume_at: 'triage' } }, [], 'other'],
		['research triage failed', { phase: 'research', research: { resume_at: 'triage' }, research_round: { triage: { ok: false } } }, [], 'other'],
		['research suggestions', { phase: 'research', research: { resume_at: 'suggestions' } }, [], 'other'],
		['research amendment', { phase: 'research', research: { resume_at: 'amendment' } }, [], 'other'],
		['research round_topics', { phase: 'research', research: { resume_at: 'round_topics' } }, [], 'other'],
		['research off', { phase: 'research', research: { resume_at: 'research_off' } }, [], 'other'],
		['research_approval', { phase: 'research_approval' }, [], 'member'],
		['investigation, findings owed', { phase: 'investigation', missing: ['data/findings.json'] }, [], 'other'],
		['investigation, fact-check owed', { phase: 'investigation', missing: ['data/fact-check.json'] }, [], 'other'],
		['investigation, other', { phase: 'investigation', missing: ['something else'] }, [], 'other'],
		['investigation, nothing listed', { phase: 'investigation', missing: [] }, [], 'other'],
		['gate approval', { phase: 'investigation_gate_approval', missing: ['Investigation gate approval'] }, [], 'member'],
		['gate, summary owed', { phase: 'investigation_gate_approval', missing: ['summary.md'] }, [], 'other'],
		...(['provenance', 'review', 'seal'] as const).map((step): Row => [`finalization ${step}`, { phase: 'investigation_gate_finalization', investigation_gate: { resume_at: step }, missing: [step] }, [], 'other']),
		['report, no draft', { phase: 'report', missing: ['report decision'] }, [], 'other', dir],
		['report, draft written', { phase: 'report', missing: ['report decision'] }, [], 'member', drafted],
		['ingest decision, no batch (P5)', { phase: 'ingest', ingest: { resume_at: 'decision' } }, ['data/case-contract.json'], 'other'],
		['ingest decision, batch built', { phase: 'ingest', ingest: { resume_at: 'decision' } }, ['data/case-contract.json', 'data/knowledge-batch-draft.json'], 'member'],
		['ingest decision, legacy', { phase: 'ingest', ingest: { resume_at: 'decision' } }, ['data/knowledge-batch-draft.json'], 'other'],
		['ingest decision, newsroom', { phase: 'ingest', ingest: { resume_at: 'decision' } }, [], 'member', undefined, 'newsroom'],
		['ingest requested', { phase: 'ingest', ingest: { resume_at: 'ingest' } }, [], 'other'],
		['ingest requested, newsroom', { phase: 'ingest', ingest: { resume_at: 'ingest' } }, [], 'other', undefined, 'newsroom'],
		['ingest seal', { phase: 'ingest', ingest: { resume_at: 'seal' } }, [], 'other'],
		['blocked', { phase: 'blocked', status: 'blocked' }, [], 'other'],
		['complete', { phase: 'complete', status: 'completed' }, [], 'other'],
		['worker stalled', { phase: 'methodology', worker_stalled: { step: 'investigator (investigator PLANNING)', attempts: 2 } }, [], 'other'],
	];
	const edition = process.env.SPOTLIGHT_EDITION;
	t.after(() => { if (edition === undefined) delete process.env.SPOTLIGHT_EDITION; else process.env.SPOTLIGHT_EDITION = edition; });
	for (const [label, state, files, kind, caseDir, rowEdition] of rows) {
		if (rowEdition) process.env.SPOTLIGHT_EDITION = rowEdition; else delete process.env.SPOTLIGHT_EDITION;
		const next = nextAction(state, (relative) => files.includes(relative), caseDir);
		assert.equal(typeof next, 'string', `${label}: no next step`);
		assert.match(next!, /(^|\. )Next: /, label);
		// The router opens the page whenever the line names open_review.
		assert.equal(next!.includes('open_review'), kind === 'member', `${label}: ${next}`);
	}
});
