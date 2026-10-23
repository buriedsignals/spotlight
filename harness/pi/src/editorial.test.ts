// The Editorial MCP client against fake Editorial MCP servers on 127.0.0.1:
// where a request carrying the newsroom token may go, and what a revoked
// token does. No network.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { EditorialClient, EditorialCredentialError, EditorialRefused, submitCase } from './editorial.ts';
import type { RunPackage } from './editorial.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { caseAtIngest, newsroomEnv, requestNewsroomIngest, workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT } from './roles.ts';

const origin = (url: string) => new URL(url).origin;

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

test('Submit opens the newsroom review page in the member\'s browser and keeps its address out of the outcome, the receipts and the case', async (t) => {
	const space = await workspace(t);
	const newsroom = await startFakeEditorial({
		handlers: {
			submit_case_review: () => ({ status: 'staged', submission_id: 'sub-1', revision: 1, review_id: 'rev-1', review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }),
			get_review_status: (args) => ({ review_id: args.review_id, state: 'pending', review_url: 'https://editorial.newsroom.example/review/rev-1?cap=secret-capability' }),
		},
	});
	t.after(() => newsroom.close());
	const env = newsroomEnv(space, [], newsroom);
	const caseDir = await caseAtIngest(space);
	await requestNewsroomIngest(caseDir, env);
	const runPackage: RunPackage = (args, input) =>
		new Promise((done) => {
			const child = execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts', 'newsroom-package.py'), ...args], { env: { ...process.env, SPOTLIGHT_CONFIG: env.SPOTLIGHT_CONFIG } }, (error, stdout, stderr) =>
				done({ code: error ? Number(error.code ?? 1) : 0, stdout, stderr }),
			);
			child.stdin?.end(input === undefined ? '' : JSON.stringify(input));
		});
	const client = new EditorialClient({ url: newsroom.url, token: newsroom.token, origins: [origin(newsroom.url)] });
	await runPackage(['register', caseDir], { case_ref: 'nr-case-1', title: 'case-a', custody_mode: 'hosted', editorial_origin: origin(newsroom.url) });
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
