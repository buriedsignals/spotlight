import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT } from '../roles.ts';
import { approve } from '../test-support/hosted-skill.ts';
import { caseFileTools } from './case-files.ts';
import type { CaseScope } from './run.ts';

async function fixture(t: TestContext, scope?: CaseScope, role?: string, env: Record<string, string> = {}) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-files-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const outside = join(root, 'outside');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(outside);
	const tools = new Map(caseFileTools({ caseDir, writeRoot: caseDir, scope, env: { ...(role ? { SPOTLIGHT_ROLE: role } : {}), ...env } }).map((tool) => [tool.name, tool]));
	const call = async (name: string, params: Record<string, unknown>) => {
		const tool = tools.get(name) as ToolDefinition;
		const result = await tool.execute('call', params, undefined, undefined, undefined as never);
		return (result.content[0] as { text: string }).text;
	};
	return { caseDir, outside, call };
}

// U3 census on 84674fd9: investigators working every direction in one context
// peaked above 30k prompt tokens. A direction's worker (directions.ts) sees only
// its own entries, and its write must keep every entry it was not shown.
test('a direction\'s worker reads only its own entries, and its write keeps every other direction\'s and the fact-checker\'s', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator', { SPOTLIGHT_DIRECTION: 'D2' });
	const data = (name: string) => join(caseDir, 'data', name);
	await writeFile(data('findings.json'), JSON.stringify({ project: 'p', findings: [{ id: 'D1-F1', claim: 'first' }, { id: 'F3', claim: 'before the split' }], gaps: ['D1 gap'] }));
	await writeFile(data('evidence-bundle.json'), JSON.stringify({ project: 'p', items: [{ id: 'D1-E1' }, { id: 'FC-E1', created_by: 'fact-checker' }] }));
	await writeFile(data('investigation-log.json'), JSON.stringify({ project: 'p', cycles: [{ cycle: 1, focus: 'D1: first direction' }] }));

	assert.deepEqual(JSON.parse(await call('read_file', { path: 'data/findings.json' })).findings, []);
	assert.deepEqual(JSON.parse(await call('read_file', { path: 'data/investigation-log.json' })).cycles, []);
	await call('write_file', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings: [{ id: 'D2-F1', claim: 'second' }], gaps: ['D2 gap'] }) });
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ project: 'p', items: [{ id: 'D2-E1' }] }) });
	await call('write_file', { path: 'data/investigation-log.json', content: JSON.stringify({ project: 'p', cycles: [{ cycle: 1, focus: 'second direction' }] }) });
	await call('edit_file', { path: 'data/findings.json', old_text: '"claim": "second"', new_text: '"claim": "second, corrected"' });

	const findings = JSON.parse(await readFile(data('findings.json'), 'utf8'));
	assert.deepEqual(findings.findings, [{ id: 'D1-F1', claim: 'first' }, { id: 'F3', claim: 'before the split' }, { id: 'D2-F1', claim: 'second, corrected' }]);
	assert.deepEqual(findings.gaps, ['D1 gap', 'D2 gap']);
	assert.deepEqual(JSON.parse(await readFile(data('evidence-bundle.json'), 'utf8')).items.map((item: { id: string }) => item.id), ['D1-E1', 'FC-E1', 'D2-E1']);
	assert.deepEqual(JSON.parse(await readFile(data('investigation-log.json'), 'utf8')).cycles.map((cycle: { focus: string }) => cycle.focus), ['D1: first direction', 'D2: second direction']);
	assert.deepEqual(JSON.parse(await call('read_file', { path: 'data/findings.json' })).findings, [{ id: 'D2-F1', claim: 'second, corrected' }]);
});

// train-tor on 52bf6183: one direction's write renamed the project and left
// out run_id and created_at, and validate-case failed on all three.
test('a direction\'s write keeps the file\'s project, version, run id and creation time', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator', { SPOTLIGHT_DIRECTION: 'D1' });
	const bundle = { schema_version: '1.0', project: 'Tor leadership', run_id: 'run-1', created_at: '2026-10-29T00:00:00Z', items: [{ id: 'D2-E1' }] };
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify(bundle));

	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ project: 'Tor — Executive Director (direction 1)', direction: 'direction:executive-director', items: [{ id: 'D1-E1' }] }) });

	const merged = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'));
	assert.deepEqual([merged.schema_version, merged.project, merged.run_id, merged.created_at], ['1.0', 'Tor leadership', 'run-1', '2026-10-29T00:00:00Z']);
	assert.deepEqual(merged.items.map((item: { id: string }) => item.id), ['D2-E1', 'D1-E1']);
});

test('a direction\'s worker writing an id without its tag is refused and the file is unchanged', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator', { SPOTLIGHT_DIRECTION: 'D2' });
	const before = JSON.stringify({ project: 'p', findings: [{ id: 'D1-F1', claim: 'first' }] });
	await writeFile(join(caseDir, 'data', 'findings.json'), before);

	await assert.rejects(
		call('write_file', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings: [{ id: 'F1', claim: 'untagged' }] }) }),
		/every finding id in data\/findings\.json starts with D2-F \(D2-F1, D2-F2\), and F1 does not; the file is unchanged/,
	);
	assert.equal(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'), before);
});

