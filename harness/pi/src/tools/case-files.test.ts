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
import { caseFileTools } from './case-files.ts';

async function fixture(t: TestContext) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-files-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	const outside = join(root, 'outside');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(outside);
	const tools = new Map(caseFileTools({ caseDir, writeRoot: caseDir }).map((tool) => [tool.name, tool]));
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

// The evidence validators (scripts/evidence_anchors.py) recompute every
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
	const text = 'alpha\r\nbeta gamma\rdelta\n\nepsilon\n';
	await mkdir(join(caseDir, 'research'));
	await writeFile(join(caseDir, 'research', 'page.md'), text);
	const doc = { items: [{ 'a/b': 'Grüße </script>', n: 3 }, { z: [1, 'x'], a: { é: true, b: null } }] };
	await writeFile(join(caseDir, 'research', 'api.json'), JSON.stringify(doc));
	const select = (name: string, ref: object) =>
		validatorSays(
			'text, error = selected_source_text(__import__("pathlib").Path(sys.argv[2]), json.loads(sys.argv[3]))\nassert error is None, error\nprint(json.dumps(text))',
			join(caseDir, 'research', name),
			JSON.stringify(ref),
		);

	for (const [start, end] of [[3, 3], [4, 6], [1, 2]]) {
		const expected = JSON.parse(select('page.md', { line_start: start, line_end: end }));
		assert.equal(await call('read_file', { path: 'research/page.md', offset: start, limit: end - start + 1 }), expected, `${start}-${end}`);
	}
	assert.match(await call('grep_files', { pattern: '^epsilon$' }), /^research\/page\.md:6:epsilon$/m);
	for (const pointer of ['/items/0/a~1b', '/items/1', '/items/0/n', '']) {
		const expected = JSON.parse(select('api.json', { json_pointer: pointer }));
		assert.equal(await call('read_file', { path: 'research/api.json', json_pointer: pointer }), expected, pointer);
	}
	await assert.rejects(call('read_file', { path: 'research/api.json', json_pointer: '/items/7' }), /does not resolve/);
});
