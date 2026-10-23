// Every typed tool that starts a backing program does so without a shell, and
// every typed tool refuses arguments outside its schema. The backing programs
// here are stand-ins on PATH that record the argv they receive.
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { runFixed } from './run.ts';
import type { ToolContext } from './run.ts';
import { typedTools } from './index.ts';
import { validateAfterWorker } from '../validators.ts';

const RECORDER = `#!/usr/bin/env node
const { appendFileSync } = require('node:fs');
const { join } = require('node:path');
const args = process.argv.slice(2);
appendFileSync(join(__dirname, 'argv.jsonl'), JSON.stringify({ program: require('node:path').basename(process.argv[1]), args }) + '\\n');
if (args.includes('integrations.search')) process.stdout.write('[]');
else process.stdout.write('{}');
`;

async function fixture(t: TestContext) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-programs-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const bin = join(root, 'bin');
	const caseDir = join(root, 'case');
	await mkdir(bin);
	await mkdir(join(caseDir, 'data'), { recursive: true });
	for (const name of ['python', 'exiftool', 'navigator', 'dev-browser']) {
		await writeFile(join(bin, name), RECORDER.replace('#!/usr/bin/env node', `#!${process.execPath}`));
		await chmod(join(bin, name), 0o755);
	}
	const context: ToolContext = {
		caseDir,
		writeRoot: caseDir,
		python: join(bin, 'python'),
		configPath: join(root, 'config.json'),
		env: { PATH: `${bin}${delimiter}${dirname(process.execPath)}`, HOME: root },
	};
	const tools = new Map(typedTools(context).map((tool) => [tool.name, tool]));
	const call = (name: string, params: Record<string, unknown>) =>
		(tools.get(name) as ToolDefinition).execute('call', params, undefined, undefined, undefined as never);
	const recorded = async () =>
		(await readFile(join(bin, 'argv.jsonl'), 'utf8').catch(() => ''))
			.split('\n')
			.filter(Boolean)
			.map((line) => JSON.parse(line) as { program: string; args: string[] });
	return { root, caseDir, context, tools, call, recorded };
}

const PYTHON = process.platform === 'win32' ? 'python' : 'python3';
const HOSTILE = '$(touch PWNED); touch PWNED && `touch PWNED` | touch PWNED';
const HOSTILE_FILE = '$(touch PWNED);x.pdf';

test('the shared runner passes metacharacters to the program as literal argv', async (t) => {
	const { root, context, recorded } = await fixture(t);

	await runFixed(context.python, [HOSTILE, '--', '-x'], { env: { PATH: context.env.PATH! }, cwd: root });

	assert.deepEqual((await recorded())[0].args, [HOSTILE, '--', '-x']);
	assert.equal(existsSync(join(root, 'PWNED')), false);
});

test('every program-backed tool and harness validator runs without a shell', async (t) => {
	const { root, caseDir, context, call, recorded } = await fixture(t);
	await writeFile(join(caseDir, HOSTILE_FILE), 'pdf');
	await writeFile(join(caseDir, 'data', 'rlm-request.json'), '{"project":"case"}');
	await writeFile(join(caseDir, 'data', 'entities.json'), '[]');

	const calls: Array<[string, Record<string, unknown>]> = [
		['search', { query: HOSTILE }],
		['search', { query: '--limit=999' }],
		['query_vault', { query: HOSTILE }],
		['query_vault', { workflow: 'dedup', proposition: HOSTILE }],
		['osint_tools', { action: 'find', query: HOSTILE }],
		['osint_tools', { action: 'categories' }],
		['exiftool', { path: HOSTILE_FILE }],
		['navigator_tools', { action: 'find', value: HOSTILE }],
		['decision_signals', { phase: 'ingest', entities: 'data/entities.json' }],
		['provenance_manifest', { action: 'check_current' }],
		['run_rlm', { request: 'data/rlm-request.json' }],
		['integration_status', {}],
	];
	for (const [name, params] of calls) await call(name, params);
	for (const role of ['investigator', 'fact-checker']) await validateAfterWorker(role, 'investigation', context);

	const runs = await recorded();
	assert.equal(existsSync(join(root, 'PWNED')), false);
	assert.equal(existsSync(join(caseDir, 'PWNED')), false);
	// Untrusted text arrives as exactly one argv element, after an end-of-options marker.
	for (const run of runs.filter((each) => each.args.some((arg) => arg.includes('touch PWNED')))) {
		const element = run.args.find((arg) => arg.includes('touch PWNED'))!;
		const asOption = /^--[a-z-]+=/.test(element) && element.endsWith(`=${HOSTILE}`);
		assert.ok(element === HOSTILE || asOption || element.endsWith(HOSTILE_FILE), JSON.stringify(run));
		if (element === HOSTILE) {
			assert.ok(run.args.indexOf('--') !== -1 && run.args.indexOf('--') < run.args.indexOf(element), JSON.stringify(run));
		}
	}
	const search = runs.find((run) => run.args.includes('--limit=999'))!;
	assert.equal(search.args[search.args.length - 1], '--limit=999');
	assert.ok(search.args.indexOf('--') === search.args.length - 2, JSON.stringify(search));
	assert.ok(runs.length >= calls.length + 3, `expected every call to start its program, saw ${runs.length}`);
});

