import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { closeSync, openSync, renameSync, writeFileSync, writeSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { CaseHeldError, acquireCaseLock } from './lock.ts';

test('a lock file with no record yet is held while young and taken over once old', async (t) => {
	const dataDir = await mkdtemp(join(tmpdir(), 'spotlight-lock-'));
	t.after(() => rm(dataDir, { recursive: true, force: true }));
	await mkdir(join(dataDir, 'locks'));
	const path = join(dataDir, 'locks', 'case-a.lock');
	// What a contender sees between another launcher's create and its write.
	await writeFile(path, '');

	assert.throws(() => acquireCaseLock(dataDir, 'case-a', '/nonexistent/pi'), CaseHeldError);
	assert.equal(await readFile(path, 'utf8'), '');

	const old = new Date(Date.now() - 60_000);
	await utimes(path, old, old);
	const lock = acquireCaseLock(dataDir, 'case-a', '/nonexistent/pi');
	assert.equal(JSON.parse(await readFile(path, 'utf8')).pid, process.pid);
	lock.release();
});

// Deterministic interleaving: the lock file is a FIFO, so the contender blocks
// reading it. While it does, a fresh lock (this test process, alive) replaces
// it; then the contender reads a stale record (a dead pid) and moves on to
// take over what is now the fresh lock.
test('a slower contender that moves a fresh lock aside puts it back and is refused', { skip: process.platform === 'win32' && 'needs a FIFO' }, async (t) => {
	const dataDir = await mkdtemp(join(tmpdir(), 'spotlight-lock-'));
	t.after(() => rm(dataDir, { recursive: true, force: true }));
	await mkdir(join(dataDir, 'locks'));
	const path = join(dataDir, 'locks', 'case-a.lock');
	execFileSync('mkfifo', [path]);
	const dead = spawn(process.execPath, ['-e', '']);
	await new Promise((done) => dead.once('exit', done));

	const lock = fileURLToPath(new URL('./lock.ts', import.meta.url));
	const contender = spawn(process.execPath, ['--input-type=module', '-e', `import { acquireCaseLock } from ${JSON.stringify(lock)};
try { acquireCaseLock(${JSON.stringify(dataDir)}, 'case-a', '/nonexistent/pi'); process.stdout.write('acquired'); }
catch (error) { process.stdout.write(error.name === 'CaseHeldError' || error.constructor.name === 'CaseHeldError' ? 'held' : String(error)); }`]);
	let out = '';
	contender.stdout.on('data', (chunk) => (out += chunk));
	const exited = new Promise((done) => contender.once('exit', done));

	const fifo = openSync(path, 'w'); // returns once the contender has opened it to read
	const fresh = { case_id: 'case-a', pid: process.pid, children: [], started_at: new Date().toISOString() };
	writeFileSync(`${path}.fresh`, JSON.stringify(fresh));
	renameSync(`${path}.fresh`, path);
	writeSync(fifo, JSON.stringify({ case_id: 'case-a', pid: dead.pid, children: [], started_at: '2000-01-01T00:00:00.000Z' }));
	closeSync(fifo);
	await exited;

	assert.equal(out, 'held');
	assert.deepEqual(JSON.parse(await readFile(path, 'utf8')), fresh);
});
