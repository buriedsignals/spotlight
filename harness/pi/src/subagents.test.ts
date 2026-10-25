// The launcher side of delegation: runWorker and the worker queue, driven
// with small stand-in worker processes instead of pi.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdtemp, readdir, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { MAX_CONCURRENT_WORKERS, WorkerQueue, runWorker } from './subagents.ts';
import type { WorkerLaunch } from './subagents.ts';

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

// Gold run 1 on 99c6567e: a worker's whole answer was "<|tool_response>", and the orchestrator took it as the result.
test('a worker that ends empty or with only a control token is a failed task', async (t) => {
	const root = await scratch(t);
	const tokenOnly = NOISY_WORKER.replace("'worker answer'", "'<|tool_response>'");

	await assert.rejects(runWorker(worker(root, process.execPath, ['-e', tokenOnly]), 'task', new AbortController().signal), /worker ended without an answer/);
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
