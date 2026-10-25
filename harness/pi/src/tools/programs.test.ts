// Every typed tool that starts a backing program does so without a shell, and
// every typed tool refuses arguments outside its schema. The backing programs
// here are stand-ins on PATH that record the argv they receive.
import assert from 'node:assert/strict';
import dns from 'node:dns';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { syncBuiltinESMExports } from 'node:module';
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
const { appendFileSync, existsSync, readFileSync } = require('node:fs');
const { join } = require('node:path');
const args = process.argv.slice(2);
appendFileSync(join(__dirname, 'argv.jsonl'), JSON.stringify({ program: require('node:path').basename(process.argv[1]), args }) + '\\n');
if (existsSync(join(__dirname, 'result.json'))) process.stdout.write(readFileSync(join(__dirname, 'result.json')));
else if (args.includes('integrations.search')) process.stdout.write('[]');
else process.stdout.write('{}');
`;

async function fixture(t: TestContext, env: Record<string, string> = {}) {
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
		env: { PATH: `${bin}${delimiter}${dirname(process.execPath)}`, HOME: root, ...env },
	};
	const tools = new Map(typedTools(context).map((tool) => [tool.name, tool]));
	const call = (name: string, params: Record<string, unknown>) =>
		(tools.get(name) as ToolDefinition).execute('call', params, undefined, undefined, undefined as never);
	const recorded = async () =>
		(await readFile(join(bin, 'argv.jsonl'), 'utf8').catch(() => ''))
			.split('\n')
			.filter(Boolean)
			.map((line) => JSON.parse(line) as { program: string; args: string[] });
	return { root, bin, caseDir, context, tools, call, recorded };
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

	const calls: Array<[string, Record<string, unknown>]> = [
		['search', { query: HOSTILE }],
		['search', { query: '--limit=999' }],
		['query_vault', { query: HOSTILE }],
		['query_vault', { workflow: 'dedup', proposition: HOSTILE }],
		['osint_tools', { action: 'find', query: HOSTILE }],
		['osint_tools', { action: 'categories' }],
		['exiftool', { path: HOSTILE_FILE }],
		['navigator_tools', { action: 'find', value: HOSTILE }],
		['decision_signals', { phase: 'report' }],
		['provenance_manifest', { action: 'check_current' }],
		['run_rlm', { request: 'data/rlm-request.json' }],
		['integration_status', {}],
		['wiki_destination', {}],
	];
	for (const [name, params] of calls) await call(name, params);
	for (const role of ['investigator', 'fact-checker']) await validateAfterWorker(role, 'investigation', 'stub-model', context);

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

// D4: solo Ingest's write runs the real scripts/wiki-destination.py on the
// active case with the harness's install config. The script refuses here (the
// configured folder is not a member wiki); the refusal naming that folder
// proves the tool reached the script with this config and wrote nothing.
test('wiki_destination runs the wiki write on the active case with the install config', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-wiki-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'cases', 'active');
	const wiki = join(root, 'not-a-wiki');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(wiki);
	await writeFile(join(root, 'config.json'), JSON.stringify({ knowledge_destination: { mode: 'solo', workspace_path: wiki } }));
	const context: ToolContext = {
		caseDir,
		writeRoot: caseDir,
		python: PYTHON,
		configPath: join(root, 'config.json'),
		env: { PATH: process.env.PATH, HOME: root },
	};
	const tool = typedTools(context).find((each) => each.name === 'wiki_destination') as ToolDefinition;
	assert.ok(tool, 'the harness offers the solo Ingest write');
	await assert.rejects(
		tool.execute('call', {}, undefined, undefined, undefined as never),
		(error: Error) => /wiki_destination failed \(exit 2\)/.test(error.message) && error.message.includes(`${wiki} is not a member wiki`),
	);
	assert.equal(existsSync(join(wiki, 'spotlight')), false);
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
		anomaly_methods: { anomaly_home: '/elsewhere' },
		read_file: { path: 3 },
		write_file: { path: 'a.txt' },
		edit_file: { path: 'a.txt', old_text: 'a' },
		list_files: { pattern: null },
		grep_files: { pattern: 1 },
		hash_files: { paths: [] },
		build_review_html: { case: 'other' },
		wiki_destination: { case: 'other' },
		source_expressions: { action: 'delete' },
	};
	assert.deepEqual([...tools.keys()].sort(), Object.keys(wrongType).sort());
	for (const [name, params] of Object.entries(wrongType)) {
		await assert.rejects(call(name, params), /outside the schema/, `${name} wrong type`);
		await assert.rejects(call(name, { ...params, unexpected: true }), /outside the schema/, `${name} extra key`);
	}
	assert.deepEqual(await recorded(), []);
});

// A legacy case (no contract) keeps contract 1.0 and gets no passage files.
test('source_expressions refuses a case that is not activated and runs the writer once it is', async (t) => {
	const { call, recorded, caseDir } = await fixture(t, { SPOTLIGHT_ROLE: 'investigator' });

	await assert.rejects(call('source_expressions', { action: 'fingerprint_findings' }), /not activated/);
	assert.deepEqual(await recorded(), []);
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	await call('source_expressions', { action: 'fingerprint_findings' });
	assert.ok((await recorded()).some((run) => run.args.includes('--fingerprint-findings')));
});

// The fact-checker is kept from the research swarm's files; the writer reads an
// anchor's file for it, so a passage anchored there is refused before it runs.
test('source_expressions refuses an anchor in a file the role may not read', async (t) => {
	const { call, recorded, caseDir, context } = await fixture(t, { SPOTLIGHT_ROLE: 'fact-checker' });
	context.scope = { denyReads: ['research/swarm/'] };
	await writeFile(join(caseDir, 'data', 'case-contract.json'), '{}');
	await mkdir(join(caseDir, 'research', 'swarm', 'web'), { recursive: true });
	await writeFile(join(caseDir, 'research', 'swarm', 'web', 'page.md'), 'swarm text');

	const proposal = { expressions: [{ id: 'p1', text: 'swarm text', anchor_ref: { path: 'research/swarm/web/page.md', line_start: 1, line_end: 1 } }] };
	await assert.rejects(call('source_expressions', { action: 'write', cycle: 1, proposal }), /outside the case files/);
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

// Tor mode: the target must never see a connection, or a DNS query, from the
// operator's machine. The target here is a local server that counts requests;
// node:dns lookup is replaced by a recorder that maps the target's name to it.
async function torTarget(t: TestContext) {
	const hits: string[] = [];
	const server = createServer((request, response) => {
		hits.push(request.url ?? '');
		response.writeHead(200).end('target');
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	const lookups: string[] = [];
	t.mock.method(dns, 'lookup', (hostname: string, _options: unknown, callback: (error: Error | null, addresses: unknown) => void) => {
		lookups.push(hostname);
		callback(null, [{ address: '127.0.0.1', family: 4 }]);
	});
	syncBuiltinESMExports();
	t.after(() => {
		t.mock.restoreAll();
		syncBuiltinESMExports();
	});
	return { url: `http://target.example.com:${(server.address() as AddressInfo).port}/page`, hits, lookups };
}

