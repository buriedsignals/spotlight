// The launcher side of delegation: runWorker and the worker queue, driven
// with small stand-in worker processes instead of pi.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, readdir, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { MAX_CONCURRENT_WORKERS, PROGRESS_EVERY_MS, WorkerQueue, runWorker, spawnAgentTool, withProgress, FACT_CHECKERS_SPENT } from './subagents.ts';
import * as subagents from './subagents.ts';
import type { WorkerLaunch } from './subagents.ts';

test('validated progress refunds one allowance once per finding and stale or cancelled work never refunds', async (t) => {
	const oldSend = process.send;
	t.after(() => { process.send = oldSend; });
	const cycle = { spawns: 0, generation: 0, credited: new Set<string>() };
	const tool = spawnAgentTool(['fact-checker'], cycle);
	let response: Record<string, unknown> = {};
	let request: { id: string } | undefined;
	process.send = ((message: { kind: string; id: string }) => {
		if (message.kind === 'spotlight-spawn') { request = message; queueMicrotask(() => process.emit('message', { kind: 'spotlight-result', id: message.id, ok: true, text: 'Claims complete.', ...response })); }
		return true;
	}) as typeof process.send;
	const spawn = (signal?: AbortSignal) => tool.execute('call', { agent: 'fact-checker', task: 'Check assigned findings.' }, signal, undefined, undefined as never);
	for (const id of ['F1', 'F2', 'F3', 'F4']) { response = { factCheckProgress: [id] }; await spawn(); }
	assert.equal(cycle.spawns, 0, 'four validated progress receipts do not consume the allowance');
	response = { factCheckProgress: ['F1', 'F2'] };
	await spawn();
	response = {};
	await spawn();
	await spawn();
	await assert.rejects(spawn(), new RegExp(FACT_CHECKERS_SPENT.slice(0, 35)));
	assert.ok(request);
	const reset = (subagents as typeof subagents & { resetFactCheckerCycle: (value: typeof cycle) => void }).resetFactCheckerCycle;
	reset(cycle);
	response = { factCheckProgress: ['F5'] };
	const controller = new AbortController();
	const cancelled = spawn(controller.signal);
	controller.abort();
	await cancelled.catch(() => {});
	assert.equal(cycle.spawns, 1, 'a cancelled result cannot refund');
	reset(cycle);
	response = { factCheckProgress: ['F6'] };
	const stale = spawn();
	reset(cycle);
	await stale;
	assert.equal(cycle.spawns, 0, 'a result from a prior generation cannot alter the new allowance');
	assert.equal(cycle.credited.has('F6'), false);
	// U9 held-kde: a spawn the launcher refused before starting a worker is no attempt, and leaves the correction unspent.
	reset(cycle);
	response = { ok: false, error: 'spawn_agent: refused', notStarted: true };
	const correction = () => tool.execute('call', { agent: 'fact-checker', task: 'MODE: EVIDENCE CORRECTION' }, undefined, undefined, undefined as never);
	for (let tries = 0; tries < 4; tries++) await assert.rejects(correction(), /refused/);
	assert.equal(cycle.spawns, 0);
	assert.equal(cycle.corrections, 0);
	response = { ok: false, error: 'ENOENT: no such file', notStarted: true };
	await assert.rejects(spawn(), /^Error: spawn_agent could not start the fact-checker: ENOENT/);
	assert.equal(cycle.spawns, 0, 'a launch that threw is no attempt');
	response = { ok: false, error: 'worker failed' };
	await assert.rejects(spawn(), /^Error: worker failed/);
	assert.equal(cycle.spawns, 1, 'a worker that started and failed is an attempt');
});

