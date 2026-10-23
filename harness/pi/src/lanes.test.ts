// The research lane runner against two scripted OpenAI-compatible servers on
// 127.0.0.1: one stands in for the swarm endpoint (OpenRouter), the other for
// the operator's local model. The search and scraping seams are a stand-in
// program; Tor mode keeps fetch's own egress walk off the network. Nothing
// here reaches the internet or a real model.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chmod, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { readConfig } from './config.ts';
import { ROUND_LEDGER, runResearchRound } from './lanes.ts';
import type { ResearchPlan, RoundSummary, TopicBrief } from './lanes.ts';
import { startFakeOpenAI } from './test-support/fake-openai.ts';
import type { ChatRequest, FakeOpenAI, Reply, ToolCall } from './test-support/fake-openai.ts';

const KEY = 'sk-or-v1-member-secret';
const PAGE_URL = 'https://registry.example.org/company/42';
const PAGE = '# Company 42\n\nAda Lovelace is President of Analytical Engines SA since 2019.\n\nFooter.\n';
const VERBATIM = 'Ada Lovelace is President of Analytical Engines SA since 2019.';
const SNIPPET_URL = 'https://news.example.org/appointment';
const SNIPPET = 'Analytical Engines SA names Ada Lovelace as its President';
const FINDING_SENTINEL = 'FINDING-SENTINEL-7Q2 the director laundered funds';
const JOURNALIST_SENTINEL = 'JOURNALIST-SENTINEL-K9 source says the mayor knew';

type Case = { root: string; caseDir: string; outside: string; env: Record<string, string> };

async function setup(t: TestContext): Promise<Case> {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-lanes-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'cases', 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await mkdir(join(caseDir, 'research'), { recursive: true });
	await writeFile(join(caseDir, 'data/findings.json'), JSON.stringify({ findings: [{ claim: FINDING_SENTINEL }] }));
	await writeFile(join(caseDir, 'research/notes.md'), `Interview notes.\n${JOURNALIST_SENTINEL}\n`);
	const outside = join(root, 'outside');
	await mkdir(outside);
	// The search and scraping seams, as the typed tools call them.
	const seam = join(root, 'python');
	await writeFile(
		seam,
		`#!${process.execPath}
const args = process.argv.slice(2);
if (args.includes('integrations.scraping')) {
	process.stdout.write(JSON.stringify({ markdown: ${JSON.stringify(PAGE)}, source_url: args.at(-1), provider: 'crawl4ai', status_code: 200, title: 'Company 42' }));
} else if (args.includes('integrations.search')) {
	process.stdout.write(JSON.stringify([{ url: ${JSON.stringify(SNIPPET_URL)}, title: 'Appointment', snippet: ${JSON.stringify(SNIPPET)}, date: null, engine: 'stub' }]));
} else {
	// The research checks and the merge run on the real interpreter.
	const run = require('node:child_process').spawnSync(process.env.SPOTLIGHT_TEST_PYTHON || 'python3', args, { stdio: 'inherit' });
	process.exit(run.status ?? 2);
}
`,
	);
	await writeFile(join(root, 'package.json'), '{"type": "commonjs"}');
	await chmod(seam, 0o755);
	return {
		root,
		caseDir,
		outside,
		env: {
			PATH: process.env.PATH ?? '',
			HOME: root,
			SPOTLIGHT_CASES_ROOT: join(root, 'cases'),
			SPOTLIGHT_CASE_ID: 'case-a',
			SPOTLIGHT_HARNESS_DATA: join(root, 'data'),
			SPOTLIGHT_PYTHON: seam,
			SPOTLIGHT_ANONYMIZE_FETCH: 'true',
			SPOTLIGHT_SENSITIVE: 'false',
		},
	};
}

function topic(id: string, lane = 'web', extra: Partial<TopicBrief> = {}): TopicBrief {
	return { topic_id: id, direction_id: 'ownership', lane_type: lane, questions: [`Who runs ${id}?`], seed_queries: ['Analytical Engines SA president'], ...extra };
}

function plan(topics: TopicBrief[], extra: Partial<ResearchPlan> = {}): ResearchPlan {
	return {
		round_budget_usd: 1,
		case_budget_usd: 5,
		mode: 'openrouter_swarm',
		consent: { swarm: true, decided_by: 'journalist:test', decided_at: '2026-10-23T09:00:00Z' },
		keys: [],
		egress: [],
		topics,
		...extra,
	};
}