// U4 prompt shrink: a 12b persona points at agents/references/ for a long
// procedure; the worker reads it there and nothing else of agents/.
test('agents/references/ is readable from a case, and the rest of agents/ is not', async (t) => {
	const { call } = await fixture(t);

	assert.match(await call('read_file', { path: 'agents/references/investigator-source-expressions.md' }), /^### Source Expressions/);
	await assert.rejects(call('read_file', { path: 'agents/investigator.md' }), /not found|outside the case files/);
});

// The one file outside the case a worker may read is its passages file
// (launcher.ts keepPassages); a sibling, a ".." path or a link is refused.
test('read_file reads the worker\'s passages file and nothing else outside the case', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-passages-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const folder = join(root, 'work', 'fact-checker', 'passages');
	await mkdir(folder, { recursive: true });
	const kept = join(folder, 'fact-checker-1.md');
	await writeFile(kept, 'CASE PASSAGES');
	await writeFile(join(folder, 'other.md'), 'another worker');
	await symlink(kept, join(folder, 'link.md'));
	const { call } = await fixture(t, undefined, 'fact-checker', { SPOTLIGHT_TASK_PASSAGES: kept });
	const linked = await fixture(t, undefined, 'fact-checker', { SPOTLIGHT_TASK_PASSAGES: join(folder, 'link.md') });

	assert.equal(await call('read_file', { path: kept }), 'CASE PASSAGES');
	for (const path of [join(folder, 'other.md'), `${folder}/../passages/other.md`, join(folder, 'link.md')]) {
		await assert.rejects(call('read_file', { path }), /outside the case files this agent may read/, path);
	}
	await assert.rejects(linked.call('read_file', { path: join(folder, 'link.md') }), /outside the case files this agent may read/);
});

// wave 1 on 038df55f: 7 of 9 big-case fact-checkers never wrote in 109-260
// calls, gathering for one whole-file write past the output cap, and both
// whole-file writes made (52k and 79k characters) were refused over one claim.
test('a fact-checker builds data/fact-check.json a claim at a time, and a refused claim names its finding', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'fact-checker');
	const data = (name: string) => join(caseDir, 'data', name);
	await writeFile(data('findings.json'), JSON.stringify({ project: 'p', findings: ['one', 'two', 'three'].map((claim, i) => ({ id: `F${i + 1}`, claim })) }));
	const claim = (n: number, claim_text: string, verdict = 'unverified') => ({ finding_id: `F${n}`, claim_text, verdict, confidence: 'low', evidence_for: [], evidence_against: [] });
	const write = (...claims: object[]) => call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ project: 'p', claims }) });
	const ids = async () => JSON.parse(await readFile(data('fact-check.json'), 'utf8')).claims.map((c: { finding_id: string; verdict: string }) => `${c.finding_id}:${c.verdict}`);

	await write(claim(1, 'one'));
	assert.match(await write(claim(2, 'two')), /1 written, 2 in the file; entries you left out stay/);
	await write(claim(3, 'three'));
	await write(claim(2, 'two', 'disputed'));
	assert.deepEqual(await ids(), ['F1:unverified', 'F2:disputed', 'F3:unverified']);

	const before = await readFile(data('fact-check.json'), 'utf8');
	await assert.rejects(write(claim(3, 'a different claim')), /checks a claim that is not its finding's[\s\S]*F3:/);
	assert.equal(await readFile(data('fact-check.json'), 'utf8'), before);

	await assert.rejects(write({ finding_id: 'F3', withdrawn: '' }), /withdrawing F3 from data\/fact-check\.json needs a reason/);
	await write({ finding_id: 'F3', withdrawn: 'checked twice by mistake' });
	const file = JSON.parse(await readFile(data('fact-check.json'), 'utf8'));
	assert.deepEqual(file.claims.map((c: { finding_id: string }) => c.finding_id), ['F1', 'F2']);
	assert.deepEqual(file.withdrawn, [{ finding_id: 'F3', reason: 'checked twice by mistake' }]);
});

test('an investigator writes findings a few at a time, a left-out finding stays, and only a stated withdrawal removes one', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator');
	const path = join(caseDir, 'data', 'findings.json');
	const write = (...findings: object[]) => call('write_file', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings }) });
	await writeFile(path, JSON.stringify({ project: 'p', findings: [] }));

	await write({ id: 'F1', claim: 'one' }, { id: 'F2', claim: 'two' });
	await write({ id: 'F3', claim: 'three' });
	await write({ id: 'F1', claim: 'one, corrected' });
	await write({ id: 'F2', withdrawn: 'no claim the evidence supports' });

	const file = JSON.parse(await readFile(path, 'utf8'));
	assert.deepEqual(file.findings, [{ id: 'F1', claim: 'one, corrected' }, { id: 'F3', claim: 'three' }]);
	assert.deepEqual(file.withdrawn, [{ id: 'F2', reason: 'no claim the evidence supports' }]);
	await assert.rejects(write({ id: 'F9', withdrawn: 'gone' }), /has no entry F9 to withdraw; the file is unchanged/);
});

test('in a direction split a partial write keeps the other directions\' entries and the direction\'s own earlier ones', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator', { SPOTLIGHT_DIRECTION: 'D2' });
	const path = join(caseDir, 'data', 'findings.json');
	await writeFile(path, JSON.stringify({ project: 'p', findings: [{ id: 'D1-F1', claim: 'first' }, { id: 'D2-F1', claim: 'mine' }] }));

	await call('write_file', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings: [{ id: 'D2-F2', claim: 'mine too' }] }) });

	assert.deepEqual(JSON.parse(await readFile(path, 'utf8')).findings.map((f: { id: string }) => f.id), ['D1-F1', 'D2-F1', 'D2-F2']);
});

