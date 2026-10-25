// invoke_skill and the Navigator-hosted Indicator Framework, against a stand-in
// `navigator` on PATH that serves a tiny synthetic skill and records its argv.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { ModelTier } from '../config.ts';
import { invokeSkillTool } from './skills.ts';

const REVISION = 'a'.repeat(64);
const SKILL = {
	id: 'indicator-framework',
	skill_revision: REVISION,
	skill_md: 'SYNTHETIC SKILL: rate each indicator.',
	files: [
		{ path: 'references/tables.md', size: 0, sha256: 'b'.repeat(64), media_type: 'text/plain; charset=utf-8' },
		{ path: 'references/tables-compact.md', size: 0, sha256: 'c'.repeat(64), media_type: 'text/plain; charset=utf-8' },
	],
};
const FILES: Record<string, string> = { 'references/tables.md': 'FULL TABLES', 'references/tables-compact.md': 'COMPACT TABLES' };

const NAVIGATOR = `#!${process.execPath}
const { appendFileSync, readFileSync } = require('node:fs');
const { join } = require('node:path');
const args = process.argv.slice(2);
appendFileSync(join(__dirname, 'argv.jsonl'), JSON.stringify(args) + '\\n');
const state = JSON.parse(readFileSync(join(__dirname, 'state.json'), 'utf8'));
if (args[1] === 'list') process.stdout.write(JSON.stringify({ schema_version: 'navigator-skills/v1', count: state.catalogue.length, skills: state.catalogue }));
else if (args[1] === 'show') process.stdout.write(JSON.stringify(state.skill));
else if (args[1] === 'file') process.stdout.write(state.files[args[3]]);
`;

async function fixture(t: TestContext, entitled: boolean) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-hosted-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const bin = join(root, 'bin');
	const caseDir = join(root, 'case');
	const dataDir = join(root, 'harness-data');
	await mkdir(bin);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(dataDir);
	await writeFile(join(bin, 'navigator'), NAVIGATOR);
	await chmod(join(bin, 'navigator'), 0o755);
	const catalogue = entitled ? [{ id: SKILL.id, title: 'Indicator Framework', description: '', category: 'lab', target_types: [], skill_revision: REVISION }] : [];
	await writeFile(join(bin, 'state.json'), JSON.stringify({ catalogue, skill: SKILL, files: FILES }));
	const env = { PATH: `${bin}${delimiter}${dirname(process.execPath)}`, HOME: root };
	const invoke = async (tier: ModelTier = 'frontier', sensitive = false) => {
		const tool = invokeSkillTool(['osint'], { caseDir, dataDir, env, tier, sensitive });
		const result = await tool.execute('call', { skill: 'indicator-framework' }, undefined, undefined, undefined as never);
		return (result.content[0] as { text: string }).text;
	};
	const calls = async () =>
		(await readFile(join(bin, 'argv.jsonl'), 'utf8').catch(() => '')).split('\n').filter(Boolean).map((line) => JSON.parse(line) as string[]);
	return { caseDir, dataDir, invoke, calls };
}

// The methodology turns the framework on, and the member's approval bound that file.
async function approve(caseDir: string, on: boolean) {
	const methodology = JSON.stringify({ indicator_framework: { on, reason: 'Are the two sites linked?' } });
	await writeFile(join(caseDir, 'data/methodology.json'), methodology);
	const sha = createHash('sha256').update(methodology).digest('hex');
	await writeFile(join(caseDir, 'data/orchestration.json'), JSON.stringify({ approvals: { methodology: { input_sha256: { 'data/methodology.json': sha } } } }));
}

test('with the flag off or without Lab access, invoke_skill loads nothing and says why', async (t) => {
	const off = await fixture(t, true);
	await approve(off.caseDir, false);
	assert.match(await off.invoke(), /available to this member \(Lab access\) but not loaded/);
	assert.deepEqual((await off.calls()).map((args) => args[1]), ['list']);

	const unentitled = await fixture(t, false);
	await approve(unentitled.caseDir, true);
	const text = await unentitled.invoke();
	assert.match(text, /unavailable: this Navigator account has no access to it/);
	assert.ok(!text.includes('SYNTHETIC SKILL'));
	assert.deepEqual((await unentitled.calls()).map((args) => args[1]), ['list']);
	assert.deepEqual(await readdir(unentitled.dataDir), [], 'nothing cached');
});

test('with the flag on, the skill is fetched by revision and cached outside the case; the 12b tier gets the compact tables; sensitive mode uses the cache only', async (t) => {
	const { caseDir, dataDir, invoke, calls } = await fixture(t, true);
	await approve(caseDir, true);

	const frontier = await invoke('frontier');
	assert.match(frontier, /SYNTHETIC SKILL/);
	assert.ok(frontier.includes('FULL TABLES') && !frontier.includes('COMPACT TABLES'));
	assert.deepEqual(await calls(), [
		['skills', 'list', '--json'],
		['skills', 'show', 'indicator-framework', '--revision', REVISION, '--json'],
		['skills', 'file', 'indicator-framework', 'references/tables.md', '--revision', REVISION],
		['skills', 'file', 'indicator-framework', 'references/tables-compact.md', '--revision', REVISION],
	]);
	assert.deepEqual(await readdir(join(dataDir, 'hosted-skills', 'indicator-framework')), [REVISION]);
	assert.deepEqual((await readdir(join(caseDir, 'data'))).sort(), ['methodology.json', 'orchestration.json'], 'nothing written to the case');

	const small = await invoke('12b');
	assert.ok(small.includes('SYNTHETIC SKILL') && small.includes('COMPACT TABLES') && !small.includes('FULL TABLES'));
	assert.equal((await calls()).length, 5, 'a cached revision is not fetched again');

	assert.equal(await invoke('frontier', true), frontier);
	assert.equal((await calls()).length, 5, 'sensitive mode makes no Navigator request');
});
