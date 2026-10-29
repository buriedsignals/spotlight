// read_file, write_file, edit_file, list_files, grep_files and hash_files,
// confined to the active case. Reads stay inside the case (plus the checkout's schemas/);
// writes stay inside the caller's write folder, never follow a symlink, and
// never touch the files only the resolver and the review page may write.
import { createHash } from 'node:crypto';
import { closeSync, constants, existsSync, globSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, writeSync } from 'node:fs';
import type { Stats } from 'node:fs';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT, deliverableFor, nextCheckText, readCheckDocs } from '../roles.ts';
import { WITHDRAW_HINT, currentText, directionView, isDirectionFile, mergeByKey, mergeDirection, mergesByKey } from '../directions.ts';
import { approvedIndicatorFramework } from './hosted-skill.ts';
import { ToolArgumentError, checkedParams, jsonResult, runPython, textResult } from './run.ts';
import type { CaseScope, ToolContext } from './run.ts';

export class FileRefused extends Error {}

const SCHEMAS_DIR = resolve(CHECKOUT_ROOT, 'schemas');
// The procedures a 12b persona points at instead of carrying (roles.ts slicePersona).
const AGENT_REFERENCES_DIR = resolve(CHECKOUT_ROOT, 'agents', 'references');
const MAX_READ_CHARS = 200_000;
export const READ_TRUNCATED = '[truncated: use offset and limit to read the rest]';
const MAX_WRITE_BYTES = 20 * 1024 * 1024;
const MAX_LIST = 500;
const MAX_MATCHES = 200;
const MAX_GREP_FILE_BYTES = 4 * 1024 * 1024;
const MAX_HASH_FILES = 50;

// Case-relative, lower-case (case-insensitive file systems), forward slashes.
const PROTECTED = [
	/^data\/orchestration\.json$/, // resolver state
	/^data\/review-nonces\.json$/, // review page
	/^data\/[^/]*-review\.json$/, // review page decisions
	/^data\/ingestion\.json$/, // ingest decision marker (resolver)
	/^data\/report-declined\.json$/, // report decline marker (resolver)
	/(^|\/)[^/]*receipt[^/]*$/, // signing, gate and ingest receipts
	/^receipts\//, // newsroom receipts (the launcher copies them)
	/^data\/editorial-case\.json$/, // newsroom case reference (the launcher)
	/^data\/newsroom-(checks|submission)\.json$/, // newsroom check flags and submission (the launcher)
	/^data\/case-contract\.json$/, // activation receipt (the launcher runs case-contract.py activate-new; the gate approval binds it)
];

// Written only by scripts/write-source-expressions.py, which computes every
// hash and fingerprint; the producers reach it through the source_expressions tool.
const SCRIPT_WRITTEN = [/^data\/source-expressions\.json$/, /^data\/source-positions\.json$/];

function inside(root: string, path: string): boolean {
	const child = relative(root, path);
	return child === '' || (child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child));
}

function caseRelative(root: string, path: string): string {
	return relative(root, path).split(sep).join('/');
}

// Skills write paths as `{CASE_DIR}/...`; a model often copies that prefix
// verbatim, and it means the case folder itself.
const CASE_DIR_PREFIX = /^\{CASE_DIR\}(?:[\\/]|$)/;

function requested(base: string, path: string): string {
	if (path.includes('\0')) throw new FileRefused('refused: path contains a NUL byte');
	const relative = path.replace(CASE_DIR_PREFIX, '');
	return isAbsolute(relative) ? resolve(relative) : resolve(base, relative);
}

// A role narrowed in harness/composition.json reaches only its named case
// files. Names compare case-insensitively, as the protected list does.
function inScope(allowed: string[] | undefined, name: string): boolean {
	return allowed === undefined || allowed.some((entry) => entry.toLowerCase() === name.toLowerCase());
}

