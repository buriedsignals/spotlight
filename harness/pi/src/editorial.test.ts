// The Editorial MCP client against fake Editorial MCP servers on 127.0.0.1:
// where a request carrying the newsroom token may go, and what a revoked
// token does. No network.
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { EditorialClient, EditorialCredentialError, EditorialRefused, editorialClient, ensureCaseRegistered, pendingSubmission, stopsTheNewsroomCase, submitCase } from './editorial.ts';
import type { RunPackage } from './editorial.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { editorialTools } from './tools/editorial.ts';
import { caseAtIngest, newsroomEnv, requestNewsroomIngest, workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT } from './roles.ts';
import { runFixed } from './tools/run.ts';
import { readConfig } from './config.ts';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = (url: string) => new URL(url).origin;
const CASE = '00000000-0000-4000-8000-00000000ca5e';
const BATCH = '00000000-0000-4000-8000-0000000ba7c4';
const REVIEW_PAGE = 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability';
// The service's answer to a stored batch, for the batch the harness sent.
const stored = (args: Record<string, unknown>, more: Record<string, unknown> = {}) => ({
	batch: { batch_info: { id: (args.request as { batch_id: string }).batch_id, status: 'submitted' }, records: {} },
	assigned_ids: [],
	...more,
});

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
		handlers: { get_record: () => ({ isError: true, text: 'no such record, see https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }) },
	});
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const error = await client.call('get_record', {}).then(() => assert.fail('a tool error was accepted'), (caught: Error) => caught);
	assert.equal(error.message, 'get_record: no such record, see [address removed]');
});

test('an HTTP or protocol error from the server reaches no log or case file with a review capability in it', async () => {
	const url = 'https://editorial.newsroom.example/mcp';
	const page = 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability';
	for (const answer of [
		(id: string) => new Response(JSON.stringify({ jsonrpc: '2.0', id, error: { code: -32000, message: `see ${page}` } }), { headers: { 'content-type': 'application/json' } }),
		() => new Response(`unavailable, see ${page}`, { status: 500 }),
		() => new Response(`unavailable, see ${page.replace('https:', '')}`, { status: 500 }),
		(id: string) => Response.json({ jsonrpc: '1.0', id, result: {} }),
	]) {
		const fetchImpl: typeof fetch = async (_url, init) => answer(JSON.parse(String(init?.body)).id);
		const client = new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)], fetchImpl });
		const error = await client.connect().then(() => assert.fail('an error was accepted'), (caught: Error) => caught);
		assert.ok(stopsTheNewsroomCase(error) && !error.message.includes('secret-capability'), error.message);
	}
});

test('an answer larger than the cap is not read to its end: the read stops and the newsroom case stops', async () => {
	const url = 'https://editorial.newsroom.example/mcp';
	let pulled = 0;
	const fetchImpl: typeof fetch = async () =>
		new Response(new ReadableStream({ pull: (stream) => (pulled++ < 64 ? stream.enqueue(new Uint8Array(1024 * 1024).fill(32)) : stream.close()) }), { headers: { 'content-type': 'application/json' } });
	const client = new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)], fetchImpl });
	const error = await client.connect().then(() => assert.fail('an oversized answer was accepted'), (caught: Error) => caught);
	assert.ok(stopsTheNewsroomCase(error) && /larger than/.test(error.message), String(error));
	assert.ok(pulled <= 20, `read ${pulled} MiB`);
});

test('newsroom data handed to a model is capped, with a marker saying so', async () => {
	const search = editorialTools(async () => JSON.stringify({ records: [{ record_id: 'claim:1', summary: 'x'.repeat(100_000) }] })).find((tool) => tool.name === 'search_knowledge')!;
	const text = ((await search.execute('id', { query: 'Acme' }, undefined, undefined, undefined as never)).content[0] as { text: string }).text;
	assert.ok(text.length < 25_000, `${text.length} characters`);
	assert.match(text, /\[\.\.\. truncated at 20000 characters\]\nNEWSROOM_DATA \S+>>>$/);
});

