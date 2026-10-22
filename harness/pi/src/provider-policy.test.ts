import assert from 'node:assert/strict';
import test from 'node:test';
import { applyFixedRequestBody, parseFixedRequestBody } from './provider-policy.ts';

test('fixed request fields override and deep-merge into the provider payload', () => {
	const outcome = applyFixedRequestBody(
		{ model: 'z-ai/glm-5.2', messages: [{ role: 'user', content: 'x' }], provider: { zdr: false, sort: 'price' } },
		{ provider: { zdr: true } },
	);

	assert.equal(outcome.blocked, false);
	assert.deepEqual(outcome.payload, {
		model: 'z-ai/glm-5.2',
		messages: [{ role: 'user', content: 'x' }],
		provider: { zdr: true, sort: 'price' },
	});
});

test('a payload the policy cannot merge into is replaced by the fixed fields alone', () => {
	const outcome = applyFixedRequestBody('raw transcript text', { provider: { zdr: true } });

	assert.equal(outcome.blocked, true);
	assert.deepEqual(outcome.payload, { provider: { zdr: true } });
});

test('fixed request policy must itself be a JSON object', () => {
	assert.throws(() => parseFixedRequestBody('[]'), /must be a JSON object/);
	assert.throws(() => parseFixedRequestBody('{'), /not valid JSON/);
});
