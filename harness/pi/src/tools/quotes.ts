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
import { factCheckerAlternative, factCheckerCapture, registeredCapture } from './fetch.ts';
import type { ToolContext } from './run.ts';

type Row = Record<string, unknown>;
type PendingQuote = { item: Row; finding: string; relation: string; supportType: string };
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
async function recordFetchedItems(context: ToolContext, tools: ToolDefinition[], pending: PendingQuote[], signal?: AbortSignal): Promise<void> {
	const bundle = list(readJson(context.caseDir, 'evidence-bundle.json'), 'items');
	const files = (item: Row) => [...ITEM_FILES.map((key) => item[key]), ...list(item, 'text_derivatives').map((d) => d.path)];
	const findings = list(readJson(context.caseDir, 'findings.json'), 'findings');
	const taken = new Set(bundle.map((item) => String(item.id)));
	let next = 1;
	const updates = new Map<string, Row>();
	for (const { item, finding, supportType } of pending) {
		const ref = (item.source_ref ?? {}) as Row;
		const path = typeof ref.path === 'string' ? ref.path : typeof item.local_file === 'string' ? item.local_file : undefined;
		if (!path) continue;
		let evidence = bundle.find((entry) => files(entry).includes(path));
		if (evidence && evidence.created_by !== 'fact-checker') continue;
		if (typeof item.evidence_bundle_id === 'string' && item.evidence_bundle_id !== evidence?.id) continue;
		const fetched = factCheckerCapture(context, path);
		if (!fetched) {
			const alternative = !evidence ? factCheckerAlternative(context, path) : undefined;
			if (alternative) throw new Error(`refused: data/fact-check.json is unchanged; ${path} is not a registered fact-checker evidence item. A separately acquired fact-checker capture of the same URL and identical bytes is available at ${alternative}. Read that capture, then resubmit the claim with its source_ref.path and matching locators. The harness will register its evidence item and passage; no new fetch is needed to resolve this registration error. Your submitted citation has not been changed.`);
			continue;
		}
		let sha256: string;
		try {
			sha256 = createHash('sha256').update(readFileSync(join(context.caseDir, path))).digest('hex');
		} catch {
			continue;
		}
		// A file rewritten since the fetch is no longer what the URL served.
		if (sha256 !== fetched.sha256) continue;
		if (!evidence) {
			while (taken.has(`FC-E${next}`)) next += 1;
			const id = `FC-E${next}`;
			taken.add(id);
			evidence = {
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
				claim_links: [],
			};
			bundle.push(evidence);
		}
		const claim = findings.find((entry) => entry.id === finding)?.claim;
		if (typeof claim !== 'string') continue;
		const links = list(evidence, 'claim_links');
		if (!links.some((link) => link.finding_id === finding && link.support_type === supportType)) links.push({ finding_id: finding, claim_text: claim, support_type: supportType });
		evidence.claim_links = links;
		updates.set(String(evidence.id), evidence);
		item.evidence_bundle_id = evidence.id;
	}
	if (updates.size === 0) return;
	const write = tools.find((tool) => tool.name === 'write_file');
	if (!write) return;
	try {
		await write.execute('fetched-items', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [...updates.values()] }) }, signal, undefined, undefined as never);
	} catch (error) {
		throw new Error(`refused: data/fact-check.json is unchanged; the harness could not add the evidence item for a file you fetched. ${(error as Error).message}`);
	}
}

// Whether an item's quote occurs in a file. An item without a quote is not
// anchored there, whatever lines it names.
function anchoredIn(caseDir: string, path: string, item: Row): boolean {
	let text: string;
	try {
		text = readFileSync(join(caseDir, path), 'utf8');
	} catch {
		return false;
	}
	return typeof item.quote === 'string' && item.quote.trim() !== '' && text.includes(item.quote.trim());
}

// Whether an item's quote occurs, exactly as written, in a file.
function verbatimIn(caseDir: string, path: string, item: Row): boolean {
	return typeof item.quote === 'string' && item.quote === item.quote.trim() && anchoredIn(caseDir, path, item);
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
	const pending: PendingQuote[] = [];
	for (const claim of list(doc, 'claims')) {
		for (const [key, relation] of Object.entries(RELATION)) {
			for (const item of list(claim, key)) {
				const refs = item.source_expression_refs;
				if (typeof item.quote === 'string' && item.quote.trim() && !(Array.isArray(refs) && refs.length > 0) && typeof claim.finding_id === 'string') {
					const support = (claim.grounding_assessment as Row | undefined)?.support_type;
					const supportType = relation === 'contradicts' ? 'contradicted' : typeof support === 'string' && ['direct', 'indirect', 'inferred', 'contradicted', 'insufficient'].includes(support) ? support : 'insufficient';
					pending.push({ item, finding: claim.finding_id, relation, supportType });
				}
			}
		}
	}
	// e2e r5 resume (31B, D3-F1): items citing the investigator's passages AND
	// a source_ref into the fact-checker's own registered capture got no bundle
	// id, and validate-case.py refused the verdict for want of an anchor. Such an
	// item is independent support only when its quote is found inside the
	// fact-checker's capture (review-fix4 round 2 N1: an existing line number
	// proves nothing): it gets that capture's evidence item. The investigator's
	// passages it also cites add nothing to its independence.
	const anchored: PendingQuote[] = [];
	for (const claim of list(doc, 'claims')) {
		for (const [key, relation] of Object.entries(RELATION)) {
			for (const item of list(claim, key)) {
				const refs = item.source_expression_refs;
				if (!(Array.isArray(refs) && refs.length > 0) || typeof claim.finding_id !== 'string' || typeof item.evidence_bundle_id === 'string') continue;
				const ref = (item.source_ref ?? {}) as Row;
				const path = typeof ref.path === 'string' ? ref.path : typeof item.local_file === 'string' ? item.local_file : undefined;
				if (!path || !factCheckerCapture(context, path) || !anchoredIn(context.caseDir, path, item)) continue;
				const support = (claim.grounding_assessment as Row | undefined)?.support_type;
				const supportType = relation === 'contradicts' ? 'contradicted' : typeof support === 'string' && ['direct', 'indirect', 'inferred', 'contradicted', 'insufficient'].includes(support) ? support : 'insufficient';
				anchored.push({ item, finding: claim.finding_id, relation, supportType });
			}
		}
	}
	if (anchored.length) await recordFetchedItems(context, tools, anchored, signal);
	if (pending.length === 0) return anchored.length ? JSON.stringify(doc, null, 2) : content;
	await recordFetchedItems(context, tools, pending, signal);
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
			// The source's own words: code found the quote, as written, in a
			// file a harness fetch saved (report-direct-quote-diff.md). Only such
			// a passage may be quoted in the report.
			...(path && registeredCapture(context, path) && verbatimIn(context.caseDir, path, item) ? { direct_quote: true } : {}),
			finding_links: [{ finding_id: finding, relation }],
		};
	});
	try {
		await sourceExpressions.execute('quotes', { action: 'write', cycle: Math.max(1, cycles), proposal: { expressions } }, signal, undefined, undefined as never);
		// Only hash anchors the strict passage writer has accepted, never model-supplied hashes.
		for (const { item } of pending) {
			const path = (item.source_ref as Row | undefined)?.path ?? item.local_file;
			if (typeof path === 'string') item.sha256 = createHash('sha256').update(readFileSync(join(context.caseDir, path))).digest('hex');
		}
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
