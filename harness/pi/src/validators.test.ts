import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
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