test('on frontier a fact-checker launch that threw still spends its attempt', async (t) => {
	const oldSend = process.send;
	const oldTier = process.env.SPOTLIGHT_MODEL_TIER;
	process.env.SPOTLIGHT_MODEL_TIER = 'frontier';
	t.after(() => { process.send = oldSend; if (oldTier === undefined) delete process.env.SPOTLIGHT_MODEL_TIER; else process.env.SPOTLIGHT_MODEL_TIER = oldTier; });
	const cycle = { spawns: 0, generation: 0 };
	const tool = spawnAgentTool(['fact-checker'], cycle);
	process.send = ((message: { kind: string; id: string }) => {
		if (message.kind === 'spotlight-spawn') queueMicrotask(() => process.emit('message', { kind: 'spotlight-result', id: message.id, ok: false, error: 'ENOENT: no such file', notStarted: true }));
		return true;
	}) as typeof process.send;
	await assert.rejects(tool.execute('call', { agent: 'fact-checker', task: 'Check assigned findings.' }, undefined, undefined, undefined as never), /ENOENT/);
	assert.equal(cycle.spawns, 1);
});

async function scratch(t: TestContext): Promise<string> {
	const root = await realpath(await mkdtemp(join(tmpdir(), 'spotlight-pi-subagents-')));
	t.after(() => rm(root, { recursive: true, force: true }));
	return root;
}

function worker(root: string, command: string, args: string[]): WorkerLaunch {
	return { command, args, env: { PATH: process.env.PATH ?? '' }, cwd: join(root, 'work'), onSpawn() {}, onStderr() {} };
}

// Answers one prompt the way Pi's RPC mode does, after one stdout line that is not JSON.
const NOISY_WORKER = `
let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
	buffer += chunk;
	let at;
	while ((at = buffer.indexOf('\\n')) !== -1) {
		const command = JSON.parse(buffer.slice(0, at));
		buffer = buffer.slice(at + 1);
		if (command.id === 'prompt') {
			process.stdout.write('Debugger listening, not JSON\\n');
			process.stdout.write(JSON.stringify({ type: 'response', id: 'prompt', success: true, data: { disposition: 'handled' } }) + '\\n');
		} else if (command.id === 'last') {
			process.stdout.write(JSON.stringify({ type: 'response', id: 'last', success: true, data: { text: 'worker answer' } }) + '\\n');
		}
	}
});
`;

test('a worker that cannot start is a failed task, not a launcher crash', async (t) => {
	const root = await scratch(t);

	await assert.rejects(
		runWorker(worker(root, join(root, 'no-such-worker'), []), 'task', new AbortController().signal),
		/ENOENT|could not start/,
	);
});

test('a worker that closes its input and exits at once is a failed task, not a launcher crash', async (t) => {
	const root = await scratch(t);
	// Larger than a pipe buffer, so the write is still pending when the worker goes.
	const task = 'x'.repeat(4 * 1024 * 1024);

	await assert.rejects(
		runWorker(worker(root, process.execPath, ['-e', 'require("node:fs").closeSync(0)']), task, new AbortController().signal),
		/worker/,
	);
});

test('a non-JSON stdout line is skipped and the transcript folder is created', async (t) => {
	const root = await scratch(t);
	const transcripts = join(root, 'transcripts', 'nested');
	const before = process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR;
	process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR = transcripts;
	t.after(() => {
		if (before === undefined) delete process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR;
		else process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR = before;
	});

	const answer = await runWorker(worker(root, process.execPath, ['-e', NOISY_WORKER]), 'task', new AbortController().signal);

	assert.equal(answer, 'worker answer');
	assert.equal((await readdir(transcripts)).length, 1);
});

