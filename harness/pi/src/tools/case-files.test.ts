import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { execFile, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT, loadComposition } from '../roles.ts';
import { approve } from '../test-support/hosted-skill.ts';
import { caseFileTools } from './case-files.ts';
import type { CaseScope } from './run.ts';
import { registerCapture, registerFixtureCapture } from '../test-support/receipts.ts';

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
	return { caseDir, outside, call, tools };
}

// Real write/edit boundary: contract-owned headers must survive identity merges.
test('activated artifact envelopes repair stale format without converting semantic rows', async (t) => {
 const { caseDir, call } = await fixture(t, undefined, 'investigator');
 await writeFile(join(caseDir, 'data/case-contract.json'), JSON.stringify({ schema_version: '1.0', project: 'Approved case', current_contract_version: '1.1', activation_events: [{ event_id: 'born', kind: 'born', activated_contract_version: '1.1', activated_at: '2026-10-31T00:00:00Z', tool_version: 'test' }] }));
 const path = join(caseDir, 'data/findings.json');
 const row = { id: 'F1', claim: 'The source states 6.4%.', confidence: 'medium', evidence: 'Exact text', perspective: 'official' };
 await writeFile(path, JSON.stringify({ schema_version: '1.0', project: 'Old label', findings: [row] }));
 await call('write_file', { path: 'data/findings.json', content: JSON.stringify({ schema_version: '1.1', project: 'Solaris', findings: [row] }) });
 let saved = JSON.parse(await readFile(path, 'utf8'));
 assert.equal(saved.schema_version, '1.1');
 assert.equal(saved.project, 'Approved case');
 assert.deepEqual(saved.findings, [row]);
 const before = await readFile(path, 'utf8');
 await assert.rejects(call('write_file', { path: 'data/findings.json', content: JSON.stringify({ schema_version: '1.0', findings: [row] }) }), /requires schema_version "1.1"[\s\S]*file is unchanged/);
 assert.equal(await readFile(path, 'utf8'), before);
 const second = { ...row, id: 'F2', claim: 'Second unchanged claim.' };
 await call('write_file', { path: 'data/findings.json', content: JSON.stringify({ findings: [second] }) });
 saved = JSON.parse(await readFile(path, 'utf8'));
 assert.equal(saved.schema_version, '1.1');
 assert.deepEqual(saved.findings, [row, second]);
 const good = await readFile(path, 'utf8');
 await assert.rejects(call('edit_file', { path: 'data/findings.json', old_text: '"schema_version": "1.1"', new_text: '"schema_version": "1.0"' }), /requires schema_version "1.1"/);
 assert.equal(await readFile(path, 'utf8'), good);
 const checker = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: 'fact-checker' } }).map(tool => [tool.name, tool]));
 const claim = { finding_id: 'F1', claim_text: row.claim, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] };
 await checker.get('write_file')!.execute('fc', { path: 'data/fact-check.json', content: JSON.stringify({ claims: [claim] }) }, undefined, undefined, undefined as never);
 const checked = JSON.parse(await readFile(join(caseDir, 'data/fact-check.json'), 'utf8'));
 assert.equal(checked.schema_version, '1.1');
 assert.deepEqual(checked.claims, [claim]);
 assert.equal(checked.checked_at, undefined, 'writing does not establish a completed fact-check time');
});

test('new planning and bundle creation times are real and historical timestamps survive edits', async (t) => {
 const { caseDir, call } = await fixture(t, undefined, 'investigator');
 const start = Date.now();
 await call('write_file', { path: 'data/methodology.json', content: JSON.stringify({ project: 'Approved label', planned_at: '2024-01-01T00:00:00Z', investigation_plan: [] }) });
 const path = join(caseDir, 'data/methodology.json');
 const first = JSON.parse(await readFile(path, 'utf8'));
 assert.ok(Date.parse(first.planned_at) >= start && Date.parse(first.planned_at) <= Date.now());
 await call('edit_file', { path: 'data/methodology.json', old_text: first.planned_at, new_text: '2020-01-01T00:00:00Z' });
 assert.equal(JSON.parse(await readFile(path, 'utf8')).planned_at, first.planned_at);
 await writeFile(join(caseDir, 'data/findings.json'), JSON.stringify({ schema_version: '1.0', project: 'Approved label', findings: [{ id: 'F1', claim: 'Legacy claim.' }] }));
 await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ schema_version: '1.0', project: 'Approved label', created_at: '2024-01-01T00:00:00Z', items: [{ id: 'E1', accessed: '2021-06-01', archive_url: null }] }) });
 const bundlePath = join(caseDir, 'data/evidence-bundle.json');
 const bundle = JSON.parse(await readFile(bundlePath, 'utf8'));
 assert.equal(bundle.schema_version, '1.0');
 assert.ok(Date.parse(bundle.created_at) >= start && Date.parse(bundle.created_at) <= Date.now());
 assert.match(bundle.run_id, /^local-[0-9a-f-]{36}$/);
 assert.deepEqual(bundle.items, [{ id: 'E1', accessed: '2021-06-01', archive_url: null }]);
 await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ created_at: '2022-01-01', items: [{ id: 'E2' }] }) });
 const updated = JSON.parse(await readFile(bundlePath, 'utf8'));
 assert.equal(updated.created_at, bundle.created_at);
 assert.equal(updated.run_id, bundle.run_id, 'the locally assigned bundle ID remains stable');
 const other = await fixture(t, undefined, 'investigator');
 const otherPath = join(other.caseDir, 'data/evidence-bundle.json');
 await other.call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({run_id:'submitted-run',items:[{id:'E1'}]}) });
 assert.equal(JSON.parse(await readFile(otherPath,'utf8')).run_id,'submitted-run');
 await other.call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({run_id:' ',items:[{id:'E2'}]}) });
 assert.equal(JSON.parse(await readFile(otherPath,'utf8')).run_id,'submitted-run');
 const legacy = await fixture(t, undefined, 'investigator');
 const legacyPath = join(legacy.caseDir, 'data/evidence-bundle.json');
 await writeFile(legacyPath, JSON.stringify({run_id:' ',items:[{id:'E1'}]}));
 await legacy.call('write_file', { path:'data/evidence-bundle.json', content:JSON.stringify({run_id:'recovered-run',items:[{id:'E2'}]}) });
 assert.equal(JSON.parse(await readFile(legacyPath,'utf8')).run_id,'recovered-run');
 const invalid = await fixture(t, undefined, 'investigator');
 const invalidPath = join(invalid.caseDir,'data/evidence-bundle.json');
 await writeFile(invalidPath, JSON.stringify({run_id:42,items:[{id:'E1'}]}));
 await invalid.call('write_file', {path:'data/evidence-bundle.json',content:JSON.stringify({items:[{id:'E2'}]})});
 assert.match(JSON.parse(await readFile(invalidPath,'utf8')).run_id,/^local-[0-9a-f-]{36}$/);
});

