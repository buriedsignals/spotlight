// Navigator is discovery, not factual evidence. Preserve the actual result once;
// methodology writes can copy its mechanical provenance without model-authored
// paths, timestamps or catalogue identifiers.
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, linkSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { FileRefused, readTarget, writeTarget } from './case-files.ts';
import type { ToolContext } from './run.ts';

const FOLDER = 'research/navigator';
const RECEIPT = /^[0-9a-f-]{36}-receipt\.json$/;
export type NavigatorReceipt = {
	outcome: 'used';
	action: 'find' | 'show';
	query: string;
	argv: string[];
	retrieved_at: string;
	response_json: string;
	response_sha256: string;
};

export function recordNavigatorLookup(context: ToolContext, input: { action: 'find' | 'show'; value: string }, response: unknown): string {
	const id = randomUUID();
	// Validate/create only the fixed case-relative parent via the same confinement
	// checks as other tools. The final receipt name is reserved from file writes;
	// wx prevents overwriting an existing receipt or following a final symlink.
	const target = writeTarget(context.caseDir, context.writeRoot, `${FOLDER}/${id}.json`);
	const receipt: NavigatorReceipt = {
		outcome: 'used', action: input.action, query: input.value,
		argv: ['navigator', 'tools', input.action, '--json', '--', input.value],
		retrieved_at: new Date().toISOString(), response_json: JSON.stringify(response),
		response_sha256: createHash('sha256').update(JSON.stringify(response)).digest('hex'),
	};
	const path = `${FOLDER}/${id}-receipt.json`;
	const temporary = `${target}.tmp`;
	try {
		writeFileSync(temporary, JSON.stringify(receipt), { encoding: 'utf8', mode: 0o600, flag: 'wx' });
		// Publishing a complete same-directory inode is atomic and exclusive:
		// readers never see partial JSON, and an existing receipt is not replaced.
		linkSync(temporary, join(dirname(target), `${id}-receipt.json`));
	} finally {
		try { unlinkSync(temporary); }
		catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
	}
	return path;
}

// The file-write boundary owns these fields, while any model-chosen selection
// and direction remain judgments. Skip states are untouched when no lookup ran.
export function completeNavigatorMetadata(caseDir: string, proposed: string): string {
	const folder = join(caseDir, FOLDER);
	if (!existsSync(folder)) return proposed;
	const names = readdirSync(folder).filter((name) => RECEIPT.test(name)).sort();
	if (!names.length) return proposed;
	let document: Record<string, unknown>;
	try { document = JSON.parse(proposed); } catch { return proposed; }
	if (!document || typeof document !== 'object' || Array.isArray(document)) return proposed;
	const nav = document.navigator && typeof document.navigator === 'object' && !Array.isArray(document.navigator)
		? document.navigator as Record<string, unknown> : {};
	const supplied = Array.isArray(nav.queries) ? nav.queries as Array<Record<string, unknown>> : [];
	const queries = names.map((name) => {
		const path = `${FOLDER}/${name}`;
		const receipt = JSON.parse(readFileSync(readTarget(caseDir, path), 'utf8')) as NavigatorReceipt;
		if (receipt.outcome !== 'used' || receipt.response_sha256 !== createHash('sha256').update(receipt.response_json).digest('hex')) {
			throw new FileRefused(`refused: Navigator receipt ${path} is invalid; do not invent lookup provenance`);
		}
		const chosen = supplied.find((query) => query && (query.receipt_path === path || query.response_path === path));
		return {
			...(chosen ?? {}), interface: 'cli', mode: 'tool_discovery', query: receipt.query,
			command: JSON.stringify(receipt.argv), retrieved_at: receipt.retrieved_at,
			response_path: path, receipt_path: path, response_sha256: receipt.response_sha256,
			selected_tools: chosen?.selected_tools ?? [],
		};
	});
	// Keep historical/manual queries: only records claiming one of our actual
	// receipts are replaced. Never manufacture selection decisions from results.
	const historical = supplied.filter((query) => query && !queries.some((actual) => query.receipt_path === actual.receipt_path || query.response_path === actual.response_path));
	document.navigator = { ...nav, required: nav.required === true, used: true, outcome: 'used', queries: [...historical, ...queries] };
	return JSON.stringify(document, null, 2);
}
