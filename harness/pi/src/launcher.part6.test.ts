// End-to-end tests of the real launcher and real pi processes against a
// scripted OpenAI-compatible server on 127.0.0.1. No network, no model.
// Part 6 of 6. The parts are one suite cut for running in parallel;
// run one area across them with --test-name-pattern.
import assert from 'node:assert/strict';
import { execFile, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { chmod, copyFile, mkdir, readFile, readdir, realpath, rm, unlink, writeFile } from 'node:fs/promises';
import { cpSync, existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { dirname, join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { isWorkerRequest, startFakeOpenAI, systemText, toolNames, toolResults } from './test-support/fake-openai.ts';
import type { ChatRequest, Reply } from './test-support/fake-openai.ts';
import { baseEnv, caseAtIngest, launch, newsroomEnv, requestNewsroomIngest, settledPrompt, waitUntil, workspace } from './test-support/launch.ts';
import { DECISION_RECORDED } from './router.ts';
import { NEXT_BRIEF } from './tools/spotlight.ts';
import { reviewRefusalStop } from './extension.ts';
import { AMENDMENT_STOP, PAGE_NOT_OPENED, REVIEW_HOLD_NOTICE } from './tools/review.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { registerCapture } from './test-support/receipts.ts';
import type { Chat, Workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT, FACT_CHECK_TASK, agentSpec, loadComposition, deliverableFor } from './roles.ts';
import { withCasePassages } from './case-search.ts';
import { BRIEF_PENDING_REFUSAL, FACT_CHECKERS_SPENT, GATE_DECIDED_REFUSAL, GATE_READY_REFUSAL } from './subagents.ts';
import { ASSESSMENT_HELD, FC_BATCH_DONE, FC_BATCH_DUE, FC_BATCH_NOTICE, FC_BATCH_STOP, registerFetchedPages } from './extension.ts';
import { CONTINUE_AFTER_FOLD } from './subagents.ts';
import { blockedStop, budgetedWebResult, CALL_BUDGET_SPENT, FACT_CHECK_DUE, FACT_CHECK_DUE_NOTICE, FACT_CHECK_DUE_REPEAT, FACT_CHECK_STOP, GATE_BOUND, GATE_BOUND_REFUSAL, LOOP_NOTICE_CAP, ORCHESTRATOR_LOOP_STOP, PROPOSAL_DONE, readAlreadyShown, repeatsItself, REVIEW_OPEN_STOP, REVIEW_REFUSAL_CAP, skillAlreadyLoaded, UNPRODUCTIVE_NEXT, UNPRODUCTIVE_STOP } from './extension.ts';
import { GATE_APPROVAL_REFUSAL, NEXT_METHODOLOGY_APPROVAL, NEXT_METHODOLOGY_FAILING } from './tools/spotlight.ts';
import { TASK_AFTER_FOLD, taskAfterFold } from './checkpoint.ts';
import { maxOutputTokens } from './config.ts';
import { FORBIDDEN_TOOL, isAlive, lockRecord, resolveThenStop, lastResolution, briefApproved, caseAtInvestigation, factCheckCase, NUDGE, nudges, DEGENERATE_SAMPLES, watchdogRun, search, record, isWriteCommit, writeCalls, anomalyStandIn, fakeDecisions, SEQ7_SKILLS, LOCAL_TOOLS, CONTENT_ONLY_PLAN, caseDigests, PROPOSAL, delegateToAnalyst, atGate, holdScript, notices, shownText, settles, unrepairable, withArtifact, stopCase, standInBsig, researchFiles, userText, factCheckerTask, newsroomRoute, orchestratorScript, SEAM, PYTHON, DISPATCH_TOOLS, caseAtGate, memberApprovesGate, REPORT_DRAFT, caseAtAmendment, caseAtRoundTopics, gateConfig, resolveResults } from './launcher-cases.ts';
import type { Row } from './launcher-cases.ts';

test('unsplit investigator work has a finite call budget on every tier', async (t) => {
	for (const tier of ['12b', 'frontier']) await t.test(tier, async (t) => {
		let calls = 0;
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				if (calls >= 100) return { text: 'Unbounded work finished.' };
				// A changed record every fifth call keeps the progress watchdog quiet,
				// so the 80-call budget is still what this test reaches.
				const n = calls++;
				return { toolCalls: [n % 5 === 4 ? { name: 'write_file', arguments: { path: 'data/findings.json', content: JSON.stringify({ project: 'case-a', findings: [{ id: `F${n}`, claim: `Claim ${n}.` }] }) } } : { name: 'list_files', arguments: { pattern: `research/probe-${n}*` } }] };
			}
			return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nInvestigate one direction.' } }] } : { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		await caseAtInvestigation(space);

		await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_MODEL_CONTEXT_WINDOW: '98304' }), 'Continue.');

		const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).at(-1)!;
		assert.ok(toolResults(worker).includes(CALL_BUDGET_SPENT));
		assert.ok(calls > 80 && calls <= 91, 'stops by the budget plus writing grace, possibly earlier on refusal streak');
		assert.match(toolResults(fake.requests.at(-1)!).join('\n'), /tool-call budget was exhausted|six tool calls in a row refused/);
	});
});

test('an investigator fetch cannot be promoted as a fact-checker acquisition', async (t) => {
	const url = 'https://registry.example.org/recheck';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'fetch', arguments: { url, save_as: 'investigator-source.md' } }] } : { text: 'Fetched.' };
		if (isWorkerRequest(request, 'fact-checker')) {
			if (toolResults(request).length) return { text: 'Stopped.' };
			const claim = { finding_id: 'F1', claim_text: 'Acme paid Doe.', verdict: 'verified', confidence: 'high', evidence_for: [{ description: 'The registry states the payment.', source: url, quote: 'Acme paid Doe in full.', source_ref: { path: 'research/investigator-source.md', line_start: 2, line_end: 2 } }], evidence_against: [], grounding_assessment: { support_type: 'direct', claim_elements_checked: ['payment'], missing_assumptions: [], confidence_cap: 'high', assessment: 'The registry states it.' } };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', project: 'p', claims: [claim] }) } }] };
		}
		const n = toolResults(request).length;
		return n === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate the source.' } }] } : n === 1 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const python = join(space.root, 'fetch-python');
	await writeFile(python, `#!${process.execPath}\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({ markdown:'Registry record\\nAcme paid Doe in full.\\n', source_url:args.at(-1), provider:'crawl4ai', status_code:200 }));\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, {stdio:['inherit','pipe','inherit']})); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_RECHECK: 'off' }), 'Continue.');
	const worker = fake.requests.find((request) => isWorkerRequest(request, 'fact-checker') && toolResults(request).length === 1);
	assert.ok(worker);
	assert.match(toolResults(worker)[0], /refused/i);
	const bundle = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'));
	assert.equal(bundle.items.some((entry: { raw_path?: string }) => entry.raw_path === 'research/investigator-source.md'), false);
});

test('an investigator quote recommends the matching independently acquired capture and accepts its resubmission', async (t) => {
	const url = 'https://registry.example.org/recheck';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'fetch', arguments: { url, save_as: 'investigator-source.md' } }] } : { text: 'Fetched.' };
		if (isWorkerRequest(request, 'fact-checker')) {
			const done = toolResults(request).length;
			if (done === 0) return { toolCalls: [{ name: 'fetch', arguments: { url, save_as: 'fc-source.md' } }] };
			if (done >= 3) return { text: 'Checked.' };
			const claim = { finding_id: 'F1', claim_text: 'Acme paid Doe.', verdict: 'verified', confidence: 'high', evidence_for: [{ description: 'The registry states the payment.', source: url, quote: 'Acme paid Doe in full.', source_ref: { path: done === 1 ? 'research/investigator-source.md' : 'research/fc-source.md', line_start: 2, line_end: 2 } }], evidence_against: [], grounding_assessment: { support_type: 'direct', claim_elements_checked: ['payment'], missing_assumptions: [], confidence_cap: 'high', assessment: 'The registry states it.' } };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', project: 'p', claims: [claim] }) } }] };
		}
		const n = toolResults(request).length;
		return n === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate the source.' } }] } : n === 1 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const python = join(space.root, 'fetch-python');
	await writeFile(python, `#!${process.execPath}\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({ markdown:'Registry record\\nAcme paid Doe in full.\\n', source_url:args.at(-1), provider:'crawl4ai', status_code:200 }));\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, {stdio:['inherit','pipe','inherit']})); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_RECHECK: 'off' }), 'Continue.');
	const worker = fake.requests.find((request) => isWorkerRequest(request, 'fact-checker') && toolResults(request).length === 3);
	assert.ok(worker);
	assert.match(toolResults(worker)[1], /refused[\s\S]*research\/fc-source\.md/i);
	assert.match(toolResults(worker)[2], /^wrote data\/fact-check\.json/);
	const bundle = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'));
	assert.equal(bundle.items.some((entry: { raw_path?: string }) => entry.raw_path === 'research/investigator-source.md'), false);
	assert.equal(bundle.items.some((entry: { raw_path?: string; created_by?: string }) => entry.raw_path === 'research/fc-source.md' && entry.created_by === 'fact-checker'), true);
});

test('a rejected fact-check write returns to the same unchanged batch only', async (t) => {
	for (const mode of ['same', 'marker alias', 'changed', 'different batch', 'oversized', 'failed worker']) await t.test(mode, async (t) => {
		const findings = [{ id: 'F1', claim: 'Fixture claim one.' }, { id: 'F2', claim: 'Fixture claim two.' }];
		const rejected = JSON.stringify({ project: 'offline-demo', ...(mode === 'oversized' ? { note: '<'.repeat(10_000) } : {}), claims: [{ finding_id: 'F1', claim_text: 'Wrong claim.', verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] });
		let caseDir = '';
		let starts = 0;
		let refusal = '';
		let retryTask = '';
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'fact-checker')) {
				const results = toolResults(request);
				if (results.length) { if (starts === 1) refusal = results[0]; return { text: mode === 'failed worker' && starts === 1 ? '' : 'Stopped.' }; }
				starts++;
				if (starts === 1) return { toolCalls: [{ name: 'write_file', arguments: { path: mode === 'marker alias' ? '{CASE_DIR}/data/fact-check.json' : 'data/fact-check.json', content: rejected } }] };
				const task = userText(request);
				if (starts === 2) retryTask = task;
				else assert.doesNotMatch(task, /A prior worker's rejected write/);
				const id = mode === 'different batch' || starts > 2 ? 'F2' : 'F1';
				const claim = mode === 'changed' && id === 'F1' ? 'Changed claim one.' : findings.find((row) => row.id === id)!.claim;
				return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ project: 'offline-demo', claims: [{ finding_id: id, claim_text: claim, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] }) } }] };
			}
			const n = toolResults(request).length;
			if (n === 1 && mode === 'changed') {
				const path = join(caseDir, 'data', 'findings.json');
				const data = JSON.parse(readFileSync(path, 'utf8')); data.findings[0].claim = 'Changed claim one.'; writeFileSync(path, JSON.stringify(data));
			}
			if (n === 1 && mode === 'different batch') writeFileSync(join(caseDir, 'data', 'fact-check.json'), JSON.stringify({ project: 'offline-demo', claims: [{ finding_id: 'F1', claim_text: findings[0].claim, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] }));
			return n < 2 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Continue pass ${n + 1}.` } }] } : { text: 'Done.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		caseDir = await factCheckCase(space, findings);
		await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_FC_BATCH_SIZE: '1' }), 'Continue.');
		assert.match(refusal, /checks a claim that is not its finding's/);
		assert.ok(starts >= 2);
		if (mode === 'same' || mode === 'marker alias') {
			assert.match(retryTask, /A prior worker's rejected write/);
			const data = JSON.parse(retryTask.split('A prior worker\'s rejected write')[1].split('<<<BEGIN UNTRUSTED CASE DATA>>>\n')[1].split('\n<<<END UNTRUSTED CASE DATA>>>')[0]);
			assert.equal(data.proposal, rejected);
			assert.equal(data.error, refusal);
		} else if (mode === 'oversized') {
			assert.match(retryTask, /proposal and refusal were too large to include/);
			assert.ok(!retryTask.includes('<'.repeat(100)));
		} else assert.doesNotMatch(retryTask, /A prior worker's rejected write/);
		const claims = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8')).claims;
		assert.ok(claims.some((row: { finding_id: string }) => row.finding_id === (mode === 'different batch' ? 'F2' : 'F1')));
	});
});

test('an accepted fact-check write clears its prior rejected proposal', async (t) => {
	const finding = { id: 'F1', claim: 'Fixture claim one.' };
	let caseDir = '';
	let starts = 0;
	let retry = '';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			const n = toolResults(request).length;
			if (n === 0) { starts++; if (starts === 2) retry = userText(request); }
			if (starts === 1 && n === 0) return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ claims: [{ finding_id: 'F1', claim_text: 'Wrong claim.', verdict: 'unverified' }] }) } }] };
			if ((starts === 1 && n === 1) || (starts === 2 && n === 0)) return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ project: 'offline-demo', claims: [{ finding_id: 'F1', claim_text: finding.claim, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] }) } }] };
			return { text: 'Checked.' };
		}
		const n = toolResults(request).length;
		if (n === 1) unlinkSync(join(caseDir, 'data', 'fact-check.json'));
		return n < 2 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Continue pass ${n + 1}.` } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	caseDir = await factCheckCase(space, [finding]);
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	assert.equal(starts, 2);
	assert.doesNotMatch(retry, /A prior worker's rejected write/);
});

