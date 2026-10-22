// Resume rules applied when a harness opens a case.
import { lstatSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';

export const LANES_DIRECTORY = 'research/swarm';

function laneCompleted(laneDirectory: string): boolean {
	try {
		const lane = JSON.parse(readFileSync(resolve(laneDirectory, 'lane.json'), 'utf8')) as unknown;
		return typeof lane === 'object' && lane !== null && (lane as { status?: unknown }).status === 'completed';
	} catch {
		return false;
	}
}

// A research lane interrupted before it wrote a completed lane.json may hold
// partial leads; it is discarded so the lane reruns from scratch. Returns the
// discarded lane folder names.
export function discardIncompleteLanes(caseDir: string): string[] {
	const root = resolve(caseDir, LANES_DIRECTORY);
	let entries: string[];
	try {
		entries = readdirSync(root);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
		throw error;
	}
	const discarded: string[] = [];
	for (const name of entries) {
		const path = resolve(root, name);
		const metadata = lstatSync(path);
		if (metadata.isDirectory() && !metadata.isSymbolicLink() && laneCompleted(path)) continue;
		rmSync(path, { recursive: true, force: true });
		discarded.push(name);
	}
	return discarded;
}
