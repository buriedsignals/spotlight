// #274: the fact-checker judges, the harness keeps the books. A fact-check
// write may cite a line by its exact text, an evidence item
// {"quote": "...", "source_ref": {"path": "...", "line_start": N, "line_end": N}},
// instead of a recorded passage. Before the write, the harness records each
// such quote through the source_expressions tool (the one writer: hashes,
// fingerprints, anchors, the role's read scope) and puts the new passage's id
// in the item's source_expression_refs. A quote the writer refuses refuses the
// write with the writer's reason; the validators stay as strict as before.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { fetchedFile } from './fetch.ts';
import type { ToolContext } from './run.ts';

type Row = Record<string, unknown>;
const RELATION: Record<string, string> = { evidence_for: 'supports', evidence_against: 'contradicts' };

function readJson(caseDir: string, name: string): Row {
	try {
		return JSON.parse(readFileSync(join(caseDir, 'data', name), 'utf8')) as Row;
	} catch {
		return {};
	}
}

const list = (doc: unknown, key: string): Row[] => {
	const value = (doc as Row | undefined)?.[key];
	return Array.isArray(value) ? value.filter((row): row is Row => !!row && typeof row === 'object') : [];
};

// arm2 ds-spectator on ce4f010e: a quote from the fact-checker's own fetch
// was refused three times because no evidence item listed the file, and the
// fact-checker cannot edit the bundle. When a cited file is one a fetch saved
// in this case (tools/fetch.ts fetchedFile) and no item lists it, the harness
// adds the fact-checker's item for it (its URL, access time, hash) through the
// fact-checker's own bundle write, and the quote cites that item.
const ITEM_FILES = ['raw_path', 'downloaded_document_path', 'screenshot_path', 'path'];
async function recordFetchedItems(context: ToolContext, tools: ToolDefinition[], items: Row[], signal?: AbortSignal): Promise<void> {
	const bundle = list(readJson(context.caseDir, 'evidence-bundle.json'), 'items');
	const listed = new Set(bundle.flatMap((item) => [...ITEM_FILES.map((key) => item[key]), ...list(item, 'text_derivatives').map((d) => d.path)].filter((path): path is string => typeof path === 'string')));
	const taken = new Set(bundle.map((item) => String(item.id)));
	let next = 1;
	const added: Row[] = [];
	for (const item of items) {
		const ref = (item.source_ref ?? {}) as Row;
		const path = typeof ref.path === 'string' ? ref.path : typeof item.local_file === 'string' ? item.local_file : undefined;
		if (!path || listed.has(path) || typeof item.evidence_bundle_id === 'string') continue;
		const fetched = fetchedFile(context, path);
		if (!fetched) continue;
		while (taken.has(`FC-E${next}`)) next += 1;
		const id = `FC-E${next}`;
		taken.add(id);
		listed.add(path);
		let sha256: string;
		try {
			sha256 = createHash('sha256').update(readFileSync(join(context.caseDir, path))).digest('hex');
		} catch {
			continue;
		}
		added.push({
			id,
			created_by: 'fact-checker',
			query_or_task: `Fetched by the fact-checker to check a finding: ${fetched.url}`,
			acquisition_method: fetched.provider === 'crawl4ai' || fetched.provider === 'firecrawl' ? fetched.provider : 'other',
			source_url: fetched.url,
			accessed: fetched.accessed,
			raw_path: path,
			sha256,
			extraction_confidence: 'medium',
			human_verification_required: false,
		});
		item.evidence_bundle_id = id;
	}
	if (added.length === 0) return;
	const write = tools.find((tool) => tool.name === 'write_file');
	if (!write) return;
	try {
		await write.execute('fetched-items', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: added }) }, signal, undefined, undefined as never);
	} catch (error) {
		throw new Error(`refused: data/fact-check.json is unchanged; the harness could not add the evidence item for a file you fetched. ${(error as Error).message}`);
	}
}

