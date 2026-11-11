// step2-design.md section 4: an AMENDMENT that sends only the directions it
// changes is completed from the approved methodology. The scenario is in
// the launcher tests ("code-built TASK: AMEND").
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { CHECKOUT_ROOT } from '../roles.ts';
import { DEFAULT_TOKEN_BUDGET, completeAmendment, fillMethodology } from './methodology-paperwork.ts';

test('a changed direction sent by its name replaces the approved one that code numbered, never a duplicate', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-amend-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'data'));
	const approved = { schema_version: '1.0', project: 'p', investigation_plan: [{ direction: 'Ownership', direction_id: 'd1', questions: ['Who?'], steps: [] }, { direction: 'Money', direction_id: 'd2', questions: ['How much?'], steps: [] }] };
	await writeFile(join(caseDir, 'data', 'methodology.json'), JSON.stringify(approved));
	// review-simp.md #3: the fragment names the direction but omits the id code gave it.
	const changed = { direction: 'Ownership', questions: ['Who?'], steps: [{ order: 1, action: 'Read the register', tool: 'fetch' }] };
	const completed = completeAmendment(caseDir, { investigation_plan: [changed] })!;
	assert.deepEqual((completed.investigation_plan as unknown[]).length, 2);
	assert.deepEqual((completed.investigation_plan as unknown[])[0], { ...changed, direction_id: 'd1' }, 'the approved id is kept');
	// By id, as before.
	const byId = completeAmendment(caseDir, { investigation_plan: [{ ...approved.investigation_plan[1], steps: [{ order: 1, action: 'Sum the payments', tool: 'fetch' }] }] })!;
	assert.equal(((byId.investigation_plan as Array<{ steps: unknown[] }>)[1]).steps.length, 1);
	// A name two approved directions share matches neither: the entry is new, for D8 to judge.
	await writeFile(join(caseDir, 'data', 'methodology.json'), JSON.stringify({ ...approved, investigation_plan: [approved.investigation_plan[0], { ...approved.investigation_plan[1], direction: 'Ownership' }] }));
	assert.equal((completeAmendment(caseDir, { investigation_plan: [changed] })!.investigation_plan as unknown[]).length, 3);
});

// Case 2026-11-05-938c2a6a: Research was on, the prompt said so, and r3's one write had no research_plan, so Research was skipped in silence.
test('with Research on and no plan, code adds a default ad-hoc plan that D1 accepts; every other route is unchanged', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-default-plan-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'data'));
	const on = { SPOTLIGHT_RESEARCH_SWARMS: 'on', SPOTLIGHT_MODEL_TIER: '12b' };
	const methodology = () => ({ schema_version: '1.0', project: 'p', investigation_plan: [{ direction: 'Ownership', questions: ['Who owns it?'], steps: [] }, { direction: 'Money', steps: [] }] });
	const filled = (env: Record<string, string>, extra: Record<string, unknown> = {}) => {
		const document: Record<string, unknown> = { ...methodology(), ...extra };
		fillMethodology(caseDir, env, document);
		return document;
	};
	const plan = filled(on).research_plan as { code_default: boolean; mode: string; consent: unknown; round_budget_usd: number; topics: Array<Record<string, unknown>> };
	assert.equal(plan.code_default, true);
	assert.deepEqual(plan.topics.map(({ topic_id, direction_id, lane_type, questions }) => ({ topic_id, direction_id, lane_type, questions })), [
		{ topic_id: 'd1-ad-hoc', direction_id: 'd1', lane_type: 'ad-hoc', questions: ['Who owns it?'] },
		{ topic_id: 'd2-ad-hoc', direction_id: 'd2', lane_type: 'ad-hoc', questions: ['Money'] },
	]);
	assert.equal(plan.round_budget_usd, 1);
	// Continuity: the check the Methodology gate runs takes the plan, so the case reaches the gate.
	await writeFile(join(caseDir, 'data', 'methodology.json'), JSON.stringify(filled(on)));
	const d1 = JSON.parse(execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'research_checks.py'), 'd1', caseDir], { encoding: 'utf8', env: { ...process.env, ...on, SPOTLIGHT_CONFIG: join(caseDir, 'none.json') } })) as { ok: boolean; problems?: string[] };
	assert.deepEqual(d1.problems ?? [], []);
	await rm(join(caseDir, 'data', 'methodology.json'));

	for (const [why, env] of [
		['switch off', { ...on, SPOTLIGHT_RESEARCH_SWARMS: 'off' }],
		['frontier', { ...on, SPOTLIGHT_MODEL_TIER: 'frontier' }],
		['sensitive', { ...on, SPOTLIGHT_SENSITIVE: 'true' }],
		['newsroom', { ...on, SPOTLIGHT_EDITION: 'newsroom' }],
	] as const) assert.equal(filled(env).research_plan, undefined, why);
	// The model's own plan stands.
	const own = { topics: [{ direction_id: 'd1', lane_type: 'web', questions: ['Q'] }] };
	assert.equal((filled(on, { research_plan: own }).research_plan as Record<string, unknown>).code_default, undefined);
	// After approval a write is never given a plan the member did not approve.
	await writeFile(join(caseDir, 'data', 'orchestration.json'), JSON.stringify({ approvals: { methodology: { approved_by: 'm' } } }));
	assert.equal(filled(on).research_plan, undefined, 'approved');
	await rm(join(caseDir, 'data', 'orchestration.json'));
	// A plan the member dropped at the review is not added back by a correction.
	await writeFile(join(caseDir, 'data', 'methodology-review.json'), JSON.stringify({ items: { 'selection:research_plan': { final: false } } }));
	assert.equal(filled(on).research_plan, undefined, 'dropped');
});

// Case b75e83cb: the model copied 200000 from the role text, so the raised default never reached its lanes.
test('before the Methodology approval a local plan\'s old default budget is lifted; any other value, frontier and an approved plan keep theirs', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-budget-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'data'));
	const budgets = (env: Record<string, string>) => {
		const topic = (id: string, token_budget?: number) => ({ topic_id: id, direction_id: 'd1', lane_type: 'web', questions: ['Who?'], ...(token_budget === undefined ? {} : { token_budget }) });
		const document = { schema_version: '1.0', project: 'p', investigation_plan: [], research_plan: { topics: [topic('copied', 200000), topic('chosen', 300000), topic('open')] } };
		fillMethodology(caseDir, env, document);
		return (document.research_plan.topics as Array<{ token_budget?: number }>).map((row) => row.token_budget);
	};
	assert.deepEqual(budgets({ SPOTLIGHT_MODEL_TIER: '12b' }), [DEFAULT_TOKEN_BUDGET, 300000, DEFAULT_TOKEN_BUDGET]);
	assert.deepEqual(budgets({ SPOTLIGHT_MODEL_TIER: 'frontier' }), [200000, 300000, DEFAULT_TOKEN_BUDGET]);
	await writeFile(join(caseDir, 'data', 'orchestration.json'), JSON.stringify({ approvals: { methodology: { approved_by: 'member' } } }));
	assert.deepEqual(budgets({ SPOTLIGHT_MODEL_TIER: '12b' }), [200000, 300000, DEFAULT_TOKEN_BUDGET], 'the number the member approved stands');
});