// A withdrawn finding leaves references a strict validator rejects. A finding
// a recorded passage cites is never withdrawn (the passage stays on record);
// for any other, the withdrawal lists what still refers to it with its owner's
// way to clear it, and once cleared the case validates and stays at its gate.
test('a finding a passage records is not withdrawn; another lists what refers to it, and once those are cleared the case still validates at the gate', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-withdraw-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const python = process.env.SPOTLIGHT_PYTHON ?? 'python3';
	execFileSync(python, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	const tools = (role: string) => {
		const byName = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: role } }).map((tool) => [tool.name, tool]));
		return async (params: Record<string, unknown>) => {
			const result = await (byName.get('write_file') as ToolDefinition).execute('call', params, undefined, undefined, undefined as never);
			return (result.content[0] as { text: string }).text;
		};
	};
	const investigator = tools('investigator');
	const factChecker = tools('fact-checker');
	const read = async (name: string) => JSON.parse(await readFile(join(caseDir, 'data', name), 'utf8'));
	const status = () => JSON.parse(execFileSync(python, [join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'), 'status', '--json', caseDir], { encoding: 'utf8' })).phase;
	const validates = () => {
		for (const script of ['validate-case.py', 'validate-fact-check.py']) {
			try {
				execFileSync(python, [join(CHECKOUT_ROOT, 'scripts', script), caseDir], { encoding: 'utf8', stdio: 'pipe' });
			} catch (error) {
				assert.fail(`${script}: ${(error as { stdout?: string }).stdout}${(error as { stderr?: string }).stderr}`);
			}
		}
	};
	assert.equal(status(), 'investigation_gate_approval');
	const before = await readFile(join(caseDir, 'data', 'findings.json'), 'utf8');
	await assert.rejects(investigator({ path: 'data/findings.json', content: JSON.stringify({ findings: [{ id: 'F1', withdrawn: 'unsupported' }] }) }), /F1 \(recorded by SX1\) cannot be withdrawn[\s\S]*the file is unchanged/);
	assert.equal(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'), before);

	// A second finding with a claim and a claim link, then withdrawn.
	const finding = (await read('findings.json')).findings[0];
	await investigator({ path: 'data/findings.json', content: JSON.stringify({ findings: [{ ...finding, id: 'F2', claim: 'Doe was paid twice.', finding_fingerprint: undefined }] }) });
	await factChecker({ path: 'data/fact-check.json', content: JSON.stringify({ claims: [{ finding_id: 'F2', claim_text: 'Doe was paid twice.', verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] }) });
	const item = (await read('evidence-bundle.json')).items[0];
	await investigator({ path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...item, claim_links: [...item.claim_links, { ...item.claim_links[0], finding_id: 'F2', claim_text: 'Doe was paid twice.' }] }] }) });
	const withdrawn = await investigator({ path: 'data/findings.json', content: JSON.stringify({ findings: [{ id: 'F2', withdrawn: 'a duplicate of F1' }] }) });
	assert.match(withdrawn, /F2 is withdrawn; these still refer to it and must be cleared: the fact-check's claim for F2 \(the fact-checker withdraws it[^)]*\); evidence item E1's claim_links/);

	await factChecker({ path: 'data/fact-check.json', content: JSON.stringify({ claims: [{ finding_id: 'F2', withdrawn: 'its finding was withdrawn' }] }) });
	await investigator({ path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [item] }) });
	validates();
	assert.equal(status(), 'investigation_gate_approval');
	assert.deepEqual((await read('findings.json')).withdrawn, [{ id: 'F2', reason: 'a duplicate of F1' }]);
});

test('a write inside the case lands there and reads back', async (t) => {
	const { caseDir, call } = await fixture(t);

	await call('write_file', { path: 'research/notes/a.md', content: 'first' });
	await call('edit_file', { path: `${caseDir}/research/notes/a.md`, old_text: 'first', new_text: 'second' });

	assert.equal(await readFile(join(caseDir, 'research/notes/a.md'), 'utf8'), 'second');
	assert.match(await call('read_file', { path: 'research/notes/a.md' }), /second/);
	assert.match(await call('list_files', { pattern: 'research/**/*.md' }), /research\/notes\/a\.md/);
	assert.match(await call('grep_files', { pattern: 'sec.nd' }), /research\/notes\/a\.md:1:second/);
	// fc-stall item 5: a leading (?i), refused as invalid 87 times, is ignore case.
	assert.match(await call('grep_files', { pattern: '(?i)SECOND' }), /research\/notes\/a\.md:1:second/);
});

test('a path copied from a skill with its {CASE_DIR}/ prefix lands in the case root', async (t) => {
	const { caseDir, call } = await fixture(t);

	await call('write_file', { path: '{CASE_DIR}/brief-directions.txt', content: 'approved' });

	assert.equal(await readFile(join(caseDir, 'brief-directions.txt'), 'utf8'), 'approved');
	assert.match(await call('read_file', { path: '{CASE_DIR}/brief-directions.txt' }), /approved/);
	assert.equal(existsSync(join(caseDir, '{CASE_DIR}')), false);
});

