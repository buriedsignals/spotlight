// fetch on the local tier: the page goes through the RLM, the agent gets the
// leads, and the raw page stays on disk beside them for citations. The
// scraping seam and the RLM are stand-ins: a program that prints the seam's
// JSON envelope, and a scripted OpenAI-compatible server on 127.0.0.1. Tor
// mode keeps the harness's own egress walk off the network.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT } from '../roles.ts';
import { fetchTool } from './fetch.ts';

const PAGE_URL = 'https://registry.example.org/company/42';
const RAW_PAGE = '# Company 42\n\nNavigation | Login | Cookies\n\nAda Lovelace is President of Analytical Engines SA since 2019.\n\nFooter boilerplate.\n';
const LEADS = '- Ada Lovelace: President, Analytical Engines SA (since 2019)';

type RLMRequest = { model: string; messages: Array<{ role: string; content: string }>; max_tokens: number };

async function standInRLM(t: TestContext, status = 200) {
	const requests: RLMRequest[] = [];
	const server = createServer((req, res) => {
		let body = '';
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			requests.push(JSON.parse(body) as RLMRequest);
			res.writeHead(status, { 'content-type': 'application/json' });
			res.end(status === 200 ? JSON.stringify({ choices: [{ message: { role: 'assistant', content: LEADS } }] }) : '{"error":"overloaded"}');
		});
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	return { baseURL: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`, requests };
}

async function fixture(t: TestContext, env: Record<string, string>) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-fetch-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const seam = join(root, 'python');
	const envelope = { markdown: RAW_PAGE, source_url: PAGE_URL, provider: 'crawl4ai', status_code: 200, title: 'Company 42' };
	await writeFile(seam, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(JSON.stringify(envelope))});\n`);
	await chmod(seam, 0o755);
	const tool = fetchTool({
		caseDir,
		writeRoot: caseDir,
		python: seam,
		configPath: join(root, 'config.json'),
		env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_ANONYMIZE_FETCH: 'true', ...env },
	}) as ToolDefinition;
	const call = async (params: Record<string, unknown>) =>
		JSON.parse((await tool.execute('call', params, undefined, undefined, undefined as never)).content[0].text) as Record<string, unknown>;
	return { caseDir, call };
}

const sha256 = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');

// The validators' own source_ref check (scripts/evidence_anchors.py), run on a case file.
function validatorErrors(caseDir: string, path: string, lineStart: number, lineEnd: number, quote: string): string[] {
	const script = [
		'import json, sys',
		'from pathlib import Path',
		'sys.path.insert(0, sys.argv[1])',
		'from evidence_anchors import validate_source_ref',
		'_, errors = validate_source_ref(Path(sys.argv[2]), {"path": sys.argv[3], "line_start": int(sys.argv[4]), "line_end": int(sys.argv[5])}, sys.argv[6])',
		'print(json.dumps(errors))',
	].join('\n');
	const python = process.platform === 'win32' ? 'python' : 'python3';
	const out = execFileSync(python, ['-c', script, join(CHECKOUT_ROOT, 'scripts'), caseDir, path, String(lineStart), String(lineEnd), quote], { encoding: 'utf8' });
	return JSON.parse(out) as string[];
}

test('on the local tier fetch returns RLM leads, keeps the raw page beside them, and the validators anchor only on the raw page', async (t) => {
	const rlm = await standInRLM(t);
	const { caseDir, call } = await fixture(t, {
		SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL,
		SPOTLIGHT_RLM_OPENAI_MODEL: 'e4b-test',
		SPOTLIGHT_RLM_INFERENCE: 'own',
	});

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	assert.equal(rlm.requests.length, 1);
	assert.equal(rlm.requests[0].model, 'e4b-test');
	assert.match(rlm.requests[0].messages[0].content, /Ada Lovelace is President of Analytical Engines SA since 2019\./);
	assert.equal(result.distilled, true);
	assert.equal(result.saved_to, 'research/company-42.md');
	assert.equal(result.raw_path, 'research/company-42.md.raw');
	assert.match(String(result.leads), /Ada Lovelace: President/);
	assert.equal(JSON.stringify(result).includes('Footer boilerplate'), false, 'the raw page must not reach the agent');

	const leadsBytes = await readFile(join(caseDir, 'research/company-42.md'));
	const rawBytes = await readFile(join(caseDir, 'research/company-42.md.raw'));
	assert.equal(rawBytes.toString('utf8'), RAW_PAGE);
	assert.equal(result.leads_sha256, sha256(leadsBytes));
	assert.equal(result.raw_sha256, sha256(rawBytes));

	const quote = 'Ada Lovelace is President of Analytical Engines SA since 2019.';
	const leadErrors = validatorErrors(caseDir, 'research/company-42.md', 1, 1, quote);
	assert.equal(leadErrors.length, 1);
	assert.match(leadErrors[0], /is an RLM-distilled lead file, not source evidence; cite company-42\.md\.raw instead/);
	assert.deepEqual(validatorErrors(caseDir, 'research/company-42.md.raw', 5, 5, quote), []);
});