// U3 census on 84674fd9: investigators working every direction in one context
// peaked above 30k prompt tokens. A direction's worker (directions.ts) sees only
// its own entries, and its write must keep every entry it was not shown.
test('a direction\'s worker reads only its own entries, and its write keeps every other direction\'s and the fact-checker\'s', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator', { SPOTLIGHT_DIRECTION: 'D2' });
	const data = (name: string) => join(caseDir, 'data', name);
	await writeFile(data('findings.json'), JSON.stringify({ project: 'p', findings: [{ id: 'D1-F1', claim: 'first' }, { id: 'F3', claim: 'before the split' }], gaps: ['D1 gap'] }));
	await writeFile(data('evidence-bundle.json'), JSON.stringify({ project: 'p', items: [{ id: 'D1-E1' }, { id: 'FC-E1', created_by: 'fact-checker' }, null, 17, { notes: 'legacy' }] }));
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
	assert.deepEqual(JSON.parse(await readFile(data('evidence-bundle.json'), 'utf8')).items, [{ id: 'D1-E1' }, { id: 'FC-E1', created_by: 'fact-checker' }, null, 17, { notes: 'legacy' }, { id: 'D2-E1' }]);
	assert.deepEqual(JSON.parse(await readFile(data('investigation-log.json'), 'utf8')).cycles.map((cycle: { focus: string }) => cycle.focus), ['D1: first direction', 'D2: second direction']);
	assert.deepEqual(JSON.parse(await call('read_file', { path: 'data/findings.json' })).findings, [{ id: 'D2-F1', claim: 'second, corrected' }]);
});

// qwen-u9-mac.md (P3W-5): the log wants techniques_used and tools_used; r3 wrote techniques and tools and the case looped.
test('an investigation log written with techniques and tools is stored with techniques_used and tools_used', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator');
	const log = join(caseDir, 'data', 'investigation-log.json');
	await writeFile(log, JSON.stringify({ project: 'p', cycles: [] }));
	const cycle = (methodology: Record<string, unknown>) => ({ cycle: 1, focus: 'f', methodology });
	await call('write_file', { path: 'data/investigation-log.json', content: JSON.stringify({ project: 'p', cycles: [cycle({ techniques: ['registry search'], tools: ['fetch'], search_queries: ['q'] })] }) });
	assert.deepEqual(JSON.parse(await readFile(log, 'utf8')).cycles[0].methodology, { techniques_used: ['registry search'], tools_used: ['fetch'], search_queries: ['q'] });
	await call('write_file', { path: 'data/investigation-log.json', content: JSON.stringify({ project: 'p', cycles: [cycle({ techniques: ['alias'], techniques_used: ['right'], tools_used: [] })] }) });
	assert.deepEqual(JSON.parse(await readFile(log, 'utf8')).cycles[0].methodology, { techniques: ['alias'], techniques_used: ['right'], tools_used: [] }, 'a key the model got right is never overwritten');
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

test('managed findings and evidence reject anonymous rows before creating or changing a file', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator');
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	for (const [name, field, id] of [['findings.json', 'findings', 'F1'], ['evidence-bundle.json', 'items', 'E1']]) {
		const path = `data/${name}`;
		const file = join(caseDir, path);
		const write = (rows: unknown[]) => call('write_file', { path, content: JSON.stringify({ project: 'p', [field]: rows }) });
		await assert.rejects(write([{ notes: 'anonymous' }]), /id/i);
		assert.equal(existsSync(file), false);
		const before = JSON.stringify({ project: 'p', [field]: [{ id }] });
		await writeFile(file, before);
		await assert.rejects(write([{ id }, { items: [], schema_version: '1.0' }]), /id/i);
		assert.equal(await readFile(file, 'utf8'), before);
		await assert.rejects(write([null]), /id|object/i);
		assert.equal(await readFile(file, 'utf8'), before);
	}
});

test('an explicit edit can remove legacy anonymous evidence without changing keyed entries or duplicating other anonymous entries', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator');
	const path = 'data/evidence-bundle.json';
	const file = join(caseDir, path);
	const bad = { notes: 'remove me' };
	const other = { notes: 'keep me' };
	const original = { project: 'p', items: [{ id: 'E1', notes: 'keyed' }, bad, other, null, 17] };
	await writeFile(file, JSON.stringify(original, null, 2));
	await call('write_file', { path, content: JSON.stringify({ items: [{ id: 'E2', notes: 'new' }] }) });
	const before = JSON.parse(await readFile(file, 'utf8'));
	assert.deepEqual(before.items, [...original.items, { id: 'E2', notes: 'new' }]);
	await call('write_file', { path, content: JSON.stringify({ items: [{ id: 'E2', notes: 'new' }] }) });
	assert.deepEqual(JSON.parse(await readFile(file, 'utf8')).items, before.items);
	const oldText = JSON.stringify(before, null, 2);
	const after = { ...before, items: before.items.filter((item: { notes?: string } | null) => item?.notes !== 'remove me' && item !== null && item !== 17) };
	await call('edit_file', { path, old_text: oldText, new_text: JSON.stringify(after, null, 2) });
	assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), after);
	const text = await readFile(file, 'utf8');
	const changedMetadata = { ...after, project: 'changed', items: after.items.filter((item: { notes: string }) => item.notes !== 'keep me') };
	await call('edit_file', { path, old_text: text, new_text: JSON.stringify(changedMetadata, null, 2) }).catch(() => {});
	assert.ok(JSON.parse(await readFile(file, 'utf8')).items.some((item: { notes: string }) => item.notes === 'keep me'), 'changing metadata must not authorize anonymous removal');
	await writeFile(file, text);
	const changedKeyed = { ...after, items: after.items.filter((item: { id?: string }) => item.id !== 'E1') };
	await call('edit_file', { path, old_text: text, new_text: JSON.stringify(changedKeyed, null, 2) }).catch(() => {});
	assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), after);
	const factChecker = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: 'fact-checker' } }).map((tool) => [tool.name, tool]));
	const edit = factChecker.get('edit_file') as ToolDefinition;
	const removeOther = { ...after, items: after.items.filter((item: { notes: string }) => item.notes !== 'keep me') };
	await assert.rejects(edit.execute('call', { path, old_text: text, new_text: JSON.stringify(removeOther, null, 2) }, undefined, undefined, undefined as never), /did not create|refus/i);
	assert.equal(await readFile(file, 'utf8'), text);
	const forged = { ...after, items: after.items.filter((item: { notes: string }) => item.notes !== 'keep me').map((item: { id?: string; notes: string }) => item.id === 'E1' ? { ...item, notes: 'changed' } : item) };
	await assert.rejects(edit.execute('call', { path, old_text: text, new_text: JSON.stringify(forged, null, 2) }, undefined, undefined, undefined as never), /did not create|refus/i);
	assert.equal(await readFile(file, 'utf8'), text);
});

