import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { openReviewTool, gateCheck } from './review.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
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
	const toolAt = (phase: string) => openReviewTool(active.caseDir, root, opener, gateCheck(active, async () => JSON.stringify({ phase }), context));

	await assert.rejects(
		toolAt('report').execute('call', {}, undefined, undefined, undefined as never),
		/review page was not opened[\s\S]*FAIL {2}STRUCTURE: missing required model-authored input/,
	);
	assert.equal(opened, 0);

	await toolAt('ingest').execute('call', {}, undefined, undefined, undefined as never);
	assert.equal(opened, 1, 'past the Report gate the page opens as before');
});

// gold-mozilla a2 on 3f37dcc3: a list-shaped research_plan reached the Methodology page,
// which refused the member's decision with an HTTP 422 the model never saw.
test('open_review at the Methodology gate refuses a methodology the methodology check rejects', async (t) => {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-review-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const active = bindActiveCase(root, join(root, 'case'));
	await mkdir(join(active.caseDir, 'data'), { recursive: true });
	const methodology = JSON.parse(await readFile(join(CHECKOUT_ROOT, 'tests/fixtures/research/methodology-research.sample.json'), 'utf8'));
	methodology.research_plan = [methodology.research_plan];
	await writeFile(join(active.caseDir, 'data/methodology.json'), JSON.stringify(methodology));
	const configPath = join(root, 'spotlight-config.json');
	await writeFile(configPath, JSON.stringify({ model_tier: '12b' }));
	const context = { caseDir: active.caseDir, writeRoot: active.caseDir, python: PYTHON, configPath, env: { PATH: process.env.PATH ?? '', SPOTLIGHT_RESEARCH_SWARMS: 'on' } };
	let opened = 0;
	const tool = openReviewTool(active.caseDir, root, async () => {
		opened += 1;
		return { status: 'opened' };
	}, gateCheck(active, async () => JSON.stringify({ phase: 'methodology_approval' }), context));

	await assert.rejects(
		tool.execute('call', {}, undefined, undefined, undefined as never),
		/review page was not opened: data\/methodology\.json does not pass validate-methodology-navigator\.py[\s\S]*research_plan must be an object/,
	);
	assert.equal(opened, 0);
});