test('a successful but malformed tool result stops the newsroom case: an id the service did not issue, a stored batch without its ids, a review address that is not an http(s) URL', async () => {
	const url = 'https://editorial.newsroom.example/mcp';
	for (const [tool, result] of [
		['get_record', { content: 'not a list' }],
		['get_record', { content: [null] }],
		['get_record', { isError: 'yes', content: [{ type: 'text', text: '{}' }] }],
		['search_knowledge', { structuredContent: { records: 'not-an-array' } }],
		['get_record', { structuredContent: { record: [] } }],
		['traverse', { structuredContent: { root: 'claim:1', nodes: [] } }],
		['get_record', `{"structuredContent":${'{"a":'.repeat(100_000)}1${'}'.repeat(100_000)}}`],
		['create_id', { structuredContent: { result: 42 } }],
		['register_case', { structuredContent: { name: 'case-a' } }],
		['get_knowledge_batch_info', { structuredContent: { id: BATCH } }],
		['start_case_review', { structuredContent: { batch: { batch_info: {} }, assigned_ids: [] } }],
		['start_case_review', { structuredContent: { batch: { batch_info: { id: BATCH } }, assigned_ids: [{ type: 'claim', client_id: 'claim:1' }] } }],
		['start_case_review', { structuredContent: { batch: { batch_info: { id: BATCH } }, assigned_ids: [], review_url: 42 } }],
		['start_case_review', { structuredContent: { batch: { batch_info: { id: BATCH } }, assigned_ids: [], review_url: 'not-a-url' } }],
	] as const) {
		const fetchImpl: typeof fetch = async (_url, init) => {
			const message = JSON.parse(String(init?.body)) as { id?: string; method: string };
			if (message.id === undefined) return new Response(null, { status: 202 });
			if (message.method === 'initialize') return Response.json({ jsonrpc: '2.0', id: message.id, result: {} });
			// A string is the raw JSON of the result.
			return typeof result === 'string' ? new Response(`{"jsonrpc":"2.0","id":"${message.id}","result":${result}}`) : Response.json({ jsonrpc: '2.0', id: message.id, result });
		};
		const client = new EditorialClient({ url, token: 'newsroom-editorial-token-123', origins: [origin(url)], fetchImpl });
		const shown = JSON.stringify(result).slice(0, 200);
		const error = await client.call(tool, {}).then(() => assert.fail(`${tool} accepted ${shown}`), (caught: Error) => caught);
		assert.ok(stopsTheNewsroomCase(error), `${shown}: ${String(error)}`);
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
test('a knowledge answer reaches the model without any review capability: no review_url key, no newsroom-endpoint address (absolute or protocol-relative) as a value or as a key', async (t) => {
	let reviewPage = '';
	let hostRelative = '';
	const newsroom = await startFakeEditorial({
		handlers: {
			get_record: () => ({ record: { record_id: 'claim:1', source_url: 'https://registry.example.org/company/42', note: `reviewed at ${reviewPage}, also ${hostRelative}`, links: [{ review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }], reviews: { [reviewPage]: 'pending', [hostRelative]: 'pending' } } }),
		},
	});
	t.after(() => newsroom.close());
	reviewPage = `${origin(newsroom.url)}/review/rev-1?cap=secret-capability`;
	hostRelative = `//${new URL(newsroom.url).host}/review/rev-2?cap=secret-capability`;
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
	const caseDir = await caseAtIngest(space, fixtureEnv, ['--proposal']);
	await requestNewsroomIngest(caseDir, env);
	// Run as the launcher runs it (runFixed, with its output limit).
	const runPackage: RunPackage = (args, input) =>
		runFixed(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'newsroom-package.py'), ...args], {
			env: { ...process.env, SPOTLIGHT_CONFIG: env.SPOTLIGHT_CONFIG } as Record<string, string>,
			cwd: CHECKOUT_ROOT,
			input: input === undefined ? '' : JSON.stringify(input),
		});
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await runPackage(['register', caseDir], { case_ref: CASE, title: 'case-a', editorial_origin: origin(newsroom.url), client_case_id: 'case-a', confirmed: true });
	return { caseDir, runPackage, client, caseRef: CASE, newsroom, newsroomOrigin: origin(newsroom.url) };
}

test('Submit sends the approved batch under the service\'s ids, opens the newsroom review page in the member\'s browser and keeps its address out of the outcome, the receipts and the case', async (t) => {
	const { caseDir, runPackage, client, caseRef, newsroom } = await submittable(t, {
		start_case_review: (args) => stored(args, { review_url: REVIEW_PAGE, assigned_ids: [{ type: 'claim', client_id: 'claim:from-the-batch', uuid: BATCH }] }),
		get_knowledge_batch_info: (args) => ({ id: args.batch_id, status: 'submitted', review_url: REVIEW_PAGE }),
	});
	const opened: string[] = [];

	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async (url) => (opened.push(url), true) });

	assert.deepEqual(opened, [REVIEW_PAGE]);
	assert.equal(outcome.state, 'submitted');
	assert.equal((outcome as { review_opened: boolean }).review_opened, true);
	// The batch travels as the service's input: the registered case, the batch id it issued, records by client id.
	const [{ request }] = newsroom.callsOf('start_case_review') as Array<{ request: { case_id: string; batch_id: string; claims: Array<{ client_id: string }> } }>;
	const approved = JSON.parse(await readFile(join(caseDir, 'data', 'knowledge-batch.json'), 'utf8')) as { claims: Array<{ id: string }> };
	assert.deepEqual([request.case_id, request.batch_id, request.claims.map((claim) => claim.client_id)], [caseRef, newsroom.staged[0], approved.claims.map((claim) => claim.id)]);
	// The ids the service assigned are kept for this case's next batch.
	assert.deepEqual(JSON.parse(await readFile(join(caseDir, 'data', 'newsroom-ids.json'), 'utf8')).assigned_ids, [{ type: 'claim', client_id: 'claim:from-the-batch', uuid: BATCH }]);
	const written = [JSON.stringify(outcome)];
	for (const entry of await readdir(caseDir, { recursive: true, withFileTypes: true })) {
		if (entry.isFile()) written.push(await readFile(join(entry.parentPath, entry.name), 'utf8').catch(() => ''));
	}
	assert.ok(!written.join('\n').includes('secret-capability'), 'the review address is nowhere a model can read');
	assert.ok(!written.join('\n').includes('review_url'));
});