test('a rejected multi-quote write is carried after its earlier evidence side effect', async (t) => {
	const url = 'https://registry.example.org/recheck';
	let starts = 0;
	let refusal = '';
	let retryTask = '';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			const results = toolResults(request);
			if (results.length === 0) starts++;
			if (starts === 1 && results.length === 0) return { toolCalls: [{ name: 'fetch', arguments: { url, save_as: 'fc-side.md' } }] };
			if (starts === 1 && results.length === 2) { refusal = results[1]; return { text: 'Stopped.' }; }
			if (starts === 2 && results.length === 0) retryTask = userText(request);
			if ((starts === 1 && results.length === 1) || (starts === 2 && results.length === 0)) {
				const evidence = { description: 'Registry payment.', source: url, quote: 'Acme paid Doe in full.', source_ref: { path: 'research/fc-side.md', line_start: 2, line_end: 2 } };
				const claim = { finding_id: 'F1', claim_text: 'Acme paid Doe.', verdict: 'verified', confidence: 'high', evidence_for: starts === 1 ? [evidence, { ...evidence, source_ref: { path: 'research/unregistered.md', line_start: 2, line_end: 2 } }] : [evidence], evidence_against: [], grounding_assessment: { support_type: 'direct', claim_elements_checked: ['payment'], missing_assumptions: [], confidence_cap: 'high', assessment: 'The registry states it.' } };
				return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', project: 'p', claims: [claim] }) } }] };
			}
			return { text: 'Checked.' };
		}
		const n = toolResults(request).length;
		return n < 2 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Continue pass ${n + 1}.` } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	await writeFile(join(caseDir, 'research', 'unregistered.md'), 'Registry record\nAcme paid Doe in full.\n');
	const python = join(space.root, 'fetch-python');
	await writeFile(python, `#!${process.execPath}\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) process.stdout.write(JSON.stringify({ markdown:'Registry record\\nAcme paid Doe in full.\\n', source_url:args.at(-1), provider:'crawl4ai', status_code:200 }));\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, {stdio:['inherit','pipe','inherit']})); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_RECHECK: 'off' }), 'Continue.');
	assert.match(refusal, /refused[\s\S]*unregistered\.md/);
	assert.match(retryTask, /A prior worker's rejected write/);
	const bundle = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'));
	assert.ok(bundle.items.some((item: { raw_path?: string }) => item.raw_path === 'research/fc-side.md'));
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
});

// step2-design.md section 3 (Tom, plan C8): a re-fetch of the same source that
// still holds the supporting passage verifies the finding. The fact-checker
// may cite the re-fetch itself, or answer judge_finding and code writes the
// claim; a page that changed leaves the finding unverified with the reason.
test('a fact-checker can cite the harness recheck as its independent acquired evidence, or answer judge_finding and code writes the claim', async (t) => {
 for (const route of ['record', 'judge', 'judge-changed', 'judge-redirected', 'record-unrelated', 'judge-contradicts', 'judge-value', 'judge-person'] as const) await t.test(route, async (t) => {
	const url = 'https://registry.example.org/recheck';
	// The case's supporting passage is "Acme paid Doe." (the fixture's SX1).
	const page = route === 'judge-changed' ? 'Registry record\nThe record was withdrawn.\n' : route === 'judge-contradicts' ? 'Registry record\nAcme [never](https://registry.example.org/note) paid Doe.\n' : 'Registry record\nAcme paid Doe.\n';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) {
			// Tom (2026-11-04): contradicts is disputed, and needs a quote from the re-fetch; first without one.
			if (route === 'judge-contradicts') {
				const done = toolResults(request).length;
				return done === 0 ? { toolCalls: [{ name: 'judge_finding', arguments: { answer: 'contradicts', reason: 'The registry says the payment never happened.' } }] }
					: done === 1 ? { toolCalls: [{ name: 'judge_finding', arguments: { answer: 'contradicts', reason: 'The registry says the payment never happened.', quote: 'Acme never paid Doe.' } }] }
					: { text: 'Checked.' };
			}
			if (toolResults(request).length) return { text: 'Checked.' };
			if (route.startsWith('judge')) return { toolCalls: [{ name: 'judge_finding', arguments: { answer: 'supports', reason: 'The registry states the payment.' } }] };
			const path = /research\/fc-recheck-E1\.md(?:\.raw)?/.exec(userText(request))?.[0];
			assert.ok(path, 'the assigned packet identifies the recheck file');
			// record-unrelated: a verified row quoting another line of the same page,
			// with no grounding assessment, gets no support type (Codex review of simp-3, P1).
			const claim = route === 'record-unrelated'
				? { finding_id: 'F1', claim_text: 'Acme paid Doe.', verdict: 'verified', evidence_for: [{ description: 'The page header.', source: url, quote: 'Registry record', source_ref: { path, line_start: 1, line_end: 1 } }], evidence_against: [] }
				: { finding_id: 'F1', claim_text: 'Acme paid Doe.', verdict: 'verified', confidence: 'high', evidence_for: [{ description: 'The rechecked registry states the payment.', source: url, quote: 'Acme paid Doe.', source_ref: { path, line_start: 2, line_end: 2 } }], evidence_against: [], grounding_assessment: { support_type: 'direct', claim_elements_checked: ['payment'], missing_assumptions: [], confidence_cap: 'high', assessment: 'The registry states it.' } };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', project: 'p', claims: [claim] }) } }] };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const bundlePath = join(caseDir, 'data', 'evidence-bundle.json');
	const bundle = JSON.parse(await readFile(bundlePath, 'utf8'));
	bundle.items[0].source_url = url;
	// One investigator item to recheck: the fixture's own FC-E1 re-fetch is left out.
	bundle.items = bundle.items.filter((item: { created_by?: string }) => item.created_by !== 'fact-checker');
	await writeFile(bundlePath, JSON.stringify(bundle));
	if (route === 'judge-value' || route === 'judge-person') {
		// The claim check: the passage is on the page, but it does not hold the finding's figure, or person.
		const findingsPath = join(caseDir, 'data', 'findings.json');
		const findings = JSON.parse(await readFile(findingsPath, 'utf8'));
		const claim = route === 'judge-person' ? 'Dennis Jackson is a member of the Acme board.' : 'Acme paid Doe 1.2M on 3 March 2024.';
		findings.findings[0].claim = claim;
		await writeFile(findingsPath, JSON.stringify(findings));
		for (const item of bundle.items) for (const link of item.claim_links ?? []) if (link.finding_id === 'F1') link.claim_text = claim;
		await writeFile(bundlePath, JSON.stringify(bundle));
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'write-source-expressions.py'), caseDir, '--fingerprint-findings']);
	}
	const runs = join(space.root, 'recheck-runs');
	const python = join(space.root, 'recheck-python');
	await writeFile(python, `#!${process.execPath}\nconst fs = require('node:fs');\nconst cp = require('node:child_process');\nconst args = process.argv.slice(2);\nif (args.includes('integrations.scraping')) { fs.appendFileSync(${JSON.stringify(runs)}, 'fetch\\n'); process.stdout.write(JSON.stringify({ markdown:${JSON.stringify(page)}, source_url:${route === 'judge-redirected' ? JSON.stringify('https://elsewhere.example/landing') : 'args.at(-1)'}, provider:'crawl4ai', status_code:200 })); }\nelse { try { process.stdout.write(cp.execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, {stdio:['inherit','pipe','inherit']})); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); } }\n`, { mode: 0o755 });
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_RECHECK: 'on', SPOTLIGHT_ANONYMIZE_FETCH: 'true' }), 'Continue.');
	assert.equal((await readFile(runs, 'utf8')).trim().split('\n').length, 1, 'only the harness fetched the source');
	const checked = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8'));
	const task = userText(fake.requests.find((request) => isWorkerRequest(request, 'fact-checker'))!);
	assert.doesNotMatch(task, /re-fetch or text match is not verification|A text match is not verification/, 'the task no longer says a re-fetch is not verification');
	assert.match(task, /still contains the passage that supports the finding, that verifies it from the same source/);
	if (route === 'judge-redirected') {
		assert.equal(checked.claims[0].verdict, 'unverified');
		assert.match(checked.claims[0].notes, /the re-fetch landed on https:\/\/elsewhere\.example\/landing, not the cited source/);
		return;
	}
	if (route === 'record-unrelated') {
		assert.throws(() => execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir], { encoding: 'utf8', stdio: 'pipe' }), (error: { stdout?: string }) => /does not link finding F1 as direct or indirect support/.test(error.stdout ?? ''));
		return;
	}
	if (route === 'judge-contradicts') {
		const results = toolResults(fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker')).at(-1)!);
		assert.match(results[0], /judge_finding: contradicts needs quote/);
		// Continuity: the claim is written disputed, low, with the re-fetch line against it, and validates.
		assert.equal(checked.claims[0].verdict, 'disputed');
		assert.equal(checked.claims[0].confidence, 'low');
		assert.equal(checked.claims[0].evidence_for.length, 0);
		assert.equal(checked.claims[0].evidence_against[0].quote, 'Acme [never](https://registry.example.org/note) paid Doe.', 'H-1: the visible words find the line, and the line is recorded as the file has it');
		assert.match(checked.claims[0].evidence_against[0].source_ref.path, /^research\/fc-recheck-E1\.md/);
		try {
			execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir], { encoding: 'utf8' });
		} catch (error) {
			assert.fail(`validate-fact-check.py: ${String((error as { stdout?: string }).stdout ?? error)} ${JSON.stringify(checked.claims)}`);
		}
		return;
	}
	if (route === 'judge-person') {
		// u9mac-verdict2bd8 D1-F5: a person swap is written unverified, names them, and validates.
		assert.equal(checked.claims[0].verdict, 'unverified');
		assert.equal(checked.claims[0].evidence_for.length, 0);
		assert.match(checked.claims[0].notes, /Harness re-check: the cited passage does not name Dennis Jackson/);
		assert.match(toolResults(fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker')).at(-1)!)[0], /F1 recorded as unverified: the cited passage does not name Dennis Jackson\. Go on to your next finding/);
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
		return;
	}
	if (route === 'judge-value') {
		// Downgraded, not refused: the claim is written, names the values, the turn goes on, and it validates.
		assert.equal(checked.claims[0].verdict, 'unverified');
		assert.equal(checked.claims[0].confidence, 'low');
		assert.equal(checked.claims[0].evidence_for.length, 0);
		assert.match(checked.claims[0].notes, /The registry states the payment\. \(Harness re-check: the finding's sources do not contain 3 March 2024, 1\.2M/);
		const reply = toolResults(fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker')).at(-1)!)[0];
		assert.match(reply, /F1 recorded as unverified: the finding's sources do not contain/);
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
		return;
	}
	if (route === 'judge-changed') {
		// Continuity: the claim is written, unverified with the reason, and validates.
		assert.equal(checked.claims[0].verdict, 'unverified');
		assert.equal(checked.claims[0].confidence, 'low');
		assert.match(checked.claims[0].notes, /The registry states the payment\. \(Harness re-check: .*supporting passage is no longer on the page/);
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
		return;
	}
	const items = JSON.parse(await readFile(bundlePath, 'utf8')).items;
	const item = items.find((entry: { raw_path?: string }) => entry.raw_path?.startsWith('research/fc-recheck-E1.md'));
	assert.ok(item);
	assert.equal(item.created_by, 'fact-checker');
	assert.equal(item.source_url, url);
	assert.equal(item.sha256, createHash('sha256').update(await readFile(join(caseDir, item.raw_path))).digest('hex'));
	assert.equal(checked.claims[0].evidence_for[0].evidence_bundle_id, item.id);
	if (route === 'judge') {
		assert.equal(checked.claims[0].verdict, 'verified');
		// review-simp.md #1: confidence is code's paperwork from the rule, so Ingest can build the row.
		assert.equal(checked.claims[0].confidence, 'medium');
		assert.match(checked.claims[0].evidence_for[0].description, /Same source as the investigation, not a second source/);
	}
	try {
		execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir], { encoding: 'utf8' });
	} catch (error) {
		assert.fail(`validate-fact-check.py: ${String((error as { stdout?: string }).stdout ?? error)} ${JSON.stringify(checked.claims)}`);
	}
 });
});

test('launcher source rechecks reach the assigned packet and reuse the case cache', async (t) => {
	let spawns = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Source examined; verdict unfinished.' };
		return spawns++ < 2 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: `Check the remaining source pass ${spawns}.` } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await factCheckCase(space, [{ id: 'F1', claim: 'Acme paid Doe.' }]);
	const bundlePath = join(caseDir, 'data/evidence-bundle.json');
	const bundle = JSON.parse(await readFile(bundlePath, 'utf8'));
	bundle.items = [{ id: 'E1', source_url: 'https://registry.example.org/recheck', query_or_task: 'Check registry', accessed: '2026-10-30T12:00:00Z', acquisition_method: 'api', extraction_confidence: 'high', human_verification_required: false, claim_links: [{ finding_id: 'F1', claim_text: 'Acme paid Doe.', support_type: 'direct' }] }];
	await writeFile(bundlePath, JSON.stringify(bundle));
	const runs = join(space.root, 'recheck-runs');
	const python = join(space.root, 'recheck-python');
	// Existing Tor posture avoids direct acquisition; the seam itself is this local executable.
	await writeFile(python, `#!${process.execPath}
const fs = require('node:fs');
const cp = require('node:child_process');
const args = process.argv.slice(2);
if (args.includes('integrations.scraping')) {
 if (!args.includes('--tor')) process.exit(3);
 fs.appendFileSync(${JSON.stringify(runs)}, 'fetch\\n');
 process.stdout.write(JSON.stringify({markdown:'Acme paid Doe.', source_url:args.at(-1), provider:'crawl4ai', status_code:200}));
} else {
 try { process.stdout.write(cp.execFileSync(${JSON.stringify(process.env.SPOTLIGHT_PYTHON ?? 'python3')}, args, {stdio:['inherit','pipe','inherit']})); }
 catch (error) { process.stdout.write(error.stdout ?? ''); process.exit(error.status ?? 1); }
}
`, { mode: 0o755 });

	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_PYTHON: python, SPOTLIGHT_RECHECK: 'on', SPOTLIGHT_ANONYMIZE_FETCH: 'true' }), 'Continue.');

	const workers = fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker'));
	assert.equal(workers.length, 2, toolResults(fake.requests.at(-1)!).join('\n'));
	for (const worker of workers) assert.match(userText(worker), /re-check: (?:still says|re-fetched)/);
	assert.equal((await readFile(runs, 'utf8')).trim().split('\n').length, 1);
});

