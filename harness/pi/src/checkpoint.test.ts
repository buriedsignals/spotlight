// Conversation folding through the real launcher and a real orchestrator pi
// process, against the scripted OpenAI-compatible server. No network, no model.
import assert from 'node:assert/strict';
import test from 'node:test';
import { startFakeOpenAI } from './test-support/fake-openai.ts';
import type { ChatRequest } from './test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, workspace } from './test-support/launch.ts';
import type { Chat } from './test-support/launch.ts';

const OLD_TURN = 'OLD-TURN-MARKER';
const CHECKPOINT = 'CHECKPOINT-SENTINEL';
// About 20,000 tokens at Pi's four-characters-per-token estimate. With the
// recent turn (about 5,000 tokens, more than the 12b tier keeps verbatim) the
// conversation passes the 12b fold point (16,384) but stays well under the
// 65,536-token window's default threshold (65,536 - 16,384).
const LONG_TURN = `${OLD_TURN} ${'The registry lists the fixture company. '.repeat(2000)}`;
const RECENT_TURN = `Next step. ${'The member asked for the filing history. '.repeat(500)}`;

// Session requests declare the role's tools; a summary request declares none.
const isSummaryRequest = (request: ChatRequest) => (request.tools ?? []).length === 0;
const requestText = (request: ChatRequest) => JSON.stringify(request.messages);

function compactions(chat: Chat): Array<Record<string, unknown>> {
	return chat.lines
		.map((line) => JSON.parse(line) as Record<string, unknown>)
		.filter((record) => record.type === 'compaction_end' && record.result);
}

async function twoTurns(t: test.TestContext, tier: string, env: Record<string, string> = {}) {
	const fake = await startFakeOpenAI((request) =>
		isSummaryRequest(request)
			? { text: `## Goal\nFind the owner.\n## Progress\n${CHECKPOINT}\n## Decisions\nNone.\n## Next\nResolve.\n## Open gate\nNone.` }
			: { text: 'Noted.' },
	);
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, {
		...baseEnv(space, fake.baseURL),
		SPOTLIGHT_MODEL_CONTEXT_WINDOW: '65536',
		SPOTLIGHT_MODEL_TIER: tier,
		SPOTLIGHT_PROVIDER_REQUEST_BODY: '{"provider":{"zdr":true}}',
		...env,
	});
	await settledPrompt(chat, LONG_TURN);
	await settledPrompt(chat, RECENT_TURN);
	await settledPrompt(chat, 'Continue.');
	return { fake, chat };
}

test('the local tier folds a conversation past its threshold into one checkpoint the next request carries', async (t) => {
	const { fake, chat } = await twoTurns(t, '12b');

	assert.equal(compactions(chat).length, 1);
	const summaries = fake.requests.filter(isSummaryRequest);
	assert.equal(summaries.length, 1);
	// The checkpoint is written from the old turn, in the checkpoint's sections,
	// and the summary request carries the fixed privacy fields too.
	assert.match(requestText(summaries[0]), new RegExp(OLD_TURN));
	assert.match(requestText(summaries[0]), /Open gate/);
	assert.deepEqual(summaries[0].provider, { zdr: true });
	const last = fake.requests.filter((request) => !isSummaryRequest(request)).at(-1)!;
	assert.match(requestText(last), new RegExp(CHECKPOINT));
	assert.doesNotMatch(requestText(last), new RegExp(OLD_TURN));
});

// r1 on 97f066b6: the 12B wrote its own checkpoints from ~24k tokens of
// transcript. With an RLM the e4b writes them, from the same prompt, and the
// session model is asked nothing.
test('with an RLM the local tier\'s checkpoint is written by the RLM, not the session model', async (t) => {
	const rlm = await startFakeOpenAI(() => ({ text: `## Goal\nFind the owner.\n## Progress\n${CHECKPOINT}\n## Decisions\nNone.\n## Next\nResolve.\n## Open gate\nNone.` }));
	t.after(() => rlm.close());
	const { fake, chat } = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL });

	assert.equal(compactions(chat).length, 1);
	assert.equal(fake.requests.filter(isSummaryRequest).length, 0);
	assert.equal(rlm.requests.length, 1);
	assert.match(requestText(rlm.requests[0]), new RegExp(OLD_TURN));
	assert.match(requestText(rlm.requests[0]), /Open gate/);
	const last = fake.requests.at(-1)!;
	assert.match(requestText(last), new RegExp(CHECKPOINT));
	assert.doesNotMatch(requestText(last), new RegExp(OLD_TURN));
});

test('in sensitive mode the checkpoint stays with the session model even when an RLM is set', async (t) => {
	const rlm = await startFakeOpenAI(() => ({ text: 'unused' }));
	t.after(() => rlm.close());
	const { fake, chat } = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL, SPOTLIGHT_SENSITIVE: 'true' });

	assert.equal(compactions(chat).length, 1);
	assert.equal(rlm.requests.length, 0);
	assert.equal(fake.requests.filter(isSummaryRequest).length, 1);
});

test('the frontier tier keeps Pi\'s default threshold and does not fold the same conversation', async (t) => {
	const { fake, chat } = await twoTurns(t, 'frontier');

	assert.equal(compactions(chat).length, 0);
	assert.equal(fake.requests.filter(isSummaryRequest).length, 0);
	const last = fake.requests.at(-1)!;
	assert.match(requestText(last), new RegExp(OLD_TURN));
});

test('the frontier tier compacts at Pi\'s default threshold with Pi\'s summary, under the fixed privacy fields', async (t) => {
	const fake = await startFakeOpenAI((request) => (isSummaryRequest(request) ? { text: `## Goal\n${CHECKPOINT}` } : { text: 'Noted.' }));
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, {
		...baseEnv(space, fake.baseURL),
		SPOTLIGHT_MODEL_TIER: 'frontier',
		SPOTLIGHT_PROVIDER_REQUEST_BODY: '{"provider":{"zdr":true}}',
	});
	// Past Pi's default threshold for the default 32,768-token window
	// (32,768 - 16,384), with more than Pi's default 20,000 tokens after the old turn.
	await settledPrompt(chat, LONG_TURN);
	await settledPrompt(chat, `${RECENT_TURN}${RECENT_TURN}${RECENT_TURN}${RECENT_TURN}${RECENT_TURN}`);
	await settledPrompt(chat, 'Continue.');

	assert.equal(compactions(chat).length, 1);
	const summaries = fake.requests.filter(isSummaryRequest);
	assert.equal(summaries.length, 1);
	assert.deepEqual(summaries[0].provider, { zdr: true });
	assert.doesNotMatch(requestText(summaries[0]), /Open gate/);
	assert.doesNotMatch(requestText(fake.requests.at(-1)!), new RegExp(OLD_TURN));
});
