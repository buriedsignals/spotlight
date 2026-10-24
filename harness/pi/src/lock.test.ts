import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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
