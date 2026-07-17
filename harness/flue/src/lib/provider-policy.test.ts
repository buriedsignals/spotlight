import assert from 'node:assert/strict';
import test from 'node:test';
import { installFixedRequestBodyPolicy, parseFixedRequestBody } from './provider-policy.ts';

test('fixed request fields override SDK fields on the selected provider only', async () => {
	const calls: Array<{ url: string; body?: BodyInit | null }> = [];
	const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ url: String(input), body: init?.body });
		return new Response('{}');
	}) as typeof fetch;
	const policy = installFixedRequestBodyPolicy(
		'https://openrouter.ai/api/v1',
		{ provider: { zdr: true } },
		fakeFetch,
	);
	await policy('https://openrouter.ai/api/v1/chat/completions', {
		method: 'POST',
		body: JSON.stringify({ model: 'z-ai/glm-5.2', provider: { zdr: false, sort: 'price' } }),
	});
	await policy('https://example.test/chat/completions', { method: 'POST', body: '{}' });
	assert.deepEqual(JSON.parse(String(calls[0].body)), {
		model: 'z-ai/glm-5.2',
		provider: { zdr: true, sort: 'price' },
	});
	assert.equal(calls[1].body, '{}');
});

test('matching non-JSON requests fail before the network call', async () => {
	let called = false;
	const fakeFetch = (async () => {
		called = true;
		return new Response('{}');
	}) as typeof fetch;
	const policy = installFixedRequestBodyPolicy('https://openrouter.ai/api/v1', { provider: { zdr: true } }, fakeFetch);
	await assert.rejects(() => policy('https://openrouter.ai/api/v1/chat/completions', { method: 'POST', body: 'not-json' }), /blocked before transmission/);
	assert.equal(called, false);
});

test('fixed request policy must itself be a JSON object', () => {
	assert.throws(() => parseFixedRequestBody('[]'), /must be a JSON object/);
	assert.throws(() => parseFixedRequestBody('{'), /not valid JSON/);
});
