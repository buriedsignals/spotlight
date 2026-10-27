// The seams that call Engine's bsig (wiki search, protected-source checks)
// must get SPOTLIGHT_BSIG and the install config. Gold sensitive runs on
// 699c537c: query_vault ran without them, found an old bsig with no `wiki`
// verb, and every search came back "unavailable". The stand-in interpreter
// records the environment each seam is started with.
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { typedTools } from './index.ts';

const PAGE_URL = 'https://registry.example.org/company/42';

test('every seam over Engine\'s bsig is started with SPOTLIGHT_BSIG and XDG, and search and fetch with the install config', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-engine-env-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const log = join(root, 'env.jsonl');
	const envelope = { markdown: '# Company 42\n', source_url: PAGE_URL, provider: 'crawl4ai', status_code: 200, title: 'Company 42' };
	const python = join(root, 'python');
	await writeFile(
		python,
		`#!${process.execPath}
const { appendFileSync } = require('node:fs');
appendFileSync(${JSON.stringify(log)}, JSON.stringify({ args: process.argv.slice(2), env: process.env }) + '\\n');
process.stdout.write(process.argv.includes('integrations.scraping') ? ${JSON.stringify(JSON.stringify(envelope))} : process.argv.includes('integrations.search') ? '[]' : '{}');
`,
	);
	await chmod(python, 0o755);
	const configPath = join(root, 'install-config.json');
	const bsig = join(root, 'engine', 'bsig');
	const tools = new Map(
		typedTools({
			caseDir,
			writeRoot: caseDir,
			python,
			configPath,
			env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_BSIG: bsig, XDG_CONFIG_HOME: join(root, 'xdg') },
		}).map((tool) => [tool.name, tool as ToolDefinition]),
	);
	const calls: Array<[string, Record<string, unknown>, string, boolean]> = [
		['query_vault', { query: 'Acme' }, 'query_vault.py', false],
		['search', { query: 'Acme' }, 'integrations.search', true],
		['fetch', { url: PAGE_URL }, 'integrations.scraping', true],
		['decision_signals', { phase: 'investigation_gate', check: true }, 'decision-signals.py', false],
	];
	for (const [name, params] of calls) await tools.get(name)!.execute('call', params, undefined, undefined, undefined as never);

	const runs = (await readFile(log, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as { args: string[]; env: Record<string, string> });
	for (const [name, , seam, readsConfigFromEnv] of calls) {
		const run = runs.find((entry) => entry.args.some((arg) => arg.includes(seam)));
		assert.ok(run, `${name} started its seam`);
		assert.equal(run.env.SPOTLIGHT_BSIG, bsig, `${name} gets SPOTLIGHT_BSIG`);
		assert.equal(run.env.XDG_CONFIG_HOME, join(root, 'xdg'), `${name} gets XDG_*`);
		if (readsConfigFromEnv) assert.equal(run.env.SPOTLIGHT_CONFIG, configPath, `${name} checks protected sources in the install's wiki`);
	}
});