// Parallel direction workers are separate processes (launcher.ts processLaunch, up to MAX_CONCURRENT_WORKERS): both merged into the file they had
// read, so one worker's finding could vanish ('parallel explicitly scoped tasks' failed 2 of 5 runs on dcb40e08, D2-F1 lost).
test('direction workers in separate processes writing at once lose no entry, and each still gets its reply', async (t) => {
	const { caseDir } = await fixture(t);
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ project: 'p', findings: [] }));
	const worker = `
		import { caseFileTools } from ${JSON.stringify(new URL('./case-files.ts', import.meta.url).href)};
		const [caseDir, tag, count] = process.argv.slice(1);
		const write = caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: 'investigator', SPOTLIGHT_DIRECTION: tag } }).find((tool) => tool.name === 'write_file');
		for (let n = 1; n <= Number(count); n++) await write.execute('call', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings: [{ id: tag + '-F' + n, claim: tag + ' ' + n }] }) });
	`;
	const run = (tag: string) => new Promise<void>((done, failed) => execFile(process.execPath, ['--input-type=module', '-e', worker, caseDir, tag, '40'], (error) => (error ? failed(error) : done())));
	await Promise.all([run('D1'), run('D2')]);
	const ids = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8')).findings.map((each: { id: string }) => each.id);
	assert.equal(ids.length, 80, `kept ${ids.length} of 80`);
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
	const { call, tools } = await fixture(t);
	assert.match(String(tools.get('read_file')?.description), /agents\/references\//);

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
	const { caseDir, call, tools } = await fixture(t, undefined, 'fact-checker');
	const data = (name: string) => join(caseDir, 'data', name);
	await writeFile(data('findings.json'), JSON.stringify({ project: 'p', findings: ['one', 'two', 'three'].map((claim, i) => ({ id: `F${i + 1}`, claim })) }));
	const claim = (n: number, claim_text: string, verdict = 'unverified') => ({ finding_id: `F${n}`, claim_text, verdict, confidence: 'low', evidence_for: [], evidence_against: [] });
	// The model-visible contract must explain the merge before its first write.
	assert.match(tools.get('write_file')!.description, /data\/fact-check.json[\s\S]*finding_id[\s\S]*omitted entries stay/);
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

	for (const claim_text of [undefined, null, '', '  ']) {
		await assert.rejects(write({ finding_id: 'F3', claim_text, verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }), /claim.*text|claim_text/i);
		assert.equal(await readFile(data('fact-check.json'), 'utf8'), before);
	}
	const legacy = JSON.parse(before);
	delete legacy.claims[0].claim_text;
	await writeFile(data('fact-check.json'), JSON.stringify(legacy));
	await write(claim(3, 'three'));
	assert.deepEqual(JSON.parse(await readFile(data('fact-check.json'), 'utf8')).claims[0], legacy.claims[0]);
	await writeFile(data('fact-check.json'), before);

	await assert.rejects(write({ finding_id: 'F3', withdrawn: '' }), /withdrawing F3 from data\/fact-check\.json needs a reason/);
	await write({ finding_id: 'F3', withdrawn: 'checked twice by mistake' });
	const file = JSON.parse(await readFile(data('fact-check.json'), 'utf8'));
	assert.deepEqual(file.claims.map((c: { finding_id: string }) => c.finding_id), ['F1', 'F2']);
	assert.deepEqual(file.withdrawn, [{ finding_id: 'F3', reason: 'checked twice by mistake' }]);
});

test('case-root aliases in artifact paths normalize only existing confined files', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-case-paths-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const python = process.env.SPOTLIGHT_PYTHON ?? 'python3';
	execFileSync(python, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--clean']);
	const tools = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: 'investigator' } }).map((tool) => [tool.name, tool]));
	const write = async (path: string, content: object) => (tools.get('write_file') as ToolDefinition).execute('call', { path, content: JSON.stringify(content) }, undefined, undefined, undefined as never);
	const findingsPath = join(caseDir, 'data', 'findings.json');
	const original = JSON.parse(await readFile(findingsPath, 'utf8'));
	const finding = structuredClone(original.findings[0]);
	finding.sources[0].local_file = '{CASE_DIR}/research/primary-record.txt';
	finding.sources[0].description = 'Literal {CASE_DIR}/research/primary-record.txt is context.';
	await write('data/findings.json', { ...original, findings: [finding] });
	const saved = JSON.parse(await readFile(findingsPath, 'utf8'));
	assert.equal(saved.findings[0].sources[0].local_file, 'research/primary-record.txt');
	assert.equal(saved.findings[0].sources[0].description, finding.sources[0].description);
	execFileSync(python, [join(CHECKOUT_ROOT, 'scripts', 'validate-case.py'), caseDir]);
	execFileSync(python, [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
	await writeFile(join(root, 'outside.txt'), 'outside');
	await symlink(join(root, 'outside.txt'), join(caseDir, 'research', 'escape.txt'));
	await symlink(join(CHECKOUT_ROOT, 'schemas', 'findings.schema.json'), join(caseDir, 'research', 'schema-link.json'));
	for (const path of ['{CASE_DIR}/research/missing.txt', '{CASE_DIR}/../outside.txt', '{CASE_DIR}/research/escape.txt', '{CASE_DIR}/research/schema-link.json', join(root, 'outside.txt')]) {
		const invalid = structuredClone(finding); invalid.sources[0].local_file = path;
		let refused = false;
		try { await write('data/findings.json', { findings: [invalid] }); } catch { refused = true; }
		const actual = JSON.parse(await readFile(findingsPath, 'utf8')).findings[0].sources[0].local_file;
		assert.equal(actual, refused ? 'research/primary-record.txt' : path, `invalid path must not become another file: ${path}`);
		await write('data/findings.json', { findings: [finding] });
	}
	const restricted = new Map(caseFileTools({ caseDir, writeRoot: caseDir, scope: { denyReads: ['research/primary-record.txt'] }, env: { SPOTLIGHT_ROLE: 'investigator' } }).map((tool) => [tool.name, tool]));
	await (restricted.get('write_file') as ToolDefinition).execute('call', { path: 'data/findings.json', content: JSON.stringify({ findings: [finding] }) }, undefined, undefined, undefined as never);
	assert.equal(JSON.parse(await readFile(findingsPath, 'utf8')).findings[0].sources[0].local_file, '{CASE_DIR}/research/primary-record.txt', 'a hidden file is not canonicalized');
	await write('data/findings.json', { findings: [finding] });
	await mkdir(join(caseDir, 'research', 'derivative'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'derivative', 'page.txt'), 'page');
	await write('data/evidence-bundle.json', { items: [{ id: 'E99', raw_path: '{CASE_DIR}/research/primary-record.txt', screenshot_path: '{CASE_DIR}/research/corroboration.txt', downloaded_document_path: '{CASE_DIR}/research/primary-record.txt', text_derivatives: [{ path: '{CASE_DIR}/research/derivative/page.txt' }], query_or_task: 'Do not alter {CASE_DIR}/research/primary-record.txt' }] });
	const item = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).items.find((entry: { id: string }) => entry.id === 'E99');
	assert.equal(item.raw_path, 'research/primary-record.txt');
	assert.equal(item.screenshot_path, 'research/corroboration.txt');
	assert.equal(item.downloaded_document_path, 'research/primary-record.txt');
	assert.equal(item.text_derivatives[0].path, 'research/derivative/page.txt');
	assert.equal(item.query_or_task, 'Do not alter {CASE_DIR}/research/primary-record.txt');
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
	registerFixtureCapture(join(root, 'harness'), caseDir);
	const tools = (role: string) => {
		const byName = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: role, SPOTLIGHT_HARNESS_DATA: join(root, 'harness') } }).map((tool) => [tool.name, tool]));
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