// U3: the runtime prepares the approved direction and retained capture packet;
// it is attached once, survives folds by private pointer and is never FC input.
test('investigation handoff is supplied by code and kept role-private across folds', async (t) => {
	const checkpoint = '## Goal\nCheck vintage.\n## Progress\nPACKET-CHECKPOINT\n## Decisions\nNone.\n## Next\nUse captures.\n## Open gate\nNone.';
	let reads = 0;
	const fake = await startFakeOpenAI((request) => {
		if ((request.tools ?? []).length === 0) return { text: checkpoint };
		if (isWorkerRequest(request, 'investigator')) return reads < 3 && !JSON.stringify(request.messages).includes('PACKET-CHECKPOINT')
			? { toolCalls: [{ name: 'read_file', arguments: { path: `research/source-${reads++}.md` } }] }
			: { text: 'Inspected.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nCheck the approved direction.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, 'Was the original figure revised?', { investigation_plan: [{ direction_id: 'vintage', direction: 'Check original and revised releases.', questions: ['Which vintage supports each figure?'], steps: [] }] });
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const n of [0, 1, 2]) await writeFile(join(caseDir, `research/source-${n}.md`), `${n} ${'The registry lists the fixture company. '.repeat(1500)}`);
	await writeFile(join(caseDir, 'data/evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', source_url: 'https://example.org/revision', raw_path: 'research/revision.md.raw', accessed: '2026-11-01T00:00:00Z', notes: 'REVISION-CAPTURE-MARKER' }] }));
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '40960' });
	await settledPrompt(chat, 'Continue.');
	const first = fake.requests.find((request) => isWorkerRequest(request, 'investigator'))!;
	assert.match(userText(first), /BEGIN UNTRUSTED INVESTIGATION PACKET/);
	assert.match(userText(first), /Which vintage supports each figure/);
	assert.match(userText(first), /REVISION-CAPTURE-MARKER/);
	const folded = fake.requests.find((request) => isWorkerRequest(request, 'investigator') && JSON.stringify(request.messages).includes('PACKET-CHECKPOINT'));
	assert.ok(folded, 'the investigator folded');
	const text = JSON.stringify(folded.messages);
	const kept = /Case passages for this task: (\/[^;]+\/work\/investigator\/passages\/investigator-[^;]+\.md); read it if you need them again\./.exec(text)?.[1];
	assert.ok(kept, text.slice(-3000));
	assert.doesNotMatch(text, /REVISION-CAPTURE-MARKER/);
	assert.match(await readFile(kept, 'utf8'), /REVISION-CAPTURE-MARKER/);
	const { caseFileTools } = await import('./tools/case-files.ts');
	const context = { caseDir, writeRoot: caseDir, scope: loadComposition()['fact-checker'], python: process.env.SPOTLIGHT_PYTHON ?? 'python3', configPath: '', env: { SPOTLIGHT_ROLE: 'fact-checker' } };
	for (const [name, args] of [['read_file', { path: kept }], ['grep_files', { path: kept, pattern: 'REVISION-CAPTURE-MARKER' }], ['list_files', { pattern: kept }]] as const) {
		const tool = caseFileTools(context).find((tool) => tool.name === name)!;
		let result;
		try { result = await tool.execute('isolation', args, undefined, undefined, undefined as never); }
		catch (error) { assert.match(String(error), /outside|relative|pattern/); continue; }
		assert.doesNotMatch(JSON.stringify(result), /REVISION-CAPTURE-MARKER/);
	}
	const files = await caseDigests(caseDir);
	assert.ok([...files.keys()].every((path) => !/passages|packet/.test(path)), 'no packet is indexed as case evidence');
});

test('a harness data folder inside the case cannot publish an Investigation packet into case search', async (t) => {
	const fake = await startFakeOpenAI(orchestratorScript([{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nCheck.' } }], () => ({ text: 'Must not run.' })));
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, 'Check release.', { investigation_plan: [{ direction: 'Check dates.', questions: [], steps: [] }] });
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_HARNESS_DATA: join(caseDir, 'runtime-data') });
	await settledPrompt(chat, 'Continue.');
	assert.ok(!fake.requests.some((request) => isWorkerRequest(request, 'investigator')));
	assert.match(toolResults(fake.requests.at(-1)!).join('\n'), /configure harness data outside the active case/);
	const files = await caseDigests(caseDir);
	assert.ok([...files.keys()].every((path) => !path.includes('/passages/')), 'no packet was written under the case');
});

// ── Dispatch-only orchestrator (constellation-20261102, reports/dispatch-audit.md) ──

test('the orchestrator holds only the dispatch tools, and a call to a removed tool changes no case file', async (t) => {
	const fake = await startFakeOpenAI((request) =>
		toolResults(request).length === 0
			? {
					toolCalls: [
						{ name: 'provenance_manifest', arguments: { action: 'build' } },
						{ name: 'build_review_html', arguments: {} },
						{ name: 'list_files', arguments: { pattern: 'data/*' } },
						{ name: 'write_file', arguments: { path: 'data/findings.json', content: '{}' } },
						{ name: 'write_file', arguments: { path: 'summary.md', content: 'Orchestrator summary.' } },
					],
				}
			: { text: 'Done.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	const before = await caseDigests(caseDir);
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');

	for (const request of fake.requests) assert.deepEqual(toolNames(request), DISPATCH_TOOLS);
	const results = toolResults(fake.requests.at(-1)!);
	assert.match(results[0], /Tool provenance_manifest not found/);
	assert.match(results[1], /Tool build_review_html not found/);
	assert.match(results[2], /list_files is not one of your tools/);
	assert.match(results[3], /refused: data\/findings\.json is a case file another role writes/);
	assert.match(results[4], /refused: summary\.md is a case file another role writes[^\n]*analyst/);
	assert.deepEqual(await caseDigests(caseDir), before);
});

test('after the member approves the gate, spotlight_resolve builds the provenance manifest and review.html and seals the gate itself', async (t) => {
	const fake = await startFakeOpenAI(resolveThenStop);
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	memberApprovesGate(caseDir);
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Done.');

	const resolved = lastResolution(chat);
	assert.equal(resolved.phase, 'report');
	assert.deepEqual((resolved.fixed_steps as Array<{ step: string; ok: boolean }>).map(({ step, ok }) => [step, ok]), [['provenance', true], ['review', true], ['seal_gate', true]]);
	assert.ok(existsSync(join(caseDir, 'data', 'provenance-manifest.json')));
	assert.match(await readFile(join(caseDir, 'review.html'), 'utf8'), /<!doctype html>/i);
});

test('a scripted orchestrator using only spotlight_resolve, spawn_agent and open_review reaches the report gate; code writes the summary and the analyst the draft', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	// P4 (qwen-u9-mac.md): r3 left summary.md a template. On a local tier no analyst writes it.
	const [finding] = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).findings;
	await unlink(join(caseDir, 'summary.md'));
	await unlink(join(caseDir, 'data', 'summary.json'));
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'analyst')) {
			if (toolResults(request).length > 0) return { text: 'Wrote it.' };
			// The analyst writes only prose into the structure the harness wrote.
			const skeleton = () => JSON.parse(readFileSync(join(caseDir, 'data', 'report-draft.json'), 'utf8'));
			const prose = () => ({ ...skeleton(), title: REPORT_DRAFT.title, deck: REPORT_DRAFT.deck, finding_treatments: REPORT_DRAFT.finding_treatments });
			return { toolCalls: [{ name: 'read_file', arguments: { path: 'data/source-expressions.json' } }, { name: 'write_file', arguments: { path: 'data/report-draft.json', content: JSON.stringify(prose()) } }] };
		}
		const last = request.messages.at(-1)!;
		if (last.role !== 'tool') return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
		const called = (request.messages.filter((message) => message.role === 'assistant').at(-1)!.tool_calls as Array<{ function: { name: string } }>)[0].function.name;
		if (called === 'open_review') return { text: 'Please decide on the review page.' };
		if (called !== 'spotlight_resolve') return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
		const state = JSON.parse(String(last.content)) as { phase: string; missing: string[] };
		const waiting = state.phase === 'investigation_gate_approval' ? state.missing.some((item) => /summary/.test(item)) : !existsSync(join(caseDir, 'data', 'report-draft.json'));
		return { toolCalls: [waiting ? { name: 'spawn_agent', arguments: { agent: 'analyst', task: '' } } : { name: 'open_review', arguments: {} }] };
	});
	t.after(() => fake.close());
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');
	memberApprovesGate(caseDir);
	await settledPrompt(chat, 'Done.');

	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'analyst'));
	const called = orchestrator.flatMap((request) => (request.messages.filter((message) => message.role === 'assistant').flatMap((message) => (message.tool_calls ?? []) as Array<{ function: { name: string } }>)).map((call) => call.function.name));
	assert.deepEqual([...new Set(called)].sort(), ['open_review', 'spawn_agent', 'spotlight_resolve']);
	assert.ok((await readFile(join(caseDir, 'summary.md'), 'utf8')).includes(finding.claim));
	const completed = JSON.parse(await readFile(join(caseDir, 'data', 'summary.json'), 'utf8'));
	assert.deepEqual(completed.findings, [{ id: 'F1', claim: finding.claim, confidence: 'high', fact_check_verdict: 'verified', source_count: 1 }]);
	assert.match(execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'scripts', 'validate-case.py'), caseDir], { encoding: 'utf8' }), /PASS|valid/i, 'the completed file passes the check that stopped the Qwen run');
	assert.match(userText(fake.requests.find((request) => isWorkerRequest(request, 'analyst'))!), /report-draft\.json/, 'the first analyst is the Report one');
	// On a local tier the headline is the fact-checked claim, set by code (claimsAsHeadlines).
	const claimOnly = REPORT_DRAFT.finding_treatments.map((treatment) => ({ ...treatment, headline: finding.claim, summary: '', why_it_matters: '' }));
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'report-draft.json'), 'utf8')), { ...REPORT_DRAFT, finding_treatments: claimOnly, caveats: [], next_steps: [] });
	assert.match(userText(fake.requests.find((request) => isWorkerRequest(request, 'analyst') && /report-draft\.json/.test(userText(request)))!), /holds the report's structure/);
	// report-direct-quote-diff.md: the Report analyst reads which passages are
	// direct quotes, the only ones quote_selections may cite.
	const drafted = fake.requests.find((request) => isWorkerRequest(request, 'analyst') && /report-draft\.json/.test(userText(request)) && toolResults(request).length > 0)!;
	assert.match(toolResults(drafted)[0], /"expressions"[\s\S]*"direct_quote"/);
	assert.equal(lastResolution(chat).phase, 'report');
	// The turn ends once the page is open, so the stdout records carry the outcome.
	const opened = chat.lines
		.map((line) => JSON.parse(line) as { type?: string; toolName?: string; isError?: boolean; result?: { content: Array<{ text: string }> } })
		.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'open_review');
	assert.equal(opened.length, 2, 'the page opened at the gate and at the report');
	for (const record of opened) assert.match(record.result!.content[0].text, /review page is open/);
});

