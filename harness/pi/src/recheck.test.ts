// #274: the harness re-fetches each cited source once per case and tells the
// fact-checker whether the recorded passages are still on it.
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { recheckSources } from './recheck.ts';

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
		context: { caseDir, writeRoot: caseDir, python: seam, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_ROLE: 'fact-checker' } },
		dataDir: join(root, 'data'),
		sensitive,
	});

	assert.deepEqual(await recheckSources(docs, ['F1'], options(true)), {}, 'a sensitive case makes no request');
	const first = await recheckSources(docs, ['F1'], options(false));
	assert.match(first['https://a.example/same'], /^still says the 1 recorded passage \(harness re-fetch [^,]+, research\/fc-recheck-E1\.md\)$/);
	assert.match(first['https://b.example/moved'], /^changed: 1 of 1 recorded passages are no longer on the page \(harness re-fetch/);
	assert.match(first['https://c.example/blocked'], /^unreachable: fetch failed: crawl4ai was bot-blocked on https:\/\/c\.example\/blocked/);
	assert.equal(await readFile(join(caseDir, 'research', 'fc-recheck-E1.md'), 'utf8'), pages['https://a.example/same'], 'the copy is kept under an fc- name');
	await recheckSources(docs, ['F1'], options(false));
	assert.equal((await readFile(runs, 'utf8')).trim().split('\n').length, 3, 'each source is fetched once per case');
});