test('a batch the case cannot prepare stops the newsroom case, and every call names the case validated at the start', async (t) => {
	const { caseDir, runPackage, client, caseRef, newsroom } = await submittable(t, {});
	const refusing: RunPackage = (args, input) => (args[0] === 'request' ? Promise.resolve({ code: 2, stdout: '', stderr: 'newsroom-package: refused: no approved batch' }) : runPackage(args, input));
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage: refusing, openReview: async () => true }), (caught: Error) => stopsTheNewsroomCase(caught) && /no approved batch/.test(caught.message));
	const crashing: RunPackage = (args, input) => (args[0] === 'request' ? Promise.reject(new Error('python3 could not run: timed out')) : runPackage(args, input));
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage: crashing, openReview: async () => true }), (caught: Error) => stopsTheNewsroomCase(caught) && /timed out/.test(caught.message));
	assert.deepEqual(newsroom.callsOf('start_case_review'), []);

	// The case reference is the one validated at the start, not whatever the file now says.
	await writeFile(join(caseDir, 'data', 'editorial-case.json'), JSON.stringify({ case_ref: BATCH, title: 'x', editorial_origin: 'http://127.0.0.1:9', client_case_id: 'case-a', confirmed: true }));
	await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true });
	assert.deepEqual((newsroom.callsOf('start_case_review') as Array<{ request: { case_id: string } }>).map((call) => call.request.case_id), [caseRef]);
});

test('a review page that does not open leaves the submission pending; the retry finds the batch stored, sends nothing again and tells the member where the review is', async (t) => {
	const { caseDir, runPackage, client, caseRef, newsroom } = await submittable(t, { start_case_review: (args) => stored(args, { review_url: REVIEW_PAGE }) });
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => { throw new Error('no browser'); } }), /no browser/);
	assert.equal(await pendingSubmission(runPackage, caseDir), true);

	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => assert.fail('no address is left to open') });
	assert.equal(outcome.state, 'submitted');
	assert.match((outcome as { notice?: string }).notice ?? '', /review address is no longer available.*newsroom's own tool/);
	assert.equal(newsroom.callsOf('start_case_review').length, 1, 'a batch id is sent once');
	assert.equal(await pendingSubmission(runPackage, caseDir), false);
});

