// The Editorial MCP client against fake Editorial MCP servers on 127.0.0.1:
// where a request carrying the newsroom token may go, and what a revoked
// token does. No network.
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { EditorialClient, EditorialCredentialError, EditorialRefused, editorialClient, ensureCaseRegistered, pendingSubmission, runChecks, stopsTheNewsroomCase, submitCase } from './editorial.ts';
import type { RunPackage } from './editorial.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { caseAtIngest, newsroomEnv, requestNewsroomIngest, workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT } from './roles.ts';
import { runFixed } from './tools/run.ts';
import { readConfig } from './config.ts';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = (url: string) => new URL(url).origin;

// An Editorial MCP that sends its headers and part of an answer, then breaks
// the connection (`cut`), or that never answers.
async function halfAnswering(t: { after(fn: () => unknown): void }, cut: boolean): Promise<EditorialClient> {
	const server = createServer((request, response) => {
		request.resume();
		if (!cut) return;
		response.writeHead(200, { 'content-type': 'application/json', 'content-length': '1000' });
		response.write('{"jsonrpc":');
		setTimeout(() => response.destroy(), 20);
	});
	await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
	t.after(() => new Promise((done) => { server.closeAllConnections(); server.close(done); }));
	const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/mcp`;
	return new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)] });
}

test('a request carrying the newsroom token goes only to an allowed newsroom endpoint: another host and a redirect are refused before the token leaves', async (t) => {
	const newsroom = await startFakeEditorial({ sse: true });
	const elsewhere = await startFakeEditorial({ token: newsroom.token });
	const redirecting = await startFakeEditorial({ token: newsroom.token, redirectTo: elsewhere.url });
	t.after(() => Promise.all([newsroom.close(), elsewhere.close(), redirecting.close()]));

	// The allowed endpoint answers over SSE, as Streamable HTTP allows.
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const answer = await client.call('search_knowledge', { query: 'Acme' });
	assert.match(JSON.stringify(answer.records), /Prior reporting on Acme/);
	assert.ok(newsroom.requests.every((request) => request.authorization === `Bearer ${newsroom.token}`));

	// Off the allowlist: refused at construction, nothing sent.
	assert.throws(
		() => new EditorialClient({ url: elsewhere.url, token: newsroom.token, origins: [origin(newsroom.url)] }),
		(error: Error) => error instanceof EditorialRefused && /not one of the newsroom's allowed endpoints/.test(error.message),
	);
	// An allowed endpoint that redirects elsewhere: the redirect is not followed.
	const redirected = new EditorialClient({ url: redirecting.url, token: newsroom.token, origins: [origin(redirecting.url)] });
	await assert.rejects(redirected.call('search_knowledge', { query: 'Acme' }), EditorialRefused);
	assert.equal(elsewhere.requests.length, 0, 'the token never reached a host outside the allowlist');
});

test('a revoked token stops with a plain sign-in message that never shows the token, even when the server echoes it', async (t) => {
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await client.call('get_record', { record_id: 'claim:1' });
	newsroom.revoke();

	const error = await client.call('get_record', { record_id: 'claim:1' }).then(
		() => assert.fail('a revoked token was accepted'),
		(caught: Error) => caught,
	);
	assert.ok(error instanceof EditorialCredentialError);
	assert.match(error.message, /sign-in is no longer valid \(revoked or expired\)\. Sign in again/);
	assert.ok(!error.message.includes(newsroom.token));
});

test('a tool error reaches the caller without any address in it', async (t) => {
	const newsroom = await startFakeEditorial({
		handlers: { submit_case_review: () => ({ isError: true, text: 'already staged, see https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }) },
	});
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const error = await client.call('submit_case_review', {}).then(() => assert.fail('a tool error was accepted'), (caught: Error) => caught);
	assert.equal(error.message, 'submit_case_review: already staged, see [address removed]');
});

test('an HTTP or protocol error from the server reaches no log or case file with a review capability in it', async () => {
	const url = 'https://editorial.newsroom.example/mcp';
	const page = 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability';
	for (const answer of [
		(id: string) => new Response(JSON.stringify({ jsonrpc: '2.0', id, error: { code: -32000, message: `see ${page}` } }), { headers: { 'content-type': 'application/json' } }),
		() => new Response(`unavailable, see ${page}`, { status: 500 }),
	]) {
		const fetchImpl: typeof fetch = async (_url, init) => answer(JSON.parse(String(init?.body)).id);
		const client = new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)], fetchImpl });
		const error = await client.connect().then(() => assert.fail('an error was accepted'), (caught: Error) => caught);
		assert.ok(stopsTheNewsroomCase(error) && !error.message.includes('secret-capability'), error.message);
	}
});

test('a successful but malformed tool result stops the newsroom case: a run_checks state or flag outside the contract, a review address that is not a string', async () => {
	const url = 'https://editorial.newsroom.example/mcp';
	for (const [tool, result] of [
		['get_record', { content: 'not a list' }],
		['get_record', { content: [null] }],
		['run_checks', { structuredContent: { job_id: 'job-1', state: 'finished' } }],
		['run_checks', { structuredContent: { job_id: 'job-1', state: 'done', flags: [{ target: 'finding:F1', reason: 'ok' }, { target: 'finding:F2' }] } }],
		['run_checks', { structuredContent: { job_id: 'job-1', state: 'done', flags: 'none' } }],
		['run_checks', { structuredContent: { job_id: 'job-1', state: 'done', flags: [], input_sha256: 'not-a-digest' } }],
		['run_checks', { structuredContent: { state: 'running' } }],
		['submit_case_review', { structuredContent: { status: 'staged', submission_id: 'sub-1', revision: 1, review_id: 'rev-1', review_url: 42 } }],
	] as const) {
		const fetchImpl: typeof fetch = async (_url, init) => {
			const message = JSON.parse(String(init?.body)) as { id?: string; method: string };
			if (message.id === undefined) return new Response(null, { status: 202 });
			return Response.json({ jsonrpc: '2.0', id: message.id, result: message.method === 'initialize' ? {} : result });
		};
		const client = new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)], fetchImpl });
		const error = await client.call(tool, {}).then(() => assert.fail(`${tool} accepted ${JSON.stringify(result)}`), (caught: Error) => caught);
		assert.ok(stopsTheNewsroomCase(error), `${JSON.stringify(result)}: ${String(error)}`);
	}
});

test('when the first caller cancels during a shared session restart, the other callers still get their answers', async (t) => {
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	// The new session's initialize takes a moment, so the cancel lands mid-way.
	let initializes = 0;
	const slowInitialize: typeof fetch = async (url, init) => {
		if (JSON.parse(String(init?.body)).method === 'initialize' && ++initializes === 2) await new Promise((done) => setTimeout(done, 200));
		return fetch(url, init);
	};
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)], fetchImpl: slowInitialize });
	await client.connect();
	newsroom.endSessions();
	const first = new AbortController();
	const cancelled = client.call('get_record', { record_id: 'claim:1' }, first.signal).then(() => assert.fail('a cancelled call finished'), (caught: Error) => caught);
	const other = client.call('get_record', { record_id: 'claim:2' });
	setTimeout(() => first.abort(), 50);
	assert.equal((await cancelled).name, 'AbortError');
	assert.equal(((await other).record as { record_id: string }).record_id, 'claim:2');
});

test('an answer that carries the token comes back with the token redacted, in values and in keys', async (t) => {
	const newsroom = await startFakeEditorial({
		handlers: {
			search_knowledge: () => ({ records: [{ record_id: 'claim:1', summary: `echo newsroom-editorial-token-123 here` }], ['newsroom-editorial-token-123']: true }),
		},
	});
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const answer = JSON.stringify(await client.call('search_knowledge', { query: 'Acme' }));
	assert.ok(!answer.includes(newsroom.token), answer);
	assert.match(answer, /echo \[redacted\] here/);
});

test('when the server ends the session under concurrent calls, the client starts one new session and every call succeeds', async (t) => {
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await client.connect();
	newsroom.endSessions();
	const answers = await Promise.all([
		client.call('get_record', { record_id: 'claim:1' }),
		client.call('get_record', { record_id: 'claim:2' }),
		client.call('get_record', { record_id: 'claim:3' }),
	]);
	assert.deepEqual(answers.map((answer) => (answer.record as { record_id: string }).record_id), ['claim:1', 'claim:2', 'claim:3']);
	assert.equal(newsroom.requests.filter((request) => request.method === 'initialize').length, 2, 'the first session and one new one');
});

// A case at ingest with ingestion requested and registered, and a client for
// a fake Editorial MCP answering with `handlers`.
test('a knowledge answer reaches the model without any review capability: no review_url key, no newsroom-endpoint address as a value or as a key', async (t) => {
	let reviewPage = '';
	const newsroom = await startFakeEditorial({
		handlers: {
			get_record: () => ({ record: { record_id: 'claim:1', source_url: 'https://registry.example.org/company/42', note: `reviewed at ${reviewPage}`, links: [{ review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }], reviews: { [reviewPage]: 'pending' } } }),
		},
	});
	t.after(() => newsroom.close());
	reviewPage = `${origin(newsroom.url)}/review/rev-1?cap=secret-capability`;
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const answer = JSON.stringify(await client.call('get_record', { record_id: 'claim:1' }));
	assert.ok(!answer.includes('secret-capability') && !answer.includes('review_url'), answer);
	assert.match(answer, /registry\.example\.org\/company\/42/, 'a source address on another host stays');
});

async function submittable(t: { after(fn: () => unknown): void }, handlers: Parameters<typeof startFakeEditorial>[0]['handlers'], fixtureEnv: Record<string, string> = {}) {
	const space = await workspace(t);
	const newsroom = await startFakeEditorial({ handlers });
	t.after(() => newsroom.close());
	const env = newsroomEnv(space, [], newsroom);
	const caseDir = await caseAtIngest(space, fixtureEnv);
	await requestNewsroomIngest(caseDir, env);
	// Run as the launcher runs it (runFixed, with its output limit).
	const runPackage: RunPackage = (args, input) =>
		runFixed(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'newsroom-package.py'), ...args], {
			env: { ...process.env, SPOTLIGHT_CONFIG: env.SPOTLIGHT_CONFIG } as Record<string, string>,
			cwd: CHECKOUT_ROOT,
			input: input === undefined ? '' : JSON.stringify(input),
		});
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await runPackage(['register', caseDir], { case_ref: 'nr-case-1', title: 'case-a', custody_mode: 'hosted', editorial_origin: origin(newsroom.url), client_case_id: 'case-a' });
	return { caseDir, runPackage, client, newsroomOrigin: origin(newsroom.url) };
}

test('Submit opens the newsroom review page in the member\'s browser and keeps its address out of the outcome, the receipts and the case', async (t) => {
	const { caseDir, runPackage, client } = await submittable(t, {
		submit_case_review: () => ({ status: 'staged', submission_id: 'sub-1', revision: 1, review_id: 'rev-1', review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }),
		get_review_status: (args) => ({ review_id: args.review_id, state: 'pending', review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }),
	});
	const opened: string[] = [];

	const outcome = await submitCase({ client, caseDir, runPackage, openReview: async (url) => (opened.push(url), true) });

	assert.deepEqual(opened, ['https://editorial.newsroom.example/review/rev-1?cap=secret-capability']);
	assert.equal(outcome.state, 'submitted');
	assert.equal((outcome as { review_opened: boolean }).review_opened, true);
	const written = [JSON.stringify(outcome)];
	for (const entry of await readdir(caseDir, { recursive: true, withFileTypes: true })) {
		if (entry.isFile()) written.push(await readFile(join(entry.parentPath, entry.name), 'utf8').catch(() => ''));
	}
	assert.ok(!written.join('\n').includes('secret-capability'), 'the review address is nowhere a model can read');
	assert.ok(!written.join('\n').includes('review_url'));
});

test('a package near the size cap reaches the newsroom whole: its base64 is larger than a program\'s output limit', { timeout: 120_000 }, async (t) => {
	// The cited original, 24.5 MiB: under the 25 MB file cap, over 32 MiB as base64.
	const { caseDir, runPackage, client } = await submittable(t, {}, { SPOTLIGHT_FIXTURE_ORIGINAL_BYTES: String(24.5 * 1024 * 1024) });

	const outcome = await submitCase({ client, caseDir, runPackage, openReview: async () => false });

	assert.equal(outcome.state, 'submitted');
});

test('a case file the harness cannot write after a newsroom answer stops the newsroom case', async (t) => {
	const { caseDir, runPackage, client } = await submittable(t, {});
	const failing: RunPackage = (args, input) => (args[0] === 'checks' ? Promise.resolve({ code: 2, stdout: '', stderr: 'disk full' }) : runPackage(args, input));
	const error = await runChecks(client, caseDir, 'findings', failing, { pollMs: 1 }).then(() => assert.fail('the check was taken as recorded'), (caught: Error) => caught);
	assert.ok(stopsTheNewsroomCase(error), String(error));
	assert.match(error.message, /disk full/);
});

test('a review page that does not open leaves the submission pending, and a retry with the same key opens it and completes', async (t) => {
	const { caseDir, runPackage, client } = await submittable(t, {
		submit_case_review: () => ({ status: 'staged', submission_id: 'sub-1', revision: 1, review_id: 'rev-1', review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }),
		get_review_status: (args) => ({ review_id: args.review_id, state: 'pending' }),
	});
	await assert.rejects(submitCase({ client, caseDir, runPackage, openReview: async () => { throw new Error('no browser'); } }), /no browser/);
	assert.equal(await pendingSubmission(runPackage, caseDir), true);

	const opened: string[] = [];
	const outcome = await submitCase({ client, caseDir, runPackage, openReview: async (url) => (opened.push(url), true) });
	assert.equal(outcome.state, 'submitted');
	assert.deepEqual(opened, ['https://editorial.newsroom.example/review/rev-1?cap=secret-capability']);
	assert.equal(await pendingSubmission(runPackage, caseDir), false);
});

test('decision-check flags and integrity failures carry no review capability into the case or the outcome', async (t) => {
	let page = '';
	const { caseDir, runPackage, client, newsroomOrigin } = await submittable(t, {
		run_checks: () => ({ job_id: 'job-1', state: 'done', flags: [{ target: 'finding:F1', question: 'partial_support', reason: `compare ${page}` }] }),
		submit_case_review: () => ({ status: 'rejected', integrity_failures: [{ check: 'file_hash', reason: `see ${page}`, review_url: page }] }),
	});
	page = `${newsroomOrigin}/review/rev-1?cap=secret-capability`;

	const checks = await runChecks(client, caseDir, 'findings', runPackage, { pollMs: 1 });
	const outcome = await submitCase({ client, caseDir, runPackage, openReview: async () => true });

	const written = [JSON.stringify(checks), JSON.stringify(outcome)];
	for (const name of ['newsroom-checks.json', 'newsroom-submission.json']) written.push(await readFile(join(caseDir, 'data', name), 'utf8'));
	assert.match(written.join('\n'), /compare \[address removed\]/);
	assert.ok(!written.join('\n').includes('secret-capability') && !written.join('\n').includes('review_url'), written.join('\n'));
});

test('a review address outside the newsroom\'s endpoints is not opened, and the member and the receipt are told so', async (t) => {
	const { caseDir, runPackage, client } = await submittable(t, {});
	const outcome = await submitCase({ client, caseDir, runPackage, openReview: async () => false });
	assert.equal(outcome.state, 'submitted');
	assert.match((outcome as { notice?: string }).notice ?? '', /outside the newsroom's allowed endpoints.*newsroom's own tool/);
	const receipt = JSON.parse(await readFile(join(caseDir, 'receipts', 'submission-sub-1.json'), 'utf8')) as { review_address?: string };
	assert.match(receipt.review_address ?? '', /^refused/);
	assert.equal(await pendingSubmission(runPackage, caseDir), false);
});

test('an answer whose body breaks after its headers stops the newsroom case', async (t) => {
	const client = await halfAnswering(t, true);
	const error = await client.connect().then(() => assert.fail('a broken answer was accepted'), (caught: unknown) => caught);
	assert.ok(stopsTheNewsroomCase(error), String(error));
});

test('a call the caller cancels is a cancellation, not a newsroom outage', async (t) => {
	const client = await halfAnswering(t, false);
	const controller = new AbortController();
	const call = client.connect(controller.signal).then(() => assert.fail('a cancelled call finished'), (caught: unknown) => caught);
	setTimeout(() => controller.abort(), 20);
	const error = await call;
	assert.equal((error as Error).name, 'AbortError');
	assert.ok(!stopsTheNewsroomCase(error));
});

test('a review status for another review, or without a valid state, leaves the submission pending', async (t) => {
	for (const status of [{ review_id: 'rev-other', state: 'approved' }, { review_id: 'rev-1' }]) {
		const { caseDir, runPackage, client } = await submittable(t, { get_review_status: () => status });
		await assert.rejects(submitCase({ client, caseDir, runPackage, openReview: async () => true }), /another review or without a valid state/);
		assert.equal(await pendingSubmission(runPackage, caseDir), true);
	}
});

test('an answer carrying another request\'s id is not taken as this request\'s answer', async (t) => {
	const newsroom = await startFakeEditorial({ answerId: 'another-request' });
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await assert.rejects(client.call('get_record', { record_id: 'claim:1' }), /no readable answer to tools\/call/);
});

test('a case folder holding another case\'s or another endpoint\'s newsroom reference is not started', async (t) => {
	const space = await workspace(t);
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	const config = readConfig(newsroomEnv(space, [{ name: 'model', base_url: 'http://127.0.0.1:9/v1', model: 'm', inference: 'own' }], newsroom, { SPOTLIGHT_CASE_ID: 'case-b' }));
	await mkdir(join(config.caseDir, 'data'), { recursive: true });
	await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify({ case_ref: 'nr-case-a', title: 'case-a', custody_mode: 'hosted', editorial_origin: origin(newsroom.url), client_case_id: 'case-a' }));
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await assert.rejects(ensureCaseRegistered(client, config, async () => ({ code: 0, stdout: '{}', stderr: '' })), /reference of another case \(case-a\)/);
	// A reference from another Editorial endpoint is not replaced by a new registration.
	await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify({ case_ref: 'nr-case-b', title: 'case-b', custody_mode: 'hosted', editorial_origin: 'https://old.newsroom.example', client_case_id: 'case-b' }));
	await assert.rejects(ensureCaseRegistered(client, config, async () => ({ code: 0, stdout: '{}', stderr: '' })), /registered with another newsroom endpoint \(https:\/\/old\.newsroom\.example\)/);
	assert.deepEqual(newsroom.callsOf('register_case'), []);
});

test('a token copied with surrounding whitespace is sent trimmed, as it was validated', async (t) => {
	const newsroom = await startFakeEditorial({});
	t.after(() => newsroom.close());
	const config = { editorial: { url: newsroom.url, tokenEnv: 'EDITORIAL_TOKEN' }, newsroomOrigins: [origin(newsroom.url)] };
	const client = editorialClient(config as never, { EDITORIAL_TOKEN: `  ${newsroom.token}\n` }, []);
	await client!.call('search_knowledge', { query: 'Acme' });
	assert.ok(newsroom.requests.every((request) => request.authorization === `Bearer ${newsroom.token}`));
});