// A role's read denylist (`denyReads` in harness/composition.json) hides case
// files from every file tool, the rest of the case staying readable; its write
// denylist (`denyWrites`) refuses write_file and edit_file on case files another
// role owns, the rest of its write folder staying writable. An entry
// ending in "/" hides that folder and everything under it; "*" matches within
// one path segment. Names compare case-insensitively.
function denyPattern(entry: string): RegExp {
	const folder = entry.endsWith('/');
	const body = (folder ? entry.slice(0, -1) : entry)
		.split('*')
		.map((part) => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&'))
		.join('[^/]*');
	return new RegExp(folder ? `^${body}(?:/.*)?$` : `^${body}$`, 'i');
}

function denied(deny: string[] | undefined, name: string): boolean {
	return deny !== undefined && deny.some((entry) => denyPattern(entry).test(name));
}

function mayRead(scope: CaseScope | undefined, name: string): boolean {
	return inScope(scope?.reads, name) && !denied(scope?.denyReads, name);
}

// Every case data file under data/ is one JSON object, and the resolver hashes
// them as objects: a list or broken JSON there leaves the case at an unknown phase.
function refuseNonObjectData(caseDir: string, target: string, content: string): void {
	const path = caseRelative(caseDir, target);
	if (!/^data\/[^/]+\.json$/.test(path)) return;
	let value: unknown;
	try {
		value = JSON.parse(content);
	} catch {
		value = undefined;
	}
	const bundle = path === 'data/evidence-bundle.json';
	// The bundle's items under another key read as no items at all (12B r3 cc: "evidence_bundle").
	if (value !== null && typeof value === 'object' && !Array.isArray(value) && (!bundle || Array.isArray((value as { items?: unknown }).items))) return;
	const shape = bundle ? ' with an `items` list' : '';
	throw new FileRefused(`refused: ${path} must stay one JSON object${shape}; the file is unchanged. Read it, change the part you need, and write the whole object back.`);
}

// LFM3 r3 kde stage 3 on Osaurus (03f008b0): the analyst's proposal failed
// interface 1, which the builder reports only after the analyst's turn, and
// the analyst read and grepped until the stall rule stopped it (s3-1), or failed
// its cross-record rules (s3-3). A proposal is checked by every check the
// builder makes when written, and refused with the builder's text.
async function refuseInvalidProposal(context: ToolContext, target: string, content: string): Promise<void> {
	if (caseRelative(context.caseDir, target) !== 'data/knowledge-proposal.json') return;
	const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', 'build-knowledge-batch.py'), context.caseDir, '--check-proposal'], { input: content, timeoutMs: 60_000 });
	// 3: the case, not the proposal, refuses the build; the harness reports
	// that when the analyst finishes, so the write is not refused for it.
	if (result.code !== 2) return;
	const reason = (result.stderr || result.stdout).trim().replace(/^build-knowledge-batch: refused: /, '');
	// After the gate the analyst cannot record a source position, so the two
	// fixes it can make are named (agents/analyst.md: attributed, findings left
	// without a claim).
	const position = / is attributed but finding \S+ has no source position/.test(reason)
		? ' If the claim states what the finding itself asserts, set attributed to false. If it reports what someone said, leave that claim out and say why in your final answer.'
		: '';
	// LFM3 r4 held-out ingest: 9 of 12 proposals put claim keys in a story
	// arc's event_keys; story_arcs is optional (schemas/knowledge-proposal.schema.json).
	const arc = /: story arc \S+ names unknown events /.test(reason)
		? ' story_arcs is optional. Its event_keys must be keys of events in this file, not claim keys. If there are no events for the arc, remove the story_arcs entry.'
		: '';
	throw new FileRefused(`refused: ${reason}; the file is unchanged. Fix the proposal as schemas/knowledge-proposal.schema.json says and write it again.${position}${arc}`);
}

// U3 ledger row 17: small models (Gemma 12B 13 of 132 runs, LFM3 r5 6 of 22)
// missed edit_file's old_text three or more times in a thread, re-guessing
// whitespace even after reading the file. A miss now shows the closest spans
// of the text the tool edits (a direction's view included), old_text's line
// count each, verbatim, as in the source-expression refusal (#249).
const CLOSEST_SPANS = 3;
const CLOSEST_SPAN_MIN = 0.5;
const CLOSEST_SPAN_MAX_CHARS = 1500;

function bigrams(text: string): Map<string, number> {
	const grams = new Map<string, number>();
	for (let i = 0; i < text.length - 1; i++) grams.set(text.slice(i, i + 2), (grams.get(text.slice(i, i + 2)) ?? 0) + 1);
	return grams;
}

function similarity(a: string, b: string): number {
	const [x, y] = [bigrams(a), bigrams(b)];
	let shared = 0;
	for (const [gram, count] of x) shared += Math.min(count, y.get(gram) ?? 0);
	const total = Math.max(a.length - 1, 0) + Math.max(b.length - 1, 0);
	return total === 0 ? 0 : (2 * shared) / total;
}

function closestSpans(text: string, oldText: string, path: string): string {
	const normal = (value: string) => value.replace(/\s+/g, ' ').trim();
	const wanted = normal(oldText);
	const lines = text.split('\n');
	const size = Math.max(1, oldText.replace(/\n+$/, '').split('\n').length);
	const scored: Array<{ start: number; score: number }> = [];
	for (let start = 0; start + size <= lines.length; start++) {
		const window = lines.slice(start, start + size).join('\n');
		if (window.length > CLOSEST_SPAN_MAX_CHARS || !window.trim()) continue;
		const score = similarity(wanted, normal(window));
		if (score >= CLOSEST_SPAN_MIN) scored.push({ start, score });
	}
	const chosen: number[] = [];
	for (const { start } of scored.sort((a, b) => b.score - a.score)) {
		if (chosen.length === CLOSEST_SPANS) break;
		if (chosen.every((other) => Math.abs(other - start) >= size)) chosen.push(start);
	}
	const json = /^data\/[^/]+\.json$/.test(path) ? ' For a data/*.json file, write_file of the whole file also works.' : '';
	if (chosen.length === 0) return `. old_text must match the file exactly, whitespace and indentation included; read the part you mean to change and copy it.${json}`;
	const spans = chosen.map((start) => `--- lines ${start + 1}-${start + size}:\n${lines.slice(start, start + size).join('\n')}`).join('\n');
	return `. old_text must match the file exactly, whitespace and indentation included. The closest spans are below; copy one exactly as old_text, without the "--- lines" marker.${json}\n${spans}`;
}

function lstatOrUndefined(path: string): Stats | undefined {
	try {
		return lstatSync(path);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
		throw error;
	}
}

// Resolves a write target under `root` without following any symlink: every
// existing parent must be a real directory, missing ones are created owner-only,
// and an existing target must be a regular file.
export function writeTarget(caseDir: string, root: string, path: string, writes?: string[], denyWrites?: string[]): string {
	// resolve() drops a trailing slash, so "research/" would become a file named
	// like the folder and block everything later written inside it.
	if (/[\\/]$/.test(path)) throw new FileRefused(`refused: ${path} names a folder; give a file name inside it (for example ${path}page.md)`);
	// Windows drops a trailing dot or space from a name, so "findings.json." would
	// reach findings.json past the protected and denied patterns.
	if (path.split(/[\\/]/).some((part) => /[. ]$/.test(part) && part !== '.' && part !== '..')) {
		throw new FileRefused(`refused: ${path} has a name ending in a dot or a space`);
	}
	const target = requested(caseDir, path);
	if (!inside(root, target) || target === root) throw new FileRefused(`refused: ${path} is outside the folder this agent may write`);
	// Checked before any parent folder is created.
	if (!inScope(writes, caseRelative(caseDir, target))) {
		throw new FileRefused(`refused: ${path} is not a file this agent may write (${(writes ?? []).join(', ') || 'none'})`);
	}
	if (denied(denyWrites, caseRelative(caseDir, target))) {
		// U3 census (84674fd9): a fact-checker wrote fact-check content over
		// data/findings.json. The refusal names the writer's own file.
		const own = deliverableFor(process.env.SPOTLIGHT_ROLE ?? '', '', '')?.files;
		throw new FileRefused(`refused: ${path} is a case file another role writes; this agent may not write it${own ? `. Your own file is ${own.join(' and ')}.` : ''}`);
	}
	const parts = relative(root, target).split(sep);
	let current = root;
	for (const part of parts.slice(0, -1)) {
		current = join(current, part);
		const stats = lstatOrUndefined(current);
		if (stats?.isSymbolicLink()) throw new FileRefused(`refused: ${caseRelative(caseDir, current)} is a symlink`);
		if (stats && !stats.isDirectory()) throw new FileRefused(`refused: ${caseRelative(caseDir, current)} is not a folder`);
		if (!stats) mkdirSync(current, { mode: 0o700 });
	}
	const stats = lstatOrUndefined(target);
	if (stats?.isSymbolicLink()) throw new FileRefused(`refused: ${path} is a symlink`);
	if (stats?.isDirectory()) throw new FileRefused(`refused: ${path} is a folder; give a file name inside it`);
	if (stats && !stats.isFile()) throw new FileRefused(`refused: ${path} is not a regular file`);
	const name = caseRelative(caseDir, target).toLowerCase();
	if (PROTECTED.some((pattern) => pattern.test(name))) {
		throw new FileRefused(`refused: ${name} is protected; only the resolver or the review page writes it`);
	}
	if (SCRIPT_WRITTEN.some((pattern) => pattern.test(name))) {
		throw new FileRefused(`refused: ${name} is written only through the source_expressions tool, which computes its hashes and fingerprints`);
	}
	// Written only by the Anomaly runner (integrations/anomaly/adapter.py),
	// which checks every signal; the gate shows it to the member as is.
	if (name === 'data/anomaly-signals.json') {
		throw new FileRefused(`refused: ${name} is written only through the anomaly_run tool, which runs the detector and checks its output`);
	}
	// The decision model's answer keeps the Investigation gate closed until each
	// table it named has a run; only the harness writes it.
	if (name === 'data/anomaly-decision.json') {
		throw new FileRefused(`refused: ${name} is written only by the harness`);
	}
	return target;
}

// Files whose presence marks a case as legacy (findings contract 1.0); the
// launcher's activation (case-contract.py activate-new) refuses such a case.
export const LEGACY_MARKERS = ['data/findings.json', 'findings.json', 'data/fact-check.json', 'data/investigation-log.json'];

// gold osmf s1: the investigator wrote 1.0 findings during the methodology
// phase, so the case skipped activation and passage grounding. A new case (no
// contract, no legacy file) gets its first investigation file only after the
// harness activates it at Investigation.
function refuseUnactivatedStart(caseDir: string, target: string): void {
	const path = caseRelative(caseDir, target);
	const exists = (name: string) => lstatOrUndefined(join(caseDir, name)) !== undefined;
	if (!LEGACY_MARKERS.includes(path) || exists('data/case-contract.json') || LEGACY_MARKERS.some(exists)) return;
	throw new FileRefused(`refused: ${path} starts the investigation record, which opens at Investigation once the harness has activated the case (data/case-contract.json); the file is unchanged. Before Investigation, write only the files your task names.`);
}

// The resolver reads the approved brief only at the case root. r6 northwind on
// dd14e2e5: the orchestrator wrote data/brief-directions.txt, the case stayed
// at Brief, and every later gate step was refused.
function refuseMisplacedBrief(caseDir: string, target: string): void {
	if (caseRelative(caseDir, target) !== 'data/brief-directions.txt') return;
	throw new FileRefused('refused: the approved brief goes to brief-directions.txt at the case root (path: brief-directions.txt); data/brief-directions.txt is not read. The file was not written.');
}

// With the Indicator Framework on, its case file and report come only from
// record_indicator_framework, which fills ratings, quadrants and items from
// the fetched framework data.
const RECORDED_FRAMEWORK = ['data/indicator-framework.json', 'data/indicator-framework.md'];
function refuseRecordedFramework(caseDir: string, target: string): void {
	const path = caseRelative(caseDir, target);
	if (!RECORDED_FRAMEWORK.includes(path.toLowerCase()) || !approvedIndicatorFramework(caseDir)) return;
	throw new FileRefused(`refused: ${path} is written only through record_indicator_framework, which fills the ratings, quadrants and items from the framework data; the file is unchanged. Call it with every indicator, branch, conclusion and item.`);
}

type Passage = { id?: unknown; expression_fingerprint?: unknown; lifecycle_events?: { event?: unknown }[]; finding_links?: { finding_id?: unknown; relation?: unknown; finding_fingerprint?: unknown; link_fingerprint?: unknown }[] };

// A verified claim whose evidence_for cites no passage blocks the case at the
// validator, and the one correction it gets is easily spent re-dropping the refs
// (gold mozilla s0 a2). Refused at write time instead, naming the refs on record
// for active passages that support the finding; nothing is added for the writer.
// In an activated 1.1 case a verified claim needs a passage even when none is
// on record yet (12B r2 cc: the fact-checker verified F1 and F2 before any
// passage existed). A cited ref counts only when it names an active passage
// supporting the finding, with the fingerprints on record (12B r3 ia: refs to
// a passage never recorded went through). An activated case's fact-check is
// 1.1 (12B r3 ia: "schema_validation": "1.1" passed as a legacy file).
// The claims whose verdict needs evidence: verified, and in a legacy file also
// partially_verified.
function positiveClaims(factCheck: { schema_version?: unknown; claims?: unknown }): { finding_id?: unknown; evidence_for?: unknown }[] {
	const positive = factCheck?.schema_version === '1.1' ? ['verified'] : ['verified', 'partially_verified'];
	return Array.isArray(factCheck?.claims) ? factCheck.claims.filter((claim) => positive.includes(claim?.verdict)) : [];
}

function refuseUncitedVerdicts(caseDir: string, target: string, content: string): void {
	if (caseRelative(caseDir, target) !== 'data/fact-check.json') return;
	let factCheck: { schema_version?: unknown; claims?: unknown };
	try {
		factCheck = JSON.parse(content);
	} catch {
		return;
	}
	const activated = lstatOrUndefined(join(caseDir, 'data', 'case-contract.json')) !== undefined;
	if (activated && factCheck?.schema_version !== '1.1') {
		throw new FileRefused('refused: data/fact-check.json in an activated case needs "schema_version": "1.1"; the file is unchanged.');
	}
	const claims = positiveClaims(factCheck);
	let passages: Passage[] = [];
	try {
		passages = (JSON.parse(readFileSync(join(caseDir, 'data', 'source-expressions.json'), 'utf8')) as { expressions?: Passage[] }).expressions ?? [];
	} catch {
		if (!activated) return;
	}
	const missing: string[] = [];
	const unsupported: string[] = [];
	for (const claim of claims as { finding_id?: unknown; evidence_for?: unknown }[]) {
		const items = Array.isArray(claim.evidence_for) ? claim.evidence_for : [];
		const refs = passages.flatMap((passage) => {
			if (passage?.lifecycle_events?.at(-1)?.event !== 'activated') return [];
			const link = passage.finding_links?.find((l) => l?.finding_id === claim.finding_id && l?.relation === 'supports');
			return link ? [{ expression_id: passage.id, expression_fingerprint: passage.expression_fingerprint, finding_fingerprint: link.finding_fingerprint, link_fingerprint: link.link_fingerprint }] : [];
		});
		// A bare id or an id-only object is resolved to the full ref after the
		// fact-checker returns (--resolve-fact-check-refs); any fingerprint given must match.
		const onRecord = (cited: unknown) => refs.some((ref) => {
			if (typeof cited === 'string') return cited === ref.expression_id;
			if (typeof cited !== 'object' || cited === null) return false;
			const given = cited as Record<string, unknown>;
			return given.expression_id === ref.expression_id && Object.entries(ref).every(([key, value]) => !(key in given) || given[key] === value);
		});
		if (items.some((item) => Array.isArray(item?.source_expression_refs) && item.source_expression_refs.some(onRecord))) continue;
		const misspelt = items.some((item) => item?.source_expression_ref !== undefined) ? ' (the key is source_expression_refs, a list, not source_expression_ref)' : '';
		const cited = items.flatMap((item) => (Array.isArray(item?.source_expression_refs) ? item.source_expression_refs : []));
		const offRecord = cited.length > 0 ? ` (${JSON.stringify(cited)} is not an active passage supporting it)` : '';
		// The ids alone: they are resolved to full refs after the fact-checker
		// returns, and the fingerprints buried the fix (LFM3 e2 stage 1 on
		// bc87c716: four refusals listing P1/P4 with fingerprints before the
		// model cited them).
		if (refs.length > 0) missing.push(`${String(claim.finding_id)}${misspelt}${offRecord}: "source_expression_refs": ${JSON.stringify(refs.map((ref) => ref.expression_id))}`);
		else if (activated) unsupported.push(`\nno passage on record supports ${String(claim.finding_id)}${offRecord}; record the passage you checked with source_expressions, then cite its ref here, or change the verdict. If no source you acquired contains a passage that supports the finding, do not record one: set the verdict to unverified (or use missing_source_gate for a source that was requested and not returned).`);
	}
	if (missing.length === 0 && unsupported.length === 0) return;
	const cite = missing.length > 0 ? ` For each finding below, add this key to one of its evidence_for items, keeping only the passages you checked against their source (the ids are enough; the fingerprints are filled in for you). If none of them supports the verdict, change the verdict instead.\n${missing.join('\n')}` : '';
	throw new FileRefused(`refused: data/fact-check.json gives a verified verdict whose evidence_for has no source_expression_refs; the file is unchanged.${cite}${unsupported.join('')}`);
}

// The first line the validators recognise as a lead file (scripts/evidence_anchors.py
// RLM_LEAD_MARKER), written by fetch: such a file can never anchor evidence; its .raw can.
export const LEAD_MARKER = '# RLM-distilled leads from';

// As scripts/evidence_anchors.py rlm_lead_failure tells a lead file: the marker
// on one of its first eight lines. Read through the case's confined reader.
function isLeadFile(caseDir: string, path: string): boolean {
	try {
		const lines = readFileSync(readTarget(caseDir, path), 'utf8').split('\n', 8);
		return lines.some((line) => line.replace(/^\uFEFF/, '').trim().startsWith(LEAD_MARKER));
	} catch {
		return false;
	}
}

// An evidence item records the page as fetched, never fetch's distilled leads
// (12B r1 okfn and fsf: items named the leads file, so every exact quote from
// the page's .raw was refused as not from an acquired source).
function refuseLeadsEvidence(caseDir: string, target: string, content: string): void {
	if (caseRelative(caseDir, target) !== 'data/evidence-bundle.json') return;
	let items: unknown;
	try {
		items = (JSON.parse(content) as { items?: unknown }).items;
	} catch {
		return;
	}
	const leads = (Array.isArray(items) ? items : []).flatMap((item) =>
		['raw_path', 'downloaded_document_path', 'path'].flatMap((key) => {
			const path = item?.[key];
			if (typeof path !== 'string') return [];
			return isLeadFile(caseDir, path) ? [`${typeof item.id === 'string' ? item.id : 'an item with no id'} (${key}: ${path})`] : [];
		}),
	);
	if (leads.length === 0) return;
	throw new FileRefused(`refused: data/evidence-bundle.json records fetch's distilled leads as evidence for ${leads.join(', ')}; the file is unchanged. Record the page as fetched instead: raw_path "<that file>.raw" with the raw_sha256 the fetch returned for it.`);
}

// A bundle write keeps what others rely on (12B r3 ia: the fact-checker
// rewrote investigator item E1 to another file, and every passage already
// anchored to it failed validation from then on). A fact-checker changes only
// its own items, as phase-investigation tells it; no role moves the files of an
// item that active passages cite.
const ITEM_FILES = ['raw_path', 'path', 'downloaded_document_path', 'text_derivatives'];

function refuseBundleRewrites(caseDir: string, target: string, content: string, role: string | undefined): void {
	if (caseRelative(caseDir, target) !== 'data/evidence-bundle.json') return;
	type Item = Record<string, unknown>;
	const itemsOf = (text: string): Map<unknown, Item> | undefined => {
		try {
			const items = (JSON.parse(text) as { items?: unknown }).items;
			return Array.isArray(items) ? new Map(items.filter((item) => item && typeof item === 'object').map((item: Item) => [item.id, item])) : undefined;
		} catch {
			return undefined;
		}
	};
	let before: Map<unknown, Item> | undefined;
	try {
		before = itemsOf(readFileSync(target, 'utf8'));
	} catch {
		return;
	}
	const after = itemsOf(content);
	if (!before || !after) return;
	if (role === 'fact-checker') {
		const changed = [...before].filter(([id, item]) => item.created_by !== 'fact-checker' && !isDeepStrictEqual(item, after.get(id))).map(([id]) => String(id));
		if (changed.length > 0) {
			throw new FileRefused(`refused: data/evidence-bundle.json changes or removes ${changed.join(', ')}, which you did not create; the file is unchanged. In data/evidence-bundle.json correct only items with created_by fact-checker; never edit an investigator item. Append your own item with "created_by": "fact-checker" instead.`);
		}
	}
	let passages: { id?: unknown; original_evidence_bundle_id?: unknown; lifecycle_events?: { event?: unknown }[] }[] = [];
	try {
		passages = JSON.parse(readFileSync(join(caseDir, 'data', 'source-expressions.json'), 'utf8')).expressions ?? [];
	} catch {
		return;
	}
	const moved = [...before].flatMap(([id, item]) => {
		const cited = passages.filter((p) => p?.original_evidence_bundle_id === id && p?.lifecycle_events?.at(-1)?.event === 'activated').map((p) => String(p.id));
		const next = after.get(id);
		if (cited.length === 0 || (next && ITEM_FILES.every((key) => isDeepStrictEqual(item[key], next[key])))) return [];
		return [`${String(id)} (cited by ${cited.join(', ')})`];
	});
	if (moved.length === 0) return;
	throw new FileRefused(`refused: data/evidence-bundle.json changes or removes the files of ${moved.join('; ')}; the file is unchanged. Recorded passages anchor to those files, so keep them as they are, and record any other file as a new item.`);
}

// A fact-check claim checks the finding it names, word for word (gold osmf
// s1: the fact-checker rewrote a finding into an invented address and marked
// it false). Compared as validate-case.py does: Unicode NFKC, whitespace runs
// collapsed.
function refuseDriftedClaims(caseDir: string, target: string, content: string): void {
	if (caseRelative(caseDir, target) !== 'data/fact-check.json') return;
	const normalized = (text: string) => text.normalize('NFKC').replace(/\s+/g, ' ').trim();
	let claims: unknown;
	let findings: Map<unknown, unknown>;
	try {
		claims = (JSON.parse(content) as { claims?: unknown }).claims;
		const recorded = (JSON.parse(readFileSync(join(caseDir, 'data', 'findings.json'), 'utf8')) as { findings?: { id?: unknown; claim?: unknown }[] }).findings;
		findings = new Map((Array.isArray(recorded) ? recorded : []).map((finding) => [finding?.id, finding?.claim]));
	} catch {
		return;
	}
	const drifted = (Array.isArray(claims) ? claims : []).flatMap((claim) => {
		const expected = findings.get(claim?.finding_id);
		const text = claim?.claim_text;
		return typeof expected === 'string' && typeof text === 'string' && normalized(text) !== normalized(expected)
			? [`${String(claim.finding_id)}: ${JSON.stringify(expected)}`]
			: [];
	});
	if (drifted.length === 0) return;
	throw new FileRefused(`refused: data/fact-check.json checks a claim that is not its finding's; the file is unchanged. Set each claim_text to its finding's claim, word for word, and judge that claim:\n${drifted.join('\n')}`);
}

// A positive verdict cites only files saved in the case (12B r5 fsf s0: F2 was
// verified citing research/fsf_leadership_browse.md, never saved). Checked are
// local_file, source_ref.path and an archive_url that is a path, not a URL.
// LFM3 e2 stage 1 on Osaurus (bc87c716): a fact-check.json with flags coded
// "context" was accepted, and the case then stayed at investigation on "needs a
// known Batch 1.1 code" without the fact-checker ever being told which codes
// exist. The codes are read from the schema the validators use.
let flagCodes: string[] | undefined;
function knownFlagCodes(): string[] {
	flagCodes ??= (JSON.parse(readFileSync(join(SCHEMAS_DIR, 'fact-check.schema.json'), 'utf8')) as { definitions: { flag_code: { enum: string[] } } }).definitions.flag_code.enum;
	return flagCodes;
}

function refuseUnknownFlags(caseDir: string, target: string, content: string): void {
	if (caseRelative(caseDir, target) !== 'data/fact-check.json') return;
	let factCheck: { schema_version?: unknown; claims?: unknown };
	try {
		factCheck = JSON.parse(content);
	} catch {
		return;
	}
	if (factCheck?.schema_version !== '1.1' || !Array.isArray(factCheck.claims)) return;
	const codes = knownFlagCodes();
	const bad: string[] = [];
	factCheck.claims.forEach((claim: { finding_id?: unknown; flags?: unknown }, index: number) => {
		if (!Array.isArray(claim?.flags)) return;
		claim.flags.forEach((flag: { code?: unknown; reason?: unknown }, k: number) => {
			const code = flag?.code;
			const reason = typeof flag?.reason === 'string' && flag.reason.trim() !== '';
			if (typeof code === 'string' && codes.includes(code) && reason) return;
			const what = typeof code === 'string' && codes.includes(code) ? 'has no reason' : `code ${JSON.stringify(code ?? null)} is not a Batch 1.1 code`;
			bad.push(`claims[${index}] (${String(claim?.finding_id ?? '?')}).flags[${k}]: ${what}`);
		});
	});
	if (bad.length === 0) return;
	throw new FileRefused(`refused: data/fact-check.json has flags the validators reject; the file is unchanged.\n${bad.join('\n')}\nA flag needs one of these codes and a non-empty reason: ${codes.join(', ')}. Flags are optional: if none of these fits, drop the flag.`);
}

function refuseMissingCitedFiles(caseDir: string, target: string, content: string): void {
	if (caseRelative(caseDir, target) !== 'data/fact-check.json') return;
	let factCheck: { schema_version?: unknown; claims?: unknown };
	try {
		factCheck = JSON.parse(content);
	} catch {
		return;
	}
	const saved = (path: string) => {
		const resolved = resolve(caseDir, path);
		return inside(caseDir, resolved) && lstatOrUndefined(resolved)?.isFile() === true;
	};
	const missing = new Set<string>();
	for (const claim of positiveClaims(factCheck)) {
		for (const item of Array.isArray(claim.evidence_for) ? claim.evidence_for : []) {
			const archive = typeof item?.archive_url === 'string' && !/^[a-z][a-z0-9+.-]*:/i.test(item.archive_url) ? item.archive_url : undefined;
			for (const path of [item?.local_file, item?.source_ref?.path, archive]) {
				if (typeof path === 'string' && path.trim() !== '' && !path.includes('\0') && !saved(path.trim())) missing.add(`${String(claim.finding_id)}: ${path}`);
			}
		}
	}
	if (missing.size === 0) return;
	throw new FileRefused(`refused: data/fact-check.json cites files that are not in the case folder; the file is unchanged.\n${[...missing].join('\n')}\nCite only a file you saved in the case (list_files shows them). If no saved source supports the finding, set the verdict to unverified.`);
}

// U4 (f9fed1d2, r5 kde stage 1): after its verdicts were accepted, the
// fact-checker fetched again with save_as onto the research files they cite,
// and the cited lines no longer stated the claims. A research/ or evidence/
// file the evidence bundle, the source expressions or the fact-check cite is
// never rewritten: the new copy goes under a new name.
function citedPaths(caseDir: string): Set<string> {
	const cited = new Set<string>();
	const read = (relative: string): unknown => {
		try {
			return JSON.parse(readFileSync(join(caseDir, relative), 'utf8'));
		} catch {
			return undefined;
		}
	};
	const add = (value: unknown) => {
		if (typeof value === 'string' && value) cited.add(value.replace(/^\.\//, ''));
	};
	for (const item of ((read('data/evidence-bundle.json') as { items?: unknown[] } | undefined)?.items ?? []) as Array<Record<string, unknown>>) {
		add(item?.raw_path);
		add(item?.downloaded_document_path);
		add(item?.path);
		for (const derivative of (Array.isArray(item?.text_derivatives) ? item.text_derivatives : []) as Array<Record<string, unknown>>) add(derivative?.path);
	}
	for (const expression of ((read('data/source-expressions.json') as { expressions?: unknown[] } | undefined)?.expressions ?? []) as Array<{ anchor_ref?: { path?: unknown } }>) add(expression?.anchor_ref?.path);
	for (const claim of ((read('data/fact-check.json') as { claims?: unknown[] } | undefined)?.claims ?? []) as Array<Record<string, unknown>>) {
		for (const key of ['evidence_for', 'evidence_against']) {
			for (const evidence of (Array.isArray(claim?.[key]) ? claim[key] : []) as Array<{ local_file?: unknown; source_ref?: { path?: unknown } }>) {
				add(evidence?.local_file);
				add(evidence?.source_ref?.path);
			}
		}
	}
	return cited;
}

function refuseCitedOverwrite(caseDir: string, target: string): void {
	const path = caseRelative(caseDir, target);
	if (!/^(research|evidence)\//.test(path) || lstatOrUndefined(target) === undefined || !citedPaths(caseDir).has(path)) return;
	const dot = path.indexOf('.', path.lastIndexOf('/'));
	const [stem, ext] = dot === -1 ? [path, ''] : [path.slice(0, dot), path.slice(dot)];
	let n = 2;
	while (lstatOrUndefined(join(caseDir, `${stem}-${n}${ext}`)) !== undefined) n += 1;
	throw new FileRefused(`refused: ${path} is cited evidence and must not change; the file is unchanged. Save the new copy under a new name, such as ${stem}-${n}${ext}.`);
}

// Writes without following a symlink at the last step either.
export function writeConfined(caseDir: string, root: string, path: string, content: string | Buffer, writes?: string[], denyWrites?: string[]): string {
	const target = writeTarget(caseDir, root, path, writes, denyWrites);
	refuseCitedOverwrite(caseDir, target);
	const bytes = typeof content === 'string' ? Buffer.from(content, 'utf8') : content;
	if (bytes.length > MAX_WRITE_BYTES) throw new FileRefused(`refused: content is larger than ${MAX_WRITE_BYTES} bytes`);
	const fd = openSync(target, constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC | constants.O_NOFOLLOW, 0o600);
	try {
		writeSync(fd, bytes);
	} finally {
		closeSync(fd);
	}
	return caseRelative(caseDir, target);
}

// Inside the case and, for a narrowed role, one of its named files and none it
// is denied; or under the checkout's schemas/.
function readable(caseDir: string, path: string, scope: CaseScope | undefined): boolean {
	if (inside(SCHEMAS_DIR, path) || inside(AGENT_REFERENCES_DIR, path)) return true;
	return inside(caseDir, path) && mayRead(scope, caseRelative(caseDir, path));
}

// A read target is wherever the path really leads, which must be inside the case
// (within the role's scope) or the checkout's schemas/.
export function readTarget(caseDir: string, path: string, scope?: CaseScope): string {
	// A relative schemas/ path names the checkout's schemas, as the tool text says.
	const target = /^(?:schemas|agents[\\/]references)[\\/]/.test(path) ? requested(CHECKOUT_ROOT, path) : requested(caseDir, path);
	// Refuse before looking: "not found" for an outside path would tell the model
	// which files exist beyond the case or the scope.
	if (!readable(caseDir, target, scope)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read`);
	let real: string;
	try {
		real = realpathSync(target);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
		// U3 census stage 1 (0af4585b): the orchestrator asked for
		// schemas/case-contract.json about 100 times; the schema has .schema.json.
		const schema = /^schemas[\\/].+\.json$/.test(path) && !path.endsWith('.schema.json') ? path.replace(/\.json$/, '.schema.json') : '';
		throw new Error(schema && existsSync(requested(CHECKOUT_ROOT, schema)) ? `not found: ${path}; the schema is ${schema}` : `not found: ${path}`);
	}
	if (!readable(caseDir, real, scope)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read`);
	return real;
}

// Lines as the evidence validators count them (scripts/evidence_anchors.py
// source_lines) and as grep -n numbers them: only a line feed ends a line, and
// a carriage return just before it is dropped. Form feeds (pdftotext page
// breaks) and other separators stay inside the line.
function sourceLines(text: string): string[] {
	if (text === '') return [];
	const lines = text.split('\n');
	if (lines[lines.length - 1] === '') lines.pop();
	return lines.map((line) => (line.endsWith('\r') ? line.slice(0, -1) : line));
}

// json.dumps(value, ensure_ascii=False, sort_keys=True) with Python's default
// separators: what the validators select for a non-string JSON Pointer value.
function pythonDumps(value: unknown): string {
	if (Array.isArray(value)) return `[${value.map(pythonDumps).join(', ')}]`;
	if (value !== null && typeof value === 'object') {
		const keys = Object.keys(value).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
		return `{${keys.map((key) => `${JSON.stringify(key)}: ${pythonDumps((value as Record<string, unknown>)[key])}`).join(', ')}}`;
	}
	return JSON.stringify(value);
}

// RFC 6901, resolved as scripts/evidence_anchors.py json_pointer does.
function selectJsonPointer(text: string, pointer: string, path: string): string {
	let current: unknown;
	try {
		current = JSON.parse(text);
	} catch (error) {
		throw new Error(`read_file: ${path} is not valid JSON: ${(error as Error).message}`);
	}
	if (pointer !== '') {
		if (!pointer.startsWith('/')) throw new Error(`read_file: JSON pointer ${JSON.stringify(pointer)} must start with '/'`);
		for (const encoded of pointer.slice(1).split('/')) {
			const token = encoded.replaceAll('~1', '/').replaceAll('~0', '~');
			if (current !== null && typeof current === 'object' && !Array.isArray(current) && Object.hasOwn(current, token)) {
				current = (current as Record<string, unknown>)[token];
			} else if (Array.isArray(current) && /^[0-9]+$/.test(token) && Number(token) < current.length) {
				current = current[Number(token)];
			} else {
				throw new Error(`read_file: JSON pointer ${JSON.stringify(pointer)} does not resolve at ${JSON.stringify(token)} in ${path}`);
			}
		}
	}
	return typeof current === 'string' ? current : pythonDumps(current);
}

const readParams = Type.Object(
	{
		path: Type.String({ minLength: 1, maxLength: 1024 }),
		offset: Type.Optional(Type.Integer({ minimum: 1 })),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 5000 })),
		json_pointer: Type.Optional(Type.String({ maxLength: 1024 })),
	},
	{ additionalProperties: false },
);
const hashParams = Type.Object(
	{ paths: Type.Array(Type.String({ minLength: 1, maxLength: 1024 }), { minItems: 1, maxItems: MAX_HASH_FILES }) },
	{ additionalProperties: false },
);
const writeParams = Type.Object(
	{ path: Type.String({ minLength: 1, maxLength: 1024 }), content: Type.String({ maxLength: MAX_WRITE_BYTES }) },
	{ additionalProperties: false },
);
const editParams = Type.Object(
	{
		path: Type.String({ minLength: 1, maxLength: 1024 }),
		old_text: Type.String({ minLength: 1 }),
		new_text: Type.String(),
	},
	{ additionalProperties: false },
);
const listParams = Type.Object({ pattern: Type.String({ minLength: 1, maxLength: 512 }) }, { additionalProperties: false });
const grepParams = Type.Object(
	{
		pattern: Type.String({ minLength: 1, maxLength: 512 }),
		path: Type.Optional(Type.String({ minLength: 1, maxLength: 1024 })),
		ignore_case: Type.Optional(Type.Boolean()),
	},
	{ additionalProperties: false },
);

// A folder inside the case, or one of the scope's files.
function scopedFolderOrFile(caseDir: string, path: string, scope: CaseScope): string {
	const target = requested(caseDir, path);
	if (!inside(caseDir, target)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read`);
	const stats = lstatOrUndefined(target);
	if (stats?.isDirectory()) return realpathSync(target);
	return readTarget(caseDir, path, scope);
}

function* caseFiles(caseDir: string, start: string): Generator<string> {
	const stats = lstatSync(start);
	if (stats.isFile()) {
		yield start;
		return;
	}
	if (!stats.isDirectory()) return;
	for (const entry of readdirSync(start, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
		const path = join(start, entry.name);
		// Symlinks are skipped: grep never reads through one.
		if (entry.isDirectory()) yield* caseFiles(caseDir, path);
		else if (entry.isFile()) yield path;
	}
}

// The passages file only as the launcher wrote it: a regular file (not a
// link) at that exact absolute path.
function keptPassages(path: string): boolean {
	return isAbsolute(path) && lstatOrUndefined(path)?.isFile() === true;
}

// The ids a write withdraws, by the key its file merges on.
function withdrawals(name: string, written: string): string[] {
	const field = name === 'data/fact-check.json' ? 'finding_id' : 'id';
	try {
		return Object.values(JSON.parse(written) as Record<string, unknown>)
			.filter(Array.isArray)
			.flat()
			.filter((entry) => entry && typeof entry === 'object' && 'withdrawn' in entry)
			.map((entry) => String((entry as Record<string, unknown>)[field]));
	} catch {
		return [];
	}
}

// A recorded passage stays on record, and the validators resolve every
// passage's finding link and evidence item, so a finding or an item a passage
// records is never withdrawn.
function refuseCitedWithdrawal(caseDir: string, target: string, written: string): void {
	const name = caseRelative(caseDir, target);
	if (name !== 'data/findings.json' && name !== 'data/evidence-bundle.json') return;
	const ids = withdrawals(name, written);
	if (ids.length === 0) return;
	let passages: Record<string, unknown>[] = [];
	try {
		passages = (JSON.parse(readFileSync(join(caseDir, 'data', 'source-expressions.json'), 'utf8')) as { expressions?: Record<string, unknown>[] }).expressions ?? [];
	} catch {
		return;
	}
	const recorded = ids.flatMap((id) => {
		const by = passages
			.filter((p) => (name === 'data/findings.json' ? Array.isArray(p.finding_links) && (p.finding_links as Record<string, unknown>[]).some((link) => link?.finding_id === id) : p.original_evidence_bundle_id === id))
			.map((p) => String(p.id));
		return by.length > 0 ? [`${id} (recorded by ${by.join(', ')})`] : [];
	});
	if (recorded.length === 0) return;
	throw new FileRefused(`refused: ${recorded.join('; ')} cannot be withdrawn, because a recorded passage stays on record; the file is unchanged. Keep it, and lower its confidence with the reason in confidence_rationale, or leave its verdict to the fact-checker.`);
}

// What still refers to an entry this write withdrew, read from the case after
// the write, so the writer can clear each reference with its owner's tool: the
// validators stay strict and a withdrawn finding never dangles unseen.
function withdrawnRefs(caseDir: string, name: string, written: string): string {
	const json = (path: string): Record<string, unknown> => {
		try {
			return JSON.parse(readFileSync(join(caseDir, path), 'utf8')) as Record<string, unknown>;
		} catch {
			return {};
		}
	};
	const list = (doc: Record<string, unknown>, key: string) => (Array.isArray(doc[key]) ? (doc[key] as Record<string, unknown>[]) : []);
	const ids = withdrawals(name, written);
	if (ids.length === 0 || (name !== 'data/findings.json' && name !== 'data/evidence-bundle.json')) return '';
	const lines = ids.flatMap((id) => {
		const refs: string[] = [];
		if (name === 'data/findings.json') {
			if (list(json('data/fact-check.json'), 'claims').some((claim) => claim.finding_id === id)) refs.push(`the fact-check's claim for ${id} (the fact-checker withdraws it: {"finding_id": "${id}", "withdrawn": "<why>"})`);
			for (const item of list(json('data/evidence-bundle.json'), 'items')) if (list(item, 'claim_links').some((link) => link.finding_id === id)) refs.push(`evidence item ${String(item.id)}'s claim_links (write the item again without that link)`);
		} else {
			for (const finding of list(json('data/findings.json'), 'findings')) if (Array.isArray(finding.evidence_bundle_refs) && finding.evidence_bundle_refs.includes(id)) refs.push(`finding ${String(finding.id)}'s evidence_bundle_refs (write the finding again without ${id})`);
		}
		return refs.length > 0 ? [` ${id} is withdrawn; these still refer to it and must be cleared: ${refs.join('; ')}.`] : [];
	});
	return lines.join('');
}