test('a submission cut off before the service stored the batch is sent again under the same batch id', async (t) => {
	let down = true;
	const { caseDir, runPackage, client, caseRef, newsroom } = await submittable(t, { start_case_review: (args) => (down ? { httpStatus: 503 } : stored(args)) });
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true }), (caught: Error) => stopsTheNewsroomCase(caught));
	assert.equal(await pendingSubmission(runPackage, caseDir), true);
	down = false;

	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true });

	assert.equal(outcome.state, 'submitted');
	const sent = (newsroom.callsOf('start_case_review') as Array<{ request: { batch_id: string } }>).map((call) => call.request.batch_id);
	assert.equal(sent.length, 2);
	assert.equal(sent[0], sent[1]);
	assert.equal(newsroom.callsOf('create_id').length, 1, 'the retry reuses the batch id it kept');
});

test('a resumed batch the service refuses as already submitted is taken as stored, not as refused', async (t) => {
	// The status read fails twice: once after the batch was stored, once when the next start asks whether it was.
	const { caseDir, runPackage, client, caseRef, newsroom } = await submittable(t, {
		get_knowledge_batch_info: (args, index) => (index < 2 ? { isError: true, text: 'status store busy' } : { id: args.batch_id, status: 'submitted' }),
	});
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true }), /status store busy/);
	assert.equal(await pendingSubmission(runPackage, caseDir), true);

	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true });

	assert.equal(outcome.state, 'submitted');
	assert.equal(newsroom.staged.length, 1, 'the service holds one batch');
	assert.equal(JSON.parse(await readFile(join(caseDir, 'data', 'newsroom-submission.json'), 'utf8')).state, 'submitted');
});

test('the reasons a refused batch comes back with carry no review capability into the case or the outcome', async (t) => {
	let page = '';
	const { caseDir, runPackage, client, caseRef, newsroomOrigin } = await submittable(t, { start_case_review: () => ({ rejected: [`claims[0].claim_ref: see ${page}`] }) });
	page = `${newsroomOrigin}/review/rev-1?cap=secret-capability`;

	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true });

	assert.deepEqual(outcome, { state: 'blocked', errors: ['claims[0].claim_ref: see [address removed]'] });
	const recorded = await readFile(join(caseDir, 'data', 'newsroom-submission.json'), 'utf8');
	assert.match(recorded, /claims\[0\]\.claim_ref: see \[address removed\]/);
	assert.ok(!recorded.includes('secret-capability'), recorded);
});

test('a review address outside the newsroom\'s endpoints is not opened, and the member and the receipt are told so', async (t) => {
	const { caseDir, runPackage, client, caseRef } = await submittable(t, {});
	const outcome = await submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => false });
	assert.equal(outcome.state, 'submitted');
	assert.match((outcome as { notice?: string }).notice ?? '', /outside the newsroom's allowed endpoints.*newsroom's own tool/);
	const [name] = (await readdir(join(caseDir, 'receipts'))).filter((file) => file.startsWith('submission-'));
	const receipt = JSON.parse(await readFile(join(caseDir, 'receipts', name), 'utf8')) as { review_address?: string };
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