test('fact-check evidence accepts readable case-root aliases without changing claim text', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'fact-checker');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'source.txt'), 'source');
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const claim = { claim_text: 'Literal {CASE_DIR}/research/source.txt', verdict: 'unverified', evidence_for: [{ local_file: '{CASE_DIR}/research/source.txt', source_ref: { path: '{CASE_DIR}/research/source.txt', line_start: 1, line_end: 1 } }], evidence_against: [{ local_file: '{CASE_DIR}/research/source.txt' }] };
	await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', claims: [claim] }) });
	const saved = JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8')).claims[0];
	assert.equal(saved.claim_text, claim.claim_text);
	assert.equal(saved.evidence_for[0].local_file, 'research/source.txt');
	assert.equal(saved.evidence_for[0].source_ref.path, 'research/source.txt');
	assert.equal(saved.evidence_against[0].local_file, 'research/source.txt');
	for (const invalid of ['{CASE_DIR}/research/missing.txt', '{CASE_DIR}/../outside', '{CASE_DIR}/research/schema-link.json']) {
		if (invalid.endsWith('schema-link.json')) await symlink(join(CHECKOUT_ROOT, 'schemas', 'findings.schema.json'), join(caseDir, 'research', 'schema-link.json'));
		await rm(join(caseDir, 'data', 'fact-check.json'), { force: true });
		try { await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', claims: [{ ...claim, evidence_for: [{ local_file: invalid }] }] }) }); } catch (error) { assert.match(String(error), /refused|not found/i); continue; }
		assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8')).claims[0].evidence_for[0].local_file, invalid);
	}
	await rm(join(caseDir, 'data', 'fact-check.json'), { force: true });
	const restricted = new Map(caseFileTools({ caseDir, writeRoot: caseDir, scope: { denyReads: ['research/source.txt'] }, env: { SPOTLIGHT_ROLE: 'fact-checker' } }).map((tool) => [tool.name, tool]));
	await (restricted.get('write_file') as ToolDefinition).execute('call', { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', claims: [claim] }) }, undefined, undefined, undefined as never);
	assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'fact-check.json'), 'utf8')).claims[0].evidence_for[0].local_file, '{CASE_DIR}/research/source.txt');
});