test('run_rlm refuses a request naming another case and leaves that case untouched', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-rlm-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const casesRoot = join(root, 'cases');
	const caseDir = join(casesRoot, 'active');
	const sibling = join(casesRoot, 'sibling');
	for (const dir of [caseDir, sibling]) {
		await mkdir(join(dir, 'data'), { recursive: true });
		await mkdir(join(dir, 'research'), { recursive: true });
		await writeFile(join(dir, 'research', 'notes.txt'), 'Contact press@example.org about the filing.\n');
	}
	const context: ToolContext = {
		caseDir,
		writeRoot: caseDir,
		python: PYTHON,
		configPath: join(root, 'config.json'),
		env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_CASES_ROOT: casesRoot },
	};
	const rlm = typedTools(context).find((tool) => tool.name === 'run_rlm') as ToolDefinition;
	const run = (project: string) =>
		writeFile(
			join(caseDir, 'data', 'rlm-request.json'),
			JSON.stringify({ project, run_id: 'r1', mode: 'lite', corpus_paths: ['research/notes.txt'] }),
		).then(() => rlm.execute('call', { request: 'data/rlm-request.json' }, undefined, undefined, undefined as never));

	await assert.rejects(run('sibling'), /refused: run_rlm request must name the active case/);
	assert.equal(existsSync(join(sibling, 'data', 'rlm-analysis.json')), false);

	await run('active');
	assert.equal(existsSync(join(caseDir, 'data', 'rlm-analysis.json')), true);
});

test('each tool rejects arguments outside its schema without starting anything', async (t) => {
	const { tools, call, recorded } = await fixture(t);
	const wrongType: Record<string, Record<string, unknown>> = {
		fetch: { url: 'gopher://example.com/' },
		http_get: { url: 42 },
		browse: { url: 'https://example.com/', capture: 'script' },
		search: { query: '' },
		query_vault: { workflow: 'drop-tables' },
		osint_tools: { action: 'build' },
		exiftool: { path: ['a', 'b'] },
		navigator_tools: { action: 'login', value: 'x' },
		decision_signals: { phase: 'methodology' },
		provenance_manifest: { action: 'delete' },
		run_rlm: { request: 7 },
		integration_status: { model_tier: '$(id)' },
		read_file: { path: 3 },
		write_file: { path: 'a.txt' },
		edit_file: { path: 'a.txt', old_text: 'a' },
		list_files: { pattern: null },
		grep_files: { pattern: 1 },
	};
	assert.deepEqual([...tools.keys()].sort(), Object.keys(wrongType).sort());
	for (const [name, params] of Object.entries(wrongType)) {
		await assert.rejects(call(name, params), /outside the schema/, `${name} wrong type`);
		await assert.rejects(call(name, { ...params, unexpected: true }), /outside the schema/, `${name} extra key`);
	}
	assert.deepEqual(await recorded(), []);
});

test('fetch, browse and http_get refuse loopback, private and metadata targets before starting anything', async (t) => {
	const { call, recorded } = await fixture(t);

	for (const url of ['http://127.0.0.1/', 'http://10.0.0.1/', 'http://169.254.169.254/latest/meta-data/', 'http://nas.local/']) {
		for (const name of ['fetch', 'browse', 'http_get']) {
			await assert.rejects(call(name, { url }), /refused/i, `${name} ${url}`);
		}
	}
	assert.deepEqual(await recorded(), []);
});