// The fact-check write with its quotes recorded, or the content unchanged when
// it cites none.
async function resolveQuotes(context: ToolContext, tools: ToolDefinition[], sourceExpressions: ToolDefinition, content: string, signal?: AbortSignal): Promise<string> {
	let doc: Row;
	try {
		doc = JSON.parse(content) as Row;
	} catch {
		return content;
	}
	const pending: Array<{ item: Row; finding: string; relation: string }> = [];
	for (const claim of list(doc, 'claims')) {
		for (const [key, relation] of Object.entries(RELATION)) {
			for (const item of list(claim, key)) {
				const refs = item.source_expression_refs;
				if (typeof item.quote === 'string' && item.quote.trim() && !(Array.isArray(refs) && refs.length > 0) && typeof claim.finding_id === 'string') {
					pending.push({ item, finding: claim.finding_id, relation });
				}
			}
		}
	}
	if (pending.length === 0) return content;
	await recordFetchedItems(context, tools, pending.map(({ item }) => item), signal);
	const passages = list(readJson(context.caseDir, 'source-expressions.json'), 'expressions');
	const taken = new Set(passages.map((p) => String(p.id)));
	let next = 1;
	const freeId = () => {
		while (taken.has(`FC-Q${next}`)) next += 1;
		taken.add(`FC-Q${next}`);
		return `FC-Q${next}`;
	};
	// A passage's language is that of the finding's recorded passages, else
	// undetermined ("und"), never guessed.
	const languageOf = (finding: string) =>
		passages.find((p) => list(p, 'finding_links').some((link) => link.finding_id === finding) && typeof p.language === 'string')?.language ?? 'und';
	const cycles = list(readJson(context.caseDir, 'investigation-log.json'), 'cycles').length;
	const expressions = pending.map(({ item, finding, relation }) => {
		const ref = (item.source_ref ?? {}) as Row;
		const path = typeof ref.path === 'string' ? ref.path : typeof item.local_file === 'string' ? item.local_file : undefined;
		const id = freeId();
		item.source_expression_refs = [id];
		return {
			id,
			text: item.quote,
			...(path ? { anchor_ref: { path, ...(Number.isInteger(ref.line_start) ? { line_start: ref.line_start, line_end: Number.isInteger(ref.line_end) ? ref.line_end : ref.line_start } : {}) } } : {}),
			original_evidence_bundle_id: typeof item.evidence_bundle_id === 'string' ? item.evidence_bundle_id : null,
			language: typeof item.language === 'string' ? item.language : languageOf(finding),
			finding_links: [{ finding_id: finding, relation }],
		};
	});
	try {
		await sourceExpressions.execute('quotes', { action: 'write', cycle: Math.max(1, cycles), proposal: { expressions } }, signal, undefined, undefined as never);
	} catch (error) {
		throw new Error(`refused: data/fact-check.json is unchanged; the harness could not record a quote you cited as a passage. ${(error as Error).message}`);
	}
	return JSON.stringify(doc, null, 2);
}

// write_file for the fact-checker, with its quotes recorded first.
export function withQuoteResolution(tools: ToolDefinition[], context: ToolContext): ToolDefinition[] {
	if (context.env?.SPOTLIGHT_ROLE !== 'fact-checker') return tools;
	const sourceExpressions = tools.find((tool) => tool.name === 'source_expressions');
	if (!sourceExpressions) return tools;
	return tools.map((tool) =>
		tool.name !== 'write_file'
			? tool
			: {
					...tool,
					async execute(id, params, signal, ...rest) {
						const input = params as { path?: unknown; content?: unknown };
						const target = typeof input.path === 'string' ? input.path.replace(/^\{CASE_DIR\}[\\/]/, '') : '';
						if (target !== 'data/fact-check.json' || typeof input.content !== 'string') return tool.execute(id, params, signal, ...rest);
						const content = await resolveQuotes(context, tools, sourceExpressions, input.content, signal);
						return tool.execute(id, { ...input, content }, signal, ...rest);
					},
				},
	);
}
