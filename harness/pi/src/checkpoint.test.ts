// Conversation folding through the real launcher and a real orchestrator pi
// process, against the scripted OpenAI-compatible server. No network, no model.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { startFakeOpenAI } from './test-support/fake-openai.ts';
import type { ChatRequest } from './test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, workspace } from './test-support/launch.ts';
import type { Chat } from './test-support/launch.ts';
import { skillAlreadyLoaded } from './extension.ts';

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

const WELL_FORMED = `## Goal\nFind the owner.\n## Progress\n${CHECKPOINT}\n## Decisions\nNone.\n## Next\nResolve.\n## Open gate\nNone.`;

async function twoTurns(t: test.TestContext, tier: string, env: Record<string, string> = {}, sessionSummary = WELL_FORMED) {
	const fake = await startFakeOpenAI((request) => (isSummaryRequest(request) ? { text: sessionSummary } : { text: 'Noted.' }));
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

// base213 on 5ce09322: the e4b RLM wrote its reasoning as the checkpoint, with
// the sections only as bullets inside it (seq 26 and 35, paths replaced), and
// the investigator after each fold produced 10,240 tokens of nothing.
const THINKING = JSON.parse(readFileSync(join(import.meta.dirname, 'test-support', 'rlm-thinking-checkpoints.json'), 'utf8')) as Record<string, string>;
const carriedSummary = (chat: Chat) => String((compactions(chat)[0].result as { summary?: string }).summary ?? '');

test('a checkpoint out of shape from the RLM falls back once to the session model, and the fold carries only the sections', async (t) => {
	for (const [name, written] of Object.entries(THINKING)) {
		const rlm = await startFakeOpenAI(() => ({ text: written }));
		t.after(() => rlm.close());
		const { fake, chat } = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL });

		assert.equal(rlm.requests.length, 1, name);
		assert.equal(fake.requests.filter(isSummaryRequest).length, 1, `${name}: the session model is asked once`);
		assert.match(carriedSummary(chat), /^## Goal\n/, name);
		const last = fake.requests.at(-1)!;
		assert.match(requestText(last), new RegExp(CHECKPOINT), name);
		assert.doesNotMatch(requestText(last), /thinking process/i, name);
	}
});

test('a checkpoint\'s preamble before ## Goal is cut, and one with no sections from both writers leaves the conversation unfolded', async (t) => {
	const preamble = await startFakeOpenAI(() => ({ text: `Here is the checkpoint you asked for.\n\n${WELL_FORMED}` }));
	t.after(() => preamble.close());
	const cut = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: preamble.baseURL });
	assert.equal(cut.fake.requests.filter(isSummaryRequest).length, 0, 'a cut RLM checkpoint needs no fallback');
	assert.match(carriedSummary(cut.chat), /^## Goal\n/);
	assert.doesNotMatch(carriedSummary(cut.chat), /Here is the checkpoint/);

	const rlm = await startFakeOpenAI(() => ({ text: THINKING.seq26 }));
	t.after(() => rlm.close());
	const failed = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL }, 'Thinking Process: the checkpoint would say the owner is unknown.');
	assert.equal(compactions(failed.chat).length, 0);
	assert.ok(failed.fake.requests.filter(isSummaryRequest).length >= 1);
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

// #246: a skill loaded before a fold must load in full after it, since the fold
// drops its text; only a repeat within the same unfolded conversation is cut.
test('after a fold the same skill loads in full again', async (t) => {
	const lastUser = (request: ChatRequest) => JSON.stringify(request.messages.filter((message) => message.role === 'user').at(-1)?.content ?? '');
	const skillCall = { toolCalls: [{ name: 'invoke_skill', arguments: { skill: 'spotlight' } }] };
	const fake = await startFakeOpenAI((request) => {
		if (isSummaryRequest(request)) return { text: WELL_FORMED };
		const last = request.messages.at(-1);
		const asked = /Load the skill/.test(lastUser(request));
		return asked && last?.role === 'user' ? skillCall : { text: 'Noted.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '65536', SPOTLIGHT_MODEL_TIER: '12b' });

	await settledPrompt(chat, 'Load the skill.');
	await settledPrompt(chat, LONG_TURN);
	await settledPrompt(chat, RECENT_TURN);
	await settledPrompt(chat, 'Continue.');
	assert.ok(compactions(chat).length >= 1, 'the conversation folded');
	await settledPrompt(chat, 'Load the skill again.');

	const loads = fake.requests
		.filter((request) => !isSummaryRequest(request))
		.flatMap((request) => request.messages.filter((message) => message.role === 'tool').map((message) => JSON.stringify(message.content)));
	const last = loads.at(-1)!;
	assert.ok(!last.includes(skillAlreadyLoaded('spotlight')) && last.length > 1000, 'after the fold the skill loads in full');
});