test('normalized fact-check paths pass strict validation of the stored claim', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-fc-paths-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const python = process.env.SPOTLIGHT_PYTHON ?? 'python3';
	execFileSync(python, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--clean', '--activated']);
	const file = join(caseDir, 'data', 'fact-check.json');
	const document = JSON.parse(await readFile(file, 'utf8'));
	const item = document.claims[0].evidence_for[0];
	item.local_file = `{CASE_DIR}/${item.local_file}`;
	item.source_ref.path = `{CASE_DIR}/${item.source_ref.path}`;
	registerFixtureCapture(join(root, 'harness'), caseDir);
	const tools = new Map(caseFileTools({ caseDir, writeRoot: caseDir, env: { SPOTLIGHT_ROLE: 'fact-checker', SPOTLIGHT_HARNESS_DATA: join(root, 'harness') } }).map((tool) => [tool.name, tool]));
	await (tools.get('write_file') as ToolDefinition).execute('call', { path: 'data/fact-check.json', content: JSON.stringify(document) }, undefined, undefined, undefined as never);
	execFileSync(python, [join(CHECKOUT_ROOT, 'scripts', 'validate-fact-check.py'), caseDir]);
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
	// fc-stall item 7: a grep of a schema the task names was refused as outside the case.
	assert.match(await call('grep_files', { pattern: '"not_checkable"', path: 'schemas/fact-check.schema.json' }), /^schemas\/fact-check\.schema\.json:\d+:.*not_checkable/);
	await assert.rejects(call('grep_files', { pattern: 'x', path: 'schemas/' }), /read_file.*schemas\/<name>\.schema\.json/, 'only one schema file, not the folder');
	await assert.rejects(call('list_files', { pattern: '../schemas/*' }), /read_file.*schemas\/<name>\.schema\.json/);
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

	const own = await fixture(t, {reads:['data/harness-checks/analyst/'],writes:['data/proposal.json']});
	await mkdir(join(own.caseDir, 'data/harness-checks/analyst'), {recursive:true});
	await mkdir(join(own.caseDir, 'data/harness-checks/fact-checker'), {recursive:true});
	await writeFile(join(own.caseDir, 'data/harness-checks/analyst/report.txt'), 'own diagnostic');
	await writeFile(join(own.caseDir, 'data/harness-checks/fact-checker/report.txt'), 'other diagnostic');
	assert.equal(await own.call('read_file',{path:'data/harness-checks/analyst/report.txt'}),'own diagnostic');
	await assert.rejects(own.call('read_file',{path:'data/harness-checks/fact-checker/report.txt'}),/may read/);
	await assert.rejects(own.call('write_file',{path:'data/harness-checks/analyst/report.txt',content:'changed'}),/may write/);
	await symlink(join(own.caseDir,'data/harness-checks/fact-checker/report.txt'),join(own.caseDir,'data/harness-checks/analyst/link.txt'));
	await assert.rejects(own.call('read_file',{path:'data/harness-checks/analyst/link.txt'}),/may read/);

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
	// fc-stall item 10: broken JSON is refused with where it breaks.
	await assert.rejects(call('write_file', { path: 'data/methodology.json', content: '{\n  "a": 1\n  "b": 2\n}' }), /must stay one JSON object; the file is unchanged\. [^\n]*position \d+[^\n]* near "\{\\n  \\"a\\": 1\\n  \\"b\\": 2\\n\}"\. Read it/);
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
	const beforeLeadEdit = await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8');
	await assert.rejects(call('edit_file', { path: 'data/evidence-bundle.json', old_text: 'page.md.raw', new_text: 'page.md' }), /distilled leads as evidence for E1 \(raw_path: research\/page\.md\)/);
	assert.equal(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'), beforeLeadEdit);
});

// The paperwork repairs are for the local tiers: on frontier the same writes get what they always got.
// Each repair also leaves alone what it cannot prove (B15, B16, B17, B14, B23).
test('code repairs only worker paperwork it can prove, on local tiers, and says so; frontier is unchanged', async (t) => {
	for (const tier of ['12b', 'frontier']) await t.test(tier, async (t) => {
		const harness = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-receipts-')));
		t.after(() => rm(harness, { recursive: true, force: true }));
		const local = tier === '12b';
		const env = { SPOTLIGHT_MODEL_TIER: tier, SPOTLIGHT_HARNESS_DATA: harness };
		const data = (caseDir: string, name: string) => join(caseDir, 'data', name);
		const sha = (text: string) => createHash('sha256').update(text).digest('hex');

		await t.test('evidence receipts', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'investigator', env);
			await mkdir(join(caseDir, 'research'));
			await writeFile(join(caseDir, 'research', 'page.md'), '# RLM-distilled leads from https://example.org/\n- a lead\n');
			await writeFile(join(caseDir, 'research', 'page.md.raw'), 'The page as fetched.\n');
			registerCapture(harness, caseDir, 'research/page.md.raw');
			const write = (item: object) => call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ id: 'E1', ...item }] }) });
			if (local) assert.match(await write({ raw_path: 'research/page.md' }), /moved E1's raw_path[\s\S]*filled E1's sha256 and accessed/);
			else await assert.rejects(write({ raw_path: 'research/page.md' }), /distilled leads as evidence for E1/);
			const reply = await write({ raw_path: 'research/page.md.raw', accessed: '2026-01-01T00:00:00Z' });
			const item = JSON.parse(await readFile(data(caseDir, 'evidence-bundle.json'), 'utf8')).items[0];
			assert.equal(item.accessed, local ? '2026-07-16T12:30:00Z' : '2026-01-01T00:00:00Z');
			assert.equal(item.sha256, local ? sha('The page as fetched.\n') : undefined);
			if (!local) assert.doesNotMatch(reply, /Code (filled|moved)/);
		});

		await t.test('suggestion inputs', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'investigator', env);
			const suggestions = { schema_version: '1.0', generated_at: '2026-11-04T10:00:00Z', inputs: { 'data/leads.json': 'typed' }, clues: [], directions: [] };
			const write = () => call('write_file', { path: 'data/research-suggestions.json', content: JSON.stringify(suggestions) });
			const inputs = async () => JSON.parse(await readFile(data(caseDir, 'research-suggestions.json'), 'utf8')).inputs;
			await writeFile(data(caseDir, 'leads.json'), '{"leads": []}\n');
			await writeFile(data(caseDir, 'clues.json'), '{"clues":  []}');
			// One input is missing: nothing to hash, so nothing is claimed.
			assert.doesNotMatch(await write(), /Code/);
			assert.deepEqual(await inputs(), suggestions.inputs);
			await writeFile(data(caseDir, 'research-triage.json'), '{}\n');
			const reply = await write();
			assert.deepEqual(await inputs(), local ? { 'data/leads.json': sha('{"leads": []}\n'), 'data/clues.json': sha('{"clues":  []}'), 'data/research-triage.json': sha('{}\n') } : suggestions.inputs);
			assert.equal(/Code set inputs in data\/research-suggestions\.json/.test(reply), local);
		});

		await t.test('fact-check verdicts', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'fact-checker', env);
			await writeFile(data(caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: ['one', 'two', 'three'].map((claim, i) => ({ id: `F${i + 1}`, claim })) }));
			const claim = (n: number, claim_text: string, verdict = 'unverified') => ({ finding_id: `F${n}`, claim_text, verdict, confidence: 'low', evidence_for: [], evidence_against: [] });
			const clash = claim(1, 'one', 'disputed');
			const noId = { claim_text: 'three', verdict: 'unverified' };
			const reply = await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ project: 'p', claims: [claim(1, 'one')], verdicts: [claim(2, 'two'), clash, noId] }) });
			const file = JSON.parse(await readFile(data(caseDir, 'fact-check.json'), 'utf8'));
			assert.deepEqual(file.claims, local ? [claim(1, 'one'), claim(2, 'two')] : [claim(1, 'one')]);
			assert.deepEqual(file.verdicts, local ? [clash, noId] : [claim(2, 'two'), clash, noId], 'a clashing or anonymous row stays where it was written');
			assert.equal(/Code moved F2 from verdicts to claims/.test(reply), local);
			// Claims alone clear what stayed: a merge keeps the file's other fields, and a row naming no finding could never be cleared otherwise.
			const next = await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ project: 'p', claims: [claim(3, 'three')] }) });
			assert.deepEqual(JSON.parse(await readFile(data(caseDir, 'fact-check.json'), 'utf8')).verdicts, local ? undefined : [claim(2, 'two'), clash, noId]);
			assert.equal(/Code removed 2 old row\(s\) from verdicts/.test(next), local);
		});

		await t.test('gate summary', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'analyst', env);
			const finding = (n: number, confidence: string, sources: number) => ({ id: `F${n}`, claim: `claim ${n}`, confidence, sources: Array(sources).fill({ url: 'https://example.org/' }) });
			await writeFile(data(caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [finding(1, 'high', 2), finding(2, 'low', 1)] }));
			await writeFile(data(caseDir, 'fact-check.json'), JSON.stringify({ project: 'p', claims: [{ finding_id: 'F1', verdict: 'verified' }] }));
			const written = { title: 'A title', summary: 'The narrative.', key_conclusions: ['One.'], findings: [{ id: 'F1', claim_text: 'typed', fact_check_verdict: 'false' }] };
			const write = (summary: object) => call('write_file', { path: 'data/summary.json', content: JSON.stringify(summary) });
			const file = async () => JSON.parse(await readFile(data(caseDir, 'summary.json'), 'utf8'));
			// A finding with no verdict yet: nothing proves its row, so the file stays as written.
			assert.doesNotMatch(await write(written), /Code/);
			assert.deepEqual(await file(), written);
			await writeFile(data(caseDir, 'fact-check.json'), JSON.stringify({ project: 'p', claims: [{ finding_id: 'F1', verdict: 'verified' }, { finding_id: 'F2', verdict: 'unverified' }] }));
			const reply = await write(written);
			const summary = await file();
			if (!local) {
				assert.deepEqual(summary, written);
				assert.doesNotMatch(reply, /Code/);
				return;
			}
			assert.match(reply, /Code filled .*findings.* in data\/summary\.json from the findings and the fact-check/);
			assert.deepEqual([summary.title, summary.summary, summary.key_conclusions], [written.title, written.summary, written.key_conclusions], 'the prose stays the analyst\'s');
			assert.deepEqual([summary.schema_version, summary.project, summary.status, summary.verified_findings], ['1.0', 'p', 'pending_review', 1]);
			assert.ok(Number.isFinite(Date.parse(summary.generated_at)) && summary.generated_at.endsWith('Z'));
			assert.deepEqual(summary.findings, [{ id: 'F1', claim: 'claim 1', confidence: 'high', fact_check_verdict: 'verified', source_count: 2 }, { id: 'F2', claim: 'claim 2', confidence: 'low', fact_check_verdict: 'unverified', source_count: 1 }]);
			// With no narrative, the required fields state only the counts, and the reply says to replace them.
			const plain = await write({ status: 'draft' });
			const counted = await file();
			assert.deepEqual([counted.title, counted.summary, counted.status], ['1 verified finding', '2 findings: 1 verified, 1 unverified.', 'draft']);
			assert.match(plain, /Code wrote a plain count as title and summary/);
			// Nothing qualified: no verdict is owed, and the file still completes.
			await writeFile(data(caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [], no_qualifying_findings: { reason: 'none' } }));
			await unlink(data(caseDir, 'fact-check.json'));
			await write({});
			const empty = await file();
			assert.deepEqual([empty.title, empty.summary, empty.verified_findings, empty.findings], ['0 verified findings', '0 findings.', 0, []]);
		});

		await t.test('research refs', async (t) => {
			// D10 (K3, K4, K5, qwen-65k.md): findings with no direction link held the case at Investigation.
			const plan = (...ids: string[]) => JSON.stringify({ research_plan: { topics: [] }, investigation_plan: ids.map((direction_id) => ({ direction: direction_id, direction_id })) });
			const finding = (id: string, research_refs?: string[]) => ({ id, claim: id, ...(research_refs ? { research_refs } : {}) });
			const refs = async (caseDir: string) => Object.fromEntries(JSON.parse(await readFile(data(caseDir, 'findings.json'), 'utf8')).findings.map((row: { id: string; research_refs?: string[] }) => [row.id, row.research_refs]));
			const write = (call: (name: string, params: Record<string, unknown>) => Promise<string>, ...findings: object[]) => call('write_file', { path: 'data/findings.json', content: JSON.stringify({ project: 'p', findings }) });

			const one = await fixture(t, undefined, 'investigator', env);
			await writeFile(data(one.caseDir, 'methodology.json'), plan('d1'));
			await writeFile(data(one.caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [] }));
			const reply = await write(one.call, finding('F1'), finding('F2', ['d1']), finding('F3', ['clue:k1']));
			assert.deepEqual(await refs(one.caseDir), local ? { F1: ['direction:d1'], F2: ['direction:d1'], F3: ['clue:k1', 'direction:d1'] } : { F1: undefined, F2: ['d1'], F3: ['clue:k1'] });
			assert.equal(/Code set research_refs of F1, F2, F3 to direction:d1/.test(reply), local);

			const split = await fixture(t, undefined, 'investigator', { ...env, SPOTLIGHT_DIRECTION: 'D2' });
			await writeFile(data(split.caseDir, 'methodology.json'), plan('d1', 'd2'));
			await writeFile(data(split.caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [finding('D1-F1')] }));
			await write(split.call, finding('D2-F1'));
			assert.deepEqual(await refs(split.caseDir), local ? { 'D1-F1': undefined, 'D2-F1': ['direction:d2'] } : { 'D1-F1': undefined, 'D2-F1': undefined }, 'only this worker\'s direction, only its findings');

			// After an amendment the plan is renumbered: findings not in this write and ids of an older tag prove nothing.
			const renumbered = await fixture(t, undefined, 'investigator', env);
			await writeFile(data(renumbered.caseDir, 'methodology.json'), plan('d2'));
			await writeFile(data(renumbered.caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [finding('D1-F1'), finding('F9')] }));
			await write(renumbered.call, finding('D1-F2'), finding('F1'));
			assert.deepEqual(await refs(renumbered.caseDir), local ? { 'D1-F1': undefined, F9: undefined, 'D1-F2': undefined, F1: ['direction:d2'] } : { 'D1-F1': undefined, F9: undefined, 'D1-F2': undefined, F1: undefined });

			// Two directions and no assigned one, or no research plan: nothing proves a direction.
			const both = await fixture(t, undefined, 'investigator', env);
			await writeFile(data(both.caseDir, 'methodology.json'), plan('d1', 'd2'));
			await writeFile(data(both.caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [] }));
			assert.doesNotMatch(await write(both.call, finding('F1')), /Code set/);
			const none = await fixture(t, undefined, 'investigator', env);
			await writeFile(data(none.caseDir, 'methodology.json'), JSON.stringify({ investigation_plan: [{ direction: 'd1', direction_id: 'd1' }] }));
			await writeFile(data(none.caseDir, 'findings.json'), JSON.stringify({ project: 'p', findings: [] }));
			assert.doesNotMatch(await write(none.call, finding('F1')), /Code set/);
		});

		// T8 on 7656d7ba: the analyst's headlines, from neighbouring lines, stood false beside the verified pill.
		await t.test('report headlines', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'analyst', env);
			const { draft, findings } = JSON.parse(await readFile(join(CHECKOUT_ROOT, 'tests', 'fixtures', 'report-draft-7656d7ba.json'), 'utf8'));
			await writeFile(data(caseDir, 'findings.json'), JSON.stringify({ findings }));
			const content = JSON.stringify(draft);
			await call('write_file', { path: 'data/report-draft.json', content });
			const written = await readFile(data(caseDir, 'report-draft.json'), 'utf8');
			if (!local) return assert.equal(written, content, 'frontier keeps the analyst\'s report byte for byte');
			const { finding_treatments: treatments } = JSON.parse(written);
			assert.equal(treatments.length, findings.length);
			for (const treatment of treatments) {
				assert.equal(treatment.headline, findings.find((finding: { id: string }) => finding.id === treatment.finding_id).claim);
				assert.equal(treatment.summary, '');
				assert.equal(treatment.why_it_matters, '');
			}
			assert.doesNotMatch(written, /Library Freedom Project|coordinates localization|OONI partnerships/);
		});

		await t.test('report draft', async (t) => {
			const { caseDir, call } = await fixture(t, undefined, 'analyst', env);
			await writeFile(data(caseDir, 'source-expressions.json'), JSON.stringify({ expressions: [{ id: 'SX1' }, { id: 'SX2' }, { id: 'SX3' }] }));
			const selections = ['SX1', { id: 'SX2', text: 'typed', source_ref: 'x' }, { expression_id: 'SX3', text: 'typed' }, 'SX9', { id: 'SX1', expression_id: 'SX2' }];
			const draft = { schema_version: '1.0', model: 'lfm3', author: 'lfm3', finding_treatments: [{ finding_id: 'F1', quote_selections: selections }] };
			const reply = await call('write_file', { path: 'data/report-draft.json', content: JSON.stringify(draft) });
			const file = JSON.parse(await readFile(data(caseDir, 'report-draft.json'), 'utf8'));
			assert.equal(file.author, 'lfm3', 'an unknown field that is not model is the validator\'s to name');
			assert.equal(file.model, local ? undefined : 'lfm3');
			assert.deepEqual(file.finding_treatments[0].quote_selections, local ? [{ expression_id: 'SX1' }, { expression_id: 'SX2' }, { expression_id: 'SX3' }, 'SX9', { id: 'SX1', expression_id: 'SX2' }] : selections);
			assert.equal(/Code dropped the top-level model field/.test(reply), local);
			assert.equal(/Code set F1's quote_selections to SX1, SX2, SX3/.test(reply), local);
			// An edit gets the same repair.
			await call('write_file', { path: 'data/report-draft.json', content: '{"schema_version": "1.0"}' });
			const edited = await call('edit_file', { path: 'data/report-draft.json', old_text: '"1.0"', new_text: '"1.0", "model": "lfm3"' });
			assert.equal(JSON.parse(await readFile(data(caseDir, 'report-draft.json'), 'utf8')).model, local ? undefined : 'lfm3');
			assert.equal(/Code dropped the top-level model field/.test(edited), local);
		});
	});
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
	const data = await mkdtemp(join(tmpdir(), 'spotlight-pi-data-'));
	t.after(() => rm(data, { recursive: true, force: true }));
	const { caseDir, call } = await fixture(t, undefined, undefined, { SPOTLIGHT_HARNESS_DATA: data });
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const link = { finding_id: 'F1', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
		{ id: 'P2', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] },
		{ id: 'P9', expression_fingerprint: '9'.repeat(64), lifecycle_events: [{ event: 'activated' }, { event: 'superseded' }], finding_links: [link] },
		// Active and supporting, but another finding's passage.
		{ id: 'P3', expression_fingerprint: '3'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [{ ...link, finding_id: 'F2' }] },
	] }));
	const ref = { expression_id: 'P2', expression_fingerprint: 'e'.repeat(64), finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	// The cited item lists the fact-checker's own registered capture (r12-ingest-audit A3).
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'fc-filing.md'), 'Acme paid Doe.\n');
	registerCapture(data, caseDir, 'research/fc-filing.md');
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', created_by: 'fact-checker', raw_path: 'research/fc-filing.md' }] }));
	// Each item carries a case-local anchor (validate-case's rule, also checked at the write).
	const claim = (item: object, verdict = 'verified') => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict, evidence_for: [{ evidence_bundle_id: 'E1', local_file: 'research/fc-filing.md', ...item }] }] });

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_ref: ref }) }),
		(error: Error) => error.message.includes('has no source_expression_refs; the file is unchanged') && error.message.includes('F1 (the key is source_expression_refs, a list, not source_expression_ref): "source_expression_refs": ["P2"]'));
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);

	// 12B r3 ia on f43df365: a ref naming a passage never recorded went through.
	// A bare id is accepted only for an active passage supporting this finding:
	// unknown, superseded or another finding's passage is refused.
	for (const cited of ['SE-F1-TAX', 'P9', 'P3', { expression_id: 'P3' }, { ...ref, link_fingerprint: '0'.repeat(64) }]) {
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: [cited] }) }),
			(error: Error) => error.message.includes(`F1 (${JSON.stringify([cited])} is not an active passage supporting it): "source_expression_refs": ["P2"]`));
		await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: ['P2', cited] }) }), /unmatched source_expression_refs/);
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
		await mkdir(join(caseDir, 'research'));
		for (const name of ['a', 'b', 'c', 'd', 'e', 'z', 'fc-recheck-E1']) await writeFile(join(caseDir, 'research', `${name}.md.raw`), 'A capture.\n');
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
			// r12 seq 87 (r12-ingest-audit A2): the fact-checker appended FC-E1 for its
			// re-fetch without created_by, and its own later edits were refused as
			// another's item. Code stamps the item it appends as the fact-checker's.
			await call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ id: 'FC-E1', query_or_task: 'Fact-checker independent acquisition', raw_path: 'research/fc-recheck-E1.md.raw', extraction_confidence: 'high' }) });
			const appended = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).items.find((item: { id: string }) => item.id === 'FC-E1');
			assert.equal(appended.created_by, 'fact-checker', 'the appended item is stamped as the fact-checker\'s');
			await call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ ...appended, extraction_confidence: 'medium' }) });
		} else {
			await call('write_file', { path: 'data/evidence-bundle.json', content: moved });
			await call('write_file', { path: 'data/evidence-bundle.json', content: bundle(investigatorItem, kept) });
			// B24 keeps receipt fields only for an item whose capture is a harness fetch; E1's file has no receipt,
			// so the investigator's own change to its sha256 and accessed stands, as before.
			const changed = await call('write_file', { path: 'data/evidence-bundle.json', content: bundle({ ...investigatorItem, sha256: 'b'.repeat(64), accessed: '2026-11-04T10:44:37Z' }, kept) });
			const e1 = JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).items.find((item: { id: string }) => item.id === 'E1');
			assert.deepEqual([e1.sha256, e1.accessed], ['b'.repeat(64), '2026-11-04T10:44:37Z']);
			assert.doesNotMatch(changed, /Code kept/);
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
	const data = await mkdtemp(join(tmpdir(), 'spotlight-pi-data-'));
	t.after(() => rm(data, { recursive: true, force: true }));
	const { caseDir, call } = await fixture(t, undefined, undefined, { SPOTLIGHT_HARNESS_DATA: data });
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const link = { finding_id: 'F2', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
		{ id: 'P1', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] },
	] }));
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'fsf_board.md'), 'The board has five members.\n');
	// The cited capture is the fact-checker's own, with its fetch receipt (r12-ingest-audit A3).
	registerCapture(data, caseDir, 'research/fsf_board.md');
	const evidence = (path: string) => ({ access_method: 'full_text', local_file: path, source_ref: { path, line_start: 1, line_end: 1 }, source_expression_refs: ['P1'] });
	const factCheck = (item: object, verdict = 'verified') => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F2', verdict, evidence_for: [item] }] }, null, 2);

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: factCheck(evidence('research/fsf_leadership_browse.md')) }),
		(error: Error) => error.message === 'refused: data/fact-check.json cites files that are not in the case folder; the file is unchanged.\nF2: research/fsf_leadership_browse.md\nCite only a file you saved in the case (list_files shows them). If no saved source supports the finding, set the verdict to unverified.');
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: factCheck({ ...evidence('research/fsf_board.md'), source_ref: { path: '../outside/x.md' } }) }), /\nF2: \.\.\/outside\/x\.md\n/);

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

