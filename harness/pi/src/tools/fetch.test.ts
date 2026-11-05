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
import { searchTool } from './search.ts';

const PAGE_URL = 'https://registry.example.org/company/42';
const RAW_PAGE = '# Company 42\n\nNavigation | Login | Cookies\n\nAda Lovelace is President of Analytical Engines SA since 2019.\n\nFooter boilerplate.\n';
const LEADS = JSON.stringify({ ranges: [{ start: 5, end: 5 }] });

type RLMRequest = { model: string; messages: Array<{ role: string; content: string }>; max_tokens: number; response_format?: { type: string; json_schema: { strict: boolean; schema: Record<string, unknown> } } };

async function standInRLM(t: TestContext, status = 200, select: (request: RLMRequest) => string = () => LEADS) {
	const requests: RLMRequest[] = [];
	const server = createServer((req, res) => {
		let body = '';
		req.on('data', (chunk) => (body += chunk));
		req.on('end', () => {
			requests.push(JSON.parse(body) as RLMRequest);
			res.writeHead(status, { 'content-type': 'application/json' });
			res.end(status === 200 ? JSON.stringify({ choices: [{ message: { role: 'assistant', content: select(requests[requests.length - 1]) } }] }) : '{"error":"overloaded"}');
		});
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	return { baseURL: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`, requests };
}

async function fixture(t: TestContext, env: Record<string, string>, page = RAW_PAGE) {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-fetch-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const seam = join(root, 'python');
	const envelope = { markdown: page, source_url: PAGE_URL, provider: 'crawl4ai', status_code: 200, title: 'Company 42' };
	await writeFile(seam, `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(JSON.stringify(envelope))});\n`);
	await chmod(seam, 0o755);
	const tool = fetchTool({
		caseDir,
		writeRoot: caseDir,
		python: seam,
		configPath: join(root, 'config.json'),
		env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_ANONYMIZE_FETCH: 'true', ...env },
	}) as ToolDefinition;
	const call = async (params: Record<string, unknown>, signal?: AbortSignal) =>
		JSON.parse((await tool.execute('call', params, signal, undefined, undefined as never)).content[0].text) as Record<string, unknown>;
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
	const scope = 'Compare original and revised August euro-area unemployment, preserving release dates.';
	const taskHeader = scope.padEnd(4000, ' ') + 'OUTSIDE-SCOPE-LIMIT';
	const rlm = await standInRLM(t, 200, () => '```json\n' + LEADS + '\n```');
	const { caseDir, call } = await fixture(t, {
		SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL,
		SPOTLIGHT_RLM_OPENAI_MODEL: 'e4b-test',
		SPOTLIGHT_RLM_INFERENCE: 'own',
		SPOTLIGHT_TASK_HEADER: taskHeader,
	});

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	assert.equal(rlm.requests.length, 1);
	assert.equal(rlm.requests[0].model, 'e4b-test');
	const requestText = rlm.requests[0].messages.map(message => message.content).join('\n');
	assert.ok(requestText.includes(scope), 'the helper needs its bounded investigation scope');
	assert.ok(!requestText.includes('OUTSIDE-SCOPE-LIMIT'), 'scope is capped at 4000 characters');
	const format = rlm.requests[0].response_format;
	assert.equal(format?.type, 'json_schema', 'the helper response is constrained as structured JSON');
	assert.equal(format?.json_schema.strict, true);
	const schema = format!.json_schema.schema;
	assert.equal(schema.type, 'object');
	assert.equal(schema.additionalProperties, false);
	assert.deepEqual(schema.required, ['ranges']);
	assert.deepEqual(Object.keys(schema.properties as object), ['ranges']);
	const ranges = (schema.properties as { ranges: { type: string; items: { type: string; required: string[]; additionalProperties: boolean; properties: Record<string, { type: string }> } } }).ranges;
	assert.equal(ranges.type, 'array');
	assert.equal(ranges.items.type, 'object');
	assert.equal(ranges.items.additionalProperties, false);
	assert.deepEqual(new Set(ranges.items.required), new Set(['start', 'end']));
	assert.deepEqual(Object.keys(ranges.items.properties).sort(), ['end', 'start']);
	assert.equal(ranges.items.properties.start.type, 'integer');
	assert.equal(ranges.items.properties.end.type, 'integer');
	assert.match(rlm.requests[0].messages[0].content, /Ada Lovelace is President of Analytical Engines SA since 2019\./);
	assert.equal(result.distilled, true);
	assert.equal(result.saved_to, 'research/company-42.md');
	assert.equal(result.raw_path, 'research/company-42.md.raw');
	assert.match(String(result.leads), /Ada Lovelace is President of Analytical Engines SA since 2019\./);
	assert.match(String(result.leads), /lines 2-7/, 'the pick keeps three lines either side, up to the end of the inspected page');
	assert.deepEqual(result.inspected_lines, { start: 1, end: 7, total: 7 });
	assert.equal(result.coverage_complete, true);
	assert.deepEqual(result.selections, [{ path: 'research/company-42.md.raw', line_start: 2, line_end: 7 }]);
	assert.equal(String(result.leads).includes('# Company 42'), false, 'the raw page must not reach the agent: line 1 is outside the widened pick');

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

test('invalid or invented selections never become source extracts', async (t) => {
	for (const reply of ['summary not JSON', '```json\n' + JSON.stringify({ranges:[{start:5,end:50}]}) + '\n```', '```json\n' + LEADS + '\n``` trailing prose', JSON.stringify({ranges:[{start:5,end:50}]}), JSON.stringify({ranges:[{start:5,end:5,quote:'invented'}]}), JSON.stringify({ranges:[]})]) await t.test(reply.slice(0, 30), async (t) => {
		const rlm = await standInRLM(t, 200, () => reply);
		const { caseDir, call } = await fixture(t, {SPOTLIGHT_RLM_OPENAI_BASE_URL:rlm.baseURL,SPOTLIGHT_RLM_INFERENCE:'own'});
		const result = await call({url:PAGE_URL,save_as:'research/a.md'});
		assert.equal(result.distilled,false);
		assert.equal(await readFile(join(caseDir,'research/a.md.raw'),'utf8'),RAW_PAGE);
		assert.equal(existsSync(join(caseDir,'research/a.md')),false);
	});
});

// Constellation G4a: the helper is a small model, so every malformed shape it
// plausibly emits must degrade to the raw page, never to a made-up extract.
test('malformed helper selections of every plausible shape never become source extracts', async (t) => {
	const ok = (ranges: unknown) => JSON.stringify({ ranges });
	const shapes: Array<[string, (first: number) => string]> = [
		['fractional start', () => ok([{ start: 5.5, end: 6 }])],
		['fractional end', () => ok([{ start: 5, end: 5.5 }])],
		['numeric strings', () => ok([{ start: '5', end: '5' }])],
		['end before start', () => ok([{ start: 6, end: 5 }])],
		['start of zero', () => ok([{ start: 0, end: 1 }])],
		['negative start', () => ok([{ start: -3, end: 5 }])],
		['extra key beside start and end', () => ok([{ start: 5, end: 5, reason: 'the title' }])],
		['extra top-level key', () => JSON.stringify({ ranges: [{ start: 5, end: 5 }], reason: 'x' })],
		['reasoning block before the JSON', () => `<think>The president line is 5.</think>${ok([{ start: 5, end: 5 }])}`],
		['a bare range list', () => JSON.stringify([{ start: 5, end: 5 }])],
		['one bad range among good ones', () => ok([{ start: 5, end: 5 }, { start: 5, end: 99 }])],
		['null range', () => ok([null])],
	];
	for (const [name, reply] of shapes) await t.test(name, async (t) => {
		const rlm = await standInRLM(t, 200, (request) => reply(Number(request.messages[0].content.match(/^(\d+): /m)?.[1])));
		const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' });
		const result = await call({ url: PAGE_URL, save_as: 'research/a.md' });
		assert.equal(result.distilled, false);
		assert.equal(await readFile(join(caseDir, 'research/a.md.raw'), 'utf8'), RAW_PAGE);
		assert.equal(existsSync(join(caseDir, 'research/a.md')), false);
		assert.equal(JSON.stringify(result).includes('Ada Lovelace'), false, 'no page text reaches the agent');
	});
});

test('a later chunk cannot select lines of an earlier chunk, and overlapping ranges still extract only exact raw lines', async (t) => {
	const page = Array.from({ length: 400 }, (_, i) => `line ${i + 1} ${'x'.repeat(150)}`).join('\n');
	const numbersOf = (request: RLMRequest) => [...request.messages[0].content.matchAll(/^(\d+): /gm)].map((m) => Number(m[1]));
	const back = await standInRLM(t, 200, (request) => {
		const numbers = numbersOf(request);
		return JSON.stringify({ ranges: [{ start: numbers[0] === 1 ? 1 : numbers[0] - 1, end: numbers[0] === 1 ? 1 : numbers[0] }] });
	});
	const rejected = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: back.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' }, page);
	const refused = await rejected.call({ url: PAGE_URL, save_as: 'research/back.md' });
	assert.equal(back.requests.length, 2, 'the second chunk was asked and answered with a line from the first');
	assert.equal(refused.distilled, false);
	assert.match(String(refused.rlm_error), /outside the inspected range/);
	assert.equal(existsSync(join(rejected.caseDir, 'research/back.md')), false);

	const overlap = await standInRLM(t, 200, (request) => { const first = numbersOf(request)[0]; return JSON.stringify({ ranges: [{ start: first + 1, end: first + 3 }, { start: first + 2, end: first + 4 }, { start: first + 2, end: first + 4 }] }); });
	const accepted = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: overlap.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' }, page);
	const result = await accepted.call({ url: PAGE_URL, save_as: 'research/overlap.md' });
	assert.equal(result.distilled, true, String(result.rlm_error));
	const raw = (await readFile(join(accepted.caseDir, 'research/overlap.md.raw'), 'utf8')).split('\n');
	for (const selection of result.selections as Array<{ line_start: number; line_end: number }>) {
		assert.ok(String(result.leads).includes(raw.slice(selection.line_start - 1, selection.line_end).join('\n')), 'every extract is the exact raw lines it names');
	}
});

// qwen real launch (ab7e6c04): the helper picked the title line and not the name two lines above it, so the page was refetched seven times.
test('a picked line keeps three lines either side, so a role and its holder arrive together, within the inspected range', async (t) => {
	const page = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join('\n');
	const back = await standInRLM(t, 200, () => JSON.stringify({ ranges: [{ start: 10, end: 10 }, { start: 2, end: 2 }] }));
	const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: back.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' }, page);
	const result = await call({ url: PAGE_URL, save_as: 'research/people.md' });
	assert.equal(result.distilled, true, String(result.rlm_error));
	assert.deepEqual(result.selections, [{ path: 'research/people.md.raw', line_start: 1, line_end: 5 }, { path: 'research/people.md.raw', line_start: 7, line_end: 13 }]);
	assert.match(String(result.leads), /\[raw lines 7-13\]\nline 7\nline 8\nline 9\nline 10\nline 11\nline 12\nline 13/);
	assert.ok(existsSync(join(caseDir, 'research/people.md.raw')));
});

// gate9 case-ab7e6c04 and u9mac-s856: the helper never picked the line holding the role, so no lead held it.
test('an investigator\'s leads add the raw lines that match its direction, with their context, on the local tiers only', async (t) => {
	const page = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`);
	page[19] = '##### isabela';
	page[21] = 'Executive Director of the Tor Project.';
	page[34] = 'Director of nothing.';
	const plan = JSON.stringify({ investigation_plan: [
		{ direction: 'Identify current Executive Director', questions: ['Who is the current Executive Director of the Tor Project?'], direction_id: 'd1' },
		{ direction: 'Map the funding', questions: ['Which grants paid for relays?'], direction_id: 'd2' },
	] });
	const run = async (env: Record<string, string>, ranges: unknown[] = [{ start: 2, end: 2 }]) => {
		const back = await standInRLM(t, 200, () => JSON.stringify({ ranges }));
		const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: back.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own', SPOTLIGHT_ROLE: 'investigator', SPOTLIGHT_DIRECTION: 'D1', ...env }, page.join('\n'));
		await mkdir(join(caseDir, 'data'));
		await writeFile(join(caseDir, 'data', 'methodology.json'), plan);
		return call({ url: PAGE_URL, save_as: 'research/people.md' });
	};
	const added = await run({});
	assert.equal(added.distilled, true, String(added.rlm_error));
	assert.deepEqual(added.selections, [{ path: 'research/people.md.raw', line_start: 1, line_end: 5 }, { path: 'research/people.md.raw', line_start: 19, line_end: 25 }]);
	assert.match(String(added.leads), /##### isabela\nline 21\nExecutive Director of the Tor Project\./);
	assert.doesNotMatch(String(added.leads), /Director of nothing/, 'one term alone is not a match');
	// The helper answered with nothing: the matching lines still make a lead.
	const rescued = await run({}, []);
	assert.deepEqual(rescued.selections, [{ path: 'research/people.md.raw', line_start: 19, line_end: 25 }]);
	// Another direction's terms, the fact-checker and frontier add nothing.
	assert.deepEqual((await run({ SPOTLIGHT_DIRECTION: 'D2' })).selections, [{ path: 'research/people.md.raw', line_start: 1, line_end: 5 }]);
	assert.deepEqual((await run({ SPOTLIGHT_ROLE: 'fact-checker' })).selections, [{ path: 'research/fc-people.md.raw', line_start: 1, line_end: 5 }]);
	assert.deepEqual((await run({ SPOTLIGHT_MODEL_TIER: 'frontier' })).selections, [{ path: 'research/people.md.raw', line_start: 2, line_end: 2 }]);
});

