import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { openReviewTool, reportDraftCheck } from './review.ts';
import { bindActiveCase } from './spotlight.ts';

const PYTHON = process.env.SPOTLIGHT_PYTHON ?? 'python3';

// gold-wikimedia s1 on 214afc8a: the orchestrator opened the Report gate on a draft
// validate-report-draft.py rejects, and claimed the report was rendered.
test('open_review at the Report gate refuses an invalid report draft with the validator\'s text', async (t) => {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-review-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const active = bindActiveCase(root, join(root, 'case'));
	await mkdir(join(active.caseDir, 'data'), { recursive: true });
	await writeFile(join(active.caseDir, 'data/report-draft.json'), '{}\n');
	const context = { caseDir: active.caseDir, writeRoot: active.caseDir, python: PYTHON, configPath: '', env: { PATH: process.env.PATH ?? '' } };
	let opened = 0;
	const opener = async () => {
		opened += 1;
		return { status: 'opened' };
	};
	const toolAt = (phase: string) => openReviewTool(active.caseDir, root, opener, reportDraftCheck(active, async () => JSON.stringify({ phase }), context));

	await assert.rejects(
		toolAt('report').execute('call', {}, undefined, undefined, undefined as never),
		/review page was not opened[\s\S]*FAIL {2}STRUCTURE: missing required model-authored input/,
	);
	assert.equal(opened, 0);

	await toolAt('ingest').execute('call', {}, undefined, undefined, undefined as never);
	assert.equal(opened, 1, 'past the Report gate the page opens as before');
});