const call = (name: string, args: Record<string, unknown>): Reply => ({ toolCalls: [{ name, arguments: args } as ToolCall] });
const done: Reply = { text: 'Done: one lead.' };
const lead = (source: string, text: string, extra: Record<string, unknown> = {}) =>
	call('record_lead', { source_id: source, summary: 'Ada Lovelace is President of Analytical Engines SA.', subjects: ['Ada Lovelace', 'Analytical Engines SA'], excerpt: { text, language: 'en', direct_quote: false }, ...extra });

const topicOf = (request: ChatRequest) => /"topic_id": "([^"]+)"/.exec(String(request.messages[1]?.content))?.[1] ?? '';
const turnOf = (request: ChatRequest) => request.messages.filter((message) => message.role === 'assistant').length;
const toolResults = (request: ChatRequest) => request.messages.filter((message) => message.role === 'tool').map((message) => String(message.content));

// Each topic follows its own script, one reply per turn; past the end it says it is done.
function scripted(scripts: Record<string, Reply[]>, usage = { total_tokens: 1000, cost: 0.001 }) {
	return (request: ChatRequest): Reply => {
		const reply = scripts[topicOf(request)]?.[turnOf(request)] ?? done;
		return typeof reply === 'object' && !('status' in reply) ? { ...reply, usage } : reply;
	};
}

async function servers(t: TestContext, swarm: (request: ChatRequest) => Reply, local: (request: ChatRequest) => Reply = () => done) {
	const pair = { swarm: await startFakeOpenAI(swarm), local: await startFakeOpenAI(local) };
	t.after(() => Promise.all([pair.swarm.close(), pair.local.close()]));
	return pair;
}

async function round(
	space: Case,
	fakes: { swarm: FakeOpenAI; local: FakeOpenAI },
	researchPlan: ResearchPlan,
	extraEnv: Record<string, string> = {},
	retry = false,
): Promise<RoundSummary> {
	const env = {
		...space.env,
		SPOTLIGHT_MODEL_BASE_URL: fakes.local.baseURL,
		SPOTLIGHT_MODEL_ID: 'local-model',
		SPOTLIGHT_RESEARCH_SWARMS: 'on',
		OPENROUTER_API_KEY: KEY,
		SPOTLIGHT_SWARM_BASE_URL: fakes.swarm.baseURL,
		SPOTLIGHT_SWARM_CONCURRENCY: '1',
		...extraEnv,
	};
	const config = readConfig(env);
	await writeFile(join(space.caseDir, 'data/methodology.json'), JSON.stringify({ schema_version: '1.0', investigation_plan: [], research_plan: researchPlan }));
	return runResearchRound({ caseDir: space.caseDir, plan: researchPlan, config, local: { baseURL: fakes.local.baseURL, model: 'local-model' }, env, retry, retryDelayMs: 1 });
}

async function leadsOf(space: Case, topicId: string): Promise<Array<Record<string, any>>> {
	const text = await readFile(join(space.caseDir, 'research/swarm', topicId, 'leads.jsonl'), 'utf8');
	return text.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
}

async function laneOf(space: Case, topicId: string): Promise<Record<string, any>> {
	return JSON.parse(await readFile(join(space.caseDir, 'research/swarm', topicId, 'lane.json'), 'utf8'));
}