// gate9 case-ab7e6c04: an investigator fetched the people page 7 and 6 times under different names.
test('an investigator\'s second fetch of a URL returns the capture it already has, on the local tiers only', async (t) => {
	const dataDir = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-reuse-')));
	t.after(() => rm(dataDir, { recursive: true, force: true }));
	const setup = async (env: Record<string, string> = {}) => {
		const back = await standInRLM(t);
		return { back, ...(await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: back.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own', SPOTLIGHT_ROLE: 'investigator', SPOTLIGHT_HARNESS_DATA: join(dataDir, String(Math.random())), ...env })) };
	};
	const { back, caseDir, call } = await setup();
	const first = await call({ url: PAGE_URL, save_as: 'research/a.md' });
	const again = await call({ url: `${PAGE_URL}#board`, save_as: 'research/b.md' });
	assert.equal(back.requests.length, 1, 'the helper is not asked again');
	assert.equal(again.reused, true);
	assert.equal(again.saved_to, 'research/a.md');
	assert.equal(again.raw_path, first.raw_path);
	assert.deepEqual(again.selections, first.selections);
	assert.equal(again.leads, first.leads);
	assert.equal(again.fetched_at, first.fetched_at);
	assert.match(String(again.note), /already fetched.*research\/a\.md/i);
	assert.ok(!existsSync(join(caseDir, 'research/b.md')) && !existsSync(join(caseDir, 'research/b.md.raw')));
	// A capture changed since it was fetched is no capture: the page is fetched afresh.
	await writeFile(join(caseDir, 'research/a.md.raw'), 'edited');
	const fresh = await call({ url: PAGE_URL, save_as: 'research/c.md' });
	assert.equal(fresh.reused, undefined);
	assert.equal(fresh.saved_to, 'research/c.md');
	assert.equal(back.requests.length, 2);
	// The fact-checker's capture stays its own acquisition, and frontier fetches as before.
	for (const env of [{ SPOTLIGHT_ROLE: 'fact-checker' }, { SPOTLIGHT_MODEL_TIER: 'frontier' }]) {
		const other = await setup(env);
		await other.call({ url: PAGE_URL, save_as: 'research/a.md' });
		assert.equal((await other.call({ url: PAGE_URL, save_as: 'research/b.md' })).reused, undefined);
		assert.equal(other.back.requests.length, 2);
	}
});

test('bounded chunks expose late exact passages and disclose uninspected tail', async (t) => {
	for (const rangeCount of [1, 14]) await t.test(`${rangeCount} exact ranges per selected chunk`, async (t) => {
		// Each pick keeps three lines below it (the chunk's end bounds it above); picks that touch merge into one range.
		const width = rangeCount === 1 ? 36 : 1;
		const widened = rangeCount * width + 3;
		const page = Array.from({length: 900}, (_, i) => `line ${i + 1} ${'x'.repeat(150)}`).join('\n');
		const rlm = await standInRLM(t, 200, request => {
			const numbers = [...request.messages[0].content.matchAll(/^(\d+): /gm)].map(m => Number(m[1]));
			const last = numbers.at(-1)!;
			return JSON.stringify({ranges: numbers[0] === 1 ? [] : Array.from({length:rangeCount}, (_, i) => ({start:last-width+1-i*width,end:last-i*width}))});
		});
		const { caseDir, call } = await fixture(t,{SPOTLIGHT_RLM_OPENAI_BASE_URL:rlm.baseURL,SPOTLIGHT_RLM_INFERENCE:'own'},page);
		const result = await call({url:PAGE_URL,save_as:'research/long.md'});
		assert.equal(result.distilled,true, String(result.rlm_error));
		assert.equal(rlm.requests.length,3);
		const coverage = result.inspected_lines as {end:number;total:number};
		assert.ok(coverage.end > 160 && coverage.end < coverage.total);
		assert.equal(result.coverage_complete, false);
		assert.match(String(result.coverage_note), /not inspected/);
		const selections = result.selections as Array<{line_start:number;line_end:number;path:string}>;
		assert.equal(selections.length,2,'one merged range per selected chunk');
		assert.ok(String(result.leads).length < 30_000);
		const rawLines = (await readFile(join(caseDir,'research/long.md.raw'),'utf8')).split('\n');
		for (const selection of selections) {
			assert.equal(selection.line_end - selection.line_start + 1, widened);
			assert.ok(String(result.leads).includes(rawLines.slice(selection.line_start-1,selection.line_end).join('\n')));
		}
		assert.ok(selections[0].line_start > 160,'selection after an empty first chunk reaches beyond the old input prefix');
	});
});

test('oversized combined selections fall back to the preserved raw page', async (t) => {
	const page = Array.from({length: 350}, (_, i) => `line ${i + 1} ${'x'.repeat(150)}`).join('\n');
	const rlm = await standInRLM(t, 200, request => {
		const numbers = [...request.messages[0].content.matchAll(/^(\d+): /gm)].map(m => Number(m[1]));
		return JSON.stringify({ranges:[{start:numbers[0],end:numbers.at(-1)}]});
	});
	const {caseDir,call} = await fixture(t,{SPOTLIGHT_RLM_OPENAI_BASE_URL:rlm.baseURL,SPOTLIGHT_RLM_INFERENCE:'own'},page);
	const result = await call({url:PAGE_URL,save_as:'research/oversized.md'});
	assert.equal(result.distilled,false);
	assert.match(String(result.rlm_error),/output bound/);
	assert.equal(await readFile(join(caseDir,'research/oversized.md.raw'),'utf8'),page);
	assert.equal(existsSync(join(caseDir,'research/oversized.md')),false);
});

test('CRLF source selections use the same line numbers as source anchors', async (t) => {
	const page = 'Title\r\n\r\nCafé filed the report.\r\n';
	const rlm = await standInRLM(t, 200, () => '```\n' + JSON.stringify({ranges:[{start:3,end:3}]}) + '\n```');
	const { caseDir, call } = await fixture(t,{SPOTLIGHT_RLM_OPENAI_BASE_URL:rlm.baseURL,SPOTLIGHT_RLM_INFERENCE:'own'},page);
	const result = await call({url:PAGE_URL,save_as:'research/crlf.md'});
	assert.equal(result.distilled,true);
	assert.deepEqual(result.inspected_lines,{start:1,end:3,total:3});
	assert.deepEqual(result.selections,[{path:'research/crlf.md.raw',line_start:1,line_end:3}]);
	assert.match(String(result.leads), /Café filed the report\./);
	assert.deepEqual(validatorErrors(caseDir,'research/crlf.md.raw',3,3,'Café filed the report.'),[]);
	assert.equal(await readFile(join(caseDir,'research/crlf.md.raw'),'utf8'),page);
});

test('a later invalid chunk rejects all extracts and preserves the full raw page', async (t) => {
	const page = Array.from({length: 400}, (_, i) => `line ${i + 1} ${'x'.repeat(150)}`).join('\r\n');
	let calls = 0;
	const rlm = await standInRLM(t, 200, request => {
		calls++;
		const numbers = [...request.messages[0].content.matchAll(/^(\d+): /gm)].map(m => Number(m[1]));
		return JSON.stringify({ranges:[{start:numbers[0],end:calls === 2 ? numbers.at(-1)! + 1 : numbers[0]}]});
	});
	const { caseDir, call } = await fixture(t,{SPOTLIGHT_RLM_OPENAI_BASE_URL:rlm.baseURL,SPOTLIGHT_RLM_INFERENCE:'own'},page);
	const result = await call({url:PAGE_URL,save_as:'research/late.md'});
	assert.equal(calls,2);
	assert.equal(result.distilled,false);
	assert.equal(await readFile(join(caseDir,'research/late.md.raw'),'utf8'),page);
	assert.equal(existsSync(join(caseDir,'research/late.md')),false);
});

test('when the RLM fails, fetch keeps the raw page on disk and still returns no raw text', async (t) => {
	const rlm = await standInRLM(t, 503);
	const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL, SPOTLIGHT_RLM_INFERENCE: 'own' });

	const result = await call({ url: PAGE_URL, save_as: 'research/company-42.md' });

	assert.equal(result.distilled, false);
	assert.match(String(result.rlm_error), /503/);
	assert.equal(result.raw_path, 'research/company-42.md.raw');
	assert.match(String(result.note), /No distilled output was written to save_as/i);
	assert.match(String(result.note), /raw_path/);
	assert.equal(await readFile(join(caseDir, 'research/company-42.md.raw'), 'utf8'), RAW_PAGE);
	assert.equal(existsSync(join(caseDir, 'research/company-42.md')), false);
	assert.equal(JSON.stringify(result).includes('Footer boilerplate'), false);
});