// Integration (watchdog x dispatch): the gate analyst's summary files are its
// code-owned deliverable (roles.ts ANALYST_DELIVERABLES), so the progress
// watchdog credits writing them and does not stop the analyst at 16 turns.
test('a gate analyst that writes the summary after ten searches is credited and not stopped by the progress watchdog', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	const summaryMd = await readFile(join(caseDir, 'summary.md'), 'utf8');
	const summaryJson = await readFile(join(caseDir, 'data', 'summary.json'), 'utf8');
	await unlink(join(caseDir, 'summary.md'));
	await unlink(join(caseDir, 'data', 'summary.json'));
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'analyst')) {
			const turn = toolResults(request).length;
			if (turn === 17) return { text: 'Wrote it.' };
			return turn === 10
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'summary.md', content: summaryMd } }, { name: 'write_file', arguments: { path: 'data/summary.json', content: summaryJson } }] }
				: { toolCalls: [{ name: 'grep_files', arguments: { pattern: `absent text ${turn}`, path: 'data/findings.json' } }] };
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task: '' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	const analyst = fake.requests.filter((request) => isWorkerRequest(request, 'analyst'));
	assert.equal(analyst.length, 17, 'ten searches, one turn of two writes, five more searches, then the answer');
	assert.equal(await readFile(join(caseDir, 'summary.md'), 'utf8'), summaryMd);
	const answer = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'analyst')).at(-1)!).join('\n');
	assert.doesNotMatch(answer, /progress watchdog limit/);
	assert.match(answer, /Wrote it\./);
});

// r6/fz1 on fix5 (reports/e2e.md): the analyst's code-built report task never
// carried validate-report-draft's errors, so it rewrote the same draft (missing
// a kept finding) 16-17 times until loop notices stopped the orchestrator. Its
// repair task now carries the harness's own FAIL lines, and after the first try
// and two repairs the harness refuses another analyst with a receipt.
// review-ur R1: at the cap the invalid draft is set aside, so the page opens
// for the member to decline; R2: the member's next message resets the count.
test('a failing report draft: repairs carry the FAIL lines, the cap sets the draft aside so the member can decline, and a member turn allows another try', async (t) => {
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests/fixtures/review_case.py'), caseDir, 'report', '--activated', '--clean']);
	const omitsKept = { ...REPORT_DRAFT, framing_finding_ids: [], finding_order: [], finding_treatments: [], caveats: [], next_steps: [] };
	let calls = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'analyst')) {
			return toolResults(request).length === 0 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/report-draft.json', content: JSON.stringify(omitsKept) } }] } : { text: 'Wrote it.' };
		}
		calls += 1;
		if (calls <= 4 || calls === 6) return { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task: `Fix the draft, call ${calls}. ORCH-SENTINEL` } }] };
		return calls === 5 ? { toolCalls: [{ name: 'open_review', arguments: {} }] } : { text: 'Stopped.' };
	});
	t.after(() => fake.close());
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');

	const analystTasks = () => fake.requests.filter((request) => isWorkerRequest(request, 'analyst') && toolResults(request).length === 0).map(userText);
	const tasks = analystTasks();
	assert.equal(tasks.length, 3, 'the first try and two repairs');
	assert.doesNotMatch(tasks[0], /FAIL/);
	for (const task of tasks.slice(1)) {
		assert.match(task, /FAIL {2}STRUCTURE: finding_order must contain every finding exactly once; missing=\['F1'\][^\n]*kept at the Investigation gate/);
		assert.doesNotMatch(task, /ORCH-SENTINEL/);
	}
	const results = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'analyst')).at(-1)!);
	assert.match(results[2], /\[report-repair receipt\] \{"cap":"report-repairs","role":"analyst","phase":"report","repairs":2,"failed_checks":3,"set_aside":"data\/harness-checks\/analyst\/report-draft-rejected-[^"]+\.json"\}/);
	assert.match(results[3], /no analyst was started[^\n]*\[report-repair receipt\]/);
	assert.match(results[3], /missing=\['F1'\]/);
	const opened = chat.lines
		.map((line) => JSON.parse(line) as { type?: string; toolName?: string; isError?: boolean; result?: { content: Array<{ text: string }> } })
		.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'open_review');
	assert.equal(opened.length, 1);
	assert.ok(!opened[0].isError, opened[0].result?.content[0].text);
	assert.match(opened[0].result!.content[0].text, /review page is open[\s\S]*can only decline the report/);
	assert.ok(!existsSync(join(caseDir, 'data', 'report-draft.json')), 'the invalid draft is no longer the case\'s draft');
	const setAside = /"set_aside":"([^"]+)"/.exec(results[2])![1];
	const kept = JSON.parse(await readFile(join(caseDir, setAside), 'utf8')) as { receipt: { failed_checks: number }; validator_report: string; draft: string };
	assert.deepEqual([kept.receipt.failed_checks, kept.draft], [3, JSON.stringify(omitsKept)]);
	assert.match(kept.validator_report, /missing=\['F1'\]/);

	await settledPrompt(chat, 'Please try the report once more.');
	assert.equal(analystTasks().length, 4, 'the member\'s message allows another try');
	assert.match(analystTasks()[3], /FAIL {2}STRUCTURE: finding_order/);
});

// Audit G4 (reports/audit-feedback-loop.md): the gate-summary analyst had no
// cap, so each resolve or spawn started another analyst on a summary that kept
// failing validate-case. It now shares the report's cap: repairs carry the FAIL
// lines, the third failure sets the summary aside with a receipt, and the
// member's next message allows another try.
test('a failing gate summary: repairs carry the FAIL lines, the cap sets the summary aside with a receipt, and a member turn allows another try', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	const summaryMd = await readFile(join(caseDir, 'summary.md'), 'utf8');
	const invalid = JSON.stringify({ ...JSON.parse(await readFile(join(caseDir, 'data', 'summary.json'), 'utf8')), cycles: 0 });
	await unlink(join(caseDir, 'summary.md'));
	await unlink(join(caseDir, 'data', 'summary.json'));
	let calls = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'analyst')) {
			return toolResults(request).length === 0
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'summary.md', content: summaryMd } }, { name: 'write_file', arguments: { path: 'data/summary.json', content: invalid } }] }
				: { text: 'Wrote it.' };
		}
		calls += 1;
		if (calls <= 4 || calls === 6) return { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task: `Fix the summary, call ${calls}.` } }] };
		return { text: 'Stopped.' };
	});
	t.after(() => fake.close());
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');

	const analystTasks = () => fake.requests.filter((request) => isWorkerRequest(request, 'analyst') && toolResults(request).length === 0).map(userText);
	assert.equal(analystTasks().length, 3, 'the first try and two repairs');
	assert.doesNotMatch(analystTasks()[0], /cycles: must be an integer/);
	for (const task of analystTasks().slice(1)) assert.match(task, /summary\.json\.cycles: must be an integer/);
	const results = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'analyst')).at(-1)!);
	assert.match(results[2], /\[report-repair receipt\] \{"cap":"report-repairs","role":"analyst","phase":"investigation_gate_approval","repairs":2,"failed_checks":3,"set_aside":"data\/harness-checks\/analyst\/summary-rejected-[^"]+\.json"\}[^\n]*ask in the chat for another try/);
	assert.match(results[3], /no analyst was started[^\n]*gate summary could not be made valid[^\n]*\[report-repair receipt\]/);
	assert.match(results[3], /cycles: must be an integer/);
	assert.ok(!existsSync(join(caseDir, 'data', 'summary.json')) && !existsSync(join(caseDir, 'summary.md')), 'the invalid summary is no longer the case\'s summary');
	const setAside = /"set_aside":"([^"]+)"/.exec(results[2])![1];
	const kept = JSON.parse(await readFile(join(caseDir, setAside), 'utf8')) as { receipt: { failed_checks: number }; validator_report: string; files: Record<string, string> };
	assert.deepEqual([kept.receipt.failed_checks, JSON.parse(kept.files['data/summary.json']).cycles, kept.files['summary.md']], [3, 0, summaryMd]);
	assert.match(kept.validator_report, /cycles: must be an integer/);

	await settledPrompt(chat, 'Please try the summary once more.');
	assert.equal(analystTasks().length, 4, 'the member\'s message allows another try');
});

test('a spawn with no agent, an unknown agent or an investigator with an empty task is refused with the fix, and no worker starts', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (systemText(request).includes(' worker.')) return { text: 'Unexpected worker.' };
		return toolResults(request).length === 0
			? { toolCalls: [
					{ name: 'spawn_agent', arguments: { agent: 'investigator', task: '   ' } },
					{ name: 'spawn_agent', arguments: { task: 'Plan the case.' } },
					{ name: 'spawn_agent', arguments: { agent: 'reporter', task: 'Plan the case.' } },
				] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await briefApproved(space);
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');

	// The refused calls start no worker: any worker request is code's own
	// planning task from a resolve's dispatch, never a refused one.
	for (const request of fake.requests.filter((request) => systemText(request).includes('You were delegated a task') && toolResults(request).length === 0)) assert.match(userText(request), /^MODE: PLANNING\nPlan the investigation of the approved brief/);
	const results = toolResults(fake.requests.find((request) => !systemText(request).includes('You were delegated a task') && toolResults(request).length >= 3)!);
	assert.match(results[0], /spawn_agent needs a task for the investigator that says what to do/);
	for (const result of results.slice(1)) assert.match(result, /spawn_agent needs agent set to one of: investigator, fact-checker, analyst/);
});

// Constellation inventory G3b: the fact-checker judges independently, so the
// orchestrator's text (which may carry the investigator's answer) never reaches it.
test('the orchestrator\'s text never reaches the fact-checker; its task is built by code', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'The investigator concluded: INV-ANSWER-SENTINEL. Confirm it.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = caseAtGate(space);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');

	const checker = fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker'));
	assert.ok(checker.length > 0, 'the fact-checker ran');
	for (const request of checker) assert.ok(!JSON.stringify(request).includes('INV-ANSWER-SENTINEL'));
	assert.match(userText(checker[0]), /Fact-check the findings the harness assigns below independently/);
	assert.match(userText(checker[0]), /- F1: "Acme paid Doe\."/);
});