test('in Tor mode fetch makes no direct connection or lookup and hands the URL to the seam with --tor', async (t) => {
	const { bin, call, recorded } = await fixture(t, { SPOTLIGHT_ANONYMIZE_FETCH: 'true' });
	const target = await torTarget(t);
	await writeFile(join(bin, 'result.json'), JSON.stringify({ markdown: 'page', source_url: target.url, provider: 'crawl4ai' }));

	await call('fetch', { url: target.url });

	assert.deepEqual(target.hits, []);
	assert.deepEqual(target.lookups, []);
	const [run] = await recorded();
	assert.deepEqual(run.args.slice(-3), ['--tor', '--', target.url]);
});

test('in Tor mode the URL the seam reports ending on is still checked', async (t) => {
	const { bin, call, recorded } = await fixture(t, { SPOTLIGHT_ANONYMIZE_FETCH: '1' });
	await torTarget(t);
	await writeFile(join(bin, 'result.json'), JSON.stringify({ markdown: 'admin', source_url: 'http://127.0.0.1:4173/review' }));

	await assert.rejects(call('fetch', { url: 'https://example.com/' }), /refused: 127\.0\.0\.1 is not a public address/);
	assert.equal((await recorded()).length, 1);
});

test('in Tor mode http_get and browse refuse without connecting, looking up or starting anything', async (t) => {
	const { call, recorded } = await fixture(t, { SPOTLIGHT_ANONYMIZE_FETCH: 'yes' });
	const target = await torTarget(t);

	await assert.rejects(call('http_get', { url: target.url }), /cannot go through Tor/);
	await assert.rejects(call('browse', { url: target.url }), /cannot go through Tor/);

	assert.deepEqual(target.hits, []);
	assert.deepEqual(target.lookups, []);
	assert.deepEqual(await recorded(), []);
});

test('integration_status passes an all-red report through but surfaces a crash as a tool error', async (t) => {
	const { context, call } = await fixture(t);
	const standIn = (body: string) => writeFile(context.python, `#!${process.execPath}\n${body}\n`);
	const report = { integrations: [{ id: 'search', status: 'red' }], summary: { green: 0, yellow: 0, red: 1 } };

	await standIn(`process.stdout.write(${JSON.stringify(JSON.stringify(report))}); process.exit(1);`);
	const allRed = await call('integration_status', {});
	assert.deepEqual(JSON.parse(allRed.content[0].text), report);

	await standIn(`process.stderr.write('Traceback: ModuleNotFoundError'); process.exit(1);`);
	await assert.rejects(call('integration_status', {}), /integration_status failed \(exit 1\): Traceback/);
});