test('writes outside the case, through a symlink, or to a protected file are refused', async (t) => {
	const { caseDir, outside, call } = await fixture(t);
	await symlink(outside, join(caseDir, 'research'), 'dir');
	await writeFile(join(outside, 'target.json'), 'outside');
	await symlink(join(outside, 'target.json'), join(caseDir, 'data', 'linked.json'));
	await writeFile(join(caseDir, 'data', 'orchestration.json'), '{"phase":"brief"}');

	for (const path of [
		'../outside/escape.txt',
		`${outside}/escape.txt`,
		'research/escape.txt',
		'data/linked.json',
		'data/orchestration.json',
		'data/../data/orchestration.json',
		'DATA/Orchestration.JSON',
		'data/review-nonces.json',
		'data/methodology-review.json',
		'data/ingestion.json',
		'data/report-declined.json',
		'data/case-contract.json',
		'data/provenance-signing-receipt.json',
		'evidence/capture-receipt.json',
	]) {
		await assert.rejects(call('write_file', { path, content: 'x' }), /refuses|outside|symlink|protected/i, path);
		await assert.rejects(call('edit_file', { path, old_text: 'phase', new_text: 'x' }), /refuses|outside|symlink|protected|not found/i, path);
	}
	// The gate shows the Anomaly signals as the runner checked them.
	await assert.rejects(call('write_file', { path: 'data/anomaly-signals.json', content: '{"runs": []}' }), /written only through the anomaly_run tool/);
	// The decision record opens or closes the Investigation gate's Anomaly check.
	await assert.rejects(call('write_file', { path: 'data/anomaly-decision.json', content: '{"decision": false}' }), /written only by the harness/);
	assert.equal(existsSync(join(outside, 'escape.txt')), false);
	assert.equal(await readFile(join(outside, 'target.json'), 'utf8'), 'outside');
	assert.equal(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'), '{"phase":"brief"}');
	for (const name of ['review-nonces.json', 'methodology-review.json', 'ingestion.json', 'report-declined.json', 'provenance-signing-receipt.json']) {
		assert.equal(existsSync(join(caseDir, 'data', name)), false, name);
	}
});

test('a read outside the case is refused whether or not the file exists', async (t) => {
	const { outside, call } = await fixture(t);
	await writeFile(join(outside, 'present.txt'), 'x');

	for (const path of ['../outside/present.txt', '../outside/absent.txt', join(outside, 'absent.txt')]) {
		await assert.rejects(call('read_file', { path }), /refused/, path);
	}
});

test('reads through a symlink that leaves the case are refused', async (t) => {
	const { caseDir, outside, call } = await fixture(t);
	await writeFile(join(outside, 'secret.txt'), 'secret');
	await symlink(join(outside, 'secret.txt'), join(caseDir, 'data', 'secret.txt'));

	await assert.rejects(call('read_file', { path: 'data/secret.txt' }), /outside/);
	await assert.rejects(call('read_file', { path: '../outside/secret.txt' }), /outside/);
	assert.doesNotMatch(await call('grep_files', { pattern: 'secret' }), /secret\.txt:/);
});

// A role narrowed in harness/composition.json (the analyst) reaches only its
// named case files, whatever path, glob, folder or symlink it gives.
test('a narrowed role reads only its named files and writes only its one file', async (t) => {
	const scope = { reads: ['data/findings.json', 'data/proposal.json', 'data/linked.json'], writes: ['data/proposal.json'] };
	const { caseDir, call } = await fixture(t, scope);
	await mkdir(join(caseDir, 'research'));
	await writeFile(join(caseDir, 'data', 'findings.json'), '{"finding": "needle"}');
	await writeFile(join(caseDir, 'research', 'page.md'), 'needle in a captured page');
	await writeFile(join(caseDir, 'data', 'journalist-input.json'), '{"note": "needle"}');
	await symlink(join(caseDir, 'research', 'page.md'), join(caseDir, 'data', 'linked.json'));

	assert.match(await call('read_file', { path: 'data/findings.json' }), /needle/);
	assert.equal(await call('list_files', { pattern: '**/*' }), 'data/findings.json\ndata/linked.json');
	assert.equal(await call('grep_files', { pattern: 'needle' }), 'data/findings.json:1:{"finding": "needle"}');
	assert.equal(await call('grep_files', { pattern: 'needle', path: 'data' }), 'data/findings.json:1:{"finding": "needle"}');
	for (const path of ['research/page.md', 'data/journalist-input.json', 'data/linked.json', 'data/absent.json']) {
		await assert.rejects(call('read_file', { path }), /may read/, path);
		await assert.rejects(call('grep_files', { pattern: 'needle', path }), /may read/, path);
		await assert.rejects(call('hash_files', { paths: [path] }), /may read/, path);
	}

	await call('write_file', { path: 'data/proposal.json', content: '{"claims": []}' });
	await call('edit_file', { path: 'data/proposal.json', old_text: '[]', new_text: '[1]' });
	assert.equal(await readFile(join(caseDir, 'data', 'proposal.json'), 'utf8'), '{"claims": [1]}');
	for (const path of ['data/findings.json', 'research/new/notes.md', 'data/knowledge-batch.json']) {
		await assert.rejects(call('write_file', { path, content: 'x' }), /may write/, path);
		await assert.rejects(call('edit_file', { path, old_text: 'needle', new_text: 'x' }), /may write/, path);
	}
	assert.equal(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'), '{"finding": "needle"}');
	assert.equal(existsSync(join(caseDir, 'research', 'new')), false);
	assert.equal(existsSync(join(caseDir, 'data', 'knowledge-batch.json')), false);
});

// D9: the fact-checker's `denyReads` (harness/composition.json) hides the
// research phase's material from every file tool, by path, glob, folder or
// symlink, while the rest of the case stays readable.
test('a role denied case files cannot read, list, grep, hash or edit them, and reads the rest of the case', async (t) => {
	const { caseDir, call } = await fixture(t, { denyReads: ['research/swarm/', 'data/leads.json', 'data/research-*.json'] });
	await mkdir(join(caseDir, 'research', 'swarm', 'lane-a'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'swarm', 'lane-a', 'leads.jsonl'), 'needle from a lane');
	await writeFile(join(caseDir, 'data', 'leads.json'), '{"lead": "needle"}');
	await writeFile(join(caseDir, 'data', 'research-triage.json'), '{"triage": "needle"}');
	await writeFile(join(caseDir, 'data', 'findings.json'), '{"finding": "needle"}');
	await writeFile(join(caseDir, 'research', 'page.md'), 'needle in a captured page');
	await symlink(join(caseDir, 'data', 'leads.json'), join(caseDir, 'research', 'linked.md'));

	assert.match(await call('read_file', { path: 'data/findings.json' }), /needle/);
	assert.equal(await call('list_files', { pattern: '**/*' }), 'data\ndata/findings.json\nresearch\nresearch/linked.md\nresearch/page.md');
	assert.equal(await call('grep_files', { pattern: 'needle' }), 'data/findings.json:1:{"finding": "needle"}\nresearch/page.md:1:needle in a captured page');
	assert.equal(await call('grep_files', { pattern: 'needle', path: 'research' }), 'research/page.md:1:needle in a captured page');
	for (const path of ['data/leads.json', 'DATA/Research-Triage.json', 'research/swarm', 'research/swarm/lane-a/leads.jsonl', 'research/linked.md', '{CASE_DIR}/research/swarm/absent.md']) {
		await assert.rejects(call('read_file', { path }), /may read/, path);
		await assert.rejects(call('grep_files', { pattern: 'needle', path }), /may read/, path);
		await assert.rejects(call('hash_files', { paths: [path] }), /may read/, path);
	}
	await assert.rejects(call('edit_file', { path: 'data/leads.json', old_text: 'needle', new_text: 'x' }), /may read/);
	assert.equal(await readFile(join(caseDir, 'data', 'leads.json'), 'utf8'), '{"lead": "needle"}');
});


// declared sha256 and re-select every source_ref. A digest, line range or JSON
// Pointer read through these tools must be the one the validators compute.
const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
const SCRIPTS = fileURLToPath(new URL('../../../../scripts', import.meta.url));

function validatorSays(program: string, ...args: string[]): string {
	return execFileSync(PYTHON, ['-c', `import sys, json\nsys.path.insert(0, sys.argv[1])\nfrom evidence_anchors import *\n${program}`, SCRIPTS, ...args], {
		encoding: 'utf8',
	});
}

test('hash_files returns the digest the evidence validators recompute for an artifact the agent wrote', async (t) => {
	const { caseDir, call } = await fixture(t);
	await call('write_file', { path: 'research/extracted.md', content: 'Zürich\r\nline two \n' });
	await writeFile(join(caseDir, 'research', 'api.json'), Buffer.from([0x7b, 0x7d, 0x0a, 0xff]));

	const hashed = JSON.parse(await call('hash_files', { paths: ['research/extracted.md', '{CASE_DIR}/research/api.json'] }));

	for (const [index, name] of ['extracted.md', 'api.json'].entries()) {
		const expected = validatorSays('print(sha256_file(__import__("pathlib").Path(sys.argv[2])), end="")', join(caseDir, 'research', name));
		assert.equal(hashed[index].sha256, expected, name);
		assert.equal(hashed[index].path, `research/${name}`);
	}
	await assert.rejects(call('hash_files', { paths: ['../outside/x'] }), /refused/);
});

test('read_file line ranges, grep_files line numbers and json_pointer select what the evidence validators select', async (t) => {
	const { caseDir, call } = await fixture(t);
	// Numbered as grep -n numbers them: only a line feed ends a line, so the
	// line separator, lone carriage return and form feed (a pdftotext page
	// break) stay inside line 2, and epsilon is line 4.
	const text = 'alpha\r\nbeta gamma\rdelta\fpage two\n\nepsilon\n';
	await mkdir(join(caseDir, 'research'));
	await writeFile(join(caseDir, 'research', 'page.md'), text);
	const doc = { items: [{ 'a/b': 'Grüße </script>', n: 3 }, { z: [1, 'x'], a: { é: true, b: null } }] };
	await writeFile(join(caseDir, 'research', 'api.json'), JSON.stringify(doc));
	const select = (name: string, ref: object) =>
		validatorSays(
			'texts, error = selected_source_texts(__import__("pathlib").Path(sys.argv[2]), json.loads(sys.argv[3]))\nassert error is None, error\nprint(json.dumps(texts[0]))',
			join(caseDir, 'research', name),
			JSON.stringify(ref),
		);

	for (const [start, end] of [[2, 2], [3, 4], [1, 2]]) {
		const expected = JSON.parse(select('page.md', { line_start: start, line_end: end }));
		assert.equal(await call('read_file', { path: 'research/page.md', offset: start, limit: end - start + 1 }), expected, `${start}-${end}`);
	}
	assert.match(await call('grep_files', { pattern: '^epsilon$' }), /^research\/page\.md:4:epsilon$/m);
	for (const pointer of ['/items/0/a~1b', '/items/1', '/items/0/n', '']) {
		const expected = JSON.parse(select('api.json', { json_pointer: pointer }));
		assert.equal(await call('read_file', { path: 'research/api.json', json_pointer: pointer }), expected, pointer);
	}
	await assert.rejects(call('read_file', { path: 'research/api.json', json_pointer: '/items/7' }), /does not resolve/);
});

// E2E D3: save_as "research/" became a file named research, and every later
// write under research/ failed with ENOTDIR.
test('a path ending in a slash or naming a folder is refused and leaves no file named like the folder', async (t) => {
	const { caseDir, call } = await fixture(t);

	await assert.rejects(call('write_file', { path: 'research/', content: 'page' }), /names a folder/);
	assert.equal(existsSync(join(caseDir, 'research')), false);
	await assert.rejects(call('write_file', { path: 'data', content: 'page' }), /is a folder/);

	await call('write_file', { path: 'research/rounds.json', content: '[]' });
	assert.equal(await readFile(join(caseDir, 'research/rounds.json'), 'utf8'), '[]');
});

// Windows drops a trailing dot or space, so such a name would reach a
// protected or denied file under another spelling.
test('a name ending in a dot or a space is refused', async (t) => {
	const { call } = await fixture(t);

	for (const path of ['data/case-contract.json.', 'data/case-contract.json ', 'data./notes.md']) {
		await assert.rejects(call('write_file', { path, content: '{}' }), /ending in a dot or a space/, path);
	}
	await call('write_file', { path: 'research/./notes.md', content: 'ok' });
});

// E2E D2: a rewrite cut off at the output cap arrived as content "" and wiped
// data/fact-check.json.
test('empty or whitespace content over an existing non-empty file is refused and the file is kept', async (t) => {
	const { caseDir, call } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'fact-check.json'), '{"claims":[]}');

	for (const content of ['', '  \n']) {
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content }), /would erase/);
	}
	assert.equal(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8'), '{"claims":[]}');

	await call('write_file', { path: 'research/empty.md', content: '' });
	assert.equal(await readFile(join(caseDir, 'research/empty.md'), 'utf8'), '');
});