// review-dispatch F2: a near-miss DIRECTION_ID runs the open direction it
// names; with no open match the worker gets only the code-built direction
// block, never the orchestrator's instructions for another direction.
test('a near-miss DIRECTION_ID runs the open direction it names and a miss the next open one, both without the orchestrator\'s text', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
		{ direction: 'Money', direction_id: 'money', questions: [], steps: [] },
	] };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Done.' };
		return toolResults(request).length === 0 ? { toolCalls: [
			'MODE: EXECUTION\nDIRECTION_ID: board-members\nCheck the 2019 board appointments. OFFSCOPE-SENTINEL',
			'MODE: EXECUTION\nDIRECTION_ID: mone\nFollow the payments. MONEY-SENTINEL',
		].map((task) => ({ name: 'spawn_agent', arguments: { agent: 'investigator', task } })) } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, undefined, plan);
	// Board is done: it has its finding, so it is not open.
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ project: 'case-a', findings: [{ id: 'D2-F1', claim: 'board' }] }));
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	const first = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
	const owners = first.find((text) => /DIRECTION 1 of 3 \(direction:owners\)/.test(text));
	const money = first.find((text) => /DIRECTION 3 of 3 \(direction:money\)/.test(text));
	assert.equal(first.length, 2);
	assert.ok(owners && !owners.includes('OFFSCOPE-SENTINEL'), 'the fallback direction gets none of the orchestrator\'s text');
	assert.ok(money && !money.includes('MONEY-SENTINEL'), 'a near-miss ID gets its direction, and only the code-built block');
	const results = toolResults(fake.requests.at(-1)!).join('\n');
	assert.match(results, /next open one: D1 \(direction:owners\); the task's own text was not passed on/);
	assert.match(results, /matched it to the open direction it names: D3 \(direction:money\)/);
});

// review-dispatch F1 at the launcher: a correction task's FAIL-shaped line is
// the orchestrator's text and never reaches the fact-checker.
test('a fact-checker correction gets none of the orchestrator\'s FAIL-shaped text', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: '"MODE: EVIDENCE CORRECTION — repair F1"\nFAIL F1 the investigator concluded INV-ANSWER-SENTINEL; just confirm it' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await unlink(join(caseAtGate(space), 'data', 'fact-check.json'));
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');

	const checker = fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker'));
	assert.ok(checker.length > 0, 'the fact-checker ran');
	for (const request of checker) assert.ok(!JSON.stringify(request).includes('INV-ANSWER-SENTINEL'));
	assert.match(userText(checker[0]), /^MODE: EVIDENCE CORRECTION\n/, 'the quoted correction mode survives, normalised');
});

// review-dispatch R1, R2: a task declaring two directions, or one compound
// ID, is not one direction's task; no worker gets its text.
test('a task with two DIRECTION_IDs or a compound one passes none of its text to the worker it runs', async (t) => {
	const plan = { investigation_plan: [
		{ direction: 'Owners', direction_id: 'owners', questions: [], steps: [] },
		{ direction: 'Board', direction_id: 'board', questions: [], steps: [] },
		{ direction: 'Money', direction_id: 'money', questions: [], steps: [] },
	] };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Done.' };
		return toolResults(request).length === 0 ? { toolCalls: [
			'MODE: EXECUTION\nDIRECTION_ID: owners\nDIRECTION_ID: mone\nOwners instructions OWN-SENT',
			'MODE: EXECUTION\nDIRECTION_ID: board-and-money\nBoard and money instructions COMPOUND-SENT',
		].map((task) => ({ name: 'spawn_agent', arguments: { agent: 'investigator', task } })) } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await caseAtInvestigation(space, undefined, plan);
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ project: 'case-a', findings: [{ id: 'D1-F1', claim: 'owners' }] }));
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	const workers = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	assert.equal(workers.filter((request) => toolResults(request).length === 0).length, 2);
	for (const request of workers) assert.doesNotMatch(JSON.stringify(request), /OWN-SENT|COMPOUND-SENT/);
});

// e2e r1 (31B): the Methodology gate passed navigator.status values the
// methodology schema refused; research-triage.py then stopped after approval.
// The gate's validator applies the schema, so open_review refuses first.
test('open_review at the Methodology gate refuses a navigator status outside the methodology schema, and opens for an on-demand one', async (t) => {
	for (const [status, opens] of [['bogus', false], ['not-needed', true]] as const) await t.test(status, async (t) => {
		const fake = await startFakeOpenAI(orchestratorScript([{ name: 'open_review', arguments: {} }]));
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = join(space.casesRoot, 'case-a');
		execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology']);
		const path = join(caseDir, 'data', 'methodology.json');
		const plan = JSON.parse(await readFile(path, 'utf8'));
		plan.navigator = { required: false, used: false, status, queries: [], fallback_used: false, fallback_reason: 'Public records are enough.' };
		await writeFile(path, JSON.stringify(plan));
		const config = join(space.root, 'spotlight-config.json');
		await writeFile(config, JSON.stringify({ model_tier: 'frontier', integrations: { osint_navigator: { status: 'red', required_in_phase_2: false } } }));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: config });
		await settledPrompt(chat, 'Continue.');
		const opened = chat.lines.map((line) => JSON.parse(line) as { type?: string; toolName?: string; result?: { content: Array<{ text: string }> } })
			.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'open_review').map((record) => record.result!.content[0].text);
		assert.ok(opened.length > 0, 'open_review ran');
		if (opens) assert.match(opened[0], /review page is open/);
		else assert.match(opened[0], /does not pass validate-methodology-navigator\.py[\s\S]*navigator\.status/);
	});
});

test('at a stalled investigation the resolver names the round, code launches the investigator with TASK: ROUND, and starts the round it writes', async (t) => {
 for (const outcome of ['accepted', 'refused'] as const) await t.test(outcome, async (t) => {
	const space = await workspace(t);
	const { caseDir, round: accepted } = caseAtRoundTopics(space);
	// refused: the round reuses a topic the plan has; the next worker writes nothing (Codex review of simp-round).
	const round = outcome === 'accepted' ? accepted : JSON.stringify({ ...JSON.parse(accepted), topics: [{ ...JSON.parse(accepted).topics[0], topic_id: 'ownership-registries' }] });
	let workers = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			if (toolResults(request).length === 0) workers += 1;
			return toolResults(request).length === 0 && workers === 1
				? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/research-round.json', content: round } }] }
				: { text: 'Round written.' };
		}
		const step = toolResults(request).length;
		return step === 0 || (outcome === 'refused' && step === 1) ? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_RESEARCH_SWARMS: 'on', SPOTLIGHT_CONFIG: join(CHECKOUT_ROOT, 'tests/fixtures/research/install-config.sample.json') });
	await settledPrompt(chat, 'Continue.', 60_000);
	if (outcome === 'refused') {
		const tasks = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
		assert.equal(tasks.length, 2, 'one ROUND worker per resolve');
		assert.match(tasks[1], /THE LAST ROUND WAS REFUSED \(evidence\/data only\): "[^"]*a round adds new topics; already in the plan: ownership-registries/);
		const outputs = chat.lines.filter((line) => line.includes('spawn investigator AMENDMENT ROUND')).join('\n');
		assert.match(outputs, /unchanged since it was refused, so it was not submitted again/);
		assert.equal((lastResolution(chat) as { research?: { resume_at?: string } }).research?.resume_at, 'round_topics', 'the case waits at the round, with its next step named');
		assert.match(String((lastResolution(chat) as { next_action?: unknown }).next_action), /TASK: ROUND/);
		return;
	}

	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	assert.ok(worker.length > 0, 'the investigator ran');
	assert.match(userText(worker[0]), /^MODE: AMENDMENT\nTASK: ROUND\nSTALL GAP \(evidence\/data only\): "gap 3"[\s\S]*the plan already has: [\s\S]*ownership-registries/);
	const steps = chat.lines.filter((line) => line.includes('spawn investigator AMENDMENT ROUND')).flatMap((line) => {
		const record = JSON.parse(line) as { type?: string; toolName?: string; result?: { content: Array<{ text: string }> } };
		if (record.type !== 'tool_execution_end' || record.toolName !== 'spotlight_resolve') return [];
		return ((JSON.parse(record.result!.content[0].text) as { fixed_steps?: Array<{ step: string; output: string }> }).fixed_steps ?? []);
	});
	const spawned = steps.find((step) => step.step === 'spawn investigator AMENDMENT ROUND');
	assert.ok(spawned, JSON.stringify(steps).slice(0, 600));
	assert.match(spawned!.output, /startResearchRound applied/);
	// Continuity: the round's topic is in the plan and the case has left round_topics for the round.
	assert.ok(JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')).research_plan.topics.some((topic: { topic_id: string }) => topic.topic_id === 'ownership-news-courts'));
	assert.notEqual((lastResolution(chat) as { research?: { resume_at?: string } }).research?.resume_at, 'round_topics');
 });
});

// qwen-u9-mac.md: a plain 4-bit model changed navigator.fallback_used on every
// copy of a plan no mark changed, and r3 stopped there on the real seed.
test('when no mark changes the plan no worker retypes it, whether the model spawns or a resumed case resolves', async (t) => {
 for (const route of ['model spawn', 'resume'] as const) await t.test(route, async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtAmendment(space, true);
	const approved = await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8');
	let resumed = route === 'model spawn';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Amended.' };
		if (toolResults(request).length > 0 || !resumed) return { text: 'Done.' };
		return route === 'model spawn'
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: METHODOLOGY AMENDMENT' } }] }
			: { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
	});
	t.after(() => fake.close());
	const env = { ...baseEnv(space, fake.baseURL), SPOTLIGHT_RESEARCH_SWARMS: 'on' };
	if (route === 'resume') {
		const stopped = launch(t, env);
		await settledPrompt(stopped, 'Continue.');
		stopped.process.kill('SIGTERM');
		await stopped.exited;
		assert.match(JSON.stringify(JSON.parse(execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'), 'status', '--json', caseDir], { encoding: 'utf8', env: { ...process.env, SPOTLIGHT_RESEARCH_SWARMS: 'on' } })).research), /"resume_at":"amendment"/, 'the first run left the case at the step');
		resumed = true;
	}
	const chat = launch(t, env);
	await settledPrompt(chat, 'Continue.');
	assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && userText(request).startsWith('MODE: AMENDMENT')).length, 0, 'no worker ran the amendment');
	assert.equal(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'), approved);
	const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.ok(state.approvals.research.amended_methodology_sha256, 'the amendment was recorded');
	if (route === 'model spawn') assert.ok(fake.requests.some((request) => toolResults(request).some((text) => text.includes('code built the amendment'))), 'the orchestrator is told');
 });
});

// Marked amendments on the local tiers are built in code (research.py build_marked_amendment).
test('on a local tier a pursued clue is added to the plan by code and no worker runs the amendment', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtAmendment(space, true, 'pursue');
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Amended.' };
		return toolResults(request).length > 0 ? { text: 'Done.' } : { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_RESEARCH_SWARMS: 'on' }), 'Continue.');
	assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && userText(request).startsWith('MODE: AMENDMENT')).length, 0, 'no worker ran the amendment');
	const steps = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')).investigation_plan[0].steps;
	assert.deepEqual(steps.at(-1).cites, ['clue:clue-1']);
	const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.ok(state.approvals.research.amended_methodology_sha256, 'the amendment was recorded');
});

test('at the amendment step the investigator gets the code-built TASK: AMEND task and code applies recordAmendment, for a whole methodology or only the changed direction', async (t) => {
 for (const shape of ['whole', 'fragment', 'fragment-changed'] as const) await t.test(shape, async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtAmendment(space);
	const methodology = await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8');
	// fragment: the first direction as approved. fragment-changed: that direction
	// with one new step; this fixture's Research approval carries no marks, so
	// D8 refuses the step itself, and only it (Codex review of simp-4, P3).
	const first = JSON.parse(methodology).investigation_plan[0];
	const added = { ...first, steps: [...first.steps, { order: first.steps.length + 1, action: 'Check the filing the research found', tool: 'fetch' }] };
	const content = shape === 'whole' ? methodology : JSON.stringify({ investigation_plan: [shape === 'fragment' ? first : added] });
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0
			? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology-amendment.json', content } }] }
			: { text: 'Amended.' };
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: METHODOLOGY AMENDMENT\nORCH-SENTINEL rewrite the whole plan.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier', SPOTLIGHT_RESEARCH_SWARMS: 'on' }), 'Continue.');

	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	assert.ok(worker.length > 0, 'the investigator ran');
	assert.match(userText(worker[0]), /^MODE: AMENDMENT\nTASK: AMEND\n/);
	for (const request of worker) assert.doesNotMatch(JSON.stringify(request), /ORCH-SENTINEL/);
	if (shape === 'fragment-changed') {
		const completed = JSON.parse(methodology);
		completed.investigation_plan[0] = added;
		assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'methodology-amendment.json'), 'utf8')), completed, 'code completed the fragment from the approved methodology');
		const refused = toolResults(fake.requests.at(-1)!).join('\n');
		assert.match(refused, /recordAmendment refused[\s\S]*new step \d+ cites no pursued clue or direction/);
		assert.doesNotMatch(refused, /an amendment changes only investigation_plan/);
		return;
	}
	assert.match(toolResults(fake.requests.at(-1)!).join('\n'), /recordAmendment applied/);
	const status = JSON.parse(execFileSync(PYTHON, [SEAM, 'status', '--json', caseDir], { env: { PATH: process.env.PATH ?? '', PYTHONNOUSERSITE: '1', SPOTLIGHT_RESEARCH_SWARMS: 'on' } }).toString());
	assert.equal(status.phase, 'investigation');
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')), JSON.parse(methodology), 'the completed amendment is the approved methodology');
 });
});

