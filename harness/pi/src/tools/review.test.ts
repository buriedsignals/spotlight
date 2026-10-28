import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { gateCheck, missingForApproval, openReviewTool } from './review.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { bindActiveCase, pythonAdapter } from './spotlight.ts';

const PYTHON = process.env.SPOTLIGHT_PYTHON ?? 'python3';

// gold-wikimedia s1 on 214afc8a: the orchestrator opened the Report gate on a draft
// validate-report-draft.py rejects, and claimed the report was rendered.
test('open_review at the Report gate refuses an invalid report draft with the validator\'s text', async (t) => {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-review-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const active = bindActiveCase(root, join(root, 'case'));
	await mkdir(join(active.caseDir, 'data'), { recursive: true });
	await writeFile(join(active.caseDir, 'data/report-draft.json'), '{"title": "Written, but no structure"}\n');
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

// LFM3 e2 kde stages 2 and 3 on Osaurus (327ec248): the Report page opened with
// no draft and the Ingest page with no batch; the member could only decline.
test('open_review at the Report or Ingest gate with nothing to approve still opens, and says what is missing and the next step', async (t) => {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-review-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const active = bindActiveCase(root, join(root, 'case'));
	await mkdir(join(active.caseDir, 'data'), { recursive: true });
	let opened = 0;
	const opener = async () => {
		opened += 1;
		return { status: 'opened' };
	};
	const toolAt = (resolved: Record<string, unknown>) => openReviewTool(active.caseDir, root, opener, undefined, () => missingForApproval(active.caseDir, resolved));
	const text = async (resolved: Record<string, unknown>) => ((await toolAt(resolved).execute('call', {}, undefined, undefined, undefined as never)).content[0] as { text: string }).text;

	assert.match(await text({ phase: 'report' }), /review page is open[\s\S]*No report draft is written yet, so the member can only decline the report on the page\. If the member wants the report, write the prose in data\/report-draft\.json/);
	// The skeleton the harness writes at Report has no prose, so it is no draft yet.
	await writeFile(join(active.caseDir, 'data/report-draft.json'), JSON.stringify({ schema_version: '1.0', title: '', deck: '', framing_finding_ids: ['F1'], finding_order: ['F1'], finding_treatments: [{ finding_id: 'F1', headline: '', summary: '', why_it_matters: '' }], caveats: [], next_steps: [] }));
	assert.match(await text({ phase: 'report' }), /No report draft is written yet/);
	assert.match(await text({ phase: 'ingest', ingest: { resume_at: 'decision' } }), /No knowledge batch exists[\s\S]*call spawn_agent with agent analyst/);
	assert.equal(opened, 3, 'the page still opens, so the member can decline');

	await writeFile(join(active.caseDir, 'data/report-draft.json'), '{"title": "Fixture report"}\n');
	await writeFile(join(active.caseDir, 'data/knowledge-batch.json'), '{}\n');
	assert.doesNotMatch(await text({ phase: 'report' }), /No report draft/);
	assert.doesNotMatch(await text({ phase: 'ingest', ingest: { resume_at: 'decision' } }), /No knowledge batch/);
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

// r3/r4 12B (A2 northwind): the orchestrator opened the Investigation gate page
// before writing the summaries, and the gate refused the member's approval
// with an HTTP 422 ("still needs: summary.md, data/summary.json").
test('open_review at the Investigation gate refuses until both summaries are written, then opens', async (t) => {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-review-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests/fixtures/review_case.py'), join(root, 'case'), 'investigation_gate', '--activated', '--clean']);
	const active = bindActiveCase(root, join(root, 'case'));
	const summaries = await Promise.all(['summary.md', 'data/summary.json'].map((file) => readFile(join(active.caseDir, file), 'utf8')));
	const context = { caseDir: active.caseDir, writeRoot: active.caseDir, python: PYTHON, configPath: '', env: { PATH: process.env.PATH ?? '' } };
	let opened = 0;
	const tool = openReviewTool(active.caseDir, root, async () => {
		opened += 1;
		return { status: 'opened' };
	}, gateCheck(active, pythonAdapter(PYTHON), context));
	const call = () => tool.execute('call', {}, undefined, undefined, undefined as never);

	await unlink(join(active.caseDir, 'summary.md'));
	await unlink(join(active.caseDir, 'data/summary.json'));
	await assert.rejects(call(), /^Error: The review page was not opened: the Investigation gate still needs: summary\.md, data\/summary\.json\. Write summary\.md and data\/summary\.json as phase-investigation-gate "Generate summary" says, then call spotlight_resolve and open_review again\.$/);
	assert.equal(opened, 0);

	await writeFile(join(active.caseDir, 'summary.md'), summaries[0]);
	await assert.rejects(call(), /still needs: data\/summary\.json\./);
	assert.equal(opened, 0);

	await writeFile(join(active.caseDir, 'data/summary.json'), summaries[1]);
	await call();
	assert.equal(opened, 1, 'with both summaries written the page opens');
});