// gold-matrix s0 on 18652cde: the fact-checker wrote data/evidence-bundle.json as a bare list,
// and the resolver could no longer hash the case or name its phase.
test('a case data file written or edited into anything but one JSON object is refused and kept', async (t) => {
	const { caseDir, call } = await fixture(t);
	const bundle = '{"items": [{"id": "E1"}]}';
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), bundle);

	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: '[{"id": "E1"}]' }), /data\/evidence-bundle\.json must stay one JSON object with an `items` list; the file is unchanged/);
	await assert.rejects(call('edit_file', { path: 'data/evidence-bundle.json', old_text: '"id": "E1"}]}', new_text: '"id": "E1"}]' }), /must stay one JSON object/);
	// 12B r3 cc on f43df365: the bundle's items under another key went through.
	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: '{"evidence_bundle": [{"id": "E1"}]}' }), /must stay one JSON object with an `items` list/);
	await assert.rejects(call('edit_file', { path: 'data/evidence-bundle.json', old_text: '"items"', new_text: '"evidence_bundle"' }), /must stay one JSON object with an `items` list/);
	assert.equal(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'), bundle);

	await call('write_file', { path: 'data/evidence-bundle.json', content: '{"items": []}' });
	await call('write_file', { path: 'research/list.json', content: '[]' });
});