export function caseFileTools(context: Pick<ToolContext, 'caseDir' | 'writeRoot' | 'scope'> & Partial<Pick<ToolContext, 'env'>>): ToolDefinition[] {
	const { caseDir, writeRoot } = context;
	const role = context.env?.SPOTLIGHT_ROLE;
	const scope = context.scope;
	const reads = scope?.reads;
	const writes = scope?.writes;
	const denyWrites = scope?.denyWrites;
	// One research direction's investigator (directions.ts) reads only its own
	// entries in the files it writes, and its writes merge into the whole file.
	const direction = context.env?.SPOTLIGHT_DIRECTION;
	// The one file outside the case a worker may read: the passages it started
	// with, which the launcher keeps in the harness data folder (launcher.ts).
	const passages = context.env?.SPOTLIGHT_TASK_PASSAGES;
	const split = (target: string) => (direction && isDirectionFile(caseRelative(caseDir, target)) ? caseRelative(caseDir, target) : undefined);
	const shown = (target: string, text: string) => {
		const name = split(target);
		if (!name) return text;
		try {
			return directionView(name, text, direction!);
		} catch {
			return text;
		}
	};
	// The investigator's and the fact-checker's writes to the files they build
	// merge by id, so each write may carry a few entries (directions.ts mergeByKey).
	const whole = (target: string, content: string) => {
		const name = split(target);
		if (name) return mergeDirection(name, currentText(target), content, direction!);
		const relative = caseRelative(caseDir, target);
		return mergesByKey(role, relative) ? mergeByKey(role!, relative, currentText(target), content) : content;
	};
	// What a merged write did, so a writer that left entries out learns they stay.
	const LISTS: Record<string, string> = { 'data/findings.json': 'findings', 'data/evidence-bundle.json': 'items', 'data/fact-check.json': 'claims', 'data/investigation-log.json': 'cycles' };
	const keptNote = (target: string, written: string, merged: string) => {
		const name = caseRelative(caseDir, target);
		if (written === merged || !(split(target) || mergesByKey(role, name))) return '';
		const size = (text: string) => {
			try {
				const list = (JSON.parse(text) as Record<string, unknown>)[LISTS[name]];
				return Array.isArray(list) ? list.length : 0;
			} catch {
				return 0;
			}
		};
		return ` (${size(written)} written, ${size(merged)} in the file; entries you left out stay. ${WITHDRAW_HINT})${withdrawnRefs(caseDir, name, written)}`;
	};
	// After the fact-checker's accepted write to its file: the next findings to
	// check, in full (roles.ts nextCheckText).
	const nextToCheck = (wrote: string) => (role === 'fact-checker' && wrote === 'data/fact-check.json' ? `\n${nextCheckText(readCheckDocs(caseDir))}` : '');
	return [
		{
			name: 'read_file',
			label: 'Read file',
			description:
				'Read a text file in the active case (path relative to the case folder, or absolute inside it), or a Spotlight schema under the checkout schemas/ folder. Optional 1-based line offset and line limit; lines are counted as the evidence validators count them, so they serve as source_ref line_start/line_end. Optional json_pointer (RFC 6901, for a JSON file) returns exactly the text the validators select for a source_ref json_pointer: a string value as is, anything else as compact sorted JSON.',
			parameters: readParams,
			async execute(_id, params) {
				const input = checkedParams<{ path: string; offset?: number; limit?: number; json_pointer?: string }>('read_file', readParams, params);
				if (input.json_pointer !== undefined && (input.offset || input.limit)) {
					throw new ToolArgumentError('read_file: use either json_pointer or offset/limit');
				}
				const source = input.path === passages && keptPassages(passages) ? passages : readTarget(caseDir, input.path, scope);
				const text = shown(source, readFileSync(source, 'utf8'));
				let selected = text;
				if (input.json_pointer !== undefined) {
					selected = selectJsonPointer(text, input.json_pointer, input.path);
				} else if (input.offset || input.limit) {
					const from = (input.offset ?? 1) - 1;
					selected = sourceLines(text).slice(from, from + (input.limit ?? Number.MAX_SAFE_INTEGER)).join('\n');
				}
				return textResult(
					selected.length > MAX_READ_CHARS
						? `${selected.slice(0, MAX_READ_CHARS)}\n${READ_TRUNCATED}`
						: selected,
				);
			},
		},
		{
			name: 'write_file',
			label: 'Write file',
			description:
				'Create or overwrite a file in the active case (path relative to the case folder). Parent folders are created. Refused outside your folder, through symlinks, for a folder path, for resolver, review and receipt files, and for empty content over an existing non-empty file.',
			parameters: writeParams,
			executionMode: 'sequential',
			async execute(_id, params) {
				const input = checkedParams<{ path: string; content: string }>('write_file', writeParams, params);
				// An output-capped call arrives with empty content; overwriting would erase
				// the case file it was meant to rewrite.
				if (input.content.trim() === '') {
					const existing = lstatOrUndefined(writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
					if (existing && existing.size > 0) {
						throw new FileRefused(`refused: empty content would erase ${input.path}. If your output was cut off, write the file again in full.`);
					}
				}
				const content = whole(writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), input.content);
				refuseCitedWithdrawal(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), input.content);
				refuseMisplacedBrief(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseUnactivatedStart(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseRecordedFramework(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseNonObjectData(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUncitedVerdicts(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseLeadsEvidence(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseBundleRewrites(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content, role);
				refuseDriftedClaims(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseMissingCitedFiles(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUnknownFlags(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				await refuseInvalidProposal(context, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				const wrote = writeConfined(caseDir, writeRoot, input.path, content, writes, denyWrites);
				return textResult(`wrote ${wrote}${keptNote(writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), input.content, content)}${nextToCheck(wrote)}`);
			},
		},
		{
			name: 'edit_file',
			label: 'Edit file',
			description: 'Replace one exact, unique occurrence of old_text with new_text in a case file. Same limits as write_file.',
			parameters: editParams,
			executionMode: 'sequential',
			async execute(_id, params) {
				const input = checkedParams<{ path: string; old_text: string; new_text: string }>('edit_file', editParams, params);
				const target = writeTarget(caseDir, writeRoot, input.path, writes, denyWrites);
				// Editing reads the file, so a file hidden from this role is refused.
				if (denied(scope?.denyReads, caseRelative(caseDir, target))) {
					throw new FileRefused(`refused: ${input.path} is outside the case files this agent may read`);
				}
				let text: string;
				try {
					text = shown(target, readFileSync(target, 'utf8'));
				} catch (error) {
					if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new Error(`not found: ${input.path}`);
					throw error;
				}
				const first = text.indexOf(input.old_text);
				if (first === -1) throw new Error(`edit_file: old_text not found in ${input.path}${closestSpans(text, input.old_text, caseRelative(caseDir, target))}`);
				if (text.indexOf(input.old_text, first + 1) !== -1) {
					throw new Error(`edit_file: old_text occurs more than once in ${input.path}; include more context`);
				}
				const updated = whole(target, text.slice(0, first) + input.new_text + text.slice(first + input.old_text.length));
				refuseRecordedFramework(caseDir, target);
				refuseNonObjectData(caseDir, target, updated);
				refuseUncitedVerdicts(caseDir, target, updated);
				refuseLeadsEvidence(caseDir, target, updated);
				refuseBundleRewrites(caseDir, target, updated, role);
				refuseDriftedClaims(caseDir, target, updated);
				refuseMissingCitedFiles(caseDir, target, updated);
				refuseUnknownFlags(caseDir, target, updated);
				await refuseInvalidProposal(context, target, updated);
				return textResult(`edited ${writeConfined(caseDir, writeRoot, input.path, updated, writes, denyWrites)}`);
			},
		},
		{
			name: 'list_files',
			label: 'List files',
			description: 'List case files matching a glob pattern relative to the case folder, for example "data/*.json" or "research/**/*.md".',
			parameters: listParams,
			async execute(_id, params) {
				const { pattern } = checkedParams<{ pattern: string }>('list_files', listParams, params);
				if (isAbsolute(pattern) || pattern.split(/[\\/]/).includes('..')) {
					throw new FileRefused('refused: list_files patterns are relative to the case and cannot contain ".."');
				}
				const matches = globSync(pattern, { cwd: caseDir })
					.map((path) => path.split(sep).join('/'))
					.filter((path) => inside(caseDir, resolve(caseDir, path)) && mayRead(scope, path))
					.sort();
				const shown = matches.slice(0, MAX_LIST);
				const more = matches.length > MAX_LIST ? `\n[${matches.length - MAX_LIST} more not shown]` : '';
				return textResult(shown.length ? shown.join('\n') + more : 'no matching files');
			},
		},
		{
			name: 'grep_files',
			label: 'Grep files',
			description:
				'Search case files for a regular expression. Optional path (file or folder, relative to the case) narrows the search. Returns path:line:text, with lines counted as read_file and the evidence validators count them.',
			parameters: grepParams,
			async execute(_id, params) {
				const input = checkedParams<{ pattern: string; path?: string; ignore_case?: boolean }>('grep_files', grepParams, params);
				let expression: RegExp;
				try {
					expression = new RegExp(input.pattern, input.ignore_case ? 'i' : '');
				} catch (error) {
					throw new Error(`grep_files: invalid regular expression: ${(error as Error).message}`);
				}
				// A narrowed role may name a folder; only its files in scope are searched.
				const start = input.path ? (reads ? scopedFolderOrFile(caseDir, input.path, scope) : readTarget(caseDir, input.path, scope)) : caseDir;
				if (!inside(caseDir, start)) throw new FileRefused(`refused: ${input.path} is outside the case`);
				const matches: string[] = [];
				for (const file of caseFiles(caseDir, start)) {
					if (!mayRead(scope, caseRelative(caseDir, file))) continue;
					if (lstatSync(file).size > MAX_GREP_FILE_BYTES) continue;
					const lines = sourceLines(readFileSync(file, 'utf8'));
					for (let index = 0; index < lines.length && matches.length < MAX_MATCHES; index += 1) {
						if (expression.test(lines[index])) {
							matches.push(`${caseRelative(caseDir, file)}:${index + 1}:${lines[index].slice(0, 400)}`);
						}
					}
					if (matches.length >= MAX_MATCHES) break;
				}
				return textResult(matches.length ? matches.join('\n') : 'no matches');
			},
		},
		{
			name: 'hash_files',
			label: 'Hash files',
			description:
				'SHA-256 of case files exactly as stored on disk (raw bytes), the digest the evidence validators recompute for sha256, anchor_sha256 and original_artifact_sha256. Use it for any artifact you wrote or derived yourself (extracted text, saved API JSON, transcripts). Paths relative to the case folder; up to 50 per call.',
			parameters: hashParams,
			async execute(_id, params) {
				const { paths } = checkedParams<{ paths: string[] }>('hash_files', hashParams, params);
				return jsonResult(
					paths.map((path) => {
						const target = readTarget(caseDir, path, scope);
						if (!lstatSync(target).isFile()) throw new Error(`hash_files: ${path} is not a regular file`);
						const bytes = readFileSync(target);
						return { path: inside(caseDir, target) ? caseRelative(caseDir, target) : path, sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length };
					}),
				);
			},
		},
	];
}
