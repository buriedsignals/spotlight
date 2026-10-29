// Conversation folding through the real launcher and a real orchestrator pi
// process, against the scripted OpenAI-compatible server. No network, no model.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { startFakeOpenAI } from './test-support/fake-openai.ts';
import type { ChatRequest } from './test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, workspace } from './test-support/launch.ts';
import type { Chat } from './test-support/launch.ts';
import { readAlreadyShown, skillAlreadyLoaded } from './extension.ts';

const OLD_TURN = 'OLD-TURN-MARKER';
const CHECKPOINT = 'CHECKPOINT-SENTINEL';
// About 20,000 tokens at Pi's four-characters-per-token estimate. With the
// recent turn (about 5,000 tokens, more than the 12b tier keeps verbatim) the
// conversation passes a 20,480 fold point, pinned with SPOTLIGHT_COMPACT_AT
// (the derived point for a 65,536 window is higher; config.test.ts covers it),
// but stays well under the 65,536-token window's default threshold
// (65,536 - 16,384), which the frontier tier keeps.
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
		SPOTLIGHT_COMPACT_AT: '20480',
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

// arms on 8699f11d (ds-spectator): the RLM answered HTTP 400 to a long
// conversation, the throw skipped the session fallback and every fold was
// cancelled until the fact-checker overflowed at 87.6k.
test('an RLM error on the checkpoint falls back to the session model, and the conversation folds', async (t) => {
	const rlm = await startFakeOpenAI(() => ({ status: 400 }));
	t.after(() => rlm.close());
	const { fake, chat } = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: rlm.baseURL });
	assert.equal(rlm.requests.length, 1);
	assert.equal(fake.requests.filter(isSummaryRequest).length, 1, 'the session model writes the checkpoint');
	assert.equal(compactions(chat).length, 1, 'the conversation folded');
	assert.match(carriedSummary(chat), /^## Goal\n/);
});

// arms on 8699f11d: the e4b RLM served 24,576 tokens per slot while a
// 98k-window fold sent about 57k, so every checkpoint request was refused. A
// conversation past the RLM's context (from /props, or SPOTLIGHT_RLM_CONTEXT)
// goes straight to the session model.
test('a checkpoint the RLM\'s context cannot hold is written by the session model, without asking the RLM', async (t) => {
	let chats = 0;
	const server = createServer((request, response) => {
		if (request.url === '/props') {
			response.writeHead(200, { 'content-type': 'application/json' });
			response.end(JSON.stringify({ default_generation_settings: { n_ctx: 2048 } }));
			return;
		}
		chats += 1;
		response.writeHead(400, { 'content-type': 'application/json' });
		response.end('{"error":"the request exceeds the available context size"}');
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	const { fake, chat } = await twoTurns(t, '12b', { SPOTLIGHT_RLM_OPENAI_BASE_URL: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1` });
	assert.equal(chats, 0, 'the RLM is not asked');
	assert.equal(fake.requests.filter(isSummaryRequest).length, 1);
	assert.equal(compactions(chat).length, 1);
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
// A phase skill carries its phase-only runtime adapter lines (roles.ts,
// skillAdapterNote): they come with the first load, the repeat points back at
// that load, and after the fold they come again with the skill.
test('after a fold the same skill loads in full again, with its phase lines', async (t) => {
	const toolText = (content: unknown) => (typeof content === 'string' ? content : Array.isArray(content) ? content.map((part) => (part as { text?: string }).text ?? '').join('') : '');
	const lastUser = (request: ChatRequest) => JSON.stringify(request.messages.filter((message) => message.role === 'user').at(-1)?.content ?? '');
	const skillCall = { toolCalls: [{ name: 'invoke_skill', arguments: { skill: 'phase-ingest' } }] };
	const fake = await startFakeOpenAI((request) => {
		if (isSummaryRequest(request)) return { text: WELL_FORMED };
		const last = request.messages.at(-1);
		const asked = /Load the skill/.test(lastUser(request));
		return asked && last?.role === 'user' ? skillCall : { text: 'Noted.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '65536', SPOTLIGHT_COMPACT_AT: '20480', SPOTLIGHT_MODEL_TIER: '12b' });

	await settledPrompt(chat, 'Load the skill.');
	await settledPrompt(chat, 'Load the skill once more.');
	await settledPrompt(chat, LONG_TURN);
	await settledPrompt(chat, RECENT_TURN);
	await settledPrompt(chat, 'Continue.');
	assert.ok(compactions(chat).length >= 1, 'the conversation folded');
	await settledPrompt(chat, 'Load the skill again.');

	// Each load's result, from the request that answered it.
	const loads = fake.requests
		.filter((request) => !isSummaryRequest(request) && request.messages.at(-1)?.role === 'tool')
		.map((request) => toolText(request.messages.at(-1)!.content));
	const phaseLine = /- Ingest, the wiki write \(orchestrator only, solo edition\)/;
	assert.match(loads[0], phaseLine, 'the first load carries the phase lines');
	assert.equal(loads[1], skillAlreadyLoaded('phase-ingest'), 'a repeat before the fold points back at the first load');
	const last = loads.at(-1)!;
	assert.equal(loads.length, 3);
	assert.ok(last !== skillAlreadyLoaded('phase-ingest') && last.length > 1000, 'after the fold the skill loads in full');
	assert.match(last, phaseLine, 'and with its phase lines');
});

// The same for reads: a file read before a fold returns its text after it.
test('after a fold an unchanged file read again returns its text', async (t) => {
	const lastUser = (request: ChatRequest) => JSON.stringify(request.messages.filter((message) => message.role === 'user').at(-1)?.content ?? '');
	const readCall = { toolCalls: [{ name: 'read_file', arguments: { path: 'brief-directions.txt' } }] };
	const fake = await startFakeOpenAI((request) => {
		if (isSummaryRequest(request)) return { text: WELL_FORMED };
		return /Read the brief/.test(lastUser(request)) && request.messages.at(-1)?.role === 'user' ? readCall : { text: 'Noted.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_CONTEXT_WINDOW: '65536', SPOTLIGHT_COMPACT_AT: '20480', SPOTLIGHT_MODEL_TIER: '12b' });
	await settledPrompt(chat, 'Write nothing yet.');
	writeFileSync(join(space.casesRoot, 'case-a', 'brief-directions.txt'), 'Who owns the fixture company?\n');

	await settledPrompt(chat, 'Read the brief.');
	await settledPrompt(chat, LONG_TURN);
	await settledPrompt(chat, RECENT_TURN);
	await settledPrompt(chat, 'Continue.');
	assert.ok(compactions(chat).length >= 1, 'the conversation folded');
	await settledPrompt(chat, 'Read the brief again.');

	const reads = fake.requests
		.filter((request) => !isSummaryRequest(request))
		.flatMap((request) => request.messages.filter((message) => message.role === 'tool').map((message) => JSON.stringify(message.content)));
	const last = reads.at(-1)!;
	assert.ok(last.includes('Who owns the fixture company?') && !last.includes(readAlreadyShown('brief-directions.txt')), 'after the fold the file is read in full');
});
