// invoke_skill and the Navigator-hosted Indicator Framework, against a stand-in
// `navigator` on PATH that serves a synthetic bundle and records its argv.
import assert from 'node:assert/strict';
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { REVISION, approve, hostedFixture } from '../test-support/hosted-skill.ts';
import { invokeSkillTool } from './skills.ts';

async function invoke(context: ReturnType<Awaited<ReturnType<typeof hostedFixture>>['context']>) {
	const tool = invokeSkillTool(['osint'], context);
	const result = await tool.execute('call', { skill: 'indicator-framework' }, undefined, undefined, undefined as never);
	return (result.content[0] as { text: string }).text;
}

test('with the flag off or without Lab access, invoke_skill loads nothing and says why', async (t) => {
	const off = await hostedFixture(t);
	await approve(off.caseDir, false);
	assert.match(await invoke(off.context()), /available to this member \(Lab access\) but not loaded/);
	assert.deepEqual((await off.calls()).map((args) => args[1]), ['list']);

	const unentitled = await hostedFixture(t, { entitled: false });
	await approve(unentitled.caseDir, true);
	const text = await invoke(unentitled.context());
	assert.match(text, /unavailable: this Navigator account has no access to it/);
	assert.ok(!text.includes('SYNTHETIC'));
	assert.deepEqual((await unentitled.calls()).map((args) => args[1]), ['list']);
	assert.deepEqual(await readdir(unentitled.dataDir), [], 'nothing cached');
});

test('with the flag on, only the harness-mode files are fetched and cached outside the case; the model gets the judgment text and a row list; sensitive mode uses the cache only', async (t) => {
	const { caseDir, dataDir, context, calls } = await hostedFixture(t);
	await approve(caseDir, true);

	const text = await invoke(context());
	assert.match(text, /SYNTHETIC JUDGMENT/);
	assert.match(text, /- Hosting: A1 Address; A2 Tag/);
	assert.ok(!text.includes('SKILL.md, never loaded') && !text.includes('CAVEAT'), 'neither SKILL.md nor the full tables');
	assert.deepEqual(await calls(), [
		['skills', 'list', '--json'],
		['skills', 'show', 'indicator-framework', '--revision', REVISION, '--json'],
		['skills', 'file', 'indicator-framework', 'assets/framework.json', '--revision', REVISION],
		['skills', 'file', 'indicator-framework', 'assets/indicator-types.json', '--revision', REVISION],
		['skills', 'file', 'indicator-framework', 'references/harness-mode.md', '--revision', REVISION],
	]);
	assert.deepEqual(await readdir(join(dataDir, 'hosted-skills', 'indicator-framework')), [REVISION]);
	assert.deepEqual((await readdir(join(caseDir, 'data'))).sort(), ['methodology.json', 'orchestration.json'], 'nothing written to the case');

	assert.equal(await invoke(context(true)), text);
	assert.equal((await calls()).length, 5, 'a cached revision is not fetched again, and sensitive mode makes no Navigator request');
});

test('a file that does not match the digest Navigator lists is not cached', async (t) => {
	const { caseDir, dataDir, context } = await hostedFixture(t, { served: { 'assets/framework.json': '{"tampered": true}' } });
	await approve(caseDir, true);

	assert.match(await invoke(context()), /unavailable: assets\/framework\.json does not match the digest Navigator listed/);
	assert.deepEqual(await readdir(join(dataDir, 'hosted-skills', 'indicator-framework')), []);
});
