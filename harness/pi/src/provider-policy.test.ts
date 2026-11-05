import assert from 'node:assert/strict';
import test from 'node:test';
import { applyFixedRequestBody, parseFixedRequestBody, withLocalThinking } from './provider-policy.ts';

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

test('local tiers send thinking off beside an operator\'s fields, whose own value wins; frontier is untouched', () => {
	const off = { enable_thinking: false };
	assert.deepEqual(withLocalThinking(undefined, undefined), { chat_template_kwargs: off });
	assert.deepEqual(withLocalThinking({ provider: { zdr: true } }, '12b'), { chat_template_kwargs: off, provider: { zdr: true } });
	assert.deepEqual(withLocalThinking({ chat_template_kwargs: { enable_thinking: true } }, '12b'), { chat_template_kwargs: { enable_thinking: true } });
	assert.equal(withLocalThinking(undefined, 'frontier'), undefined);
	assert.equal(withLocalThinking(undefined, 'frontier', true), undefined);
	assert.deepEqual(withLocalThinking({ provider: { zdr: true } }, '12b', true), { chat_template_kwargs: { enable_thinking: true }, provider: { zdr: true } });
});