// review-integration I1: a correction task had no deliverable, so the progress
// watchdog credited only findings and bundle writes; a METHODOLOGY CORRECTION
// that rewrote data/methodology.json at turn 10 was stopped at 16 as making
// no first record.
test('a METHODOLOGY CORRECTION investigator that writes data/methodology.json is credited and not stopped at 16 turns', async (t) => {
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology']);
	const plan = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'));
	const corrected = JSON.stringify({ ...plan, tools_required: [...(plan.tools_required ?? []), 'Corrected registry search'] });
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			const turn = toolResults(request).length;
			if (turn === 16) return { text: 'Corrected.' };
			return turn === 10 ? { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: corrected } }] } : search(turn);
		}
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: METHODOLOGY CORRECTION\nAdd the registry search the validator asked for.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator'));
	assert.ok(toolResults(worker.at(-1)!)[10].startsWith('wrote '), `the methodology write was accepted: ${toolResults(worker.at(-1)!)[10]}`);
	// Credited at turn 11, the worker answers at turn 17, past the 16-turn first-record limit.
	assert.equal(worker.length, 17, 'the worker ran to its own answer');
	const answer = toolResults(fake.requests.filter((request) => !isWorkerRequest(request, 'investigator')).at(-1)!).join('\n');
	assert.doesNotMatch(answer, /progress-watchdog receipt/);
	assert.match(answer, /Corrected\./);
});

// review-fix1 X1: a retry worker that writes nothing must not re-submit the
// refused amendment, which would be the second refusal and block the case.
test('a second amendment worker that writes nothing does not re-apply the refused amendment, and the case is not blocked', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtAmendment(space);
	const plan = JSON.parse(await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8'));
	// An amendment the resolver refuses (here, outside the methodology schema).
	const bad = JSON.stringify({ ...plan, investigation_plan: 'not a list' });
	let spawns = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			if (toolResults(request).length > 0 || spawns === 2) return { text: 'Done.' };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology-amendment.json', content: bad } }] };
		}
		const done = toolResults(request).length;
		if (done < 2) { spawns = done + 1; return { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Amend.' } }] }; }
		return { text: 'Done.' };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier', SPOTLIGHT_RESEARCH_SWARMS: 'on' }), 'Continue.');
	const status = JSON.parse(execFileSync(PYTHON, [SEAM, 'status', '--json', caseDir], { env: { PATH: process.env.PATH ?? '', PYTHONNOUSERSITE: '1', SPOTLIGHT_RESEARCH_SWARMS: 'on' } }).toString());
	assert.notEqual(status.status, 'blocked', 'the unchanged amendment was not submitted a second time');
	assert.equal(status.research?.resume_at, 'amendment');
	// A refusal is recorded by the resolver, not raised; the result says which it was.
	const results = toolResults(fake.requests.at(-1)!);
	assert.match(results[0], /recordAmendment refused the amendment/);
	assert.match(results[1], /unchanged since it was last applied, so recordAmendment was not applied again/);
});

// review-fix2 Z1: a resolver error is not an outcome. The amendment it never
// checked must stay unapplied, so the next worker's finish applies it.
test('an amendment whose recordAmendment failed with a resolver error is applied when the next worker finishes', async (t) => {
	const space = await workspace(t);
	const caseDir = caseAtAmendment(space);
	const methodology = await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8');
	// The resolver fails on the first record-amendment only.
	const flag = join(space.root, 'record-amendment-failed');
	const python = join(space.root, 'python-flaky-resolver');
	await writeFile(python, `#!${process.execPath}\nconst { execFileSync } = require('node:child_process');\nconst fs = require('node:fs');\nconst args = process.argv.slice(2);\nif (args.includes('record-amendment') && !fs.existsSync(${JSON.stringify(flag)})) { fs.writeFileSync(${JSON.stringify(flag)}, ''); process.stderr.write('resolver unavailable'); process.exit(1); }\ntry { process.stdout.write(execFileSync(${JSON.stringify(PYTHON)}, args, { stdio: ['inherit', 'pipe', 'inherit'] })); } catch (error) { process.stdout.write(error.stdout ?? ''); process.exitCode = error.status ?? 1; }\n`, { mode: 0o755 });
	let spawns = 0;
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) {
			if (toolResults(request).length > 0 || spawns === 2) return { text: 'Done.' };
			return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology-amendment.json', content: methodology } }] };
		}
		const done = toolResults(request).length;
		if (done < 2) { spawns = done + 1; return { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Amend.' } }] }; }
		return { text: 'Done.' };
	});
	t.after(() => fake.close());
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier', SPOTLIGHT_RESEARCH_SWARMS: 'on', SPOTLIGHT_PYTHON: python }), 'Continue.');
	const status = JSON.parse(execFileSync(PYTHON, [SEAM, 'status', '--json', caseDir], { env: { PATH: process.env.PATH ?? '', PYTHONNOUSERSITE: '1', SPOTLIGHT_RESEARCH_SWARMS: 'on' } }).toString());
	assert.equal(status.phase, 'investigation', 'the unchecked amendment was applied on the retry');
	const results = toolResults(fake.requests.at(-1)!);
	assert.match(results[0], /did not reach an outcome \(the resolver failed\)/);
	assert.match(results[1], /recordAmendment applied/);
});

// ── Auto-dispatch at resolve (reports/autodispatch-scope.md, approved 2026-11-02) ──

// Plan step 4 (reports/step4-routing-design.md): with the code router on, an
// orchestrator model that only ever writes text reaches the same places; the
// router's calls are marked code-made in the session record and logged with
// their reason.
test('a scripted orchestrator that only resolves, answers the member and opens the review reaches Investigation and the fact-checker; code spawns every worker; with the code router, a model that only writes text does too', async (t) => {
 for (const route of ['scripted', 'router'] as const) await t.test(route, async (t) => {
	const space = await workspace(t);
	// K5 Tor P1: the member's change at the methodology review reaches the plan.
	const asked = 'Before I approve, add a research topic on the registry\'s board today: which sources confirm the current members?';
	// After the page approval the last resolve the router saw can still be the review's (Codex review of simp-relay, P2).
	const late = 'Also add a research topic on the registry\'s auditors.';
	const decisions = route === 'router' ? await fakeDecisions(0.9, (message) => (message === asked || message === late ? 'change_request' : 'approval_talk')) : undefined;
	if (decisions) t.after(() => decisions.close());
	const source = join(space.root, 'outputs');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), source, 'investigation_gate', '--activated', '--clean']);
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await copyFile(join(source, 'brief-directions.txt'), join(caseDir, 'brief-directions.txt'));
	const methodology = await readFile(join(source, 'data', 'methodology.json'), 'utf8');
	let investigated = false;
	const fake = await startFakeOpenAI((request) => {
		const done = toolResults(request).length;
		if (isWorkerRequest(request, 'investigator')) {
			const text = userText(request);
			if (/MODE: PLANNING/.test(text) && done === 0) return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: methodology } }] };
			if (/MODE: METHODOLOGY CORRECTION/.test(text) && text.includes(asked) && done === 0) {
				const plan = JSON.parse(methodology);
				plan.investigation_plan[0].questions.push(asked);
				return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: JSON.stringify(plan) } }] };
			}
			if (/MODE: EXECUTION/.test(text) && !investigated) {
				// The worker's investigation, as the fixture's outputs.
				investigated = true;
				for (const name of ['findings.json', 'evidence-bundle.json', 'investigation-log.json', 'source-expressions.json']) if (existsSync(join(source, 'data', name))) cpSync(join(source, 'data', name), join(caseDir, 'data', name));
				for (const folder of ['research', 'evidence']) if (existsSync(join(source, folder))) cpSync(join(source, folder), join(caseDir, folder), { recursive: true });
			}
			// A long answer, as workers often give: the receipt must still carry
			// the harness's verdict (audit G3, codex-review-comb 4).
			return { text: /MODE: EXECUTION/.test(text) ? `Investigated. ${'Each registry page was read in full. '.repeat(50)}` : 'Done.' };
		}
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
		// The router route's model never calls a tool: it only writes words.
		if (route === 'router') return { text: 'Here is where the case stands.' };
		const last = request.messages.at(-1)!;
		if (last.role !== 'tool') return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
		const called = (request.messages.filter((message) => message.role === 'assistant').at(-1)!.tool_calls as Array<{ function: { name: string } }>)[0].function.name;
		if (called === 'spotlight_resolve' && /"phase":"methodology_approval"/.test(String(last.content))) return { toolCalls: [{ name: 'open_review', arguments: {} }] };
		return { text: 'Here is where the case stands.' };
	});
	t.after(() => fake.close());
	const config = await gateConfig(space);
	if (decisions) await writeFile(config, JSON.stringify({ ...JSON.parse(await readFile(config, 'utf8')), integrations: { ...JSON.parse(await readFile(config, 'utf8')).integrations, decisions: { endpoint: { url: decisions.url, api: 'openrouter-decisions' } } } }));
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: config, ...(route === 'router' ? { SPOTLIGHT_CODE_ROUTER: 'on' } : {}) });
	await settledPrompt(chat, 'Go ahead with that direction.');
	if (route === 'router') {
		await settledPrompt(chat, asked);
		assert.ok(fake.requests.some((request) => isWorkerRequest(request, 'investigator') && /^MODE: METHODOLOGY CORRECTION\n/.test(userText(request)) && userText(request).includes(`\n${asked}\n`)), 'the investigator got the member\'s words verbatim');
		assert.ok((await readFile(join(caseDir, 'data', 'methodology.json'), 'utf8')).includes('registry\'s board today'), 'the plan carries the change');
		assert.match(chat.stderr(), /\[router\] spawn_agent \(member change_request at the methodology review\) \[D1 change_request[^\n]*\n(?:[^\n]*\n)*?[^\n]*\[router\] spotlight_resolve[^\n]*\n(?:[^\n]*\n)*?[^\n]*\[router\] open_review \(the resolver names open_review\)/);
	}
	execFileSync(PYTHON, [SEAM, 'approve', 'methodology', '--approved-by', 'journalist:fixture', '--approved-at', '2026-08-23T12:00:00Z', caseDir]);
	if (route === 'router') {
		await settledPrompt(chat, late);
		assert.match(chat.stderr(), /\[router\] model \(member change_request, but the case is no longer at the methodology review\)/);
		assert.ok(!fake.requests.some((request) => isWorkerRequest(request, 'investigator') && userText(request).includes(late)), 'no correction after the approval');
	}
	await settledPrompt(chat, 'I approved the plan.');

	const orchestrator = fake.requests.filter((request) => !systemText(request).includes('You were delegated a task'));
	const called = new Set(orchestrator.flatMap((request) => request.messages.filter((message) => message.role === 'assistant').flatMap((message) => ((message.tool_calls ?? []) as Array<{ function: { name: string } }>).map((call) => call.function.name))));
	assert.deepEqual([...called].sort(), route === 'router' ? ['open_review', 'spawn_agent', 'spotlight_resolve'] : ['open_review', 'spotlight_resolve']);
	if (route === 'router') {
		// Every routing call in the history the model is sent is code-made: its id carries the router marker on the wire.
		const ids = orchestrator.flatMap((request) => request.messages.filter((message) => message.role === 'assistant').flatMap((message) => ((message.tool_calls ?? []) as Array<{ id: string }>).map((call) => call.id)));
		assert.ok(ids.length > 0 && ids.every((id) => id.startsWith('router-')), ids.join(', '));
		// Each router call is marked code-made in the run's records (the stdout
		// records e2e calls.jsonl is built from), and logged with its reason.
		const routed = chat.lines.filter((line) => line.includes('"responseModel":"spotlight-code-router"'));
		assert.ok(routed.length > 0, 'router calls are marked in the records');
		assert.ok(routed.some((line) => line.includes('"responseId":"router: member approval_talk"')));
		assert.match(chat.stderr(), /\[router\] spotlight_resolve \(member approval_talk\) \[D1 approval_talk p=0\.90 \d+ ms\]/);
		assert.match(chat.stderr(), /\[router\] open_review \(the resolver names open_review\)/);
		assert.ok(decisions!.states.length >= 2, 'the decision model read each member message');
	}
	const steps = resolveResults(chat).flatMap((state) => ((state.fixed_steps ?? []) as Array<{ step: string }>).map((receipt) => receipt.step));
	assert.ok(steps.includes('spawn investigator PLANNING'), steps.join(', '));
	assert.ok(steps.includes('spawn investigator EXECUTION'), steps.join(', '));
	assert.ok(steps.includes('spawn fact-checker'), steps.join(', '));
	const execution = resolveResults(chat).flatMap((state) => (state.fixed_steps ?? []) as Array<{ step: string; output: string }>).find((receipt) => receipt.step === 'spawn investigator EXECUTION')!;
	assert.match(execution.output, /^Investigated\. [\s\S]*\n\n\[Harness checks after the investigator finished: all passed\]/);
	assert.ok(fake.requests.some((request) => isWorkerRequest(request, 'fact-checker')), 'the fact-checker was handed the case');
	assert.ok(resolveResults(chat).some((state) => (state.phase_skill as { name?: string } | undefined)?.name === 'phase-methodology'), 'the phase skill came with the resolve');
 });
});