// Constellation G4b: a helper that accepts the request and never answers must
// cost the worker a bounded wait, not the page. The tool's abort signal is the
// seam (RLM_TIMEOUT_MS joins it in rlmComplete), so no test-only knob is needed.
test('a helper that never answers leaves the raw page on disk and returns distilled:false once the call is cut off', async (t) => {
	const sockets = new Set<import('node:net').Socket>();
	let asked = 0;
	const server = createServer(() => { asked++; });
	server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => new Promise<void>((resolve) => { for (const socket of sockets) socket.destroy(); server.close(() => resolve()); }));
	const { caseDir, call } = await fixture(t, { SPOTLIGHT_RLM_OPENAI_BASE_URL: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`, SPOTLIGHT_RLM_INFERENCE: 'own' });
	const cutOff = new AbortController();
	setTimeout(() => cutOff.abort(), 500).unref();
	const started = Date.now();
	const result = await call({ url: PAGE_URL, save_as: 'research/hang.md' }, cutOff.signal);
	assert.ok(Date.now() - started < 10_000, 'the call returns promptly once cut off');
	assert.equal(asked, 1, 'the helper was asked once and never retried');
	assert.equal(result.distilled, false);
	assert.match(String(result.rlm_error), /abort|cancel/i);
	assert.equal(await readFile(join(caseDir, 'research/hang.md.raw'), 'utf8'), RAW_PAGE);
	assert.equal(existsSync(join(caseDir, 'research/hang.md')), false);
	assert.equal(JSON.stringify(result).includes('Ada Lovelace'), false, 'no page text reaches the agent');
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

// fc-stall items 4 and 13 (pq-gold-wikimedia on 52bf6183: one Reuters page
// fetched 43 times; each failure was crawl4ai's log with a deprecation warning
// first): the cause comes first, and a page a site blocked is not fetched again.
test('a failed fetch says its cause first, and a page a site blocked is refused the next time with the next step', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-fetch-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const runs = join(root, 'runs.txt');
	const stderr = [
		"/x/crawl4ai/async_configs.py:853: UserWarning: The 'proxy' parameter is deprecated and will be removed in a future release. Use 'proxy_config' instead.",
		'  warnings.warn("The \'proxy\' parameter is deprecated", UserWarning)',
		'[FETCH]... ↓ https://news.example.com/story  | ✓ | ⏱: 0.92s',
		'[ERROR]... × https://news.example.com/story  | Error: Blocked by anti-bot protection: DataDome captcha',
		'scrape failed: crawl4ai was bot-blocked/empty on https://news.example.com/story and no FIRECRAWL_API_KEY is set',
	].join('\n');
	const seam = join(root, 'python');
	await writeFile(seam, `#!${process.execPath}\nrequire('fs').appendFileSync(${JSON.stringify(runs)}, 'x');\nprocess.stderr.write(${JSON.stringify(stderr)});\nprocess.exit(3);\n`);
	await chmod(seam, 0o755);
	const tool = fetchTool({
		caseDir,
		writeRoot: caseDir,
		python: seam,
		configPath: join(root, 'config.json'),
		env: { PATH: process.env.PATH, HOME: root, SPOTLIGHT_ANONYMIZE_FETCH: 'true', SPOTLIGHT_HARNESS_DATA: join(root, 'data') },
	}) as ToolDefinition;
	const fetchOnce = () => tool.execute('call', { url: 'https://news.example.com/story', save_as: 'story.md' }, undefined, undefined, undefined as never);
	await assert.rejects(fetchOnce(), (error: Error) => error.message === 'fetch failed: crawl4ai was bot-blocked/empty on https://news.example.com/story and no FIRECRAWL_API_KEY is set');
	await assert.rejects(fetchOnce(), /^Error: fetch: https:\/\/news\.example\.com\/story already failed in this case \(crawl4ai was bot-blocked[^)]*\); it is not fetched again\. Try its archived copy \(https:\/\/web\.archive\.org\/web\/https:\/\/news\.example\.com\/story\) with fetch, open it with browse, or use another source\.$/);
	assert.equal(await readFile(runs, 'utf8'), 'x', 'the blocked page is fetched once');
});