test('a swarm request carries only its approved brief and its own web results; case files, findings and journalist input stay local', async (t) => {
	const space = await setup(t);
	const fakes = await servers(
		t,
		scripted({
			'ownership-web': [
				call('search', { query: 'Analytical Engines SA president' }),
				call('fetch', { url: PAGE_URL }),
				lead('s2', VERBATIM, { facts: [{ subject: 'Analytical Engines SA', attribute: 'president', value: 'Ada Lovelace' }, { subject: 'Analytical Engines SA', attribute: 'founded', value: '1843' }] }),
				lead('s1', SNIPPET),
			],
		}),
		scripted({ 'ownership-local-corpus': [call('read_case_file', { path: 'research/notes.md' }), lead('s1', JOURNALIST_SENTINEL)] }),
	);
	const smuggled = { ...topic('ownership-news-courts', 'news-courts'), findings: [FINDING_SENTINEL] } as TopicBrief;
	const summary = await round(
		space,
		fakes,
		plan(
			[
				topic('ownership-web'),
				topic('ownership-local-corpus', 'local-corpus', { journalist_input: true }),
				smuggled,
				topic('ownership-local-corpus-2', 'local-corpus', { route: 'swarm' }),
			],
			{ consent: { swarm: true, journalist_input: true, decided_by: 'journalist:test', decided_at: '2026-10-23T09:00:00Z' } },
		),
	);

	assert.equal(summary.stopped, undefined);
	assert.deepEqual(summary.refused.map((item) => item.topic_id).sort(), ['ownership-local-corpus-2', 'ownership-news-courts']);
	assert.ok(fakes.swarm.requests.length > 0);
	for (const request of fakes.swarm.requests) {
		const body = JSON.stringify(request);
		assert.equal(topicOf(request), 'ownership-web', 'only the web topic reaches the swarm');
		assert.ok(!body.includes(FINDING_SENTINEL) && !body.includes(JOURNALIST_SENTINEL) && !body.includes('notes.md'), 'no case material in a swarm request');
		assert.ok(!body.includes(KEY), 'the key travels only in the Authorization header');
		assert.deepEqual((request.tools ?? []).map((tool) => tool.function.name).sort(), ['fetch', 'record_absence', 'record_lead', 'search']);
		assert.deepEqual(request.provider, { zdr: true });
		assert.equal(request.messages.length >= 2 && request.messages[0].role === 'system' && request.messages[1].role === 'user', true);
	}
	assert.ok(fakes.local.requests.length > 0);
	assert.ok(fakes.local.requests.every((request) => topicOf(request) === 'ownership-local-corpus'), 'the local corpus lane runs on the local model only');

	const [page, snippet] = await leadsOf(space, 'ownership-web');
	const stored = await readFile(join(space.caseDir, 'research/swarm/ownership-web', page.content_ref));
	assert.equal(page.status, 'candidate');
	assert.equal(page.stored, 'page');
	assert.equal(page.content_sha256, createHash('sha256').update(stored).digest('hex'));
	assert.equal(page.capture.source_url, PAGE_URL);
	assert.equal(page.provenance.method, 'agent_candidate');
	assert.deepEqual(page.facts, [{ subject: 'Analytical Engines SA', attribute: 'president', value: 'Ada Lovelace' }], 'a fact whose value is not in the excerpt is dropped');
	assert.equal(snippet.stored, 'snippet');
	assert.equal(snippet.status, 'unverifiable', 'D3: a snippet has no stored source to find the excerpt in');
	assert.equal(snippet.capture.source_url, SNIPPET_URL);
	for (const field of ['content_ref', 'content_sha256', 'derivatives']) assert.equal(field in snippet, false, `a snippet lead has no ${field}`);
	const [local] = await leadsOf(space, 'ownership-local-corpus');
	assert.equal(local.journalist_input, true);
	assert.equal(local.capture.route, 'manual');
	assert.equal((await laneOf(space, 'ownership-web')).status, 'completed');
	assert.equal((await laneOf(space, 'ownership-news-courts')).status, 'failed', 'a refused topic still ends, so the merge can run');

	// Every approved topic ended, so the harness ran the merge itself.
	assert.equal(summary.merge?.ok, true, JSON.stringify(summary.merge));
	const merged = JSON.parse(await readFile(join(space.caseDir, 'data/leads.json'), 'utf8'));
	assert.equal(merged.round, 1);
	assert.ok(merged.leads.some((entry: { lead: { lead_id: string } }) => entry.lead.lead_id === page.lead_id));
	await readFile(join(space.caseDir, 'data/clues.json'));
});

test('no swarm request without the opt-in: switch off, no consent, or a local-only plan', async (t) => {
	const space = await setup(t);
	const fakes = await servers(t, scripted({}));

	const localOnly = await round(space, fakes, plan([topic('ownership-web')], { mode: 'local_only' }));
	assert.deepEqual(localOnly.left_out.map((item) => item.topic_id), ['ownership-web']);
	const noConsent = await round(space, fakes, plan([topic('ownership-web')], { consent: { swarm: false, decided_by: 'j', decided_at: '2026-10-23T09:00:00Z' } }));
	assert.deepEqual(noConsent.left_out.map((item) => item.topic_id), ['ownership-web']);
	const switchOff = await round(space, fakes, plan([topic('ownership-web')]), { SPOTLIGHT_RESEARCH_SWARMS: 'off' });
	assert.equal(switchOff.stopped?.code, 'swarm_unavailable');
	assert.match(switchOff.stopped!.message, /Research swarms/);
	const noKey = await round(space, fakes, plan([topic('ownership-web-2')]), { OPENROUTER_API_KEY: '' });
	assert.equal(noKey.stopped?.code, 'swarm_unavailable');

	assert.equal(fakes.swarm.requests.length, 0);
	assert.equal(fakes.local.requests.length, 0, 'no fallback to the local model');
});

