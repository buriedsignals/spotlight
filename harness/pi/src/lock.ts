// One writer per case. The harness holding a case keeps a lock file in its
// data folder with its own PID and its pi children's PIDs. A second chat on a
// held case is refused; a lock left by a dead harness is taken over after its
// leftover pi processes are stopped, so none of them keeps writing.
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { linkSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

export type LockRecord = { case_id: string; pid: number; children: number[]; started_at: string };

export class CaseHeldError extends Error {}

function isAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch (error) {
		return (error as NodeJS.ErrnoException).code === 'EPERM';
	}
}

// Only stop a recorded child that is still a pi process from this harness's
// pinned CLI, so a reused PID never takes down an unrelated program.
function isOurPi(pid: number, piCli: string): boolean {
	try {
		const command = execFileSync('ps', ['-o', 'command=', '-p', String(pid)], { encoding: 'utf8' });
		return command.includes(piCli);
	} catch {
		return false;
	}
}

function readRecord(path: string): LockRecord | undefined {
	try {
		const value = JSON.parse(readFileSync(path, 'utf8')) as LockRecord;
		return typeof value.pid === 'number' ? value : undefined;
	} catch {
		return undefined;
	}
}

export class CaseLock {
	readonly path: string;
	#record: LockRecord;
	#released = false;

	constructor(path: string, record: LockRecord) {
		this.path = path;
		this.#record = record;
	}

	setChildren(children: number[]): void {
		if (this.#released) return;
		this.#record = { ...this.#record, children };
		const temporary = `${this.path}.${process.pid}.tmp`;
		writeFileSync(temporary, JSON.stringify(this.#record), { mode: 0o600 });
		renameSync(temporary, this.path);
	}

	release(): void {
		if (this.#released) return;
		this.#released = true;
		const current = readRecord(this.path);
		if (current?.pid === this.#record.pid) rmSync(this.path, { force: true });
	}
}

// A lock file without a readable record is still being written by a launcher
// from before the atomic publish below, or was left by a crash mid-write:
// held while this young, stale after.
const UNREADABLE_GRACE_MS = 2_000;

function isYoung(path: string): boolean {
	try {
		return Date.now() - statSync(path).mtimeMs < UNREADABLE_GRACE_MS;
	} catch {
		return false;
	}
}

export function acquireCaseLock(dataDir: string, caseId: string, piCli: string): CaseLock {
	const directory = resolve(dataDir, 'locks');
	mkdirSync(directory, { recursive: true, mode: 0o700 });
	const path = resolve(directory, `${caseId}.lock`);
	const record: LockRecord = { case_id: caseId, pid: process.pid, children: [], started_at: new Date().toISOString() };
	// Publish the record atomically: the link fails if a lock exists, and the
	// lock file never exists without its record.
	const temporary = `${path}.${process.pid}.new`;
	writeFileSync(temporary, JSON.stringify(record), { mode: 0o600 });
	try {
		return lockWithRecord(path, temporary, record, caseId, piCli);
	} finally {
		rmSync(temporary, { force: true });
	}
}

function lockWithRecord(path: string, temporary: string, record: LockRecord, caseId: string, piCli: string): CaseLock {
	for (let attempt = 0; attempt < 3; attempt += 1) {
		try {
			linkSync(temporary, path);
			return new CaseLock(path, record);
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
		}
		const holder = readRecord(path);
		if (holder ? isAlive(holder.pid) : isYoung(path)) {
			throw new CaseHeldError(`case "${caseId}" is already open in another Spotlight chat. Close that chat first.`);
		}
		for (const pid of holder?.children ?? []) {
			if (isAlive(pid) && isOurPi(pid, piCli)) process.kill(pid, 'SIGKILL');
		}
		// Move the stale lock aside atomically, then check that what moved is
		// the record judged stale. If a faster launcher took the lock over in
		// between, its fresh lock moved instead: put it back (link, never
		// replacing) and try again, which then sees it held.
		const sideline = `${path}.stale-${process.pid}-${randomBytes(4).toString('hex')}`;
		try {
			renameSync(path, sideline);
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
			continue;
		}
		const moved = readRecord(sideline);
		if (moved?.pid !== holder?.pid || moved?.started_at !== holder?.started_at) {
			try {
				linkSync(sideline, path);
			} catch (error) {
				if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
			}
		}
		rmSync(sideline, { force: true });
	}
	throw new CaseHeldError(`case "${caseId}" could not be locked; another Spotlight chat may be opening it.`);
}