// fc-stall item 8 (incr-test3 sp98 seq 35): a fact-check write that added no
// claim read "wrote data/fact-check.json" while the count stayed at 3 of 22.
test('a fact-check write that adds no claim says so and why', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'fact-checker');
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ project: 'p', findings: [{ id: 'F1', claim: 'One.' }, { id: 'F2', claim: 'Two.' }] }));
	const claim = (id: string) => ({ project: 'p', claims: [{ finding_id: id, claim_text: 'One.', verdict: 'unverified', confidence: 'low', evidence_for: [], evidence_against: [] }] });
	const first = await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify(claim('F1')) });
	assert.doesNotMatch(first, /0 claims added/);
	assert.match(await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify(claim('F1')) }), /^wrote data\/fact-check\.json \(0 claims added: F1 already had a claim, which this write replaced\)/);
	assert.match(await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify(claim('F9')) }), /0 claims added: F9 is not a finding id in data\/findings\.json/);
});

// arms on 8699f11d (r5-spectator): a verified claim with no finding_id was
// refused as "no passage on record supports undefined".
test('a verified claim with no finding_id is told to name its finding', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'fact-checker');
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const content = JSON.stringify({ schema_version: '1.1', project: 'p', claims: [{ claim_text: 'One.', verdict: 'verified', confidence: 'high', evidence_for: [], evidence_against: [] }] });
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content }), (error: Error) => error.message.includes('a verified claim has no finding_id; give it the id of the finding it checks') && !error.message.includes('undefined'));
});

