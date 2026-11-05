import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { validateAfterWorker, validateFactCheckBatch, validateInvestigationInputs } from './validators.ts';
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

async function partialCase(t: TestContext) {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-pi-batch-validation-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'data'));
	const findings = { project: 'partial case', findings: ['F1', 'F2', 'F3', 'F4'].map((id) => ({ id, claim: `Registry entry ${id}`, evidence: 'Stored registry record', confidence: 'medium', sources: [{ url: 'https://registry.example.org' }] as Array<{ url: string; local_file?: string }> })) };
	const factCheck = { project: findings.project, claims: findings.findings.slice(0, 3).map((row) => ({ finding_id: row.id, claim_text: row.claim, verdict: 'unverified', confidence: 'medium', evidence_for: [{ description: 'Registry capture', source: 'https://registry.example.org' } as { description?: string; source: string }] })) };
	const save = async () => {
		await writeFile(join(caseDir, 'data/findings.json'), JSON.stringify(findings));
		await writeFile(join(caseDir, 'data/fact-check.json'), JSON.stringify(factCheck));
	};
	await save();
	const context: ToolContext = { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON ?? 'python3', configPath: join(caseDir, 'config.json'), env: process.env };
	return { context, findings, factCheck, save };
}

test('a valid partial batch advances while the final full validator still rejects missing findings', async (t) => {
	const { context } = await partialCase(t);
	const batch = await validateFactCheckBatch(context, ['F4']);
	assert.equal(batch.passed, true, batch.report);
	assert.match(batch.report, /1 future findings remain unassessed: F4/);
	const final = await validateAfterWorker('fact-checker', 'investigation', 'local-model', context);
	assert.equal(final.passed, false);
	assert.match(final.report, /missing:F4/);
	const wrong = await validateFactCheckBatch(context, ['not-F4']);
	assert.equal(wrong.passed, false);
	assert.match(wrong.report, /FAIL missing:F4 \[fact-checker\]/);
});

test('completed-looking malformed claims stop a batch with the exact correction pointer', async (t) => {
	const { context, factCheck, save } = await partialCase(t);
	delete factCheck.claims[1].evidence_for[0].description;
	await save();
	const result = await validateFactCheckBatch(context, ['F4']);
	assert.equal(result.passed, false);
	assert.match(result.report, /fact-check.json.claims\[1\].evidence_for\[0\]: missing or empty 'description'/);
});

test('the future whitelist cannot hide a missing batch claim or a withdrawn previously assessed claim', async (t) => {
	const { context, factCheck, save } = await partialCase(t);
	// F4 was the only future finding before this worker; F2 must stay assessed.
	factCheck.claims.splice(1, 1);
	await save();
	const result = await validateFactCheckBatch(context, ['F4']);
	assert.equal(result.passed, false);
	assert.match(result.report, /FAIL missing:F2/);
	assert.match(result.report, /future findings remain unassessed: F4/);
});

test('a broken source record on a future finding is never excused as future work', async (t) => {
	const { context, findings, save } = await partialCase(t);
	findings.findings[3].sources[0].local_file = 'research/not-acquired.md';
	await save();
	const result = await validateFactCheckBatch(context, ['F4']);
	assert.equal(result.passed, false);
	assert.match(result.report, /FAIL record:F4 \[investigator\].*does not resolve/);
});

test('batch checks retain the full case and Indicator Framework validators', async (t) => {
	for (const [file, content, error] of [
		['investigation-log.json', {}, /investigation-log.json/],
		['indicator-framework.json', { indicators: [{ id: 'ind-1', finding_ids: ['F9'] }] }, /indicator ind-1 finding_ids: F9 is not a finding/],
	] as const) {
		await t.test(file, async (t) => {
			const { context } = await partialCase(t);
			await writeFile(join(context.caseDir, 'data', file), JSON.stringify(content));
			const result = await validateFactCheckBatch(context, ['F4']);
			assert.equal(result.passed, false, result.report);
			assert.match(result.report, error);
		});
	}
});

test('batch validation rejects malformed checker protocols and unexpected exits', async (t) => {
	const { context } = await partialCase(t);
	const python = context.python;
	for (const [name, stdout, code] of [
		['invalid JSON', '{', 3],
		['missing verdicts', '{"ok":true,"verdicts":[]}', 0],
		['invalid row', '{"ok":false,"verdicts":[{"id":"missing:F4","ok":false}]}', 3],
		['invalid owner', '{"ok":false,"verdicts":[{"id":"missing:F4","ok":false,"owner":[],"reason":"future"}]}', 3],
		['other owner', '{"ok":false,"verdicts":[{"id":"missing:F4","ok":false,"owner":"investigator","reason":"future"}]}', 3],
		['unexpected exit', '{"ok":false,"verdicts":[{"id":"missing:F4","ok":false,"owner":"fact-checker","reason":"future"}]}', 7],
		['inconsistent exit', '{"ok":true,"verdicts":[{"id":"missing:F4","ok":false,"owner":"fact-checker","reason":"future"}]}', 0],
	] as const) {
		const shim = join(context.caseDir, 'checker-shim');
		// Only corrupt the checker protocol; all preceding programs remain real.
		await writeFile(shim, `#!${process.execPath}\nconst {spawnSync}=require('node:child_process'); const args=process.argv.slice(2); if(args.includes('--json')) { process.stdout.write(${JSON.stringify(stdout)}); process.exit(${code}); } const r=spawnSync(${JSON.stringify(python)},args,{stdio:'inherit'}); process.exit(r.status ?? 1);\n`, { mode: 0o700 });
		const result = await validateFactCheckBatch({ ...context, python: shim }, ['F4']);
		assert.equal(result.passed, false, name);
		assert.match(result.report, /validate-fact-check.py: FAILED/, name);
	}
});