test('http_get percent-encodes its query parameters and appends them to the URL', async (t) => {
	const { call } = await fixture(t);
	// A stand-in for the HTTP transport that answers with the request target it
	// was given, so the test sees exactly what would go on the wire.
	const asked: string[] = [];
	t.mock.method(http, 'request', (url: URL) => {
		asked.push(url.href);
		const request = Object.assign(new EventEmitter(), {
			end() {
				const response = Object.assign(Readable.from([Buffer.from(url.pathname + url.search)]), { statusCode: 200, headers: {} });
				queueMicrotask(() => request.emit('response', response));
			},
			destroy() {},
		});
		return request;
	});
	syncBuiltinESMExports();
	t.after(() => {
		t.mock.restoreAll();
		syncBuiltinESMExports();
	});

	const result = JSON.parse(
		(
			await call('http_get', {
				url: 'http://web.archive.org/cdx/search/cdx?output=json',
				query: { url: 'https://example.com/a b?x=1&y=ü#frag', limit: 3, fl: 'timestamp,original' },
			})
		).content[0].text,
	);

	assert.deepEqual(asked, [
		'http://web.archive.org/cdx/search/cdx?output=json&url=https%3A%2F%2Fexample.com%2Fa%20b%3Fx%3D1%26y%3D%C3%BC%23frag&limit=3&fl=timestamp%2Coriginal',
	]);
	assert.equal(result.final_url, asked[0]);
	await assert.rejects(call('http_get', { url: 'http://example.com/', query: { 'a b': 'x' } }), /outside the schema/);
});

test('build_review_html builds review.html from the case files with script-safe data', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-review-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	const hostile = 'Filed </script><script>globalThis.pwned = 1</script>\u2028 in Zürich';
	await writeFile(
		join(caseDir, 'data', 'findings.json'),
		JSON.stringify({ project: 'case', cycle: 2, findings: [{ id: 'F1', claim: hostile, evidence: ['one', 'two'], confidence: 'medium' }, { id: 'F2', claim: 'second' }] }),
	);
	await writeFile(
		join(caseDir, 'data', 'fact-check.json'),
		JSON.stringify({ project: 'case', cycle: 2, claims: [{ finding_id: 'F1', verdict: 'verified', notes: 'checked' }, { finding_id: 'F2', verdict: 'not_checkable' }] }),
	);
	await writeFile(join(caseDir, 'data', 'summary.json'), JSON.stringify({ title: 'Case title', summary: 'Overview.', key_conclusions: ['C1'], limitations: ['L1'], cycles: 2 }));
	const context: ToolContext = { caseDir, writeRoot: caseDir, python: PYTHON, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, HOME: root } };
	const tool = typedTools(context).find((each) => each.name === 'build_review_html') as ToolDefinition;

	const result = JSON.parse((await tool.execute('call', {}, undefined, undefined, undefined as never)).content[0].text);

	const html = await readFile(join(caseDir, 'review.html'), 'utf8');
	assert.equal(result.written, 'review.html');
	assert.doesNotMatch(html, /<script>globalThis\.pwned/);
	const data = JSON.parse(/<script id="investigation-data" type="application\/json">([\s\S]*?)<\/script>/.exec(html)![1]);
	assert.equal(data.project, 'case');
	assert.equal(data.findings[0].claim, hostile);
	assert.equal(data.findings[0].evidence, 'one\n\ntwo');
	assert.deepEqual(data.findings[0].verdict, { finding_id: 'F1', verdict: 'verified', notes: 'checked' });
	assert.deepEqual(data.fact_check_summary, { total_claims: 2, verified: 1, unverified: 0, disputed: 0, false: 0, not_checkable: 1 });
	assert.deepEqual(data.summary, { headline: 'Case title', overview: 'Overview.', conclusions: ['C1'], limitations: ['L1'] });
	assert.equal(data.cycles, 2);
	assert.deepEqual(data.source_expressions, []);
	assert.equal(data.provenance_manifest, null);
});

// anomaly_methods runs the real adapter (integrations/anomaly/adapter.py
// methods). The pinned catalogue's listing is the adapter's own contract
// (tests/anomaly-methods-check.py); here: the tool reaches the install named by
// ANOMALY_HOME and reports the hidden feature with its reason, no methods.
test('anomaly_methods reports the feature hidden, with the reason, unless the pinned catalogue is installed', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-anomaly-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	const other = join(root, 'anomaly-other');
	await mkdir(join(other, 'detectors'), { recursive: true });
	await writeFile(join(other, 'detectors', 'catalogue.json'), JSON.stringify({ schema_version: 1, detectors: [], catalogue_hash: `sha256:${'0'.repeat(64)}` }));
	const list = async (env: Record<string, string>) => {
		const context: ToolContext = { caseDir, writeRoot: caseDir, python: PYTHON, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, HOME: root, ...env } };
		const tool = typedTools(context).find((each) => each.name === 'anomaly_methods') as ToolDefinition;
		const result = await tool.execute('call', {}, undefined, undefined, undefined as never);
		return JSON.parse((result.content[0] as { text: string }).text) as { available: boolean; reason: string; methods: unknown[] };
	};

	const unset = await list({});
	assert.equal(unset.available, false);
	assert.match(unset.reason, /ANOMALY_HOME is not set/);
	const unpinned = await list({ ANOMALY_HOME: other });
	assert.equal(unpinned.available, false);
	assert.match(unpinned.reason, /not the pinned release/);
	for (const outcome of [unset, unpinned]) assert.deepEqual(outcome.methods, []);
});