test('a local install keeps worker transcripts in its harness data folder unless told otherwise', async (t) => {
	const root = await scratch(t);
	const saved = { dir: process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR, data: process.env.SPOTLIGHT_HARNESS_DATA, tier: process.env.SPOTLIGHT_MODEL_TIER };
	t.after(() => { for (const [key, value] of [['SPOTLIGHT_WORKER_TRANSCRIPT_DIR', saved.dir], ['SPOTLIGHT_HARNESS_DATA', saved.data], ['SPOTLIGHT_MODEL_TIER', saved.tier]]) { if (value === undefined) delete process.env[key!]; else process.env[key!] = value; } });
	delete process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR;
	process.env.SPOTLIGHT_HARNESS_DATA = join(root, 'data');
	delete process.env.SPOTLIGHT_MODEL_TIER;
	await runWorker(worker(root, process.execPath, ['-e', NOISY_WORKER]), 'task', new AbortController().signal);
	assert.equal((await readdir(join(root, 'data', 'transcripts'))).length, 1);
	process.env.SPOTLIGHT_MODEL_TIER = 'frontier';
	await runWorker(worker(root, process.execPath, ['-e', NOISY_WORKER]), 'task', new AbortController().signal);
	assert.equal((await readdir(join(root, 'data', 'transcripts'))).length, 1, 'frontier writes none');
});

// Gold run 1 on 99c6567e: a worker's whole answer was "<|tool_response>", and the orchestrator took it as the result.
test('a worker that ends empty or with only a control token is a failed task', async (t) => {
	const root = await scratch(t);
	const tokenOnly = NOISY_WORKER.replace("'worker answer'", "'<|tool_response>'");

	await assert.rejects(runWorker(worker(root, process.execPath, ['-e', tokenOnly]), 'task', new AbortController().signal), /worker ended without an answer/);
});

// Case 96f45e2d: a closed laptop cut a worker; its earlier text read as an answer, and the correction counted.
test('a worker whose last turn ended in a model error is a failed task, even with earlier text; a retry that lands is not', async (t) => {
	const root = await scratch(t);
	const after = (...reasons: string[]) => NOISY_WORKER.replace("'worker answer'", "(" + JSON.stringify(reasons) + ".forEach((reason) => console.log(JSON.stringify({ type: 'message_end', message: { role: 'assistant', stopReason: reason } }))), 'earlier text')");

	await assert.rejects(runWorker(worker(root, process.execPath, ['-e', after('stop', 'error')]), 'task', new AbortController().signal), /worker was cut off/);
	assert.equal(await runWorker(worker(root, process.execPath, ['-e', after('error', 'stop')]), 'task', new AbortController().signal), 'earlier text');
});

test('a task cancelled while queued never starts its worker', async (t) => {
	const root = await scratch(t);
	const queue = new WorkerQueue();
	const release = Promise.withResolvers<void>();
	const busy = Array.from({ length: MAX_CONCURRENT_WORKERS }, () => queue.run(() => release.promise));
	const marker = join(root, 'started');
	const controller = new AbortController();

	const queued = queue.run(() =>
		runWorker(
			worker(root, process.execPath, ['-e', `require("node:fs").writeFileSync(${JSON.stringify(marker)}, "")`]),
			'task',
			controller.signal,
		),
	);
	controller.abort();
	release.resolve();
	await Promise.all(busy);

	await assert.rejects(queued, { name: 'AbortError' });
	assert.equal(existsSync(marker), false);
});

// P6: silence during long phases. The beat stops with the worker, success or failure.
test('a running worker reports start, a beat a minute and its end, and a failed worker stops the beat', async (t) => {
	t.mock.timers.enable({ apis: ['setInterval'] });
	const said: string[] = [];
	const { promise, resolve } = Promise.withResolvers<string>();
	const running = withProgress((text) => said.push(text), 'investigator', promise);
	t.mock.timers.tick(PROGRESS_EVERY_MS * 2);
	resolve('done');
	assert.equal(await running, 'done');
	t.mock.timers.tick(PROGRESS_EVERY_MS * 3);
	assert.deepEqual(said, ['The investigator started.', 'The investigator is still working (1 min).', 'The investigator is still working (2 min).', 'The investigator finished.']);

	said.length = 0;
	await assert.rejects(withProgress((text) => said.push(text), 'analyst', Promise.reject(new Error('stopped'))), /stopped/);
	t.mock.timers.tick(PROGRESS_EVERY_MS);
	assert.deepEqual(said, ['The analyst started.', 'The analyst finished.']);
	assert.equal(await withProgress(undefined, 'analyst', Promise.resolve(1)), 1, 'frontier says nothing');
});