// The analyst contract limits case inputs to approved findings and their
// verification sources; task wording cannot grant access to other case files.
test('the analyst reads canonical inputs and refuses the brief and methodology', async (t) => {
	const reads = loadComposition().analyst.reads;
	const { caseDir, call } = await fixture(t, { reads }, 'analyst');
	await writeFile(join(caseDir, 'data', 'findings.json'), '{"findings": []}');
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the company?');
	await writeFile(join(caseDir, 'data', 'methodology.json'), '{"directions": []}');
	assert.equal(await call('read_file', { path: 'data/findings.json' }), '{"findings": []}');
	for (const path of ['brief-directions.txt', 'data/methodology.json']) {
		await assert.rejects(call('read_file', { path }), /outside the case files this agent may read/);
	}
});

// Replay check on 8699f11d: refusals with no next step. A schema refusal names
// the parameters, a missing file points at list_files, a literal-text grep says
// how to escape, and hash_files takes one path as a string.
test('refusals name the next step, and hash_files takes one path as a string', async (t) => {
	const { caseDir, call } = await fixture(t);
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'a.md'), 'x');
	await assert.rejects(call('write_file', { content: 'x' }), /write_file: arguments outside the schema \([^)]*\)\. write_file takes path, content; call it again with those\./);
	await assert.rejects(call('read_file', { path: 'research/missing.md' }), /not found: research\/missing\.md\. Call list_files with a pattern/);
	await assert.rejects(call('grep_files', { pattern: '## [TARRY, Jason](https://x' }), /put a backslash before \[ \] \( \)/);
	assert.match(await call('hash_files', { paths: 'research/a.md' }), /research\/a\.md/);
	await writeFile(join(caseDir, 'data', 'findings.json'), '{"findings": []}');
	await assert.rejects(call('read_file', { path: 'data/findings.json', json_pointer: 'findings' }), /must start with '\/', for example \/findings\/0/);
	await assert.rejects(call('read_file', { path: 'data/findings.json', json_pointer: '/findings/3' }), /Read a shorter pointer to see the keys at that level\./);
	await assert.rejects(call('read_file', { path: '../outside.txt' }), /Give a path relative to the case folder, such as data\/findings\.json\./);
	await assert.rejects(call('write_file', { path: 'data/source-expressions.json', content: '{}' }), /Call source_expressions with action write and the passage proposal instead\./);
});