test('when the RLM fails, fetch keeps the raw page on disk and still returns no raw text', async (t) => {
	const rlm = await standInRLM(t, 503);
	const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' });

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	assert.equal(result.distilled, false);
	assert.match(String(result.rlm_error), /503/);
	assert.equal(result.raw_path, 'research/company-42.md.raw');
	assert.equal(await readFile(join(caseDir, 'research/company-42.md.raw'), 'utf8'), RAW_PAGE);
	assert.equal(existsSync(join(caseDir, 'research/company-42.md')), false);
	assert.equal(JSON.stringify(result).includes('Footer boilerplate'), false);
});

test('fetch refuses an RLM endpoint the launcher did not confirm as one the operator runs, and a local-tier fetch with nowhere to keep the raw page', async (t) => {
	const rlm = await standInRLM(t);
	const unconfirmed = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL });
	await assert.rejects(unconfirmed.call({ url: PAGE_URL, save_as: 'research/a.md' }), /not confirmed as an endpoint you run yourself/);
	assert.equal(existsSync(join(unconfirmed.caseDir, 'research/a.md')), false);
	assert.equal(existsSync(join(unconfirmed.caseDir, 'research/a.md.raw')), false);

	const local = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' });
	await assert.rejects(local.call({ url: PAGE_URL }), /needs save_as/);
	assert.equal(rlm.requests.length, 0);
});

test('without an RLM endpoint fetch returns the page as before and says it was not distilled', async (t) => {
	const { caseDir, call } = await fixture(t, {});

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	assert.equal(result.distilled, false);
	assert.match(String(result.distillation), /SPOTLIGHT_RLM_OPENAI_BASE_URL is not set/);
	assert.equal(result.markdown, RAW_PAGE);
	assert.equal(await readFile(join(caseDir, 'research/company-42.md'), 'utf8'), RAW_PAGE);
	assert.equal(existsSync(join(caseDir, 'research/company-42.md.raw')), false);
});

// gold-asf s0 and gold-wikimedia s1 on 135e8796: with no time in the result, the model invented access dates and Wayback URLs.
test('fetch returns the harness time it fetched the page and no archive capture', async (t) => {
	const { call } = await fixture(t, {});
	const before = Date.now();

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	const fetchedAt = Date.parse(String(result.fetched_at));
	assert.match(String(result.fetched_at), /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
	assert.ok(fetchedAt >= before && fetchedAt <= Date.now());
	assert.equal(result.archive_url, null);
});

// E2E D1: a provider that compiles `pattern` as a whole-value grammar let the
// model emit only the prefix the pattern named: url "https://", save_as "research/".
test('the url and save_as patterns still admit a real URL and file when applied as full matches', () => {
	const properties = (fetchTool({ caseDir: '/', writeRoot: '/', python: 'python3', configPath: '/', env: {} }).parameters as unknown as {
		properties: Record<string, { pattern: string }>;
	}).properties;
	const fullMatch = (name: string, value: string) => new RegExp(`^(?:${properties[name].pattern})$`).test(value);

	assert.equal(fullMatch('url', PAGE_URL), true);
	assert.equal(fullMatch('url', 'https://'), false);
	assert.equal(fullMatch('save_as', 'research/company-42.md'), true);
	assert.equal(fullMatch('save_as', 'research/'), false);
	assert.equal(fullMatch('save_as', 'company-42.md'), true);
});

// Gold run 1: the teacher sent save_as "executive-team.md" and was refused 14 times.
test('fetch saves a bare save_as file name under research/', async (t) => {
	const { caseDir, call } = await fixture(t, {});

	const result = await call({ url: PAGE_URL, save_as: 'company-42.md' });

	assert.equal(result.saved_to, 'research/company-42.md');
	assert.equal(await readFile(join(caseDir, 'research/company-42.md'), 'utf8'), RAW_PAGE);
});

// E2E D12: save_as *.json held fenced Markdown, so JSON-pointer anchors failed.
test('fetch refuses a .json save_as and writes nothing', async (t) => {
	const { caseDir, call } = await fixture(t, {});

	await assert.rejects(call({ url: PAGE_URL, save_as: 'research/company-42.JSON' }), /http_get/);
	assert.equal(existsSync(join(caseDir, 'research/company-42.JSON')), false);
});
