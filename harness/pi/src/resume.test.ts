import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { LANES_DIRECTORY, discardIncompleteLanes } from './resume.ts';

test('only lanes with a completed lane.json survive a resume', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-pi-lanes-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	const lanes = join(caseDir, LANES_DIRECTORY);
	const lane = async (name: string, laneJson?: string) => {
		await mkdir(join(lanes, name), { recursive: true });
		await writeFile(join(lanes, name, 'leads.jsonl'), '{}\n');
		if (laneJson !== undefined) await writeFile(join(lanes, name, 'lane.json'), laneJson);
	};
	await lane('ownership-registries', '{"status":"completed"}');
	await lane('ownership-web', '{"status":"running"}');
	await lane('people-news-courts');
	await lane('people-archives', '{"status":"comp');

	const discarded = discardIncompleteLanes(caseDir);

	assert.deepEqual(discarded.sort(), ['ownership-web', 'people-archives', 'people-news-courts']);
	assert.deepEqual(await readdir(lanes), ['ownership-registries']);
	assert.deepEqual(discardIncompleteLanes(join(caseDir, 'no-such-case')), []);
});
