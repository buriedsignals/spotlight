import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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
		'data/provenance-signing-receipt.json',
		'evidence/capture-receipt.json',
	]) {
		await assert.rejects(call('write_file', { path, content: 'x' }), /refuses|outside|symlink|protected/i, path);
		await assert.rejects(call('edit_file', { path, old_text: 'phase', new_text: 'x' }), /refuses|outside|symlink|protected|not found/i, path);
	}
	assert.equal(existsSync(join(outside, 'escape.txt')), false);
	assert.equal(await readFile(join(outside, 'target.json'), 'utf8'), 'outside');
	assert.equal(await readFile(join(caseDir, 'data', 'orchestration.json'), 'utf8'), '{"phase":"brief"}');
	for (const name of ['review-nonces.json', 'methodology-review.json', 'provenance-signing-receipt.json']) {
		assert.equal(existsSync(join(caseDir, 'data', name)), false, name);
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
