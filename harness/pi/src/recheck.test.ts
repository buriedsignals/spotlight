// #274: the harness re-fetches each cited source once per case and tells the
// fact-checker whether the recorded passages are still on it.
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, unlink, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { recheckSources } from './recheck.ts';
import * as fetchTools from './tools/fetch.ts';

test('a cited source is re-fetched once per case and reported as still saying, changed or unreachable; a sensitive case is not fetched', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-recheck-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const runs = join(root, 'runs.txt');
	// A stand-in for the scraping seam: the page for each URL, or a failure.
	const pages = { 'https://a.example/same': 'Header. Acme paid Doe in 2024. Footer.', 'https://b.example/moved': 'This page now says something else.' };
	const seam = join(root, 'python');
	await writeFile(
		seam,
		`#!${process.execPath}\nconst url = process.argv.at(-1);\nrequire('fs').appendFileSync(${JSON.stringify(runs)}, url + '\\n');\nconst pages = ${JSON.stringify(pages)};\nif (!pages[url]) { process.stderr.write('scrape failed: crawl4ai was bot-blocked on ' + url); process.exit(3); }\nprocess.stdout.write(JSON.stringify({ markdown: pages[url], source_url: url, provider: 'crawl4ai', status_code: 200 }));\n`,
	);
	await chmod(seam, 0o755);
	const docs = {
		findings: { findings: [{ id: 'F1', claim: 'Acme paid Doe.', evidence_bundle_refs: ['E1', 'E2', 'E3'] }] },
		bundle: { items: [
			{ id: 'E1', source_url: 'https://a.example/same' },
			{ id: 'E2', source_url: 'https://b.example/moved' },
			{ id: 'E3', source_url: 'https://c.example/blocked' },
		] },
		expressions: { expressions: [
			{ id: 'P1', text: 'Acme paid Doe in 2024.', original_evidence_bundle_id: 'E1' },
			{ id: 'P2', text: 'Doe signed the filing.', original_evidence_bundle_id: 'E2' },
		] },
	};
	const options = (sensitive: boolean) => ({
		context: { caseDir, writeRoot: caseDir, python: seam, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_HARNESS_DATA: join(root, 'data'), SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_ROLE: 'fact-checker' } },
		dataDir: join(root, 'data'),
		sensitive,
	});

	assert.deepEqual(await recheckSources(docs, ['F1'], options(true)), {}, 'a sensitive case makes no request');
	const first = await recheckSources(docs, ['F1'], options(false));
	assert.equal(first['https://a.example/same'].status, 'still says the 1 recorded passage');
	assert.equal(first['https://b.example/moved'].status, 'changed: 1 of 1 recorded passages are no longer on the page');
	assert.match(first['https://c.example/blocked'].status, /^unreachable: fetch failed: crawl4ai was bot-blocked on https:\/\/c\.example\/blocked/);
	assert.equal(await readFile(join(caseDir, 'research', 'fc-recheck-E1.md'), 'utf8'), pages['https://a.example/same'], 'the copy is kept under an fc- name');
	assert.equal(first['https://a.example/same'].acquisition?.acquired_by, 'fact-checker');
	await recheckSources(docs, ['F1'], options(false));
	assert.equal((await readFile(runs, 'utf8')).trim().split('\n').length, 3, 'each source is fetched once per case');
	await writeFile(join(caseDir, 'research', 'fc-recheck-E1.md'), 'changed after capture');
	const stale = await recheckSources(docs, ['F1'], options(false));
	assert.equal(stale['https://a.example/same'].acquisition, undefined);
	const store = join(root, 'data', 'work', 'recheck', `${createHash('sha256').update(caseDir).digest('hex').slice(0, 16)}.json`);
	await writeFile(store, JSON.stringify({ 'https://a.example/same': 'legacy context' }));
	const legacy = await recheckSources(docs, ['F1'], options(false));
	assert.equal(legacy['https://a.example/same'], 'legacy context');
});