// 12B r1 okfn and fsf on 97f066b6: bundle items named fetch's distilled leads file, so
// every exact quote from the page's .raw was refused as not from an acquired source.
test('an evidence item naming a distilled leads file is refused, naming its .raw; an item on the .raw is kept', async (t) => {
	const { caseDir, call } = await fixture(t);
	await mkdir(join(caseDir, 'research'));
	await writeFile(join(caseDir, 'research', 'page.md'), '# RLM-distilled leads from https://example.org/\n- a lead\n');
	await writeFile(join(caseDir, 'research', 'page.md.raw'), 'The page as fetched.\n');
	const bundle = (item: object) => JSON.stringify({ items: [{ id: 'E1', ...item }] });

	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ path: 'research/page.md' }) }),
		(error: Error) => error.message === 'refused: data/evidence-bundle.json records fetch\'s distilled leads as evidence for E1 (path: research/page.md); the file is unchanged. Record the page as fetched instead: raw_path "<that file>.raw" with the raw_sha256 the fetch returned for it.');
	assert.equal(existsSync(join(caseDir, 'data', 'evidence-bundle.json')), false);
	// The marker counts on any of the first eight lines, as the validator reads it.
	await writeFile(join(caseDir, 'research', 'late.md'), '\n\uFEFF  # RLM-distilled leads from https://example.org/\n');
	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ raw_path: 'research/late.md' }) }), /distilled leads as evidence for E1/);

	await call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ raw_path: 'research/page.md.raw' }) });
	await assert.rejects(call('edit_file', { path: 'data/evidence-bundle.json', old_text: 'page.md.raw', new_text: 'page.md' }), /distilled leads as evidence for E1 \(raw_path: research\/page\.md\)/);
	assert.equal(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'), bundle({ raw_path: 'research/page.md.raw' }));
});

// LFM3 e2 stage 1 on Osaurus (bc87c716): flags coded "context" were accepted,
// and the case stalled on "needs a known Batch 1.1 code" the fact-checker never saw.
test('a fact-check flag with an unknown code or no reason is refused, naming the allowed codes', async (t) => {
	const { caseDir, call } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const write = (flags: unknown) => call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict: 'unverified', flags }] }) });

	await assert.rejects(write([{ code: 'context', reason: 'Background on the organisation.' }]),
		(error: Error) => error.message.includes('claims[0] (F1).flags[0]: code "context" is not a Batch 1.1 code') && error.message.includes('partial_support') && error.message.includes('drop the flag'));
	await assert.rejects(write([{ code: 'partial_support', reason: ' ' }]), /claims\[0\] \(F1\)\.flags\[0\]: has no reason/);
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	await write([{ code: 'partial_support', reason: 'The filing names the chair, not the date.' }]);
	await write([]);
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: '"flags":[]', new_text: '"flags":[{"code":"context","reason":"x"}]' }), /"context" is not a Batch 1\.1 code/);
});

// gold mozilla s0 a2 on a2ab7e20: the fact-checker wrote a verified claim with its ref under
// a misspelt key, the validator blocked the case, and the one correction was spent.
test('a verified claim citing no passage is refused, naming the refs on record, and kept', async (t) => {
	const { caseDir, call } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const link = { finding_id: 'F1', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
		{ id: 'P2', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] },
		{ id: 'P9', expression_fingerprint: '9'.repeat(64), lifecycle_events: [{ event: 'activated' }, { event: 'superseded' }], finding_links: [link] },
		// Active and supporting, but another finding's passage.
		{ id: 'P3', expression_fingerprint: '3'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [{ ...link, finding_id: 'F2' }] },
	] }));
	const ref = { expression_id: 'P2', expression_fingerprint: 'e'.repeat(64), finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	const claim = (item: object, verdict = 'verified') => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict, evidence_for: [item] }] });

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_ref: ref }) }),
		(error: Error) => error.message.includes('has no source_expression_refs; the file is unchanged') && error.message.includes('F1 (the key is source_expression_refs, a list, not source_expression_ref): "source_expression_refs": ["P2"]'));
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);

	// 12B r3 ia on f43df365: a ref naming a passage never recorded went through.
	// A bare id is accepted only for an active passage supporting this finding:
	// unknown, superseded or another finding's passage is refused.
	for (const cited of ['SE-F1-TAX', 'P9', 'P3', { expression_id: 'P3' }, { ...ref, link_fingerprint: '0'.repeat(64) }]) {
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: [cited] }) }),
			(error: Error) => error.message.includes(`F1 (${JSON.stringify([cited])} is not an active passage supporting it): "source_expression_refs": ["P2"]`));
	}
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	for (const cited of ['P2', { expression_id: 'P2' }]) await call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: [cited] }) });
	await call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: [ref] }) });
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: '"source_expression_refs"', new_text: '"source_expression_ref"' }), /has no source_expression_refs/);
	await call('write_file', { path: 'data/fact-check.json', content: claim({}, 'unverified') });
});