// fc-stall item 14: 48 fact-checker saves were refused for landing on a research
// file an investigator's citation points at. Its saves take an fc- prefix.
test('a fact-checker\'s fetch is saved under an fc- name, an investigator\'s as given', async (t) => {
	const checker = await fixture(t, { SPOTLIGHT_ROLE: 'fact-checker' });
	assert.equal((await checker.call({ url: PAGE_URL, save_as: 'research/company-42.md' })).saved_to, 'research/fc-company-42.md');
	assert.equal((await checker.call({ url: PAGE_URL, save_as: 'fc-again.md' })).saved_to, 'research/fc-again.md');
	const investigator = await fixture(t, { SPOTLIGHT_ROLE: 'investigator' });
	assert.equal((await investigator.call({ url: PAGE_URL, save_as: 'research/company-42.md' })).saved_to, 'research/company-42.md');
});

// briefs/qwen-lanes.md Search modes: the seam is the only guard for a sensitive case, and a program sees only the variables it is handed.
test('the search and fetch seams are told a run is sensitive', async (t) => {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-seam-env-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'research'), { recursive: true });
	const seen = join(root, 'seen.txt');
	const seam = join(root, 'python');
	await writeFile(seam, `#!/bin/sh\necho "$SPOTLIGHT_SENSITIVE" >> ${JSON.stringify(seen)}\necho '[]'\n`);
	await chmod(seam, 0o755);
	const context = { caseDir, writeRoot: caseDir, python: seam, configPath: join(root, 'config.json'), env: { PATH: process.env.PATH, SPOTLIGHT_SENSITIVE: 'true', SPOTLIGHT_ANONYMIZE_FETCH: 'true' } };
	await searchTool(context).execute('s', { query: 'Acme filing' }, undefined, undefined, undefined as never);
	await fetchTool(context).execute('f', { url: PAGE_URL }, undefined, undefined, undefined as never).catch(() => undefined);
	assert.deepEqual((await readFile(seen, 'utf8')).trim().split('\n'), ['true', 'true']);
});