// Phase B (Gemma12 worker state, 31B live run r5): a positive verdict the
// write accepted was then refused by validate-case.py for want of a case-local
// evidence anchor. The write applies the same rule and names the next step.
test('a verified verdict with no case-local evidence anchor, or one anchored only in another worker\'s capture, is refused at the write, naming fetch or unverified', async (t) => {
	const data = await mkdtemp(join(tmpdir(), 'spotlight-pi-data-'));
	t.after(() => rm(data, { recursive: true, force: true }));
	const { caseDir, call } = await fixture(t, undefined, undefined, { SPOTLIGHT_HARNESS_DATA: data });
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	const link = { finding_id: 'F1', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [{ id: 'P1', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] }] }));
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'filing.md'), 'Acme paid Doe.\n');
	const claim = (item: object) => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict: 'verified', evidence_for: [item] }] });
	const cited = { source_ref: { path: 'research/filing.md', line_start: 1, line_end: 1 }, source_expression_refs: ['P1'] };
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim(cited) }),
		/F1 has a verified verdict with no case-local evidence anchor: no evidence_for item has an evidence_bundle_id, or an access_method \(full_text, open_access or archive_copy\)[\s\S]*Cite a capture you acquired[\s\S]*acquire an independent capture with fetch[\s\S]*set the verdict to unverified/);
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	// r12 (r12-ingest-audit A3): anchored, but only in the investigator's capture
	// (E1, the F1 shape). Refused, naming the fact-checker's step; once the
	// fact-checker's own capture of the file is its item, the same claim passes.
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', raw_path: 'research/filing.md' }] }));
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ ...cited, access_method: 'full_text', evidence_bundle_id: 'E1' }) }),
		/a verified verdict needs evidence the fact-checker acquired, and these cite only another worker's capture[\s\S]*\nF1: cite a capture you acquired with fetch \(save_as under research\/\), in your evidence item, or set the verdict to unverified\./);
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	// Codex review of fix11: created_by is anyone's to write. An item that says
	// fact-checker over the investigator's file (as an investigator can write
	// it) does not make it the fact-checker's acquisition.
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', raw_path: 'research/filing.md' }, { id: 'FC-E9', created_by: 'fact-checker', raw_path: 'research/filing.md' }] }));
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ ...cited, access_method: 'full_text', evidence_bundle_id: 'FC-E9' }) }), /a verified verdict needs evidence the fact-checker acquired/);
	await writeFile(join(caseDir, 'research', 'fc-filing.md'), 'Acme paid Doe.\n');
	registerCapture(data, caseDir, 'research/fc-filing.md');
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ id: 'E1', raw_path: 'research/filing.md' }, { id: 'FC-E1', created_by: 'fact-checker', raw_path: 'research/fc-filing.md' }] }));
	// Codex review of fix11: the fact-checker's item id with the quote pointed at
	// the investigator's file certifies nothing; the cited item must list the
	// very file quoted.
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ ...cited, access_method: 'full_text', evidence_bundle_id: 'FC-E1' }) }), /a verified verdict needs evidence the fact-checker acquired/);
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);
	// Codex re-review of fix11: an item naming two files (validate-fact-check.py
	// refuses it) is refused at the write, naming the one to keep.
	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ ...cited, source_ref: { path: 'research/fc-filing.md', line_start: 1, line_end: 1 }, local_file: 'research/filing.md', access_method: 'full_text', evidence_bundle_id: 'FC-E1' }) }),
		/an evidence item must name one file:\nF1 evidence_for\[0\]: local_file is research\/filing\.md but source_ref\.path is research\/fc-filing\.md; set local_file to research\/fc-filing\.md/);
	await call('write_file', { path: 'data/fact-check.json', content: claim({ ...cited, source_ref: { path: 'research/fc-filing.md', line_start: 1, line_end: 1 }, access_method: 'full_text', evidence_bundle_id: 'FC-E1' }) });
	await call('write_file', { path: 'data/fact-check.json', content: JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict: 'unverified', evidence_for: [{ ...cited, evidence_bundle_id: 'E1' }] }] }) });
});

// Case 275a35b6: an investigator's whole-row write dropped the claim_links record_finding had set, the correction
// could only add a row, and it invented one (example.com, research/evidence2.md) that took over a real id.
test('a local worker\'s write keeps the links code recorded, and a row with no capture on disk is refused by name', async (t) => {
	const { caseDir, call } = await fixture(t, undefined, 'investigator');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'eurostat_unemployment_article.md.raw'), 'Euro area unemployment at 6.5%\n');
	const links = [{ finding_id: 'D1-F1', claim_text: 'The rate is 6.5%.', support_type: 'direct' }];
	const real = { id: 'D1-E1', query_or_task: 'Fetch Eurostat unemployment article', acquisition_method: 'crawl4ai', source_url: 'https://ec.europa.eu/eurostat/web/products-euro-indicators/w/3-30102026-bp', accessed: '2026-11-06T02:10:16.713Z', raw_path: 'research/eurostat_unemployment_article.md.raw', extraction_confidence: 'high', human_verification_required: false };
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ items: [{ ...real, claim_links: links }] }));
	const stored = async () => JSON.parse(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')).items as Array<{ id: string; claim_links?: unknown }>;

	const reply = await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [real] }) });
	assert.deepEqual((await stored())[0].claim_links, links, 'the row keeps its links');
	assert.match(reply, /kept D1-E1's claim_links/);

	const fake = { id: 'D1-E2', query_or_task: 'Search for evidence related to D1', acquisition_method: 'crawl4ai', source_url: 'https://example.com/evidence2', accessed: '2026-11-06T00:00:00Z', raw_path: 'research/evidence2.md', claim_links: links };
	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [fake] }) }),
		(error: Error) => error.message.startsWith('refused: data/evidence-bundle.json item D1-E2 names research/evidence2.md') && error.message.includes('claim_links'));
	assert.deepEqual((await stored()).map((item) => item.id), ['D1-E1'], 'nothing was written');

	// Continuity: the route the refusal names works, and a stated empty list still removes links.
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...real, claim_links: [...links, { finding_id: 'D1-F2', claim_text: 'The 6.4% claim is wrong.', support_type: 'direct' }] }] }) });
	assert.equal(((await stored())[0].claim_links as unknown[]).length, 2);
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...real, claim_links: [] }] }) });
	assert.deepEqual((await stored())[0].claim_links, []);
});