// 12B r3 ia on ad768b54: the fact-checker rewrote investigator item E1 to another file,
// and every passage already anchored to E1 failed validation from then on.
test('a fact-checker changes only its own bundle items, and no role moves the files of an item passages cite', async (t) => {
	const investigatorItem = { id: 'E1', raw_path: 'research/a.md.raw', sha256: 'a'.repeat(64) };
	const ownItem = { id: 'E2', raw_path: 'research/b.md.raw', created_by: 'fact-checker' };
	const bundle = (...items: object[]) => JSON.stringify({ items }, null, 2);
	for (const role of ['fact-checker', 'investigator']) {
		const { caseDir, call } = await fixture(t, undefined, role);
		await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), bundle(investigatorItem, ownItem));
		// A rewrite that keeps every item as it was, and an appended item, are fine.
		await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [investigatorItem, ownItem, { id: 'E3', raw_path: 'research/c.md.raw', created_by: 'fact-checker' }] }) });
		await call('write_file', { path: 'data/evidence-bundle.json', content: bundle(investigatorItem, { ...ownItem, raw_path: 'research/d.md.raw' }) });
		const kept = { ...ownItem, raw_path: 'research/d.md.raw' };
		const moved = bundle({ ...investigatorItem, raw_path: 'research/z.md.raw' }, kept);
		if (role === 'fact-checker') {
			await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: moved }),
				(error: Error) => error.message.startsWith('refused: data/evidence-bundle.json changes or removes E1, which you did not create') && error.message.includes('never edit an investigator item'));
			// Writes merge by item id: an item left out stays, and only a stated
			// withdrawal removes one, which for an investigator item is refused.
			await call('write_file', { path: 'data/evidence-bundle.json', content: bundle(kept) });
			assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).items.map((item: { id: string }) => item.id), ['E1', 'E2', 'E3']);
			await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ id: 'E1', withdrawn: 'not needed' }) }), /changes or removes E1, which you did not create/);
		} else {
			await call('write_file', { path: 'data/evidence-bundle.json', content: moved });
			await call('write_file', { path: 'data/evidence-bundle.json', content: bundle(investigatorItem, kept) });
		}
		await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
			{ id: 'P1', original_evidence_bundle_id: 'E2', lifecycle_events: [{ event: 'activated' }] },
			{ id: 'P2', original_evidence_bundle_id: 'E2', lifecycle_events: [{ event: 'activated' }, { event: 'withdrawn' }] },
		] }));
		const current = await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8');
		await assert.rejects(call('edit_file', { path: 'data/evidence-bundle.json', old_text: 'research/d.md.raw', new_text: 'research/e.md.raw' }),
			(error: Error) => error.message.startsWith('refused: data/evidence-bundle.json changes or removes the files of E2 (cited by P1); the file is unchanged.'));
		assert.equal(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'), current);
		await call('edit_file', { path: 'data/evidence-bundle.json', old_text: '"id": "E2"', new_text: '"id": "E2", "extraction_confidence": "high"' });
	}
});

// 12B r2 cc on ef4e6ab0: with no passage recorded yet, the fact-checker verified F1 and F2
// citing nothing, the write went through, and the validator blocked the case afterwards.
// 12B r3 ia on f43df365: a fact-check written with "schema_validation": "1.1" was taken for a
// legacy file and its verified claim went through.
test('in an activated case a verified claim is refused while no passage is on record, and a fact-check that is not 1.1 is refused', async (t) => {
	const { caseDir, call } = await fixture(t);
	const claim = (verdict: string, schema_version = '1.1') => JSON.stringify({ schema_version, claims: [{ finding_id: 'F1', verdict, evidence_for: [{}] }] });
	const none = 'no passage on record supports F1; record the passage you checked with source_expressions, then cite its ref here, or change the verdict. If no source you acquired contains a passage that supports the finding, do not record one: set the verdict to unverified (or use missing_source_gate for a source that was requested and not returned).';

	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	for (const expressions of [undefined, '{"expressions": []}']) {
		if (expressions) await writeFile(join(caseDir, 'data', 'source-expressions.json'), expressions);
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim('verified') }),
			(error: Error) => error.message === `refused: data/fact-check.json gives a verified verdict whose evidence_for has no source_expression_refs; the file is unchanged.\n${none}`);
	}
	await call('write_file', { path: 'data/fact-check.json', content: claim('unverified') });
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: '"unverified"', new_text: '"verified"' }), /no passage on record supports F1/);
	for (const content of [claim('verified', '1.0'), claim('verified').replace('schema_version', 'schema_validation')]) {
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content }),
			(error: Error) => error.message === 'refused: data/fact-check.json in an activated case needs "schema_version": "1.1"; the file is unchanged.');
	}
});

// gold osmf s1 on a2ab7e20: the investigator wrote 1.0 findings in the methodology phase, the
// case was never activated, and verified claims reached the report with no grounded passage.
test('a new case gets no investigation file before the harness activates it; activated and legacy cases do', async (t) => {
	const { caseDir, call } = await fixture(t);
	for (const path of ['data/findings.json', 'data/fact-check.json', 'data/investigation-log.json']) {
		await assert.rejects(call('write_file', { path, content: '{"schema_version": "1.0"}' }), /starts the investigation record, which opens at Investigation/, path);
		assert.equal(existsSync(join(caseDir, path)), false, path);
	}
	await call('write_file', { path: 'data/methodology.json', content: '{}' });

	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	await call('write_file', { path: 'data/findings.json', content: '{"schema_version": "1.1"}' });

	const legacy = await fixture(t);
	await writeFile(join(legacy.caseDir, 'data', 'findings.json'), '{"schema_version": "1.0"}');
	await legacy.call('write_file', { path: 'data/investigation-log.json', content: '{}' });
});