test('batch validation fails closed on an unavailable interpreter and propagates cancellation', async (t) => {
	const { context } = await partialCase(t);
	const missing = await validateFactCheckBatch({ ...context, python: join(context.caseDir, 'missing-python') }, ['F4']);
	assert.equal(missing.passed, false);
	assert.match(missing.report, /could not run/);
	const controller = new AbortController();
	controller.abort(new Error('operator cancelled batch'));
	await assert.rejects(validateFactCheckBatch(context, ['F4'], controller.signal), /operator cancelled batch/);
	const hanging = join(context.caseDir, 'hanging-python');
	await writeFile(hanging, `#!${process.execPath}\nsetInterval(() => {}, 1000);\n`, { mode: 0o700 });
	const active = new AbortController();
	const cancelled = validateFactCheckBatch({ ...context, python: hanging }, ['F4'], active.signal);
	const timer = setTimeout(() => active.abort(new Error('cancelled active checker')), 50);
	try {
		await assert.rejects(cancelled, /cancelled active checker/);
	} finally {
		clearTimeout(timer);
	}
});

test('after the investigator and the fact-checker the Indicator Framework file is checked when one exists', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-pi-validators-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	const context = { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON ?? 'python3', configPath: join(caseDir, 'config.json'), env: process.env } as ToolContext;
	// The locator repair runs after every fact-checker, passages or not (gold sens4).
	assert.deepEqual((await validateAfterWorker('fact-checker', 'investigation', 'local-model', context)).ran, ['write-source-expressions.py --resolve-fact-check-refs', 'validate-case.py', 'validate-fact-check.py']);
	await mkdir(join(caseDir, 'data'));
	await writeFile(join(caseDir, 'data', 'indicator-framework.json'), JSON.stringify({ indicators: [{ id: 'ind-1', finding_ids: ['F9'] }] }));
	for (const role of ['investigator', 'fact-checker']) {
		const outcome = await validateAfterWorker(role, 'investigation', 'local-model', context);
		assert.equal(outcome.ran.at(-1), 'validate-indicator-framework.py', role);
		assert.match(outcome.report, /validate-indicator-framework\.py: FAILED\nFAIL  indicator ind-1 finding_ids: F9 is not a finding/, role);
	}
});

test('validator handoffs preserve early repair owners and the complete diagnostic', async (t) => {
 const {context}=await partialCase(t);
 const output='FAIL record:F1 [investigator]: repair the source link.\n'+'PASS F2: record accepted.\n'.repeat(600);
 const python=join(context.caseDir,'validator-output');
 await writeFile(python, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(output)});process.exitCode=3;`,{mode:0o755});
 context.python=python;
 const result=await validateAfterWorker('investigator','investigation','stub-model',context);
 assert.equal(result.passed,false);
 assert.match(result.report,/FAIL record:F1 \[investigator\]: repair the source link/);
 assert.ok(result.report.length<6500);
 const pointer=/Full diagnostic: ([^\s]+)/.exec(result.report)?.[1];
 assert.ok(pointer,'complete output is available explicitly');
 assert.ok((await readFile(join(context.caseDir,pointer),'utf8')).includes(output.trim()));
});

test('investigation input admission requires a valid complete owner-tagged validator response', async (t) => {
 const { context } = await partialCase(t);
 const cases = [
  ['valid', {ok:true,errors:[]}, 0, true],
  ['FC repair', {ok:false,errors:[{message:'claim needs repair',owner:'fact-checker'}]}, 1, true],
  ['mixed owners', {ok:false,errors:[{message:'claim',owner:'fact-checker'},{message:'source',owner:'investigator'}]}, 1, false],
  ['invalid owner', {ok:false,errors:[{message:'bad',owner:'unknown'}]}, 1, false],
  ['missing message', {ok:false,errors:[{owner:'fact-checker'}]}, 1, false],
  ['bad errors', {ok:false,errors:'bad'}, 1, false],
  ['false with no errors', {ok:false,errors:[]}, 1, false],
  ['true with errors', {ok:true,errors:[{message:'bad',owner:'fact-checker'}]}, 0, false],
  ['unexpected exit', {ok:false,errors:[{message:'claim',owner:'fact-checker'}]}, 7, false],
  ['inconsistent exit', {ok:false,errors:[{message:'claim',owner:'fact-checker'}]}, 0, false],
  ['malformed JSON', '{', 1, false],
 ] as const;
 const shim = join(context.caseDir, 'input-validator-shim');
 for (const [name, payload, code, expected] of cases) {
  const output = typeof payload === 'string' ? payload : JSON.stringify(payload);
  await writeFile(shim, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(output)});process.exit(${code});\n`, {mode:0o700});
  const result = await validateInvestigationInputs({...context, python:shim});
  assert.equal(result.passed, expected, name);
 }
});