// B11 (qwen-u9-pod.md): held-sfc sent one refused analyst spawn 416 times, and
// the router's resolve between two spawns kept the loop guard from ever firing.
test('with the code router a model that repeats a refused spawn is stopped, the member is told why, and their next turn starts afresh', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return { text: 'Done.' };
		return fake.requests.length > 40 ? { text: 'The test ended this.' } : { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task: 'Write the summary.' } }] };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CODE_ROUTER: 'on' });
	await settledPrompt(chat, 'Continue.');
	assert.ok(chat.lines.some((line) => line.includes('Spotlight stopped this turn because the analyst was refused 2 times')), 'the member reads the stop');
	assert.match(chat.stderr(), /\[router\] stop \(the analyst spawn was refused 2 times and code can prove no move\)/);
	const asked = fake.requests.filter((request) => !systemText(request).includes('You were delegated a task')).length;
	assert.ok(asked <= 4, `${asked} model requests`);
	await settledPrompt(chat, 'Try again.');
	const after = fake.requests.filter((request) => !systemText(request).includes('You were delegated a task')).length;
	assert.ok(after > asked, 'the member\'s turn reaches the model again');
});

// r3 held-sfc: 373 spawns threw while the worker's launch was built, the text
// named no refusal, and only the 500-call cap ended the run.
test('with the code router a spawn that throws before a worker starts ends in a member stop', async (t) => {
	const fake = await startFakeOpenAI((request) => (fake.requests.length > 40 ? { text: 'The test ended this.' } : { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Keep investigating.' } }] }));
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	await mkdir(join(space.dataDir, 'work'), { recursive: true });
	await writeFile(join(space.dataDir, 'work', 'investigator'), 'a file where the worker\'s folder goes');
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CODE_ROUTER: 'on' });
	await settledPrompt(chat, 'Continue.');
	assert.ok(chat.lines.some((line) => line.includes('Spotlight stopped this turn because the investigator was refused 2 times')), 'the member reads the stop');
	assert.ok(fake.requests.length <= 6, `${fake.requests.length} model requests`);
	assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).length, 0, 'no worker ran');
});

// Member consent: code never crosses a gate. At a member gate it acts only when
// the gate's own check fails (a correction); nothing runs after the gate is
// ready for the member, and nothing starts Investigation before the approval.
test('at a member gate resolve starts no worker unless the gate check fails, and then only the correction', async (t) => {
	for (const [label, status, workers] of [['valid plan awaiting approval', 'red', 0]] as const) await t.test(label, async (t) => {
		const fake = await startFakeOpenAI((request) => (isWorkerRequest(request, 'investigator') ? { text: 'Done.' } : resolveThenStop(request)));
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = join(space.casesRoot, 'case-a');
		execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology']);
		const path = join(caseDir, 'data', 'methodology.json');
		const plan = JSON.parse(await readFile(path, 'utf8'));
		plan.navigator = { required: false, used: false, status, queries: [], fallback_used: false, fallback_reason: 'Public records are enough.' };
		await writeFile(path, JSON.stringify(plan));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: await gateConfig(space) });
		await settledPrompt(chat, 'Continue.');
		const started = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
		assert.equal(started.length, workers);
		for (const text of started) assert.match(text, /^MODE: METHODOLOGY CORRECTION\n[\s\S]*navigator\.status/);
		assert.ok(resolveResults(chat).every((state) => state.phase === 'methodology_approval'), 'the case stays at the gate for the member');
	});
	// e2e r8 on comb: after the member's research request the plan failed its
	// check, code's corrections stopped at a per-turn cap of three, and resolve
	// still said "call open_review", so the run waited on the member. Now the
	// corrections go on until the plan passes, resolve reports the failing check
	// meanwhile, and the review opens once the plan is valid (fix6 a, b).
	// review-fix6 F1: a 12b orchestrator that narrates after a failing-plan
	// resolve is nudged (the nudge reads the case as resolve reports it) and
	// still reaches the page.
	for (const narrate of [false, true]) await t.test(`a research request whose plan fails the gate check is corrected by code until it passes, then the review opens${narrate ? ', after the 12b orchestrator narrates once' : ''}`, async (t) => {
		let corrections = 0;
		let narrated = false;
		let tried = false;
		const detour: Array<{ name: string; arguments: Record<string, unknown> }> = [];
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, 'investigator')) {
				if (toolResults(request).length > 0) return { text: `Corrected the plan (${corrections}).` };
				corrections += 1;
				const plan = { ...valid, navigator: { ...valid.navigator, status: corrections < 5 ? `bogus-${corrections}` : 'red' } };
				return { toolCalls: [{ name: 'write_file', arguments: { path: 'data/methodology.json', content: JSON.stringify(plan) } }] };
			}
			const last = request.messages.at(-1)!;
			if (last.role !== 'tool') return { toolCalls: [{ name: narrated ? 'spotlight_resolve' : 'spawn_agent', arguments: narrated ? {} : { agent: 'investigator', task: 'MODE: METHODOLOGY CORRECTION\nAdd the research topic the member asked for.' } }] };
			const called = (request.messages.filter((message) => message.role === 'assistant').at(-1)!.tool_calls as Array<{ function: { name: string } }>)[0].function.name;
			if (called === 'open_review' && !String(last.content).includes(NEXT_METHODOLOGY_FAILING)) return { text: 'The plan is on the review page.' };
			// review-fix7: after the refused open_review, five unproductive reads and
			// a refused spawn; their next steps must say what resolve says.
			if (called === 'open_review' && !narrate && detour.length === 0 && !fake.requests.some((r) => JSON.stringify(r.messages).includes('missing-1.json'))) detour.push(...[1, 2, 3, 4, 5].map((n) => ({ name: 'read_file', arguments: { path: `data/missing-${n}.json` } })), { name: 'spawn_agent', arguments: { agent: 'analyst', task: 'Summarise the plan.' } });
			if (detour.length) return { toolCalls: [detour.shift()!] };
			if (called === 'spotlight_resolve' && String(last.content).includes(NEXT_METHODOLOGY_APPROVAL)) return { toolCalls: [{ name: 'open_review', arguments: {} }] };
			// An early open_review on the failing plan is refused with what resolve says.
			if (!narrate && !tried && called === 'spotlight_resolve' && String(last.content).includes(NEXT_METHODOLOGY_FAILING)) {
				tried = true;
				return { toolCalls: [{ name: 'open_review', arguments: {} }] };
			}
			if (narrate && !narrated && called === 'spotlight_resolve' && String(last.content).includes(NEXT_METHODOLOGY_FAILING)) {
				narrated = true;
				return { text: 'I am fixing the schema and will let you know.' };
			}
			return { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = join(space.casesRoot, 'case-a');
		execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology']);
		const path = join(caseDir, 'data', 'methodology.json');
		const valid = { ...JSON.parse(await readFile(path, 'utf8')), navigator: { required: false, used: false, status: 'red', queries: [], fallback_used: false, fallback_reason: 'Public records are enough.' } };
		await writeFile(path, JSON.stringify(valid));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: await gateConfig(space), SPOTLIGHT_MODEL_TIER: narrate ? '12b' : 'frontier' });
		await settledPrompt(chat, 'Please add a research topic on the 2019 filings.');
		if (narrate) assert.ok(narrated && fake.requests.some((request) => JSON.stringify(request.messages).includes('Your turn ended with text, but the case is at methodology')), 'the narration was nudged');
		if (!narrate) {
			const results = toolResults(fake.requests.filter((request) => !systemText(request).includes('You were delegated a task')).at(-1)!);
			const refusal = results.find((result) => /does not pass validate-methodology-navigator/.test(result));
			assert.ok(refusal?.includes(NEXT_METHODOLOGY_FAILING), `the early open_review refusal says what resolve says: ${refusal?.slice(0, 300)}`);
			const fifth = results.filter((result) => result.startsWith('not found: '))[4];
			assert.ok(fifth?.includes(NEXT_METHODOLOGY_FAILING), `the unproductive notice says what resolve says: ${fifth?.slice(0, 300)}`);
			const spawn = results.find((result) => /the analyst runs only at/.test(result));
			assert.ok(spawn?.includes(NEXT_METHODOLOGY_FAILING), `the spawn refusal's next step says what resolve says: ${spawn?.slice(0, 300)}`);
		}
		const started = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
		assert.equal(started.length, 5, 'the member relay, then code corrections until the plan passes');
		for (const text of started.slice(1)) assert.match(text, /^MODE: METHODOLOGY CORRECTION\n[\s\S]*navigator\.status/);
		const states = resolveResults(chat);
		for (const state of states) {
			if (state.methodology_check) assert.deepEqual([state.phase, state.next_action], ['methodology', NEXT_METHODOLOGY_FAILING], 'a failing plan is never offered for approval');
			else assert.deepEqual([state.phase, state.next_action], ['methodology_approval', NEXT_METHODOLOGY_APPROVAL]);
		}
		// Continuity: the case reaches its gate and the member's page opens.
		assert.equal(states.at(-1)!.phase, 'methodology_approval');
		assert.equal(chat.opened().length, 1, 'the review page opened once the plan passed');
	});
	await t.test('Investigation gate ready for the member', async (t) => {
		const fake = await startFakeOpenAI((request) => (systemText(request).includes('You were delegated a task') ? { text: 'Unexpected.' } : resolveThenStop(request)));
		t.after(() => fake.close());
		const space = await workspace(t);
		caseAtGate(space);
		const chat = launch(t, baseEnv(space, fake.baseURL));
		await settledPrompt(chat, 'Continue.');
		assert.equal(fake.requests.filter((request) => systemText(request).includes('You were delegated a task')).length, 0);
		assert.equal(lastResolution(chat).phase, 'investigation_gate_approval');
	});
});

// A resolve that spawns a worker runs as long as the worker: the worker keeps
// its own progress watchdog, its stop comes back as the receipt, and an abort
// of the orchestrator's turn reaches the worker.
test('an auto-spawned worker keeps its own progress watchdog, and aborting the resolve stops the worker', async (t) => {
	await t.test('watchdog', async (t) => {
		const fake = await startFakeOpenAI((request) => (isWorkerRequest(request, 'investigator') ? search(toolResults(request).length) : resolveThenStop(request)));
		t.after(() => fake.close());
		const space = await workspace(t);
		await briefApproved(space);
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' });
		await settledPrompt(chat, 'Continue.');
		assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).length, 16, 'stopped at its own 16-turn limit, once');
		const [receipt] = (lastResolution(chat).fixed_steps as Array<{ step: string; output: string }>);
		assert.equal(receipt.step, 'spawn investigator PLANNING');
		assert.match(receipt.output, /progress-watchdog receipt/);
	});
	await t.test('abort', async (t) => {
		const fake = await startFakeOpenAI((request) => (isWorkerRequest(request, 'investigator') ? 'hang' : resolveThenStop(request)));
		t.after(() => fake.close());
		const space = await workspace(t);
		await briefApproved(space);
		const chat = launch(t, baseEnv(space, fake.baseURL));
		await waitUntil(() => existsSync(join(space.dataDir, 'locks', 'case-a.lock')), 30_000);
		// The lock is written with no children before the orchestrator is tracked: `before` must hold it, or the test waits for it to die.
		await waitUntil(() => { try { return JSON.parse(readFileSync(join(space.dataDir, 'locks', 'case-a.lock'), 'utf8')).children?.length > 0; } catch { return false; } }, 30_000);
		const before = new Set((await lockRecord(space)).children);
		chat.send({ id: 'p-1', type: 'prompt', message: 'Continue.' });
		await waitUntil(() => fake.requests.some((request) => isWorkerRequest(request, 'investigator')), 30_000);
		const workers = (await lockRecord(space)).children.filter((pid) => !before.has(pid));
		assert.ok(workers.length > 0 && workers.every(isAlive), 'the auto-spawned worker is running');
		chat.send({ id: 'abort-1', type: 'abort' });
		await waitUntil(() => workers.every((pid) => !isAlive(pid)), 20_000);
		assert.ok((await lockRecord(space)).children.some((pid) => before.has(pid) && isAlive(pid)), 'the orchestrator keeps running');
	});
});