// gold osmf s1 a1 on d74a171e: the fact-checker rewrote F3's claim into an invented London
// address and marked it false; matrix-stall s0 verified claims under the wrong finding ids.
test('a fact-check claim that is not its finding\'s claim is refused, naming the claim to copy', async (t) => {
	const { caseDir, call } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ findings: [{ id: 'F3', claim: 'The registered office is in Cambridge, CB4 0WS.' }] }));
	const factCheck = (claimText: string) => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F3', claim_text: claimText, verdict: 'false' }] });

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: factCheck('The registered office is 103-105 High Street, London.') }),
		(error: Error) => error.message.includes("checks a claim that is not its finding's; the file is unchanged") && error.message.includes('F3: "The registered office is in Cambridge, CB4 0WS."'));
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);

	await call('write_file', { path: 'data/fact-check.json', content: factCheck('The registered office is in  Cambridge,\nCB4 0WS.') });
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: 'Cambridge', new_text: 'London' }), /not its finding's/);
});

// 12B r5 fsf s0 on 2f357029: the fact-checker verified F2 citing research/fsf_leadership_browse.md,
// a file never saved in the case, and the write went through.
test('a positive verdict citing a file that is not in the case is refused, naming each path', async (t) => {
	const { caseDir, call } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const link = { finding_id: 'F2', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
		{ id: 'P1', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] },
	] }));
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'fsf_board.md'), 'The board has five members.\n');
	const evidence = (path: string, archive_url = path) => ({ local_file: path, archive_url, source_ref: { path, line_start: 1, line_end: 1 }, source_expression_refs: ['P1'] });
	const factCheck = (item: object, verdict = 'verified') => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F2', verdict, evidence_for: [item] }] }, null, 2);

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: factCheck(evidence('research/fsf_leadership_browse.md')) }),
		(error: Error) => error.message === 'refused: data/fact-check.json cites files that are not in the case folder; the file is unchanged.\nF2: research/fsf_leadership_browse.md\nCite only a file you saved in the case (list_files shows them). If no saved source supports the finding, set the verdict to unverified.');
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: factCheck({ ...evidence('research/fsf_board.md'), source_ref: { path: '../outside/x.md' } }) }), /\nF2: \.\.\/outside\/x\.md\n/);

	await call('write_file', { path: 'data/fact-check.json', content: factCheck(evidence('research/fsf_board.md', 'https://web.archive.org/web/2026/https://fsf.org/')) });
	await call('write_file', { path: 'data/fact-check.json', content: factCheck(evidence('research/fsf_board.md')) });
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: '"local_file": "research/fsf_board.md"', new_text: '"local_file": "research/gone.md"' }), /\nF2: research\/gone\.md\n/);
	await call('write_file', { path: 'data/fact-check.json', content: factCheck(evidence('research/fsf_leadership_browse.md'), 'unverified') });
});

test('with the Indicator Framework on, its case file and report are written only through record_indicator_framework', async (t) => {
	const on = await fixture(t);
	await approve(on.caseDir, true);
	for (const path of ['data/indicator-framework.json', 'data/indicator-framework.md']) {
		await assert.rejects(on.call('write_file', { path, content: '{}' }), /written only through record_indicator_framework/, path);
		assert.equal(existsSync(join(on.caseDir, path)), false, path);
	}
	await writeFile(join(on.caseDir, 'data/indicator-framework.json'), '{"a": 1}');
	await assert.rejects(on.call('edit_file', { path: 'data/indicator-framework.json', old_text: '1', new_text: '2' }), /written only through record_indicator_framework/);

	const off = await fixture(t);
	await approve(off.caseDir, false);
	await off.call('write_file', { path: 'data/indicator-framework.json', content: '{}' });
});

// E2E D11: the tool text promises the checkout's schemas/, but a relative
// schemas/ path resolved inside the case and came back "not found".
test('a relative schemas/ path reads the checkout schema', async (t) => {
	const { call } = await fixture(t);

	const text = await call('read_file', { path: 'schemas/methodology.schema.json' });

	assert.equal(text, await readFile(join(CHECKOUT_ROOT, 'schemas', 'methodology.schema.json'), 'utf8'));
});

// fc-stall item 3 (incr-test2 sp98): fact-checker threads folded after whole
// reads of the big case files and started over. A worker's whole read of one
// past INDEXED_READ_CHARS returns its entries by json_pointer instead.
test('a worker reading a big findings.json whole gets its entries by json_pointer, and reads one that way', async (t) => {
	const { caseDir, call } = await fixture(t);
	const findings = { findings: Array.from({ length: 40 }, (_, n) => ({ id: `F${n + 1}`, claim: `Claim ${n + 1}. ${'x'.repeat(400)}` })) };
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify(findings, null, 2));
	const index = await call('read_file', { path: 'data/findings.json' });
	assert.match(index, /^data\/findings\.json is [\d,]+ characters, too long to read whole\. Its findings by json_pointer: \/findings\/0 F1; \/findings\/1 F2; .* \/findings\/39 F40\. Read the ones you need with json_pointer, or a range with offset and limit\.$/);
	assert.match(await call('read_file', { path: 'data/findings.json', json_pointer: '/findings/1' }), /"id": "F2"/);
	assert.match(await call('read_file', { path: 'data/findings.json', offset: 1, limit: 3 }), /"findings"/);
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ findings: [{ id: 'F1', claim: 'Small.' }] }));
	assert.match(await call('read_file', { path: 'data/findings.json' }), /"Small\."/, 'a small file reads whole');
});
