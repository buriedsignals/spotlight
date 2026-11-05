// step2-design.md section 4: an AMENDMENT that sends only the directions it
// changes is completed from the approved methodology. The scenario is in
// launcher.test.ts ("code-built TASK: AMEND").
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { completeAmendment } from './methodology-paperwork.ts';

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