test('only an intact fact-checker capture with valid metadata is independently attributable', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-capture-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const python = join(root, 'python');
	await writeFile(python, `#!${process.execPath}\nprocess.stdout.write(JSON.stringify({markdown:'Registry says Acme paid Doe.', source_url:process.argv.at(-1), provider:'crawl4ai', status_code:200}));\n`, { mode: 0o755 });
	const context = (role: string) => ({ caseDir, writeRoot: caseDir, python, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_HARNESS_DATA: join(root, 'data'), SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_ROLE: role } });
	const fetch = async (role: string, name: string) => fetchTools.fetchTool(context(role)).execute('fetch', { url: 'https://registry.example.org/record', save_as: name }, undefined, undefined, undefined as never);
	await fetch('fact-checker', 'fc-test.md');
	await fetch('investigator', 'inv.md');
	const capture = (fetchTools as typeof fetchTools & { factCheckerCapture: (ctx: ReturnType<typeof context>, path: string) => unknown }).factCheckerCapture;
	assert.equal(typeof capture, 'function');
	assert.ok(capture(context('fact-checker'), 'research/fc-test.md'));
	assert.equal(capture(context('fact-checker'), 'research/inv.md'), undefined);
	const ledger = join(root, 'data', 'work', 'fetched', `${createHash('sha256').update(caseDir).digest('hex').slice(0, 16)}.json`);
	const original = JSON.parse(await readFile(ledger, 'utf8'));
	for (const change of [{ acquired_by: undefined }, { acquired_by: 'investigator' }, { url: 'not a url' }, { url: 'https://' }, { accessed: 'not a time' }, { sha256: '0'.repeat(64) }]) {
		await writeFile(ledger, JSON.stringify({ ...original, 'research/fc-test.md': { ...original['research/fc-test.md'], ...change } }));
		assert.equal(capture(context('fact-checker'), 'research/fc-test.md'), undefined);
	}
	await writeFile(ledger, JSON.stringify({ ...original, 'research/fc-test.md': 'legacy' }));
	assert.equal(capture(context('fact-checker'), 'research/fc-test.md'), undefined);
	await writeFile(ledger, JSON.stringify(original));
	const alternative = (fetchTools as typeof fetchTools & { factCheckerAlternative: (ctx: ReturnType<typeof context>, path: string) => string | undefined }).factCheckerAlternative;
	assert.equal(alternative(context('fact-checker'), 'research/inv.md'), 'research/fc-test.md');
	for (const change of [{ url: 'https://other.example/record' }, { acquired_by: undefined }, { acquired_by: 'investigator' }, { sha256: '0'.repeat(64) }]) {
		await writeFile(ledger, JSON.stringify({ ...original, 'research/fc-test.md': { ...original['research/fc-test.md'], ...change } }));
		assert.equal(alternative(context('fact-checker'), 'research/inv.md'), undefined);
	}
	await writeFile(ledger, JSON.stringify(original));
	await writeFile(join(caseDir, 'research', 'inv.md'), 'changed investigator capture');
	assert.equal(alternative(context('fact-checker'), 'research/inv.md'), undefined);
	await writeFile(join(caseDir, 'research', 'inv.md'), 'Registry says Acme paid Doe.');
	await writeFile(join(caseDir, 'research', 'fc-test.md'), 'changed independent capture');
	assert.equal(alternative(context('fact-checker'), 'research/inv.md'), undefined);
	await writeFile(join(caseDir, 'research', 'fc-test.md'), 'Registry says Acme paid Doe.');
	await writeFile(join(root, 'outside'), 'Registry says Acme paid Doe.');
	await writeFile(ledger, JSON.stringify({ 'research/inv.md': original['research/inv.md'], '../outside': original['research/fc-test.md'] }));
	assert.equal(alternative(context('fact-checker'), 'research/inv.md'), undefined);
	await writeFile(ledger, JSON.stringify(original));
	await unlink(join(caseDir, 'research', 'fc-test.md'));
	assert.equal(capture(context('fact-checker'), 'research/fc-test.md'), undefined);
	assert.equal(alternative(context('fact-checker'), 'research/inv.md'), undefined);
});
