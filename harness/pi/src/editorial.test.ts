// The Editorial MCP client against fake Editorial MCP servers on 127.0.0.1:
// where a request carrying the newsroom token may go, and what a revoked
// token does. No network.
import assert from 'node:assert/strict';
import test from 'node:test';
import { EditorialClient, EditorialCredentialError, EditorialRefused } from './editorial.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';

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
