import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { validateAfterWorker } from './validators.ts';
import type { ToolContext } from './tools/run.ts';

test('an investigator in AMENDMENT mode at Research is not checked as if it wrote findings', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-pi-validators-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	const context = { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON ?? 'python3', configPath: join(caseDir, 'config.json') } as ToolContext;
	for (const phase of ['research', 'research_approval']) {
		const outcome = await validateAfterWorker('investigator', phase, 'local-model', context);
		assert.deepEqual([outcome.ran, outcome.passed], [[], true], phase);
	}
	const execution = await validateAfterWorker('investigator', 'investigation', 'local-model', context);
	assert.deepEqual(execution.ran, ['validate-case.py']);
});

test('after the investigator and the fact-checker the Indicator Framework file is checked when one exists', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-pi-validators-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	const context = { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON ?? 'python3', configPath: join(caseDir, 'config.json'), env: process.env } as ToolContext;
	assert.deepEqual((await validateAfterWorker('fact-checker', 'investigation', 'local-model', context)).ran, ['validate-case.py', 'validate-fact-check.py']);
	await mkdir(join(caseDir, 'data'));
	await writeFile(join(caseDir, 'data', 'indicator-framework.json'), JSON.stringify({ indicators: [{ id: 'ind-1', finding_ids: ['F9'] }] }));
	for (const role of ['investigator', 'fact-checker']) {
		const outcome = await validateAfterWorker(role, 'investigation', 'local-model', context);
		assert.equal(outcome.ran.at(-1), 'validate-indicator-framework.py', role);
		assert.match(outcome.report, /validate-indicator-framework\.py: FAILED\nFAIL  indicator ind-1 finding_ids: F9 is not a finding/, role);
	}
});