test('the round budget holds: no topic starts once it is spent, running ones finish, and the left-out topics are recorded', async (t) => {
	const space = await setup(t);
	// Each answer costs $0.006; the first topic takes two answers.
	const fakes = await servers(t, scripted({ 'a-web': [call('search', { query: 'q' })] }, { total_tokens: 500, cost: 0.006 }));
	const summary = await round(space, fakes, plan([topic('a-web'), topic('b-web'), topic('c-web')], { round_budget_usd: 0.01 }));

	assert.deepEqual(summary.ran, ['a-web']);
	assert.deepEqual(summary.left_out.map((item) => item.topic_id), ['b-web', 'c-web']);
	assert.match(summary.left_out[0].reason, /budget was spent/);
	assert.equal((await laneOf(space, 'a-web')).status, 'completed', 'the running topic finished past the budget');
	assert.ok(fakes.swarm.requests.every((request) => topicOf(request) === 'a-web'));
	const ledger = JSON.parse(await readFile(join(space.caseDir, ROUND_LEDGER), 'utf8'));
	assert.deepEqual(ledger.rounds[0].left_out.map((item: { topic_id: string }) => item.topic_id), ['b-web', 'c-web']);
	assert.equal(ledger.rounds[0].spent_usd, 0.012);
	assert.equal((await laneOf(space, 'b-web')).status, 'not_started');
	assert.deepEqual(JSON.parse(await readFile(join(space.caseDir, 'data/leads.json'), 'utf8')).left_out, ['b-web', 'c-web']);
});

test('a rejected key stops Research with a fix-the-key message, no local fallback, and a retry runs only the unfinished topics', async (t) => {
	const space = await setup(t);
	let rejecting = false;
	const ok = scripted({});
	const fakes = await servers(t, (request) => (rejecting && topicOf(request) !== 'a-web' ? { status: 401 } : ok(request)));
	const researchPlan = plan([topic('a-web'), topic('b-web')]);

	// a-web finishes, then the key is revoked before b-web.
	const first = await round(space, fakes, plan([topic('a-web')]));
	assert.equal(first.stopped, undefined);
	rejecting = true;
	const stopped = await round(space, fakes, researchPlan);
	assert.equal(stopped.stopped?.code, 'key_rejected');
	assert.match(stopped.stopped!.message, /rejected the key/);
	assert.match(stopped.stopped!.message, /Engine/);
	assert.ok(!JSON.stringify(stopped).includes(KEY), 'the message never shows the key');
	assert.equal((await laneOf(space, 'b-web')).failure.code, 'key_rejected');
	assert.equal(fakes.local.requests.length, 0, 'no local fallback');
	assert.equal(stopped.merge, undefined, 'a stopped round is not merged');

	// Without the member's retry nothing runs again.
	const before = fakes.swarm.requests.length;
	assert.equal((await round(space, fakes, researchPlan)).nothing_to_run, true);
	assert.equal(fakes.swarm.requests.length, before);

	rejecting = false;
	const retried = await round(space, fakes, researchPlan, {}, true);
	assert.equal(retried.stopped, undefined);
	assert.deepEqual(retried.ran, ['b-web'], 'only the unfinished topic runs again');
	assert.ok(fakes.swarm.requests.slice(before).every((request) => topicOf(request) === 'b-web'));
	assert.deepEqual(retried.completed.sort(), ['a-web', 'b-web']);
});

test('upstream errors that outlast the retries stop Research', async (t) => {
	const space = await setup(t);
	const fakes = await servers(t, () => ({ status: 503 }));
	const summary = await round(space, fakes, plan([topic('a-web')]));
	assert.equal(summary.stopped?.code, 'upstream_error');
	assert.equal(fakes.swarm.requests.length, 4, 'one request and three retries');
});