// review-fix4 A1, A2: a member's follow-up and a recorded cycle gap carry
// instructions code does not route; that cycle stays the model's.
test('with a member follow-up or a recorded cycle gap open, resolve starts no worker', async (t) => {
	for (const [label, args] of [
		['member follow-up', ['request-follow-up', '--instructions', 'Re-check F1 against the 2024 filing. FOLLOWUP-SENTINEL']],
		['recorded cycle gap', ['record-attempt', 'investigation-cycle', '--gap', 'F1 needs the 2024 filing.']],
	] as const) await t.test(label, async (t) => {
		const fake = await startFakeOpenAI((request) => (systemText(request).includes('You were delegated a task') ? { text: 'Unexpected.' } : resolveThenStop(request)));
		t.after(() => fake.close());
		const space = await workspace(t);
		const caseDir = caseAtGate(space);
		execFileSync(PYTHON, [SEAM, ...args, caseDir]);
		const chat = launch(t, baseEnv(space, fake.baseURL));
		await settledPrompt(chat, 'Continue.');
		assert.equal(lastResolution(chat).phase, 'investigation');
		assert.equal(fake.requests.filter((request) => systemText(request).includes('You were delegated a task')).length, 0, 'the model routes this cycle');
		assert.equal(lastResolution(chat).fixed_steps, undefined);
	});
});

// Phase B (Gemma12): an enum refusal names the field and its allowed values,
// so the worker need not retry blindly.
test('a schema refusal on an enum field names the allowed values from the tool schema', async (t) => {
	const finding = { claim: 'The filing names the payer.', evidence: 'The filing.', sources: [], confidence: 'medium', grounding: { support_type: 'direct_support', source_role: 'primary_source', claim_elements_supported: ['payer'], missing_assumptions: [], confidence_cap: 'medium', misgrounding_risk: 'low', grounding_rationale: 'The filing says so.' } };
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'record_finding', arguments: { finding } }] } : { text: 'Done.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: EXECUTION\nRecord the finding.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	await caseAtInvestigation(space);
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: 'frontier' }), 'Continue.');
	const worker = fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).at(-1)!;
	const [refusal] = toolResults(worker);
	assert.match(refusal, /finding\.grounding\.support_type: must be equal to one of the allowed values \(allowed: "direct", "indirect"/);
	assert.match(refusal, /finding\.grounding\.source_role: must be equal to one of the allowed values \(allowed: "primary", "secondary", "contextual"\)/);
});

// LFM3 Part A on fix5: after a failed check the result said "send the worker
// the correction the phase skill describes", and the model read
// methodology.json or spawned by hand where the next spotlight_resolve sends
// the correction in code. The result now says to resolve.
test('a failed worker check says call spotlight_resolve only when resolve will send the correction', async (t) => {
	const checkResult = async (t: TestContext, agent: string, task: string, setUp: (space: Workspace, caseDir: string) => Promise<string | undefined>) => {
		const fake = await startFakeOpenAI((request) => {
			if (isWorkerRequest(request, agent)) return { text: 'Done.' };
			return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent, task } }] } : { text: 'Stopping here.' };
		});
		t.after(() => fake.close());
		const space = await workspace(t);
		const config = await setUp(space, join(space.casesRoot, 'case-a'));
		const chat = launch(t, { ...baseEnv(space, fake.baseURL), ...(config ? { SPOTLIGHT_CONFIG: config } : {}) });
		await settledPrompt(chat, 'Continue.');
		const answered = fake.requests.find((request) => !systemText(request).includes('You were delegated a task') && toolResults(request).length > 0)!;
		return toolResults(answered).join('\n');
	};
	await t.test('a plan the methodology check fails', async (t) => {
		const result = await checkResult(t, 'investigator', 'MODE: METHODOLOGY CORRECTION\nFix the plan.', async (space, caseDir) => {
			execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'methodology']);
			const path = join(caseDir, 'data', 'methodology.json');
			const plan = JSON.parse(await readFile(path, 'utf8'));
			plan.navigator = { required: false, used: false, status: 'bogus', queries: [], fallback_used: false, fallback_reason: 'Public records are enough.' };
			await writeFile(path, JSON.stringify(plan));
			return gateConfig(space);
		});
		assert.match(result, /\[Harness checks after the investigator finished: FAILED: call spotlight_resolve next; it sends the worker this correction itself\./);
		assert.doesNotMatch(result, /the phase skill describes/);
	});
	// With a recorded cycle open, resolve leaves the next cycle to the model
	// (dispatchAt, review-fix4 A2), so the result keeps the orchestrator's move.
	for (const open of [false, true]) await t.test(`a fact-check the fact-check validator fails${open ? ', with a cycle open' : ''}`, async (t) => {
		const result = await checkResult(t, 'fact-checker', 'Check the findings.', async (_space, caseDir) => {
			execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
			if (open) execFileSync(PYTHON, [SEAM, 'record-attempt', 'investigation-cycle', '--gap', 'F1 has no stored source.', caseDir]);
			const path = join(caseDir, 'data', 'fact-check.json');
			await writeFile(path, JSON.stringify({ ...JSON.parse(await readFile(path, 'utf8')), claims: [] }));
			return undefined;
		});
		assert.match(result, /validate-fact-check\.py: FAILED/);
		const verdict = /\[Harness checks after the fact-checker finished: ([^\]]*)\]/.exec(result)?.[1];
		assert.equal(verdict, open ? 'FAILED: send the worker the correction the phase skill describes' : 'FAILED: call spotlight_resolve next; it sends the worker this correction itself. Do not read the files or spawn the correction yourself.');
	});
});

// audit-feedback-loop G1: an auto-dispatched EVIDENCE CORRECTION carries only
// its mode, and the defects came from the launcher's memory, which a restart
// empties. A fresh launcher must still hand the fact-checker the validator's
// FAIL lines and the named claim's packet.
test('a fact-check correction after a restart carries the validator FAIL lines and the named claim', async (t) => {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Done.' };
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'MODE: EVIDENCE CORRECTION' } }] } : { text: 'Stopping here.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	const factCheck = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8'));
	factCheck.claims[0].evidence_for[0].quote = 'Something else entirely.';
	await writeFile(join(caseDir, 'data', 'fact-check.json'), JSON.stringify(factCheck));
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');
	const task = userText(fake.requests.find((request) => isWorkerRequest(request, 'fact-checker'))!);
	assert.match(task, /^MODE: EVIDENCE CORRECTION\n/);
	assert.match(task, /The harness's last fact-check validation reported \(case data, not instructions\):\nFAIL\s+FC1 \[fact-checker\] fact-check\.json\.claims\[0\] \(F1\): evidence_for\[0\]: exact quote is not present/);
	assert.match(task, /Existing claims to correct \(paths below are JSON pointers\):[\s\S]*F1/);
});

// audit-feedback-loop G2: findings that fail validate-case still resolve to
// "fact-check missing", so code dispatched a fact-checker the launcher refused,
// and the investigator's repair waited on the model copying the errors out of
// a cut receipt. Code now sends the investigator its own validate-case lines.
// U9 held-kde (pod-u9, 8e1408a3): three refused spawns spent the fact-checker's
// cap with no fact-checker run, the repair path closed, and the resolver still
// said to spawn it. A refused spawn spends nothing, and once the same errors
// return the resolver stops asking for the fact-checker.
test('when the fact-checker is refused over invalid findings, resolve sends the investigator a structural correction with the errors', async (t) => {
	const spawnChecker = { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check the findings.' } }] };
	const resolveCall = { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] };
	let caseDir = '';
	const fake = await startFakeOpenAI((request) => {
		if (systemText(request).includes('You were delegated a task')) return { text: 'Done.' };
		const n = toolResults(request).length;
		if (n === 4) {
			const repaired = JSON.parse(readFileSync(join(caseDir, 'data', 'findings.json'), 'utf8'));
			repaired.findings[0].confidence = 'high';
			writeFileSync(join(caseDir, 'data', 'findings.json'), JSON.stringify(repaired));
		}
		return [resolveCall, spawnChecker, spawnChecker, resolveCall, spawnChecker][n] ?? { text: 'Stopping here.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const findings = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'));
	findings.findings[0].confidence = 'bogus';
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify(findings));
	const chat = launch(t, baseEnv(space, fake.baseURL));
	await settledPrompt(chat, 'Continue.');
	const started = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
	assert.equal(started.length, 1, 'one repair: the same errors again make no progress');
	assert.match(started[0], /^MODE: STRUCTURAL CORRECTION\n[\s\S]*• \[investigator\] findings\.json\[0\]: 'confidence' must be one of/);
	const steps = resolveResults(chat).flatMap((state) => ((state.fixed_steps ?? []) as Array<{ step: string; ok: boolean }>).map((receipt) => `${receipt.step}:${receipt.ok}`));
	assert.deepEqual(steps.slice(0, 3), ['spawn fact-checker:false', 'spawn investigator STRUCTURAL CORRECTION (inputs):true', 'spawn fact-checker:false']);
	const results = toolResults(fake.requests.filter((request) => !systemText(request).includes('You were delegated a task')).at(-1)!);
	for (const index of [1, 2]) assert.match(results[index], /no fact-checker was started[\s\S]*\[investigator\] findings\.json\[0\]/, 'a refused spawn keeps naming the investigator\'s errors');
	assert.ok(!results.some((result) => result.includes(FACT_CHECKERS_SPENT)), 'refused spawns spend no attempt');
	assert.doesNotMatch(String(resolveResults(chat).at(-1)!.next_action), /spawn the fact-checker/, 'the resolver does not ask for the worker that was just refused');
	assert.equal(fake.requests.filter((request) => isWorkerRequest(request, 'fact-checker') && toolResults(request).length === 0).length, 1, 'with the findings repaired the fact-checker starts');
});

// qwen-u9-mac.md (aca1e074): the orchestrator retyped the validator's keys into
// its correction task, the investigator never fixed them, nothing counted, and
// the loop ran 43 corrections. On a local tier code builds the task from the
// validator and counts a correction that leaves the errors; two end the case.
test('on a local tier a structural correction that leaves the errors is built from the validator, counted, and ends the case at two', async (t) => {
	const mangled = { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'MODE: STRUCTURAL CORRECTION\nTHE RESOLVER REPORTS:\nmethodology.techniquesÑ is required' } }] };
	const fake = await startFakeOpenAI((request) => {
		if (systemText(request).includes('You were delegated a task')) return { text: 'Done.' };
		return [mangled, mangled, mangled][toolResults(request).length] ?? { text: 'Stopping here.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	const findings = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'));
	findings.findings[0].confidence = 'bogus';
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify(findings));
	await settledPrompt(launch(t, baseEnv(space, fake.baseURL)), 'Continue.');
	const started = fake.requests.filter((request) => isWorkerRequest(request, 'investigator') && toolResults(request).length === 0).map(userText);
	assert.equal(started.length, 2, 'two corrections ran, the third spawn was refused');
	for (const task of started) {
		assert.match(task, /^MODE: STRUCTURAL CORRECTION\n[\s\S]*• \[investigator\] findings\.json\[0\]: 'confidence' must be one of/);
		assert.doesNotMatch(task, /techniquesÑ/);
	}
	const state = JSON.parse(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'));
	assert.equal(state.attempts['structural-correction'], 2);
	assert.equal(state.blocked.exhausted_attempt, 'structural-correction');
});

