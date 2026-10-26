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
import { caseFileTools } from './case-files.ts';
import type { CaseScope } from './run.ts';

async function fixture(t: TestContext, scope?: CaseScope) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-files-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const outside = join(root, 'outside');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(outside);
	const tools = new Map(caseFileTools({ caseDir, writeRoot: caseDir, scope }).map((tool) => [tool.name, tool]));
	const call = async (name: string, params: Record<string, unknown>) => {
		const tool = tools.get(name) as ToolDefinition;
		const result = await tool.execute('call', params, undefined, undefined, undefined as never);
		return (result.content[0] as { text: string }).text;
	};
	return { caseDir, outside, call };
}

test('a write inside the case lands there and reads back', async (t) => {
	const { caseDir, call } = await fixture(t);

	await call('write_file', { path: 'research/notes/a.md', content: 'first' });
	await call('edit_file', { path: `${caseDir}/research/notes/a.md`, old_text: 'first', new_text: 'second' });

	assert.equal(await readFile(join(caseDir, 'research/notes/a.md'), 'utf8'), 'second');
	assert.match(await call('read_file', { path: 'research/notes/a.md' }), /second/);
	assert.match(await call('list_files', { pattern: 'research/**/*.md' }), /research\/notes\/a\.md/);
	assert.match(await call('grep_files', { pattern: 'sec.nd' }), /research\/notes\/a\.md:1:second/);
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
	assert.equal(await readFile(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8'), bundle);

	await call('write_file', { path: 'data/evidence-bundle.json', content: '{"items": []}' });
	await call('write_file', { path: 'research/list.json', content: '[]' });
});

// gold mozilla s0 a2 on a2ab7e20: the fact-checker wrote a verified claim with its ref under
// a misspelt key, the validator blocked the case, and the one correction was spent.
test('a verified claim citing no passage is refused, naming the refs on record, and kept', async (t) => {
	const { caseDir, call } = await fixture(t);
	const link = { finding_id: 'F1', relation: 'supports', finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	await writeFile(join(caseDir, 'data', 'source-expressions.json'), JSON.stringify({ expressions: [
		{ id: 'P2', expression_fingerprint: 'e'.repeat(64), lifecycle_events: [{ event: 'activated' }], finding_links: [link] },
		{ id: 'P9', expression_fingerprint: '9'.repeat(64), lifecycle_events: [{ event: 'activated' }, { event: 'superseded' }], finding_links: [link] },
	] }));
	const ref = { expression_id: 'P2', expression_fingerprint: 'e'.repeat(64), finding_fingerprint: 'f'.repeat(64), link_fingerprint: 'l'.repeat(64) };
	const claim = (item: object, verdict = 'verified') => JSON.stringify({ schema_version: '1.1', claims: [{ finding_id: 'F1', verdict, evidence_for: [item] }] });

	await assert.rejects(call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_ref: ref }) }),
		(error: Error) => error.message.includes('has no source_expression_refs; the file is unchanged') && error.message.includes(`F1 (the key is source_expression_refs, a list, not source_expression_ref): ${JSON.stringify([ref])}`));
	assert.equal(existsSync(join(caseDir, 'data', 'fact-check.json')), false);

	await call('write_file', { path: 'data/fact-check.json', content: claim({ source_expression_refs: [ref] }) });
	await assert.rejects(call('edit_file', { path: 'data/fact-check.json', old_text: '"source_expression_refs"', new_text: '"source_expression_ref"' }), /has no source_expression_refs/);
	await call('write_file', { path: 'data/fact-check.json', content: claim({}, 'unverified') });
});

// E2E D11: the tool text promises the checkout's schemas/, but a relative
// schemas/ path resolved inside the case and came back "not found".
test('a relative schemas/ path reads the checkout schema', async (t) => {
	const { call } = await fixture(t);

	const text = await call('read_file', { path: 'schemas/methodology.schema.json' });

	assert.equal(text, await readFile(join(CHECKOUT_ROOT, 'schemas', 'methodology.schema.json'), 'utf8'));
});