test('a batch status for another batch leaves the submission pending', async (t) => {
	const { caseDir, runPackage, client, caseRef } = await submittable(t, { get_knowledge_batch_info: () => ({ id: CASE, status: 'approved' }) });
	await assert.rejects(submitCase({ client, caseRef, caseDir, runPackage, openReview: async () => true }), /answered for another batch/);
	assert.equal(await pendingSubmission(runPackage, caseDir), true);
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
	await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify({ case_ref: 'nr-case-a', title: 'case-a', editorial_origin: origin(newsroom.url), client_case_id: 'case-a', confirmed: true }));
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await assert.rejects(ensureCaseRegistered(client, config, async () => ({ code: 0, stdout: '{}', stderr: '' })), /reference of another case \(case-a\)/);
	// A reference from another Editorial endpoint is not replaced by a new registration.
	await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify({ case_ref: 'nr-case-b', title: 'case-b', editorial_origin: 'https://old.newsroom.example', client_case_id: 'case-b', confirmed: true }));
	await assert.rejects(ensureCaseRegistered(client, config, async () => ({ code: 0, stdout: '{}', stderr: '' })), /registered with another newsroom endpoint \(https:\/\/old\.newsroom\.example\)/);
	// An incomplete stored reference stops the case before any call.
	for (const stored of [{ case_ref: 'nr case b', editorial_origin: origin(newsroom.url), client_case_id: 'case-b' }, { case_ref: 'nr-case-b', client_case_id: 'case-b' }]) {
		await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify(stored));
		await assert.rejects(ensureCaseRegistered(client, config, async () => ({ code: 0, stdout: '{}', stderr: '' })), (caught: Error) => stopsTheNewsroomCase(caught) && /not a complete case reference/.test(caught.message));
	}
	assert.deepEqual(newsroom.calls, []);
});

test('a case is registered under an id the service issued and the case kept first; a registration whose answer was lost is confirmed, not repeated', async (t) => {
	const space = await workspace(t);
	const newsroom = await startFakeEditorial();
	t.after(() => newsroom.close());
	const env = newsroomEnv(space, [{ name: 'model', base_url: 'http://127.0.0.1:9/v1', model: 'm', inference: 'own' }], newsroom, { SPOTLIGHT_CASE_ID: 'case-a', SPOTLIGHT_CASE_TITLE: 'Acme payments' });
	const config = readConfig(env);
	await mkdir(join(config.caseDir, 'data'), { recursive: true });
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	const kept: Array<Record<string, unknown>> = [];
	const runPackage: RunPackage = async (args, input) => {
		if (args[0] === 'register') {
			kept.push(input as Record<string, unknown>);
			await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify(input));
		}
		return { code: 0, stdout: '{}', stderr: '' };
	};

	const first = await ensureCaseRegistered(client, config, runPackage);

	const id = first.reference.case_ref;
	assert.deepEqual(newsroom.calls.map((call) => [call.tool, call.args]), [['create_id', {}], ['register_case', { case_id: id, case_name: 'Acme payments' }]]);
	// Kept before the service was asked to register it, then confirmed.
	assert.deepEqual(kept.map((reference) => [reference.case_ref, reference.confirmed]), [[id, false], [id, true]]);
	assert.equal(first.registered, true);

	// The answer to register_case was lost: the case holds the id, unconfirmed.
	await writeFile(join(config.caseDir, 'data', 'editorial-case.json'), JSON.stringify({ ...kept[0] }));
	const resumed = await ensureCaseRegistered(client, config, runPackage);
	assert.deepEqual([resumed.reference.case_ref, resumed.reference.confirmed, resumed.registered], [id, true, false]);
	assert.equal(newsroom.callsOf('register_case').length, 1);

	// A confirmed case calls nothing.
	const before = newsroom.calls.length;
	await ensureCaseRegistered(client, config, runPackage);
	assert.equal(newsroom.calls.length, before);
});

test('the harness learns which tools the service offers from its list', async (t) => {
	const newsroom = await startFakeEditorial({ unlisted: ['traverse', 'coverage'] });
	t.after(() => newsroom.close());
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	assert.deepEqual([await client.offers('search_knowledge'), await client.offers('traverse')], [true, false]);
	assert.equal(newsroom.requests.filter((request) => request.method === 'tools/list').length, 1);
});

test('a token copied with surrounding whitespace is sent trimmed, as it was validated', async (t) => {
	const newsroom = await startFakeEditorial({});
	t.after(() => newsroom.close());
	const config = { editorial: { url: newsroom.url, tokenEnv: 'EDITORIAL_TOKEN' }, newsroomOrigins: [origin(newsroom.url)] };
	const client = editorialClient(config as never, { EDITORIAL_TOKEN: `  ${newsroom.token}\n` }, []);
	await client!.call('search_knowledge', { query: 'Acme' });
	assert.ok(newsroom.requests.every((request) => request.authorization === `Bearer ${newsroom.token}`));
});
