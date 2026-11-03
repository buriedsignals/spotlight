// #274: before a fact-check batch the harness re-fetches each cited source
// once per case and says whether the recorded passages are still on it. A
// re-fetch that still holds the supporting passage verifies the finding
// (Tom, 2026-11-03, plan C8); a second source is optional. The copy is saved as research/fc-recheck-<item>.md(.raw), named and
// dated as harness-acquired; it is not added to the evidence bundle, so it is
// evidence only if the fact-checker records it as an item of its own. Sensitive
// cases make no request.
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import type { CheckDocs, Recheck, SourceRecheck } from './roles.ts';
import { factCheckerCapture, fetchTool } from './tools/fetch.ts';
import type { ToolContext } from './tools/run.ts';

type Row = Record<string, unknown>;
const rows = (doc: unknown, key: string): Row[] => {
	const value = (doc as Row | undefined)?.[key];
	return Array.isArray(value) ? value.filter((row): row is Row => !!row && typeof row === 'object') : [];
};
const flat = (text: string) => text.replace(/\s+/g, ' ').trim();

// Re-fetches in flight at once.
export const RECHECK_PARALLEL = 4;

export type RecheckOptions = { context: ToolContext; dataDir: string; sensitive: boolean; signal?: AbortSignal };

// The re-check of each cited http(s) source of these findings, by URL; done
// once per case, read back from <dataDir>/work/recheck/<case>.json after that.
export async function recheckSources(docs: CheckDocs, ids: string[], options: RecheckOptions): Promise<Recheck> {
	if (options.sensitive) return {};
	// One file per case: a case's batches run one after another, and two cases
	// running at once never write the same file (Codex review of #274).
	const store = resolve(options.dataDir, 'work', 'recheck', `${createHash('sha256').update(options.context.caseDir).digest('hex').slice(0, 16)}.json`);
	let done: Recheck = {};
	try {
		done = JSON.parse(readFileSync(store, 'utf8')) as Recheck;
	} catch {
		done = {};
	}
	const findings = rows(docs.findings, 'findings').filter((row) => ids.includes(String(row.id)));
	const refs = new Set(findings.flatMap((row) => (Array.isArray(row.evidence_bundle_refs) ? row.evidence_bundle_refs.map(String) : [])));
	const items = rows(docs.bundle, 'items').filter((item) => refs.has(String(item.id)) || rows(item, 'claim_links').some((link) => ids.includes(String(link.finding_id))));
	const passages = rows(docs.expressions, 'expressions');
	const tool = fetchTool(options.context);
	// step2-design.md section 3: the sources are fetched in parallel, a few at a
	// time, each URL once; a failure stays that URL's status.
	const pending = new Map<string, Row>();
	for (const item of items) {
		const url = item.source_url ?? item.url;
		if (typeof url === 'string' && /^https?:\/\//.test(url) && !done[url] && !pending.has(url)) pending.set(url, item);
	}
	const queue = [...pending.entries()];
	const worker = async () => {
		for (let next = queue.shift(); next && !options.signal?.aborted; next = queue.shift()) await recheckOne(next[0], next[1]);
	};
	const recheckOne = async (url: string, item: Row) => {
		const quotes = passages.filter((p) => p.original_evidence_bundle_id === item.id).map((p) => flat(String(p.text ?? ''))).filter(Boolean);
		const stamp = new Date().toISOString();
		try {
			const result = JSON.parse((await tool.execute('recheck', { url, save_as: `fc-recheck-${String(item.id)}.md` }, options.signal, undefined, undefined as never)).content[0].text) as { raw_path?: string; saved_to?: string; markdown?: string };
			const path = result.raw_path ?? result.saved_to;
			const page = flat(path ? readFileSync(resolve(options.context.caseDir, path), 'utf8') : (result.markdown ?? ''));
			const missing = quotes.filter((quote) => !page.includes(quote)).length;
			const status = quotes.length === 0 ? 're-fetched, no recorded passage to compare' : missing === 0 ? `still says the ${quotes.length} recorded passage${quotes.length === 1 ? '' : 's'}` : `changed: ${missing} of ${quotes.length} recorded passages are no longer on the page`;
			const capture = path ? factCheckerCapture(options.context, path) : undefined;
			done[url] = { status, ...(capture && path ? { acquisition: { ...capture, path } } : {}) };
		} catch (error) {
			done[url] = { status: `unreachable: ${(error as Error).message.slice(0, 160)} (harness re-fetch ${stamp})` };
		}
	};
	await Promise.all(Array.from({ length: Math.min(RECHECK_PARALLEL, queue.length) }, worker));
	try {
		mkdirSync(dirname(store), { recursive: true, mode: 0o700 });
		writeFileSync(store, JSON.stringify(done), { mode: 0o600 });
	} catch {
		// A re-check that cannot be kept is done again for the next batch.
	}
	const urls = new Set(items.map((item) => item.source_url ?? item.url));
	// Cached captures retain their real acquisition time. Revalidate the proof
	// on each use; status strings and modified/missing files cannot grant trust.
	return Object.fromEntries(Object.entries(done).filter(([url]) => urls.has(url)).map(([url, check]) => {
		if (!check || (typeof check !== 'string' && (typeof check !== 'object' || typeof check.status !== 'string'))) return [url, { status: 'Cached recheck metadata is unusable' }];
		if (typeof check === 'string' || !check.acquisition) return [url, check];
		const { acquisition, status } = check;
		const current = factCheckerCapture(options.context, acquisition.path);
		const valid = acquisition.acquired_by === 'fact-checker' && current && current.url === acquisition.url && current.accessed === acquisition.accessed && current.sha256 === acquisition.sha256;
		return [url, valid ? check : { status } satisfies SourceRecheck];
	}));
}