test('an excerpt not in the captured source is returned to the lane once, then set aside', async (t) => {
	const space = await setup(t);
	const paraphrase = 'Lovelace has led Analytical Engines since 2019.';
	const fakes = await servers(
		t,
		scripted({
			'a-web': [
				call('fetch', { url: PAGE_URL }),
				lead('s1', paraphrase),
				lead('s1', paraphrase, { revise: 'a-web-l1' }),
				lead('s1', 'Ada Lovelace is President of Analytical  Engines SA'),
				lead('s1', paraphrase),
				lead('s1', VERBATIM, { revise: 'a-web-l3' }),
				lead('s1', VERBATIM, { revise: 'a-web-l3' }),
			],
		}),
	);
	await round(space, fakes, plan([topic('a-web')]));

	const leads = await leadsOf(space, 'a-web');
	assert.deepEqual(leads.map((item) => [item.lead_id, item.status]), [
		['a-web-l1', 'unverifiable'],
		['a-web-l2', 'candidate'],
		['a-web-l3', 'candidate'],
	]);
	const last = fakes.swarm.requests.at(-1)!;
	const results = toolResults(last);
	assert.match(results[1], /a-web-l1 returned/);
	assert.match(results[2], /set aside/);
	assert.match(results.at(-1)!, /refused: a-web-l3 is not a lead returned/, 'a lead is returned only once');
});

test('a lane cannot write outside its folder', async (t) => {
	const space = await setup(t);
	await mkdir(join(space.caseDir, 'research/swarm'), { recursive: true });
	await symlink(space.outside, join(space.caseDir, 'research/swarm/a-web'));
	const fakes = await servers(t, scripted({ 'a-web': [call('fetch', { url: PAGE_URL })] }));
	const summary = await round(space, fakes, plan([topic('a-web')]));

	assert.match(summary.failed[0]?.reason ?? '', /not a real folder/);
	assert.deepEqual(await readdir(space.outside), []);
	assert.equal(fakes.swarm.requests.length, 0);
});

test('a topic with Anomaly methods names its stored tables as method inputs, and the runner keeps only approved methods on its own D3-passed page leads', async (t) => {
	const space = await setup(t);
	const ZSCORE = 'method:numeric.zscore_outliers@1.1.0';
	const input = (method: string, leadId: string) => call('record_method_input', { method, lead_id: leadId, format: 'csv', fields: { id: 'record_id', amount: 'value' } });
	const fakes = await servers(
		t,
		scripted({
			'a-web': [
				call('fetch', { url: PAGE_URL }),
				lead('s1', VERBATIM),
				call('search', { query: 'Analytical Engines SA' }),
				lead('s2', SNIPPET),
				input('method:table.duplicate_rows@1.1.0', 'a-web-l1'),
				input(ZSCORE, 'a-web-l2'),
				input(ZSCORE, 'a-web-l9'),
				input(ZSCORE, 'a-web-l1'),
			],
		}),
	);
	// An Anomaly install without its catalogue: the merge must see ANOMALY_HOME to say so.
	const anomalyHome = join(space.root, 'anomaly');
	await mkdir(anomalyHome);
	const summary = await round(space, fakes, plan([topic('a-web', 'web', { anomaly_methods: [ZSCORE] })]), { ANOMALY_HOME: anomalyHome });

	const last = fakes.swarm.requests.at(-1)!;
	assert.ok((last.tools ?? []).some((tool) => tool.function.name === 'record_method_input'));
	const results = toolResults(last).slice(-4);
	assert.match(results[0], /refused: .* not one of this topic's approved methods/);
	assert.match(results[1], /refused: a-web-l2 is not a stored page that passed/, 'a snippet lead is not a table');
	assert.match(results[2], /refused: a-web-l9 is not a lead this lane recorded/);
	const written = JSON.parse(await readFile(join(space.caseDir, 'research/swarm/a-web/method-inputs.json'), 'utf8'));
	assert.deepEqual(written, { schema_version: '1.0', inputs: [{ method: ZSCORE, lead_id: 'a-web-l1', format: 'csv', fields: { id: 'record_id', amount: 'value' } }] });
	assert.equal(summary.merge?.ok, true, JSON.stringify(summary.merge));
	assert.ok((summary.merge as { report: string[] }).report.some((line) => /methods not run: detectors\/catalogue\.json is missing/.test(line)), JSON.stringify(summary.merge));
});
