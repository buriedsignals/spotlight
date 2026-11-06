// read_file, write_file, edit_file, list_files, grep_files and hash_files,
// confined to the active case. Reads stay inside the case (plus the checkout's schemas/);
// writes stay inside the caller's write folder, never follow a symlink, and
// never touch the files only the resolver and the review page may write.
import { createHash, randomUUID } from 'node:crypto';
import { closeSync, constants, existsSync, globSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, rmSync, writeSync } from 'node:fs';
import type { Stats } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT, ORCHESTRATOR, deliverableFor, factCheckChecklist, readCheckDocs } from '../roles.ts';
import { WITHDRAW_HINT, currentText, directionView, isDirectionFile, mergeByKey, mergeDirection, mergesByKey, planDirections, removesAnonymousRows } from '../directions.ts';
import { approvedIndicatorFramework } from './hosted-skill.ts';
import { factCheckerAlternative, factCheckerCapture, factCheckerSourceURLs, fetchedFile, registeredCapture } from './fetch.ts';
import { missingValues } from './claim-values.ts';
import { flatText, linkText, sameSourceSupport } from './quotes.ts';
import { completeNavigatorMetadata } from './navigator-receipts.ts';
import { completeAmendment, fillMethodology } from './methodology-paperwork.ts';
import { HARNESS_PROPOSALS, RECEIPTS, ToolArgumentError, checkedParams, jsonResult, runPython, textResult } from './run.ts';
import type { CaseScope, ToolContext } from './run.ts';

export class FileRefused extends Error {}

const SCHEMAS_DIR = resolve(CHECKOUT_ROOT, 'schemas');
// The procedures a 12b persona points at instead of carrying (roles.ts slicePersona).
const AGENT_REFERENCES_DIR = resolve(CHECKOUT_ROOT, 'agents', 'references');
const MAX_READ_CHARS = 200_000;
export const READ_TRUNCATED = '[truncated: use offset and limit to read the rest]';

// H-K3a (K3 Tor P3): at the 12b tier's 40,960 window (fold point 20,480), one
// whole read of a ~30k-character capture crossed the fold point, the fold
// dropped it, and the worker read it again, seven times. Below this fold point
// a read returns at most a page, a quarter of the fold point at 2.3 characters
// a token, of whole lines, and names the next offset and limit. The frontier
// tier has no fold point, and larger windows are unchanged.
export const READ_PAGE_FOLD_MAX = 32_768;
export function readPageChars(env: Record<string, string | undefined> = process.env): number | undefined {
	const at = Number(env.SPOTLIGHT_FOLD_AT);
	if (env.SPOTLIGHT_MODEL_TIER === 'frontier' || !Number.isInteger(at) || at <= 0 || at > READ_PAGE_FOLD_MAX) return undefined;
	return Math.floor((at * 2.3) / 4);
}
// The paperwork repairs below are for the local tiers (C13, briefs/simp-code.md): a frontier write gets what it always got.
export function localTier(env: Record<string, string | undefined> | undefined): boolean {
	return ((env?.SPOTLIGHT_MODEL_TIER ?? process.env.SPOTLIGHT_MODEL_TIER)?.trim() || '12b') !== 'frontier';
}
function readPage(lines: string[], first: number, total: number, page: number, limit?: number): string {
	let used = 0;
	let count = 0;
	for (const line of lines) {
		if (count > 0 && used + line.length + 1 > page) break;
		used += line.length + 1;
		count += 1;
	}
	const last = first + count - 1;
	// One line longer than a page is cut, and said so: read_file has no cursor
	// inside a line (Codex review of simp-read).
	const body = lines.slice(0, count).join('\n').slice(0, page);
	const cut = lines[0].length > page ? ` Line ${first} is longer than a page (${lines[0].length} characters) and was cut at ${page}.` : '';
	// Within the caller's own range, the next read stays inside it (Codex review of simp-read).
	const next = limit === undefined ? count : Math.min(count, limit - count);
	const more = next > 0 && last < total ? ` Read the next part with read_file offset ${last + 1} and limit ${next}.` : '';
	return `${body}\n[page: lines ${first}-${last} of ${total}, so the read stays under this model's context fold.${cut}${more}]\n${READ_TRUNCATED}`;
}

// fc-stall scan (incr-test2 sp98): fact-checker threads folded at 50-54k after
// whole reads of findings.json, evidence-bundle.json and source-expressions.json,
// then started over. A worker's whole read of one of these, past this size,
// returns the file's entries by json_pointer instead of the file.
export const INDEXED_READ_CHARS = 12_000;
const INDEXED_LISTS: Record<string, string> = { 'data/findings.json': 'findings', 'data/evidence-bundle.json': 'items', 'data/source-expressions.json': 'expressions' };

export function readIndex(name: string, text: string): string | undefined {
	const key = INDEXED_LISTS[name];
	if (!key || text.length <= INDEXED_READ_CHARS) return undefined;
	let rows: unknown;
	try {
		rows = (JSON.parse(text) as Record<string, unknown>)[key];
	} catch {
		return undefined;
	}
	if (!Array.isArray(rows)) return undefined;
	const entries = rows.map((row, index) => `/${key}/${index} ${String((row as Record<string, unknown> | null)?.id ?? '?')}`);
	return `${name} is ${text.length.toLocaleString('en-US')} characters, too long to read whole. Its ${key} by json_pointer: ${entries.join('; ')}. Read the ones you need with json_pointer, or a range with offset and limit.`;
}
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
// files or explicitly named read-only folders. Writes stay exact-file scoped.
// Names compare case-insensitively, as the protected list does.
function inScope(allowed: string[] | undefined, name: string, folders = false): boolean {
	const lower = name.toLowerCase();
	return allowed === undefined || allowed.some((entry) => entry.toLowerCase() === lower || (folders && entry.endsWith('/') && lower.startsWith(entry.toLowerCase())));
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
	return inScope(scope?.reads, name, true) && !denied(scope?.denyReads, name);
}

// Every case data file under data/ is one JSON object, and the resolver hashes
// them as objects: a list or broken JSON there leaves the case at an unknown phase.
function refuseNonObjectData(caseDir: string, target: string, content: string): void {
	const path = caseRelative(caseDir, target);
	if (!/^data\/[^/]+\.json$/.test(path)) return;
	let value: unknown;
	// fc-stall scan: 57 investigator writes refused as not one JSON object, with
	// nothing on where the JSON broke.
	let broken = '';
	try {
		value = JSON.parse(content);
	} catch (error) {
		value = undefined;
		const at = Number(/at position (\d+)/.exec((error as Error).message)?.[1]);
		const near = Number.isFinite(at) ? ` near ${JSON.stringify(content.slice(Math.max(0, at - 40), at + 40))}` : '';
		broken = ` ${(error as Error).message}${near}.`;
	}
	const bundle = path === 'data/evidence-bundle.json';
	// The bundle's items under another key read as no items at all (12B r3 cc: "evidence_bundle").
	if (value !== null && typeof value === 'object' && !Array.isArray(value) && (!bundle || Array.isArray((value as { items?: unknown }).items))) return;
	const shape = bundle ? ' with an `items` list' : '';
	throw new FileRefused(`refused: ${path} must stay one JSON object${shape}; the file is unchanged.${broken} Read it, change the part you need, and write the whole object back.`);
}

// LFM3 r3 kde stage 3 on Osaurus (03f008b0): the analyst's proposal failed
// interface 1, which the builder reports only after the analyst's turn, and
// the analyst read and grepped until the stall rule stopped it (s3-1), or failed
// its cross-record rules (s3-3). A proposal is checked by every check the
// builder makes when written, and refused with the builder's text.
async function refuseInvalidProposal(context: ToolContext, target: string, content: string): Promise<void> {
	if (caseRelative(context.caseDir, target) !== 'data/knowledge-proposal.json') return;
	const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', 'build-knowledge-batch.py'), context.caseDir, '--check-proposal'], { input: content, timeoutMs: 60_000, keys: [RECEIPTS] });
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
// Who writes each worker file, and the task that asks them to change it.
const FILE_OWNERS: Record<string, { role: string; task: string }> = {
	'data/methodology.json': { role: 'investigator', task: 'starting "MODE: METHODOLOGY CORRECTION" that says what to change' },
	'data/findings.json': { role: 'investigator', task: 'that names the findings to change and how' },
	'data/evidence-bundle.json': { role: 'investigator', task: 'that names the evidence items to change and how' },
	'data/investigation-log.json': { role: 'investigator', task: 'that says what the log is missing' },
	'data/fact-check.json': { role: 'fact-checker', task: 'that names the claims to check again and why' },
	'summary.md': { role: 'analyst', task: 'at the Investigation gate (the harness builds it)' },
	'data/summary.json': { role: 'analyst', task: 'at the Investigation gate (the harness builds it)' },
	'data/report-draft.json': { role: 'analyst', task: 'at Report (the harness builds it)' },
	'data/knowledge-proposal.json': { role: 'analyst', task: 'at Ingest (the harness builds it, and builds the knowledge batch when the analyst finishes)' },
};
const ownerNext = (relativePath: string) => {
	const owner = FILE_OWNERS[relativePath];
	return owner ? ` It is the ${owner.role}'s file: to change it, call spawn_agent with agent "${owner.role}" and a task ${owner.task}.` : '';
};

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
	if (denied(denyWrites, caseRelative(caseDir, target))) {
		// U3 census (84674fd9): a fact-checker wrote fact-check content over
		// data/findings.json. The refusal names the writer's own file.
		const own = deliverableFor(process.env.SPOTLIGHT_ROLE ?? '', '', '')?.files;
		// fc-stall scan: 93 orchestrator writes to workers' files in 48 runs;
		// the refusal names the owner and the spawn that changes the file.
		const next = own ? '' : ownerNext(caseRelative(caseDir, target));
		throw new FileRefused(`refused: ${path} is a case file another role writes; this agent may not write it${own ? `. Your own file is ${own.join(' and ')}.` : '.'}${next}`);
	}
	// Checked before any parent folder is created, after the denylist so a
	// refusal for another role's file names that role, and after the misplaced
	// brief, whose refusal names the right path.
	refuseMisplacedBrief(caseDir, target);
	if (!inScope(writes, caseRelative(caseDir, target))) {
		// overnight-summary.md H-K2c: the orchestrator's write of the analyst's
		// proposal was refused without naming the analyst or the next step.
		// Only the orchestrator can spawn; a worker is not told to (Codex review of simp-text).
		const next = process.env.SPOTLIGHT_ROLE === 'orchestrator' ? ownerNext(caseRelative(caseDir, target)) : '';
		throw new FileRefused(`refused: ${path} is not a file this agent may write (${(writes ?? []).join(', ') || 'none'})${next ? `.${next}` : ''}`);
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
		throw new FileRefused(`refused: ${name} is written only through the source_expressions tool, which computes its hashes and fingerprints. Call source_expressions with action write and the passage proposal instead.`);
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

// validate-case.py's anchor rule for a positive verdict, unchanged.
function hasCaseLocalAnchor(item: unknown, verdict: unknown): boolean {
	if (!item || typeof item !== 'object') return false;
	const row = item as { evidence_bundle_id?: unknown; access_method?: unknown; local_file?: unknown; source_ref?: unknown };
	const nonEmpty = (value: unknown) => typeof value === 'string' && value.trim() !== '';
	const access = ['full_text', 'open_access', 'archive_copy', ...(verdict === 'partially_verified' ? ['abstract_only'] : [])];
	return nonEmpty(row.evidence_bundle_id) || (access.includes(String(row.access_method)) && (nonEmpty(row.local_file) || (!!row.source_ref && typeof row.source_ref === 'object' && !Array.isArray(row.source_ref))));
}

// r12 (r12-ingest-audit A3): both verified rows cited only the investigator's
// capture, while the fact-checker's own identical re-fetch sat uncited. In an
// activated case a verified row needs a passage the fact-checker acquired. The
// fetch registry's receipt decides (Codex review of fix11: created_by can be
// written by anyone, and an item id can name a file other than the one quoted):
// the file the row quotes from is a registered fact-checker capture, and a
// cited evidence item lists that same file. validate-fact-check.py and the
// batch builder read the same receipt.
export const ACQUIRED_FILES = ['raw_path', 'downloaded_document_path', 'screenshot_path', 'path'];
function factCheckerAcquired(context: ToolContext, entry: Record<string, unknown>, bundle: Record<string, unknown>[]): boolean {
	const ref = (entry.source_ref ?? {}) as Record<string, unknown>;
	const paths = [ref.path, entry.local_file].filter((path): path is string => typeof path === 'string' && path.trim() !== '');
	const cited = entry.evidence_bundle_id === undefined ? undefined : bundle.find((item) => item.id === entry.evidence_bundle_id);
	const lists = (item: Record<string, unknown>, path: string) => [...ACQUIRED_FILES.map((key) => item[key]), ...(Array.isArray(item.text_derivatives) ? item.text_derivatives.map((d: { path?: unknown }) => d?.path) : [])].includes(path);
	return paths.some((path) => !!factCheckerCapture(context, path) && (entry.evidence_bundle_id === undefined || (!!cited && lists(cited, path))));
}

// Phase B (Gemma12 worker state and the 31B live run r5): a positive verdict
// was accepted here and validate-case.py then refused it ("positive verdict
// needs an accessible case-local evidence anchor"). The same rule at the
// write, after the more specific passage and file checks, naming the step.
function refuseUnanchoredVerdicts(context: ToolContext, target: string, content: string): void {
	const caseDir = context.caseDir;
	if (caseRelative(caseDir, target) !== 'data/fact-check.json') return;
	let factCheck: { schema_version?: unknown; claims?: unknown };
	try {
		factCheck = JSON.parse(content);
	} catch {
		return;
	}
	const unanchored = positiveClaims(factCheck).filter((claim) => {
		const items = Array.isArray(claim.evidence_for) ? claim.evidence_for : [];
		return items.length > 0 && !items.some((item) => hasCaseLocalAnchor(item, (claim as { verdict?: unknown }).verdict));
	});
	// Codex re-review of fix11: validate-fact-check.py refuses an entry whose
	// local_file and source_ref.path name different files; the write says so first.
	const split = positiveClaims(factCheck).flatMap((claim) => (Array.isArray(claim.evidence_for) ? claim.evidence_for : []).flatMap((item: unknown, index: number) => {
		const row = (item ?? {}) as Record<string, unknown>;
		const local = row.local_file, path = ((row.source_ref ?? {}) as Record<string, unknown>).path;
		if (typeof local !== 'string' || typeof path !== 'string' || !local.trim() || !path.trim() || resolve(caseDir, local) === resolve(caseDir, path)) return [];
		return [`${String(claim.finding_id)} evidence_for[${index}]: local_file is ${local} but source_ref.path is ${path}; set local_file to ${path}, the file your source_ref locates the quote in, or remove local_file.`];
	}));
	if (split.length) throw new FileRefused(`refused: data/fact-check.json is unchanged; an evidence item must name one file:\n${split.join('\n')}`);
	if (unanchored.length) throw new FileRefused(`refused: data/fact-check.json is unchanged; ${unanchored.map((claim) => String(claim.finding_id)).join(', ')} has a verified verdict with no case-local evidence anchor: no evidence_for item has an evidence_bundle_id, or an access_method (full_text, open_access or archive_copy) with a local_file or source_ref. Cite a capture you acquired: give its source_ref.path from your fetch with the quote found there (or its line_start/line_end), and the harness adds its evidence_bundle_id; passages alone are not an anchor. Or acquire an independent capture with fetch (save_as under research/), or set the verdict to unverified.`);
	if (factCheck?.schema_version !== '1.1') return;
	let bundle: Record<string, unknown>[] = [];
	try {
		const items = (JSON.parse(readFileSync(join(caseDir, 'data', 'evidence-bundle.json'), 'utf8')) as { items?: unknown }).items;
		bundle = Array.isArray(items) ? items.filter((item) => item && typeof item === 'object') : [];
	} catch {
		// No bundle: only a registered capture can count.
	}
	const borrowed = (Array.isArray(factCheck.claims) ? factCheck.claims : []).filter((claim: { verdict?: unknown; evidence_for?: unknown }) => {
		if (claim?.verdict !== 'verified') return false;
		const items = Array.isArray(claim.evidence_for) ? claim.evidence_for : [];
		return items.length > 0 && !items.some((item: unknown) => item && typeof item === 'object' && factCheckerAcquired(context, item as Record<string, unknown>, bundle));
	});
	if (!borrowed.length) return;
	const lines = borrowed.map((claim: { finding_id?: unknown; evidence_for?: unknown[] }) => {
		const cited = (claim.evidence_for ?? []).flatMap((item) => {
			const row = (item ?? {}) as Record<string, unknown>;
			const path = ((row.source_ref ?? {}) as Record<string, unknown>).path ?? row.local_file;
			return typeof path === 'string' ? [path] : [];
		});
		const own = cited.map((path) => factCheckerAlternative(context, path)).find(Boolean);
		return `${String(claim.finding_id)}: ${own ? `cite your own capture ${own}, which holds the same bytes, in your evidence item` : 'cite a capture you acquired with fetch (save_as under research/), in your evidence item'}, or set the verdict to unverified.`;
	});
	throw new FileRefused(`refused: data/fact-check.json is unchanged; a verified verdict needs evidence the fact-checker acquired, and these cite only another worker's capture (an investigator's saved copy alone never makes a verdict verified):\n${lines.join('\n')}`);
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
		// arms on 8699f11d (r5-spectator): "no passage on record supports undefined"
		// for a verified claim written without its finding_id.
		if (typeof claim.finding_id !== 'string') {
			unsupported.push('\na verified claim has no finding_id; give it the id of the finding it checks, as in the next-to-check list.');
			continue;
		}
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
		if (items.some((item) => Array.isArray(item?.source_expression_refs) && item.source_expression_refs.some(onRecord))) {
			const unmatched = items.flatMap((item) => Array.isArray(item?.source_expression_refs) ? item.source_expression_refs.filter((ref: unknown) => !onRecord(ref)) : []);
			if (unmatched.length) throw new FileRefused(`refused: data/fact-check.json is unchanged; ${claim.finding_id} has unmatched source_expression_refs ${JSON.stringify(unmatched)}. Keep only passages you checked that support this finding; its active supporting passage ids are ${JSON.stringify(refs.map((ref) => ref.expression_id))}. Do not copy another finding's fingerprints.`);
			continue;
		}
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

// B24 (K3 Eurostat P3 rerun on 40b77272, seq 24): the investigator rewrote the item the cite route had written for its capture, without
// sha256 and with an invented accessed time, the merge took it, and the passage recorded from that capture no longer matched its item.
// For an item whose capture is a harness fetch, unchanged since, and whose sha256 matches that receipt, sha256 and accessed are code's:
// while the item keeps its raw_path, a merge keeps them as they were and reports which ones it kept. A changed raw_path is left to the
// write checks (refuseLeadsEvidence names the .raw, refuseBundleRewrites refuses moving a cited item), which refuse it with a reason
// instead of undoing it silently. Every other field stays the worker's.
// B16, B17, B23(b) (K3 Eurostat P3, K3 Tor P3 rerun: the same refused write 6x, K5-3: 60-character retyped hashes), local tiers: an item new to
// the bundle takes both fields from the receipt of its registered, unchanged capture, and raw_path on fetch's leads moves to the .raw when that
// .raw is such a capture, the repair refuseLeadsEvidence names. With no such capture the item stays as written, for the write checks.
const RECEIPT_FIELDS = ['sha256', 'accessed'] as const;
function keepReceiptFields(context: ToolContext, current: string | undefined, merged: string): { text: string; kept: string[]; filled: string[] } {
	type Item = Record<string, unknown>;
	const itemsOf = (doc: unknown): Item[] => (doc && typeof doc === 'object' && Array.isArray((doc as { items?: unknown }).items) ? ((doc as { items: unknown[] }).items.filter((item) => item && typeof item === 'object') as Item[]) : []);
	let before: unknown;
	let after: unknown;
	try {
		before = current === undefined ? {} : JSON.parse(current);
		after = JSON.parse(merged);
	} catch {
		return { text: merged, kept: [], filled: [] };
	}
	const was = new Map(itemsOf(before).filter((item) => typeof item.id === 'string').map((item) => [item.id, item]));
	const kept: string[] = [];
	const filled: string[] = [];
	const local = localTier(context.env);
	for (const item of itemsOf(after)) {
		const old = was.get(item.id);
		// A fact-checker's write leaves items it did not change as stored: any repair here reads as the fact-checker changing them, and refuseBundleRewrites then refuses its add for good (gold-asf on 3767f400).
		if (old && context.env.SPOTLIGHT_ROLE === 'fact-checker' && isDeepStrictEqual(item, old)) continue;
		// Case 275a35b6: an investigator rewrote its row without the claim_links record_finding had set, so the finding lost its support. A row for the same file keeps them unless the write says otherwise.
		if (local && old && item.claim_links === undefined && Array.isArray(old.claim_links) && old.claim_links.length > 0 && item.raw_path === old.raw_path) {
			item.claim_links = old.claim_links;
			filled.push(`Code kept ${String(item.id)}'s claim_links, the finding links already recorded.`);
		}
		const path = item.raw_path;
		const fromLeads = local && typeof path === 'string' && isLeadFile(context.caseDir, path) && !!registeredCapture(context, `${path}.raw`);
		if (fromLeads) {
			item.raw_path = `${path}.raw`;
			filled.push(`Code moved ${String(item.id)}'s raw_path from ${path} to ${path}.raw, the page as fetched.`);
		}
		if (local && (fromLeads || !old)) {
			const capture = typeof item.raw_path === 'string' ? registeredCapture(context, item.raw_path) : undefined;
			const fields = capture ? RECEIPT_FIELDS.filter((field) => !isDeepStrictEqual(item[field], capture[field])) : [];
			for (const field of fields) item[field] = capture![field];
			if (fields.length) filled.push(`Code filled ${String(item.id)}'s ${fields.join(' and ')} from the fetch receipt.`);
			continue;
		}
		if (!old || typeof old.raw_path !== 'string' || item.raw_path !== old.raw_path) continue;
		const receipt = registeredCapture(context, old.raw_path);
		if (!receipt || receipt.sha256 !== old.sha256) continue;
		// The values come from the receipt itself, never from the item, so a time a worker once wrote is not kept as the receipt's (Codex P2).
		const fields = RECEIPT_FIELDS.filter((field) => !isDeepStrictEqual(item[field], receipt[field]));
		if (fields.length === 0) continue;
		for (const field of fields) item[field] = receipt[field];
		kept.push(`${String(item.id)}'s ${fields.join(' and ')}`);
	}
	return { text: kept.length || filled.length ? `${JSON.stringify(after, null, 2)}\n` : merged, kept, filled };
}

// A bundle write keeps what others rely on (12B r3 ia: the fact-checker
// rewrote investigator item E1 to another file, and every passage already
// anchored to it failed validation from then on). A fact-checker changes only
// its own items, as phase-investigation tells it; no role moves the files of an
// item that active passages cite.
const ITEM_FILES = ['raw_path', 'path', 'downloaded_document_path', 'text_derivatives'];

// Case 275a35b6: a correction invented D1-E2 (example.com, research/evidence2.md) with
// claim_links to real findings, and nothing refused a row with no capture behind it.
// On a local tier a new or moved row must name a file in the case. Refused whole, so
// the real rows stand, and the text names the route that does work.
function refuseMissingCapture(caseDir: string, target: string, content: string, env: Record<string, string | undefined>): void {
	if (!localTier(env) || caseRelative(caseDir, target) !== 'data/evidence-bundle.json') return;
	const rowsOf = (text: string): Array<Record<string, unknown>> => {
		try {
			const items = (JSON.parse(text) as { items?: unknown }).items;
			return Array.isArray(items) ? items.filter((item) => item && typeof item === 'object') : [];
		} catch {
			return [];
		}
	};
	let stored: Array<Record<string, unknown>> = [];
	try { stored = rowsOf(readFileSync(target, 'utf8')); } catch { /* the first write: nothing stored yet */ }
	const known = new Map(stored.map((item) => [item.id, item.raw_path]));
	for (const item of rowsOf(content)) {
		const path = item.raw_path;
		if (typeof path !== 'string' || !path.trim() || known.get(item.id) === path) continue;
		const file = resolve(caseDir, path);
		if (file.startsWith(`${caseDir}${sep}`) && existsSync(file)) continue;
		throw new FileRefused(`refused: data/evidence-bundle.json item ${String(item.id)} names ${path}, which is not a capture in this case; the file is unchanged. An evidence item records a file you fetched. To link a finding to an existing item, read data/evidence-bundle.json and write that item as it is with claim_links added. To record a new source, fetch it first.`);
	}
}

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
		if (typeof id !== 'string' || !id.trim()) return [];
		const cited = passages.filter((p) => p?.original_evidence_bundle_id === id && p?.lifecycle_events?.at(-1)?.event === 'activated').map((p) => String(p.id));
		const next = after.get(id);
		if (cited.length === 0 || (next && ITEM_FILES.every((key) => isDeepStrictEqual(item[key], next[key])))) return [];
		return [`${String(id)} (cited by ${cited.join(', ')})`];
	});
	if (moved.length === 0) return;
	throw new FileRefused(`refused: data/evidence-bundle.json changes or removes the files of ${moved.join('; ')}; the file is unchanged. Recorded passages anchor to those files, so keep them as they are. To link a finding to one of these items, read data/evidence-bundle.json and write the item as it is with claim_links added. Record a file you fetched that is not listed as a new item.`);
}

// r12 (r12-ingest-audit A2): the fact-checker appended FC-E1 for its own
// re-fetch without created_by, so its own later edits were refused, and it was
// never counted as its acquisition. Code stamps what it knows: an item the
// fact-checker appends is the fact-checker's, and one whose raw_path is its
// registered capture was read in full (access_method full_text). A fact-check
// entry tied by id to such an item gets the same access_method, which is what
// the Batch 1.1 builder reads as re_fetched (build-knowledge-batch.py).
function stampFactCheckerRecords(context: ToolContext, target: string, content: string, role: string | undefined): string {
	if (role !== 'fact-checker') return content;
	const name = caseRelative(context.caseDir, target);
	if (name !== 'data/evidence-bundle.json' && name !== 'data/fact-check.json') return content;
	let document: Record<string, unknown>;
	try {
		document = JSON.parse(content);
	} catch {
		return content;
	}
	if (!document || typeof document !== 'object' || Array.isArray(document)) return content;
	const rows = (value: unknown) => (Array.isArray(value) ? value.filter((row) => row && typeof row === 'object' && !Array.isArray(row)) : []) as Record<string, unknown>[];
	const captured = (path: unknown) => typeof path === 'string' && !!factCheckerCapture(context, path);
	let changed = false;
	if (name === 'data/evidence-bundle.json') {
		let existing = new Set<unknown>();
		try {
			existing = new Set(rows((JSON.parse(readFileSync(target, 'utf8')) as { items?: unknown }).items).map((item) => item.id));
		} catch {
			// A new bundle: every item is this write's.
		}
		for (const item of rows(document.items)) {
			if (!existing.has(item.id) && item.created_by !== 'fact-checker') {
				item.created_by = 'fact-checker';
				changed = true;
			}
			if (item.created_by === 'fact-checker' && item.access_method === undefined && captured(item.raw_path)) {
				item.access_method = 'full_text';
				changed = true;
			}
		}
	} else {
		let bundle: Record<string, unknown>[] = [];
		try {
			bundle = rows((JSON.parse(readFileSync(join(context.caseDir, 'data', 'evidence-bundle.json'), 'utf8')) as { items?: unknown }).items);
		} catch {
			// No bundle: only a cited capture path can be stamped.
		}
		for (const claim of rows(document.claims)) {
			for (const entry of [...rows(claim.evidence_for), ...rows(claim.evidence_against)]) {
				if (entry.access_method !== undefined) continue;
				// Only an entry already tied by id to an item that lists the
				// registered fact-checker capture it quotes from: the quote path ties
				// one only once the quote is found there. A line number or a path
				// alone never counts (review-fix4 N1).
				if (entry.evidence_bundle_id !== undefined && factCheckerAcquired(context, entry, bundle)) {
					entry.access_method = 'full_text';
					changed = true;
				}
			}
		}
	}
	return changed ? `${JSON.stringify(document, null, 2)}\n` : content;
}

// A fact-check claim checks the finding it names, word for word (gold osmf
// s1: the fact-checker rewrote a finding into an invented address and marked
// it false). Compared as validate-case.py does: Unicode NFKC, whitespace runs
// collapsed.
function refuseDriftedClaims(caseDir: string, target: string, content: string, requireText = false): void {
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
		if (requireText && claim && typeof claim === 'object' && 'withdrawn' in claim) return [];
		return typeof expected === 'string' && ((requireText && typeof text !== 'string') || (typeof text === 'string' && normalized(text) !== normalized(expected)))
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
	if (!readable(caseDir, target, scope)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read. Give a path relative to the case folder, such as data/findings.json.`);
	let real: string;
	try {
		real = realpathSync(target);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
		// U3 census stage 1 (0af4585b): the orchestrator asked for
		// schemas/case-contract.json about 100 times; the schema has .schema.json.
		const schema = /^schemas[\\/].+\.json$/.test(path) && !path.endsWith('.schema.json') ? path.replace(/\.json$/, '.schema.json') : '';
		throw new Error(schema && existsSync(requested(CHECKOUT_ROOT, schema)) ? `not found: ${path}; the schema is ${schema}` : `not found: ${path}. Call list_files with a pattern (for example "research/*") to see the files that exist.`);
	}
	if (!readable(caseDir, real, scope)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read. Give a path relative to the case folder, such as data/findings.json.`);
	return real;
}

const SUGGESTION_INPUTS = ['data/leads.json', 'data/clues.json', 'data/research-triage.json'];

// P3W-1 (pod-3way-qwen35-9b.md: 15 validate-case errors on summary.json ended the run one step before the gate): every field of data/summary.json but the
// prose is in the findings and the fact-check, so code writes it there. The analyst's title, summary, conclusions and limitations stay; a title or summary left
// empty gets a plain count, which is a fact, not a narrative. A finding with no verdict yet proves no row, and the file stays as written.
function completeSummary(caseDir: string, merged: string, notes: string[]): string {
	let summary: Record<string, unknown>;
	let project: unknown;
	let findings: { id?: unknown; claim?: unknown; confidence?: unknown; sources?: unknown }[];
	let claims: { finding_id?: unknown; verdict?: unknown }[];
	try {
		summary = JSON.parse(merged);
		const findingsDoc = JSON.parse(readFileSync(join(caseDir, 'data', 'findings.json'), 'utf8'));
		project = findingsDoc.project;
		findings = findingsDoc.findings;
		// An investigation that found nothing qualifying has no verdict to read (Codex P2).
		claims = Array.isArray(findings) && findings.length === 0 ? [] : JSON.parse(readFileSync(join(caseDir, 'data', 'fact-check.json'), 'utf8')).claims;
	} catch {
		return merged;
	}
	if (!summary || typeof summary !== 'object' || Array.isArray(summary) || !Array.isArray(findings) || !Array.isArray(claims)) return merged;
	// A finding with several fact-check rows is verified only if every row is (Codex R2).
	const verdicts = new Map<unknown, unknown>();
	for (const claim of claims) if (!verdicts.has(claim?.finding_id) || verdicts.get(claim?.finding_id) === 'verified') verdicts.set(claim?.finding_id, claim?.verdict);
	const rows = findings.map((finding) => ({ id: finding?.id, claim: finding?.claim, confidence: finding?.confidence, fact_check_verdict: verdicts.get(finding?.id), ...(Array.isArray(finding?.sources) ? { source_count: finding.sources.length } : {}) }));
	if (rows.some((row) => typeof row.fact_check_verdict !== 'string')) return merged;
	const counts = new Map<unknown, number>();
	for (const row of rows) counts.set(row.fact_check_verdict, (counts.get(row.fact_check_verdict) ?? 0) + 1);
	const verified = counts.get('verified') ?? 0;
	const given = (value: unknown) => typeof value === 'string' && value.trim().length > 0;
	const filled: Record<string, unknown> = { ...summary, schema_version: '1.0', ...(typeof project === 'string' ? { project } : {}), generated_at: new Date().toISOString(), status: given(summary.status) ? summary.status : 'pending_review', verified_findings: verified, findings: rows };
	const changed = Object.keys(filled).filter((key) => !isDeepStrictEqual(filled[key], summary[key]));
	notes.push(`Code filled ${changed.join(', ')} in data/summary.json from the findings and the fact-check.`);
	if (!given(summary.title) || !given(summary.summary)) {
		if (!given(summary.title)) filled.title = `${verified} verified finding${verified === 1 ? '' : 's'}`;
		if (!given(summary.summary)) filled.summary = `${rows.length} finding${rows.length === 1 ? '' : 's'}${rows.length ? `: ${[...counts].map(([verdict, count]) => `${count} ${String(verdict)}`).join(', ')}` : ''}.`;
		notes.push('Code wrote a plain count as title and summary where they were empty; write your own in their place.');
	}
	return `${JSON.stringify(filled, null, 2)}\n`;
}

// P4 (qwen-u9-mac.md: r3 left summary.md an unfilled template): on a local tier
// code writes both gate summaries from the records, so the text is the case's
// own: the lead, verified claims word for word, the open gaps. It reads plainer
// than a model's overview and adds nothing the records do not hold. generated_at
// is in both files, so a regenerated summary is never stale by hash (stale_summaries).
export function writeGateSummary(caseDir: string): string {
	const record = (path: string): Record<string, unknown> => {
		try {
			const value = JSON.parse(readFileSync(join(caseDir, path), 'utf8'));
			return value && typeof value === 'object' ? value : {};
		} catch {
			return {};
		}
	};
	const texts = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []);
	const methodology = record('data/methodology.json');
	const cycles = Array.isArray(record('data/investigation-log.json').cycles) ? (record('data/investigation-log.json').cycles as { gaps_remaining?: unknown }[]) : [];
	const lead = typeof methodology.lead === 'string' ? methodology.lead.trim() : '';
	const limitations = [...new Set([...texts(cycles.at(-1)?.gaps_remaining), ...texts(methodology.limitations)])];
	const title = lead.length > 120 ? `${lead.slice(0, lead.lastIndexOf(' ', 117))}...` : lead;
	const done = completeSummary(caseDir, JSON.stringify({ title, cycles: Math.max(cycles.length, 1), limitations }), []);
	const summary = JSON.parse(done) as { title: string; generated_at?: string; cycles: number; summary: string; findings?: { id: string; claim: string; confidence: unknown; fact_check_verdict: string; source_count?: number }[] };
	if (!summary.findings || !summary.generated_at) throw new Error('the gate summary needs data/findings.json and a fact-check verdict for every finding');
	// Unverified findings can hold invented names, so they stay out of the overview and the main list.
	const verified = summary.findings.filter((row) => row.fact_check_verdict === 'verified');
	const doubtful = summary.findings.filter((row) => row.fact_check_verdict !== 'verified');
	const conclusions = verified.map((row) => row.claim);
	const cell = (value: unknown) => String(value ?? '').replace(/\s+/g, ' ').replaceAll('|', '\\|');
	const findingTable = (rows: NonNullable<typeof summary.findings>, none: string) => (rows.length ? ['| # | Claim | Confidence | Verdict | Sources |', '|---|-------|------------|---------|---------|', ...rows.map((row) => `| ${[row.id, row.claim, row.confidence, row.fact_check_verdict, row.source_count ?? ''].map(cell).join(' | ')} |`)].join('\n') : none);
	const list = (items: string[], none: string) => (items.length ? items.map((item) => `- ${item}`).join('\n') : none);
	const markdown = [
		`# ${summary.title}`,
		`**Date:** ${summary.generated_at} | **Cycles:** ${summary.cycles} | **Status:** Pending review`,
		`## Overview\n\n${lead ? `Asked: ${lead}\n\n` : ''}${summary.summary}`,
		`## Key Conclusions\n\n${list(conclusions, 'No finding is verified.')}`,
		`## Findings\n\n${findingTable(verified, summary.findings.length ? 'No finding is verified.' : 'No finding was recorded.')}`,
		...(doubtful.length ? [`## Not verified, check before use\n\n${findingTable(doubtful, '')}`] : []),
		`## Limitations\n\n${list(limitations, 'None recorded.')}`,
	].join('\n\n');
	writeConfined(caseDir, caseDir, 'data/summary.json', `${JSON.stringify({ ...summary, key_conclusions: conclusions }, null, 2)}\n`);
	writeConfined(caseDir, caseDir, 'summary.md', `${markdown}\n`);
	return `code wrote summary.md and data/summary.json: ${summary.summary}`;
}

// Only documented path fields get the case-root alias; other text is evidence
// or judgment, and an absent or unsafe path remains for the validator to flag.
// These are record-envelope facts, never source dates or semantic judgments.
// Derive the active format after merging: mergeByKey preserves the old header,
// so an explicit repair of a stale 1.0 header otherwise cannot take effect.
function artifactEnvelope(caseDir: string, target: string, submitted: string, merged: string, env: Record<string, string | undefined> = {}, notes: string[] = []): string {
	const name = caseRelative(caseDir, target);
	// B15 (K3-2: invented hashes): the suggestions name the bytes they were written for, which code can hash and a model cannot (research.py suggestions_problems
	// compares the same three). With an input missing there is nothing to prove, and the file stays as written.
	if (name === 'data/research-suggestions.json') {
		if (!localTier(env)) return merged;
		let suggestions: unknown;
		let inputs: Record<string, string>;
		try {
			suggestions = JSON.parse(merged);
			inputs = Object.fromEntries(SUGGESTION_INPUTS.map((path) => [path, createHash('sha256').update(readFileSync(readTarget(caseDir, path))).digest('hex')]));
		} catch {
			return merged;
		}
		if (!suggestions || typeof suggestions !== 'object' || Array.isArray(suggestions) || isDeepStrictEqual((suggestions as { inputs?: unknown }).inputs, inputs)) return merged;
		notes.push('Code set inputs in data/research-suggestions.json to the hashes of the leads, clues and triage as they are now.');
		return `${JSON.stringify({ ...suggestions, inputs }, null, 2)}\n`;
	}
	// H-K2h (C13): the proposal's envelope is the runtime's, filled by code.
	if (name === 'data/summary.json') return localTier(env) ? completeSummary(caseDir, merged, notes) : merged;
	if (name === 'data/knowledge-proposal.json') {
		let proposal: unknown;
		try { proposal = JSON.parse(merged); } catch { return merged; }
		const model = env.SPOTLIGHT_MODEL_ID ?? process.env.SPOTLIGHT_MODEL_ID;
		if (!proposal || typeof proposal !== 'object' || Array.isArray(proposal) || !model) return merged;
		const filled = { ...(proposal as Record<string, unknown>), interface_version: '1', model };
		return JSON.stringify(filled) === JSON.stringify(proposal) ? merged : `${JSON.stringify(filled, null, 2)}\n`;
	}
	if (name === 'data/methodology-amendment.json') {
		let fragment: unknown;
		try { fragment = JSON.parse(submitted); } catch { return merged; }
		const whole = fragment && typeof fragment === 'object' && !Array.isArray(fragment) ? completeAmendment(caseDir, fragment as Record<string, unknown>) : undefined;
		return whole ? `${JSON.stringify(whole, null, 2)}\n` : merged;
	}
	if (!['data/methodology.json', 'data/findings.json', 'data/fact-check.json', 'data/evidence-bundle.json', 'data/investigation-log.json'].includes(name)) return merged;
	const object = (text: string | undefined): Record<string, unknown> | undefined => {
		if (text === undefined) return undefined;
		try {
			const value = JSON.parse(text);
			return value && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
		} catch { return undefined; }
	};
	const input = object(submitted), document = object(name === 'data/methodology.json' ? completeNavigatorMetadata(caseDir, merged) : merged);
	if (!input || !document) return merged;
	const before = JSON.stringify(object(merged));
	const contract = object(currentText(join(caseDir, 'data/case-contract.json')));
	const events = contract?.activation_events;
	const activated = contract?.schema_version === '1.0' && contract.current_contract_version === '1.1'
		&& Array.isArray(events) && events.some((event) => event?.activated_contract_version === '1.1');
	if (activated && ['data/findings.json', 'data/fact-check.json'].includes(name)) {
		if (input.schema_version !== undefined && input.schema_version !== '1.1') {
			throw new FileRefused(`refused: ${name} belongs to an activated case and requires schema_version "1.1". Submit its 1.1 fields and exact evidence references; do not rename legacy verdicts or invent missing evidence. Omit the version to use the active contract. The file is unchanged.`);
		}
		document.schema_version = '1.1';
	}
	const existing = object(currentText(target));
	if (activated && typeof contract?.project === 'string' && contract.project.trim()) document.project = contract.project;
	else if (typeof existing?.project === 'string' && existing.project.trim()) document.project = existing.project;
	if (name === 'data/evidence-bundle.json') {
		// The schema's constant (inv-retest-def8674f.md: a bundle the commit step
		// created had none, validate-case failed and no fact-checker could start).
		if (document.schema_version === undefined) document.schema_version = '1.0';
		const valid = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
		document.run_id = valid(existing?.run_id) ? existing.run_id : valid(input.run_id) ? input.run_id : `local-${randomUUID()}`;
	}
	const created = name === 'data/methodology.json' ? 'planned_at' : name === 'data/evidence-bundle.json' ? 'created_at' : undefined;
	if (created) document[created] = existing?.[created] ?? new Date().toISOString();
	if (name === 'data/methodology.json') fillMethodology(caseDir, env, document);
	if (name === 'data/investigation-log.json' && localTier(env)) renameLogAliases(document, notes);
	return JSON.stringify(document) === before ? merged : `${JSON.stringify(document, null, 2)}\n`;
}

// qwen-u9-mac.md (P3W-5): the log's methodology keys are techniques_used and tools_used, and a model writes techniques and tools. Code renames them
// where the right key is absent, so the alias cannot hold a case in correction.
function renameLogAliases(document: Record<string, unknown>, notes: string[]): void {
	const renamed = new Set<string>();
	for (const cycle of Array.isArray(document.cycles) ? document.cycles : []) {
		const methodology = (cycle as { methodology?: unknown } | null)?.methodology;
		if (!methodology || typeof methodology !== 'object' || Array.isArray(methodology)) continue;
		const keys = methodology as Record<string, unknown>;
		for (const [alias, key] of [['techniques', 'techniques_used'], ['tools', 'tools_used']]) {
			if (!Object.hasOwn(keys, alias) || Object.hasOwn(keys, key)) continue;
			keys[key] = keys[alias];
			delete keys[alias];
			renamed.add(`${alias} to ${key}`);
		}
	}
	if (renamed.size) notes.push(`Code renamed methodology ${[...renamed].join(' and ')} in data/investigation-log.json.`);
}

// B23(a) (K5-3: claims and verdicts in one file, 9 rewrites without clearing it): contract 1.1 has one list, claims. A row moves unchanged, and only
// with a finding_id no other row of this write has; any other row stays in verdicts for the validators to name. Moved rows meet every claim check.
function moveVerdicts(document: Record<string, unknown>, notes: string[]): void {
	const { verdicts, claims = [] } = document;
	if (!Array.isArray(verdicts) || !Array.isArray(claims)) return;
	const idOf = (row: unknown) => (row as { finding_id?: unknown } | null)?.finding_id;
	const ids = [...claims, ...verdicts].map(idOf);
	const distinct = (row: unknown) => typeof idOf(row) === 'string' && ids.filter((id) => id === idOf(row)).length === 1;
	const moved = verdicts.filter(distinct);
	if (moved.length === 0) return;
	const left = verdicts.filter((row) => !distinct(row));
	document.claims = [...claims, ...moved];
	if (left.length) document.verdicts = left;
	else delete document.verdicts;
	notes.push(`Code moved ${moved.map(idOf).join(', ')} from verdicts to claims, unchanged: claims is the file's one list.${left.length ? ` ${left.length} row(s) stayed in verdicts: their finding_id is missing or repeated.` : ''}`);
}

// A merge keeps the file's other fields, so a verdicts list already in the file outlives a write that leaves it out, and claims alone can never clear it
// (Codex P2). A row whose finding now has a claim, or that names no finding, counted for nothing and goes; a row awaiting its claim stays: moving it here
// could refuse an unrelated write.
function dropAnsweredVerdicts(merged: string, written: string, notes: string[]): string {
	let document: Record<string, unknown>;
	try {
		if (Object.hasOwn(JSON.parse(written), 'verdicts')) return merged;
		document = JSON.parse(merged);
	} catch {
		return merged;
	}
	const { verdicts, claims } = document;
	if (!Array.isArray(verdicts) || !Array.isArray(claims)) return merged;
	const answered = new Set(claims.map((claim) => claim?.finding_id).filter((id) => typeof id === 'string'));
	const left = verdicts.filter((row) => typeof row?.finding_id === 'string' && !answered.has(row.finding_id));
	if (left.length === verdicts.length) return merged;
	if (left.length) document.verdicts = left;
	else delete document.verdicts;
	notes.push(`Code removed ${verdicts.length - left.length} old row(s) from verdicts: their findings have claims, or they name none.`);
	return `${JSON.stringify(document, null, 2)}\n`;
}

// D10 (K3, K4, K5, qwen-65k.md: verified findings held at Investigation for want of a direction link). A research case's findings must name their direction, and
// code knows it in two cases: a split worker's own direction, for the findings it owns, and the only direction of a one-direction plan. It adds that ref and reads a
// bare direction id as that ref. Only findings in this write count: an amendment renumbers directions, so a D2- id from an older plan proves nothing (Codex P2), and an
// id carrying a tag is never claimed by an untagged worker. Anything else stays the worker's: a pursued clue's ref, another direction's, every uncertain case.
function fillResearchRefs(context: Pick<ToolContext, 'caseDir' | 'env'>, written: string, merged: string, notes: string[]): string {
	if (!localTier(context.env)) return merged;
	let document: { findings?: unknown };
	let researched: unknown;
	let submitted: Set<unknown>;
	try {
		document = JSON.parse(merged);
		const rows = JSON.parse(written).findings;
		submitted = new Set((Array.isArray(rows) ? rows : []).map((row) => row?.id));
		researched = JSON.parse(readFileSync(join(context.caseDir, 'data', 'methodology.json'), 'utf8')).research_plan;
	} catch {
		return merged;
	}
	if (!researched || !Array.isArray(document?.findings)) return merged;
	const tag = context.env?.SPOTLIGHT_DIRECTION;
	const directions = planDirections(context.caseDir);
	const owner = tag ? directions.find((direction) => direction.tag === tag) : directions.length === 1 ? directions[0] : undefined;
	if (!owner) return merged;
	const bare = owner.ref.slice('direction:'.length);
	const changed: string[] = [];
	for (const finding of document.findings as Record<string, unknown>[]) {
		if (!finding || typeof finding !== 'object' || !submitted.has(finding.id) || (tag ? !String(finding.id).startsWith(`${tag}-`) : /^D\d+-/.test(String(finding.id)))) continue;
		const refs = Array.isArray(finding.research_refs) ? finding.research_refs : [];
		const next = [...new Set([...refs.map((ref) => (ref === bare ? owner.ref : ref)), owner.ref])];
		if (isDeepStrictEqual(next, refs)) continue;
		finding.research_refs = next;
		changed.push(String(finding.id));
	}
	if (changed.length === 0) return merged;
	notes.push(`Code set research_refs of ${changed.join(', ')} to ${owner.ref}: the findings of this direction.`);
	return `${JSON.stringify(document, null, 2)}\n`;
}

// B14, B23(c) (K2-13: rejected for one top-level "model"; K5-4: bare strings, then {id, text, source_ref}). Only that field goes, never another unknown one.
// A selection becomes {expression_id} when it names one passage on record; the quote's text always comes from that passage, so a typed copy is dropped.
function repairDraft(caseDir: string, document: Record<string, unknown>, notes: string[]): void {
	if (Object.hasOwn(document, 'model')) {
		delete document.model;
		notes.push('Code dropped the top-level model field, which a report draft does not have.');
	}
	let expressions: unknown;
	try {
		expressions = JSON.parse(readFileSync(join(caseDir, 'data', 'source-expressions.json'), 'utf8')).expressions;
	} catch {
		return;
	}
	const known = new Set((Array.isArray(expressions) ? expressions : []).map((row) => row?.id).filter((id) => typeof id === 'string'));
	const named = (selection: unknown): unknown => {
		if (typeof selection === 'string' || !selection || typeof selection !== 'object') return selection;
		const { id, expression_id: expression } = selection as { id?: unknown; expression_id?: unknown };
		return id !== undefined && expression !== undefined && id !== expression ? undefined : expression ?? id;
	};
	for (const treatment of Array.isArray(document.finding_treatments) ? document.finding_treatments : []) {
		const selections: unknown = treatment?.quote_selections;
		if (!Array.isArray(selections)) continue;
		const repaired = selections.map((selection) => (known.has(named(selection)) ? { expression_id: named(selection) } : selection));
		const changed = repaired.filter((selection, index) => !isDeepStrictEqual(selection, selections[index]));
		if (changed.length === 0) continue;
		treatment.quote_selections = repaired;
		notes.push(`Code set ${String(treatment.finding_id)}'s quote_selections to ${changed.map((selection) => selection.expression_id).join(', ')}, each an expression_id on record; the quote's text comes from its passage.`);
	}
}

// The shapes of a worker's whole-file write that have one meaning, repaired before the write checks run; `notes` says what changed, for the reply.
function repairPaperwork(caseDir: string, target: string, content: string, env: Record<string, string | undefined> | undefined, notes: string[]): string {
	const name = caseRelative(caseDir, target);
	if ((name !== 'data/fact-check.json' && name !== 'data/report-draft.json') || !localTier(env)) return content;
	let document: Record<string, unknown>;
	try { document = JSON.parse(content); } catch { return content; }
	if (!document || typeof document !== 'object' || Array.isArray(document)) return content;
	if (name === 'data/fact-check.json') moveVerdicts(document, notes);
	else repairDraft(caseDir, document, notes);
	return notes.length ? `${JSON.stringify(document, null, 2)}\n` : content;
}

function normalizeArtifactPaths(caseDir: string, target: string, content: string, scope?: CaseScope): string {
	const name = caseRelative(caseDir, target);
	if (name !== 'data/findings.json' && name !== 'data/evidence-bundle.json' && name !== 'data/fact-check.json') return content;
	let document: Record<string, unknown>;
	try { document = JSON.parse(content); } catch { return content; }
	if (!document || typeof document !== 'object' || Array.isArray(document)) return content;
	let changed = false;
	const paths = (row: unknown, fields: string[]) => {
		if (!row || typeof row !== 'object' || Array.isArray(row)) return;
		const value = row as Record<string, unknown>;
		for (const field of fields) {
			const path = value[field];
			if (typeof path !== 'string' || !path.startsWith('{CASE_DIR}/')) continue;
			const suffix = path.slice('{CASE_DIR}/'.length);
			if (!suffix || suffix.split(/[\\/]/).some((part) => !part || part === '.' || part === '..')) continue;
			try {
				const file = readTarget(caseDir, path, scope);
				if (!inside(caseDir, file) || !lstatSync(file).isFile()) continue;
				value[field] = relative(caseDir, file).split(sep).join('/');
				changed = true;
			} catch (error) {
				if (error instanceof FileRefused || (error as NodeJS.ErrnoException).code === 'ENOENT' || (error instanceof Error && error.message.startsWith('not found:'))) continue;
				throw error;
			}
		}
	};
	const rows = (value: unknown) => Array.isArray(value) ? value : [];
	if (name === 'data/findings.json') {
		for (const finding of rows(document.findings)) for (const source of rows(finding?.sources)) paths(source, ['local_file']);
	} else if (name === 'data/fact-check.json') {
		for (const claim of rows(document.claims)) {
			for (const item of [...rows(claim?.evidence_for), ...rows(claim?.evidence_against)]) {
				paths(item, ['local_file']);
				paths(item?.source_ref, ['path']);
			}
		}
	} else {
		for (const item of rows(document.items)) {
			paths(item, ['raw_path', 'screenshot_path', 'downloaded_document_path']);
			for (const derivative of rows(item?.text_derivatives)) paths(derivative, ['path']);
		}
	}
	return changed ? JSON.stringify(document, null, 2) : content;
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
		if (!pointer.startsWith('/')) throw new Error(`read_file: JSON pointer ${JSON.stringify(pointer)} must start with '/', for example /findings/0`);
		for (const encoded of pointer.slice(1).split('/')) {
			const token = encoded.replaceAll('~1', '/').replaceAll('~0', '~');
			if (current !== null && typeof current === 'object' && !Array.isArray(current) && Object.hasOwn(current, token)) {
				current = (current as Record<string, unknown>)[token];
			} else if (Array.isArray(current) && /^[0-9]+$/.test(token) && Number(token) < current.length) {
				current = current[Number(token)];
			} else {
				throw new Error(`read_file: JSON pointer ${JSON.stringify(pointer)} does not resolve at ${JSON.stringify(token)} in ${path}. Read a shorter pointer to see the keys at that level.`);
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
	if (!inside(caseDir, target)) throw new FileRefused(`refused: ${path} is outside the case files this agent may read. Give a path relative to the case folder, such as data/findings.json.`);
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

// A held lock is a folder outside the case, so no worker lists it and no case artifact changes. Waiting is bounded and never refuses: after MERGE_WAIT_MS the write
// goes ahead as it did before this lock, and a lock left by a dead worker is taken over after MERGE_LOCK_STALE_MS.
const MERGE_WAIT_MS = 5_000;
const MERGE_LOCK_STALE_MS = 30_000;
export async function oneMergeAtATime<T>(target: string, job: () => Promise<T>): Promise<T> {
	const root = join(tmpdir(), 'spotlight-merge-locks');
	try {
		mkdirSync(root, { recursive: true });
	} catch {
		return job();
	}
	const lock = join(root, createHash('sha256').update(target).digest('hex'));
	const deadline = Date.now() + MERGE_WAIT_MS;
	let held = false;
	while (!held && Date.now() < deadline) {
		try {
			mkdirSync(lock);
			held = true;
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== 'EEXIST') break;
			const age = Date.now() - (lstatOrUndefined(lock)?.mtimeMs ?? Date.now());
			if (age > MERGE_LOCK_STALE_MS) rmSync(lock, { recursive: true, force: true });
			else await new Promise((wake) => setTimeout(wake, 5));
		}
	}
	try {
		return await job();
	} finally {
		if (held) rmSync(lock, { recursive: true, force: true });
	}
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
	// B24: the receipt fields code wrote stay through a worker's bundle merge (keepReceiptFields); keptReceipt says which, for the reply.
	let keptReceipt: string[] = [];
	let filledReceipt: string[] = [];
	const whole = (target: string, content: string, exactEdit = false) => {
		keptReceipt = [];
		filledReceipt = [];
		const merged = mergedWrite(target, content, exactEdit);
		if (caseRelative(caseDir, target) === 'data/fact-check.json' && localTier(context.env)) return dropAnsweredVerdicts(merged, content, filledReceipt);
		if (caseRelative(caseDir, target) === 'data/findings.json' && role === 'investigator') return fillResearchRefs(context, content, merged, filledReceipt);
		if (caseRelative(caseDir, target) !== 'data/evidence-bundle.json') return merged;
		const { text, kept, filled } = keepReceiptFields(context, currentText(target), merged);
		keptReceipt = kept;
		filledReceipt = filled;
		return text;
	};
	const mergedWrite = (target: string, content: string, exactEdit: boolean) => {
		const name = split(target);
		if (name) return mergeDirection(name, currentText(target), content, direction!);
		const relative = caseRelative(caseDir, target);
		if (!mergesByKey(role, relative)) return content;
		const current = currentText(target);
		if (exactEdit && removesAnonymousRows(role!, relative, current, content)) return content;
		return mergeByKey(role!, relative, current, content);
	};
	const receiptNote = () => `${keptReceipt.length ? ` Code kept ${keptReceipt.join('; ')} as the fetch receipt of the capture has them: recorded passages are checked against them.` : ''}${filledReceipt.map((note) => ` ${note}`).join('')}`;
	const repairNote = (notes: string[]) => notes.map((note) => ` ${note}`).join('');
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
	const assignedWrites = new Set<string>();
	const nextToCheck = (wrote: string, written: string) => {
		if (role !== 'fact-checker' || wrote !== 'data/fact-check.json') return '';
		// #274: a batch fact-checker finishes once its findings have claims; the
		// launcher starts the next batch with a fresh worker.
		const batch = context.env?.SPOTLIGHT_FC_BATCH?.split(',').filter(Boolean);
		if (!batch?.length) return `\n${factCheckChecklist(readCheckDocs(caseDir))}`;
		for (const claim of (JSON.parse(written) as { claims?: Array<{ finding_id?: string }> }).claims ?? []) {
			if (typeof claim?.finding_id === 'string' && batch.includes(claim.finding_id)) assignedWrites.add(claim.finding_id);
		}
		const claimed = new Set((((readCheckDocs(caseDir).factCheck ?? {}) as { claims?: Array<{ finding_id?: unknown }> }).claims ?? []).map((claim) => claim?.finding_id));
		const open = batch.filter((id) => !claimed.has(id) || !assignedWrites.has(id));
		return open.length === 0 ? `\nEvery finding in your batch has a claim (${batch.join(', ')}). Batch validation is pending.` : `\nStill awaiting an accepted claim write in your batch: ${open.join(', ')}. Write their claims, then finish.`;
	};
	// fc-stall scan (incr-test3 sp98 seq 35): a fact-check write that added no
	// claim still read "wrote data/fact-check.json", and the count stayed put.
	const claimsAdded = (wrote: string, before: string | undefined, written: string): string => {
		if (wrote !== 'data/fact-check.json') return '';
		const ids = (text: string | undefined) => {
			try {
				const claims = (JSON.parse(text ?? '{}') as { claims?: unknown }).claims;
				return Array.isArray(claims) ? claims.map((claim) => (claim as { finding_id?: unknown } | null)?.finding_id) : [];
			} catch {
				return [];
			}
		};
		const sent = ids(written);
		if (sent.length === 0) return '';
		const had = new Set(ids(before));
		const findingRows = (readCheckDocs(caseDir).findings as { findings?: unknown } | undefined)?.findings;
		const findings = new Set(Array.isArray(findingRows) ? findingRows.map((row) => (row as { id?: unknown } | null)?.id) : []);
		if (sent.some((id) => typeof id === 'string' && !had.has(id) && findings.has(id))) return '';
		const replaced = sent.filter((id): id is string => typeof id === 'string' && had.has(id));
		const unknown = sent.filter((id) => typeof id !== 'string' || !findings.has(id));
		const why = [
			...(replaced.length ? [`${replaced.join(', ')} already had a claim, which this write replaced`] : []),
			...(unknown.length ? [`${unknown.map(String).join(', ')} is not a finding id in data/findings.json`] : []),
		].join('; ');
		return ` (0 claims added: ${why || 'no claim had a finding_id'})`;
	};
	const tools: ToolDefinition[] = [
		{
			name: 'read_file',
			label: 'Read file',
			description:
				'Read a text file in the active case (path relative to the case folder, or absolute inside it), or a Spotlight schema under the checkout schemas/ folder or agent instruction under agents/references/. Optional 1-based line offset and line limit; lines are counted as the evidence validators count them, so they serve as source_ref line_start/line_end. Optional json_pointer (RFC 6901, for a JSON file) returns exactly the text the validators select for a source_ref json_pointer: a string value as is, anything else as compact sorted JSON.',
			parameters: readParams,
			async execute(_id, params) {
				const input = checkedParams<{ path: string; offset?: number; limit?: number; json_pointer?: string }>('read_file', readParams, params);
				if (input.json_pointer !== undefined && (input.offset || input.limit)) {
					throw new ToolArgumentError('read_file: use either json_pointer or offset/limit');
				}
				const source = input.path === passages && keptPassages(passages) ? passages : readTarget(caseDir, input.path, scope);
				const text = shown(source, readFileSync(source, 'utf8'));
				const index = role !== ORCHESTRATOR && input.json_pointer === undefined && !input.offset && !input.limit ? readIndex(caseRelative(caseDir, source), text) : undefined;
				if (index) return textResult(index);
				let selected = text;
				if (input.json_pointer !== undefined) {
					selected = selectJsonPointer(text, input.json_pointer, input.path);
				} else if (input.offset || input.limit) {
					const from = (input.offset ?? 1) - 1;
					selected = sourceLines(text).slice(from, from + (input.limit ?? Number.MAX_SAFE_INTEGER)).join('\n');
				}
				const page = input.json_pointer === undefined ? readPageChars() : undefined;
				if (page && selected.length > page) {
					const all = sourceLines(text);
					const first = input.offset ?? 1;
					return textResult(readPage(all.slice(first - 1, input.limit ? first - 1 + input.limit : undefined), first, all.length, page, input.limit));
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
				'Create or overwrite a file in the active case (path relative to the case folder). Exception: fact-checker writes to data/fact-check.json merge claims by finding_id; omitted entries stay. For that file, write only this batch’s new or corrected claims, not the existing file. Investigator writes to data/findings.json and investigator/fact-checker writes to data/evidence-bundle.json likewise merge entries by id. The harness supplies a stable local bundle run_id when absent. For activated findings and fact-checks, omit schema_version to use the active contract; an explicit incompatible version is refused. The harness supplies the durable project and new planning/bundle creation timestamps, preserving historical creation times. You supply claims, judgments and evidence. Parent folders are created. Refused outside your folder, through symlinks, for a folder path, for resolver, review and receipt files, and for empty content over an existing non-empty file.',
			parameters: writeParams,
			executionMode: 'sequential',
			prepareArguments: (args: unknown) => {
				if (args && typeof args === 'object' && !Array.isArray(args) && !Object.hasOwn(args, 'path')) {
					// Pi's schema error otherwise repeats the entire proposed file,
					// inflating the context while the model tries to add one field.
					throw new ToolArgumentError('write_file: path is required alongside content. Reissue the call with the case-relative destination named in your task, using {"path":"...","content":"..."}. No file was written.');
				}
				return args;
			},
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
				const notes: string[] = [];
				const written = repairPaperwork(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), input.content, context.env, notes);
				refuseDriftedClaims(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), written, true);
				const normalized = normalizeArtifactPaths(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), written, scope);
				const destination = writeTarget(caseDir, writeRoot, input.path, writes, denyWrites);
				const content = stampFactCheckerRecords(context, destination, artifactEnvelope(caseDir, destination, normalized, whole(destination, normalized), context.env, notes), role);
				refuseCitedWithdrawal(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), input.content);
				refuseMisplacedBrief(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseUnactivatedStart(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseRecordedFramework(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				refuseNonObjectData(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUncitedVerdicts(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseLeadsEvidence(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseMissingCapture(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content, context.env);
				refuseBundleRewrites(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content, role);
				refuseDriftedClaims(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseMissingCitedFiles(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUnknownFlags(caseDir, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUnanchoredVerdicts(context, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				refuseUnacquiredArchiveDocument(context, destination, content);
				await refuseInvalidProposal(context, writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), content);
				const before = currentText(writeTarget(caseDir, writeRoot, input.path, writes, denyWrites));
				const wrote = writeConfined(caseDir, writeRoot, input.path, content, writes, denyWrites);
				return textResult(`wrote ${wrote}${claimsAdded(wrote, before, written)}${keptNote(writeTarget(caseDir, writeRoot, input.path, writes, denyWrites), written, content)}${receiptNote()}${repairNote(notes)}${nextToCheck(wrote, written)}`);
			},
		},
		{
			name: 'edit_file',
			label: 'Edit file',
			description: 'Replace one exact, unique occurrence of old_text with new_text in a case file. Same role and path limits as write_file. For managed findings and evidence lists, an unscoped authorized writer can explicitly remove malformed rows without a valid id, keeping all keyed records, their order, and file metadata unchanged. Omission in write_file still keeps existing rows.',
			parameters: editParams,
			executionMode: 'sequential',
			async execute(_id, params) {
				const input = checkedParams<{ path: string; old_text: string; new_text: string }>('edit_file', editParams, params);
				const target = writeTarget(caseDir, writeRoot, input.path, writes, denyWrites);
				// Editing reads the file, so a file hidden from this role is refused.
				if (denied(scope?.denyReads, caseRelative(caseDir, target))) {
					throw new FileRefused(`refused: ${input.path} is outside the case files this agent may read. Give a path relative to the case folder, such as data/findings.json.`);
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
				const notes: string[] = [];
				const replacement = repairPaperwork(caseDir, target, text.slice(0, first) + input.new_text + text.slice(first + input.old_text.length), context.env, notes);
				const updated = stampFactCheckerRecords(context, target, artifactEnvelope(caseDir, target, replacement, whole(target, replacement, true), context.env, notes), role);
				refuseRecordedFramework(caseDir, target);
				refuseNonObjectData(caseDir, target, updated);
				refuseUncitedVerdicts(caseDir, target, updated);
				refuseLeadsEvidence(caseDir, target, updated);
				refuseMissingCapture(caseDir, target, updated, context.env);
				refuseBundleRewrites(caseDir, target, updated, role);
				refuseDriftedClaims(caseDir, target, updated);
				refuseMissingCitedFiles(caseDir, target, updated);
				refuseUnknownFlags(caseDir, target, updated);
				refuseUnanchoredVerdicts(context, target, updated);
				refuseUnacquiredArchiveDocument(context, target, updated);
				await refuseInvalidProposal(context, target, updated);
				return textResult(`edited ${writeConfined(caseDir, writeRoot, input.path, updated, writes, denyWrites)}${receiptNote()}${repairNote(notes)}`);
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
					throw new FileRefused('refused: list_files patterns are relative to the case and cannot contain "..". To read a schema, use read_file with schemas/<name>.schema.json.');
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
				'Search case files for a regular expression. Optional path (file or folder, relative to the case, or one schema under schemas/) narrows the search. Returns path:line:text, with lines counted as read_file and the evidence validators count them.',
			parameters: grepParams,
			async execute(_id, params) {
				const input = checkedParams<{ pattern: string; path?: string; ignore_case?: boolean }>('grep_files', grepParams, params);
				let expression: RegExp;
				// fc-stall scan: a leading (?i) (87 hits, investigator and fact-checker)
				// was refused as an invalid regex; it is the ignore-case flag.
				const inline = /^\(\?i\)/.test(input.pattern);
				try {
					expression = new RegExp(inline ? input.pattern.slice(4) : input.pattern, input.ignore_case || inline ? 'i' : '');
				} catch (error) {
					throw new Error(`grep_files: invalid regular expression: ${(error as Error).message}. The pattern is a regular expression: for literal text, put a backslash before [ ] ( ) . * + ? or search a shorter plain word.`);
				}
				// A narrowed role may name a folder; only its files in scope are searched.
				const start = input.path ? (reads ? scopedFolderOrFile(caseDir, input.path, scope) : readTarget(caseDir, input.path, scope)) : caseDir;
				const matches: string[] = [];
				// fc-stall scan: greps of a schema the task names were refused as
				// outside the case (24 hits, every role); read_file reads them, so
				// grep_files searches one schema file too.
				const schema = input.path !== undefined && /^schemas[\\/][^\\/]+\.json$/.test(input.path) && start.startsWith(resolve(CHECKOUT_ROOT, 'schemas'));
				if (schema) {
					const lines = sourceLines(readFileSync(start, 'utf8'));
					for (let index = 0; index < lines.length && matches.length < MAX_MATCHES; index += 1) {
						if (expression.test(lines[index])) matches.push(`${input.path}:${index + 1}:${lines[index].slice(0, 400)}`);
					}
					return textResult(matches.length ? matches.join('\n') : 'no matches');
				}
				if (!inside(caseDir, start)) throw new FileRefused(`refused: ${input.path} is outside the case.${/^schemas[\\/]?$/.test(input.path ?? '') ? ' To read a schema, use read_file with schemas/<name>.schema.json.' : ''}`);
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
				// Replay check on 8699f11d: one path given as a string was refused.
				const given = params as { paths?: unknown };
				const { paths } = checkedParams<{ paths: string[] }>('hash_files', hashParams, typeof given?.paths === 'string' ? { ...given, paths: [given.paths] } : params);
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
	// Parallel direction workers are separate processes that merge into one file: one merge at a time, or a write built on a file another worker is replacing
	// loses that worker's entries or fails on a half-written file ('parallel explicitly scoped tasks', 2 of 5 runs on dcb40e08).
	for (const tool of tools.filter(({ name }) => name === 'write_file' || name === 'edit_file')) {
		const run = tool.execute;
		tool.execute = (id, params, ...rest) => {
			let target: string | undefined;
			try {
				const path = (params as { path?: unknown }).path;
				target = typeof path === 'string' ? writeTarget(caseDir, writeRoot, path, writes, denyWrites) : undefined;
			} catch {
				// The tool itself refuses this path, with its reason.
			}
			const merges = target !== undefined && (!!split(target) || mergesByKey(role, caseRelative(caseDir, target)));
			return merges ? oneMergeAtATime(target!, () => run(id, params, ...rest)) : run(id, params, ...rest);
		};
	}
	return tools;
}

// Canonical write identity shared by budget/completion hooks and the worker's
// rejected-proposal carry. Typed records retain the file boundary's receipts.
export function canonicalWriteCall(tool: string, args: unknown): { path: string; content?: string } | undefined {
	const input = args as Record<string, unknown> | undefined;
	if (!input || typeof input !== 'object') return undefined;
	if (tool === 'write_file' || tool === 'edit_file') {
		return typeof input.path === 'string'
			? { path: input.path, ...(typeof input.content === 'string' ? { content: input.content } : {}) }
			: undefined;
	}
	// judge_finding writes the fact-check through record_assessment; its content is code's.
	if (tool === 'judge_finding') return { path: 'data/fact-check.json' };
	const key = tool === 'record_finding' ? 'finding' : tool === 'record_assessment' ? 'assessment' : undefined;
	if (!key || !input[key] || typeof input[key] !== 'object' || Array.isArray(input[key])) return undefined;
	return {
		path: key === 'finding' ? 'data/findings.json' : 'data/fact-check.json',
		content: JSON.stringify({ [key === 'finding' ? 'findings' : 'claims']: [input[key]] }),
	};
}

type RecordRow = Record<string, unknown>;
const recordRows = (value: unknown): RecordRow[] => Array.isArray(value)
	? value.filter((row): row is RecordRow => !!row && typeof row === 'object' && !Array.isArray(row))
	: [];

// Project the canonical row schema into a structured tool parameter, rather
// than maintaining a second taxonomy. Local references are inlined because
// this row is now the top-level tool schema, not the original document.
function recordSchema(file: string, key: string, omitted: string[], added: string[] = [], cite = false): ReturnType<typeof Type.Object> {
	const doc = JSON.parse(readFileSync(join(SCHEMAS_DIR, file), 'utf8'));
	const expand = (value: unknown): unknown => {
		if (Array.isArray(value)) return value.map(expand);
		if (!value || typeof value !== 'object') return value;
		const row = value as RecordRow;
		if (typeof row.$ref === 'string' && row.$ref.startsWith('#/')) {
			const ref = row.$ref.slice(2).split('/').reduce((at: RecordRow, part) => at[part] as RecordRow, doc);
			const expanded = expand(ref) as RecordRow;
			// Tools accept the same shorthand as the canonical writer. The
			// worker validator resolves it to full immutable refs before the
			// persistent schema is checked; supplied fingerprints still must
			// have the canonical shape and match the selected relation.
			return row.$ref === '#/definitions/source_expression_ref'
				? { anyOf: [{ type: 'string', minLength: 1 }, { ...expanded, required: ['expression_id'] }] }
				: expanded;
		}
		const expanded = Object.fromEntries(Object.entries(row).map(([name, child]) => [name, expand(child)]));
		if (!Array.isArray(expanded.type)) return expanded;
		// Gemma's native tool template expects a scalar type. A JSON Schema
		// type array is the same union expressed as anyOf, including null;
		// preserve sibling constraints and any pre-existing union unchanged.
		const { type, ...constraints } = expanded;
		const union = { anyOf: (type as unknown[]).map((item) => ({ type: item })) };
		return constraints.anyOf === undefined
			? { ...constraints, ...union }
			: { ...constraints, allOf: [...(Array.isArray(constraints.allOf) ? constraints.allOf : []), union] };
	};
	const row = expand(doc.properties[key].items) as { required: string[]; allOf?: unknown };
	row.required = [...row.required.filter((name) => !omitted.includes(name)), ...added.filter((name) => !row.required.includes(name))];
	// Positive anchors are completed by quote resolution, then checked by the
	// existing strict write and final validators. A real exact-quote proposal
	// must reach that production step before its references can be validated.
	delete row.allOf;
	return Type.Object({ [key === 'findings' ? 'finding' : 'assessment']: Type.Unsafe(row), ...(cite ? { cite: CITE, support_type: Type.Optional(Type.Union([Type.Literal('direct'), Type.Literal('indirect')], { description: 'How the cited lines support the claim; direct unless you say indirect.' })) } : {}) }, { additionalProperties: false });
}

function recordDocument(context: ToolContext, path: string): RecordRow {
	if (!existsSync(join(context.caseDir, path))) return {};
	return JSON.parse(readFileSync(readTarget(context.caseDir, path, context.scope), 'utf8')) as RecordRow;
}

const recordCitations = (row: RecordRow | undefined): RecordRow[] =>
	['sources', 'evidence_for', 'evidence_against'].flatMap((field) => recordRows(row?.[field]));

function refuseUnacquiredArchiveCitation(context: Parameters<typeof caseFileTools>[0], source: RecordRow, previous: RecordRow[], tool: string): void {
	if (typeof source.archive_url !== 'string' || !source.archive_url.trim()) return;
	// Historical/manual records stay readable and editable only with their
	// original archive identity; changing the capture needs a real receipt.
	const identity = ['archive_url', 'url', 'source', 'source_url', 'local_file', 'source_ref', 'evidence_bundle_id', 'raw_path', 'downloaded_document_path', 'screenshot_path', 'path', 'sha256', 'text_derivatives'];
	if (previous.some((old) => identity.every((key) => isDeepStrictEqual(old[key], source[key])))) return;
	const ref = source.source_ref as RecordRow | undefined;
	const path = [source.local_file, ref?.path, source.raw_path, source.downloaded_document_path, source.screenshot_path, source.path].find((value): value is string => typeof value === 'string');
	// fetchedFile reads only the case directory and optional runtime env.
	const capture = path ? fetchedFile(context as ToolContext, path) : undefined;
	if (path && context.env?.SPOTLIGHT_ROLE && capture?.url === source.archive_url && capture.acquired_by === context.env.SPOTLIGHT_ROLE) {
		const bytes = readFileSync(readTarget(context.caseDir, path, context.scope));
		if (createHash('sha256').update(bytes).digest('hex') === capture.sha256) return;
	}
	throw new FileRefused(`${tool}: archive_url needs an actual acquired archive capture with its matching local_file/source_ref. Omit it when no capture exists; the artifact is unchanged.`);
}

// Every model-accessible canonical write, including the generic file tools,
// checks new archive metadata before committing. Otherwise a generic write
// could turn an invented URL into a supposedly historical typed record.
function refuseUnacquiredArchiveDocument(context: Parameters<typeof caseFileTools>[0], target: string, content: string): void {
	const name = caseRelative(context.caseDir, target);
	const list = name === 'data/findings.json' ? 'findings' : name === 'data/fact-check.json' ? 'claims' : name === 'data/evidence-bundle.json' ? 'items' : undefined;
	if (!list) return;
	const current = currentText(target);
	let previous: RecordRow[] = [];
	if (current) {
		try { previous = recordRows((JSON.parse(current) as RecordRow)[list]); }
		catch (error) {
			// Broken JSON supplies no trusted historical citation identity, but
			// must not prevent a valid replacement without archive metadata.
			if (!(error instanceof SyntaxError)) throw error;
		}
	}
	const proposed = recordRows((JSON.parse(content) as RecordRow)[list]);
	const key = list === 'claims' ? 'finding_id' : 'id';
	for (const row of proposed) {
		const old = typeof row[key] === 'string' ? previous.find((candidate) => candidate[key] === row[key]) : undefined;
		const oldCitations = list === 'items' ? old ? [old] : [] : recordCitations(old);
		for (const source of list === 'items' ? [row] : recordCitations(row)) {
			refuseUnacquiredArchiveCitation(context, source, oldCitations, name);
		}
	}
}

// fz2-fix9 seq 58/59 (Gemma 26B, batch D1-F1, D1-F2): finding_id was right
// inside assessment and repeated beside it, and the copy beside it was refused
// with raw schema text ("root: must not have additional properties"), so the
// fact-checker wrote nothing. A top-level finding_id is folded into the
// assessment: the same id is dropped, a missing one moves in, and a different
// one is refused naming both. A one-finding batch's schema has no finding_id
// (code fills it), so there the folded id must be the batch's and is dropped.
// It runs as the tool's prepareArguments: Pi checks the schema before execute.
function foldFindingId(given: unknown, batch: string[], filled: string | undefined): unknown {
	if (!given || typeof given !== 'object') return given;
	const outer = { ...(given as Record<string, unknown>) };
	const inner = outer.assessment && typeof outer.assessment === 'object' && !Array.isArray(outer.assessment) ? { ...(outer.assessment as Record<string, unknown>) } : undefined;
	if (!inner || outer.finding_id === undefined) return given;
	const yours = batch.length ? ` Your batch: ${batch.join(', ')}.` : '';
	if (inner.finding_id !== undefined && inner.finding_id !== outer.finding_id) {
		throw new ToolArgumentError(`record_assessment: finding_id ${JSON.stringify(outer.finding_id)} beside assessment differs from ${JSON.stringify(inner.finding_id)} inside it; give finding_id once, inside assessment.${yours}`);
	}
	inner.finding_id = outer.finding_id;
	delete outer.finding_id;
	if (filled) {
		if (inner.finding_id !== filled) throw new ToolArgumentError(`record_assessment: your batch holds ${filled} only; omit finding_id or use ${filled}.`);
		delete inner.finding_id;
	}
	outer.assessment = inner;
	return outer;
}

// step2-design.md section 2 (TUNE W1, W2): the investigator states the claim
// and cites the lines of a capture; code copies their exact text and keeps the
// books (sources, evidence item, passage). A cite is checked whole before any
// write. Code fills only what the cite determines: the evidence text (Tom,
// 2026-11-03: the cited quote text) and the sources from the capture's receipt;
// never the claim, confidence or grounding.
type Cite = { path: string; line_start: number; line_end: number };
type ResolvedCite = Cite & { text: string; url: string; accessed: string; item?: RecordRow; capture: NonNullable<ReturnType<typeof registeredCapture>> };
const CITE = Type.Optional(Type.Array(Type.Object({
	path: Type.String({ minLength: 1 }),
	line_start: Type.Integer({ minimum: 1 }),
	line_end: Type.Integer({ minimum: 1 }),
}, { additionalProperties: false }), { minItems: 1, maxItems: 10 }));

function bundleFiles(item: RecordRow): unknown[] {
	return [...['raw_path', 'downloaded_document_path', 'screenshot_path', 'path'].map((key) => item[key]), ...recordRows(item.text_derivatives).map((entry) => entry.path)];
}

function resolveCites(context: ToolContext, cites: Cite[]): ResolvedCite[] {
	const bundle = recordRows(recordDocument(context, 'data/evidence-bundle.json').items);
	return cites.map((cite, index) => {
		const where = `record_finding: cite[${index}] (${cite.path})`;
		let text: string;
		try {
			text = readFileSync(readTarget(context.caseDir, cite.path, context.scope), 'utf8');
		} catch (error) {
			throw new ToolArgumentError(`${where}: ${(error as Error).message} Nothing was written.`);
		}
		let lines = sourceLines(text);
		// fetch's leads file (X) is a short distillation of the page it saved as X.raw. A cite outside X that X.raw holds is the page's lines
		// (qwen real launch: the same refused cite repeated until the worker ended). Local tiers only; frontier gets the refusal.
		let path = cite.path;
		if (cite.line_end > lines.length && cite.line_end >= cite.line_start && localTier(context.env) && registeredCapture(context, `${cite.path}.raw`)) {
			try {
				const raw = sourceLines(readFileSync(readTarget(context.caseDir, `${cite.path}.raw`, context.scope), 'utf8'));
				if (cite.line_end <= raw.length) { lines = raw; path = `${cite.path}.raw`; }
			} catch {
				// No readable .raw: the refusal below stands.
			}
		}
		if (cite.line_end < cite.line_start || cite.line_end > lines.length) throw new ToolArgumentError(`${where}: lines ${cite.line_start}-${cite.line_end} are outside the file (lines 1-${lines.length}); give line_start and line_end inside it, as read_file numbers them. Nothing was written.`);
		const quote = lines.slice(cite.line_start - 1, cite.line_end).join('\n').trim();
		if (!quote) throw new ToolArgumentError(`${where}: lines ${cite.line_start}-${cite.line_end} are empty; cite the lines that hold the text. Nothing was written.`);
		// Provenance only from the investigator's own fetch receipt, never from
		// a bundle row it could write or another role's capture (Codex review of simp-2, P1).
		const capture = registeredCapture(context, path);
		if (!capture || capture.acquired_by !== 'investigator') throw new ToolArgumentError(`${where}: cite works only on a capture your own fetch saved and that is unchanged since; this file is not one. Fetch the source (fetch with save_as under research/) and cite that capture, or record this finding with evidence and sources of your own. Nothing was written.`);
		// Reuse an existing item only when it is this role's and direction's and records
		// this very capture (Codex review of simp-link: a foreign-direction item is
		// refused by the direction merge; a hand-written one may name another source).
		const direction = context.env.SPOTLIGHT_DIRECTION;
		const item = bundle.find((entry) => bundleFiles(entry).includes(path) && entry.created_by !== 'fact-checker'
			&& (!direction || String(entry.id).startsWith(`${direction}-`))
			&& entry.source_url === capture.url && (entry.sha256 === undefined || entry.sha256 === capture.sha256));
		return { ...cite, path, text: quote, url: capture.url, accessed: capture.accessed, item, capture };
	});
}

// Before Pi's schema check: the cite supplies a missing evidence and sources,
// and a model-written source must be one of the cited ones (Codex review of
// the step 2 design, P1: a source is otherwise checked only for its shape).
function applyCites(context: ToolContext, given: unknown): unknown {
	const args = given as { finding?: unknown; cite?: unknown } | undefined;
	if (!args || !Array.isArray(args.cite) || !args.cite.length || !args.finding || typeof args.finding !== 'object' || Array.isArray(args.finding)) return given;
	const cites = args.cite as Cite[];
	if (cites.some((cite) => !cite || typeof cite.path !== 'string' || !Number.isInteger(cite.line_start) || !Number.isInteger(cite.line_end))) return given;
	const resolved = resolveCites(context, cites);
	const finding = { ...(args.finding as RecordRow) };
	// The cited texts, always, as a list with one entry per cite: with a cite
	// the evidence is code's copy of the lines (Tom, C13), never a model-typed
	// value (inv-retest-def8674f.md: evidence came back as an array of single
	// characters; Codex review of simp-review: supplied evidence bypassed this).
	finding.evidence = resolved.map((cite) => cite.text);
	const urls = [...new Set(resolved.map((cite) => cite.url))];
	const stray = recordRows(finding.sources).filter((source) => typeof source.url === 'string' && !urls.includes(source.url));
	if (stray.length) throw new ToolArgumentError(`record_finding: sources ${stray.map((source) => JSON.stringify(source.url)).join(', ')} are not among the cited captures (${urls.join(', ')}); omit sources and code lists the cited ones. Nothing was written.`);
	// The sources are the receipts' (Codex review of simp-2, P2).
	finding.sources = urls.map((url) => {
		const cite = resolved.find((entry) => entry.url === url)!;
		return { url, accessed: cite.accessed, local_file: cite.path };
	});
	return { ...args, finding };
}

// The evidence item for each cited capture that has none, written before the
// finding so its evidence_bundle_refs resolve (the fact-checker's re-fetch
// finds a finding's sources through them).
// inv-retest-def8674f.md: the cited item must also link the finding (its claim
// and how the cited lines support it), or validate-fact-check refuses any
// verdict on it ('no explicit finding link') and judge_finding cannot verify.
type ClaimLink = { finding_id: string; claim_text: string; support_type: 'direct' | 'indirect' | 'inferred' | 'contradicted' | 'insufficient' };
const linked = (item: RecordRow, link: ClaimLink): RecordRow => ({ ...item, claim_links: [...recordRows(item.claim_links).filter((entry) => entry.finding_id !== link.finding_id), link] });
async function citeItems(context: ToolContext, write: ToolDefinition, cites: ResolvedCite[], id: string, signal: AbortSignal | undefined, link: ClaimLink): Promise<{ itemOf: Map<string, string>; added: string[] }> {
	const prefix = context.env.SPOTLIGHT_DIRECTION ? `${context.env.SPOTLIGHT_DIRECTION}-` : '';
	const taken = new Set(recordRows(recordDocument(context, 'data/evidence-bundle.json').items).map((item) => String(item.id)));
	const added: RecordRow[] = [];
	const relinked = new Map<string, RecordRow>();
	const itemOf = new Map<string, string>();
	for (const cite of cites) {
		const existing = cite.item ?? added.find((item) => item.raw_path === cite.path);
		if (existing) {
			itemOf.set(cite.path, String(existing.id));
			if (cite.item) relinked.set(String(cite.item.id), linked(relinked.get(String(cite.item.id)) ?? cite.item, link));
			continue;
		}
		let next = 1;
		while (taken.has(`${prefix}E${next}`)) next += 1;
		const itemId = `${prefix}E${next}`;
		taken.add(itemId);
		const provider = cite.capture?.provider;
		added.push({
			id: itemId,
			created_by: 'investigator',
			query_or_task: `Fetched by the investigator: ${cite.url}`,
			acquisition_method: provider === 'crawl4ai' || provider === 'firecrawl' ? provider : 'other',
			source_url: cite.url,
			accessed: cite.accessed,
			raw_path: cite.path,
			sha256: cite.capture.sha256,
			extraction_confidence: 'medium',
			human_verification_required: false,
			claim_links: [link],
		});
		itemOf.set(cite.path, itemId);
	}
	const items = [...added, ...relinked.values()];
	if (items.length) await write.execute(`${id}-cite-items`, { path: 'data/evidence-bundle.json', content: JSON.stringify({ items }) }, signal, undefined, undefined as never);
	return { itemOf, added: added.map((item) => String(item.id)) };
}

// After the finding is written, the passage for each cite (activated cases),
// through the one passage writer as a harness proposal, so direct_quote may be set.
async function citePassages(context: ToolContext, sourceExpressions: ToolDefinition, finding: RecordRow, cites: ResolvedCite[], itemOf: Map<string, string>, id: string, signal: AbortSignal | undefined): Promise<string> {
	const prefix = context.env.SPOTLIGHT_DIRECTION ? `${context.env.SPOTLIGHT_DIRECTION}-` : '';
	const passages = recordRows(recordDocument(context, 'data/source-expressions.json').expressions);
	const used = new Set(passages.map((passage) => String(passage.id)));
	const language = passages.find((passage) => recordRows(passage.finding_links).some((link) => link.finding_id === finding.id) && typeof passage.language === 'string')?.language ?? 'und';
	let next = 1;
	const expressions = cites.map((cite) => {
		while (used.has(`${prefix}Q${next}`)) next += 1;
		const passageId = `${prefix}Q${next}`;
		used.add(passageId);
		return {
			id: passageId,
			text: cite.text,
			anchor_ref: { path: cite.path, line_start: cite.line_start, line_end: cite.line_end },
			original_evidence_bundle_id: itemOf.get(cite.path),
			language,
			// The source's own words: copied from a capture a harness fetch saved, unchanged since.
			direct_quote: true,
			finding_links: [{ finding_id: finding.id, relation: 'supports' }],
		};
	});
	const cycles = recordRows(recordDocument(context, 'data/investigation-log.json').cycles).length;
	const proposal = { action: 'write', cycle: Math.max(1, cycles), proposal: { expressions } };
	HARNESS_PROPOSALS.add(proposal);
	try {
		await sourceExpressions.execute(`${id}-cite-passages`, proposal, signal, undefined, undefined as never);
	} catch (error) {
		const message = (error as Error).message;
		const repair = olderPassageRepair(context, message, new Set(expressions.map((expression) => expression.id)));
		throw new Error(`record_finding: finding ${String(finding.id)} is recorded, but its cited passage was not: ${message} ${repair ?? 'Call record_finding again with the same claim and a corrected cite, omitting id; the finding keeps its id.'}`);
	}
	return `Passage(s) ${expressions.map((expression) => expression.id).join(', ')} recorded from the cited lines.`;
}

// B25 (K3 Eurostat P3 rerun on 40b77272, seq 39-46): the passage writer validates every recorded passage, so an older passage whose
// evidence item was changed refuses each new cite, and "a corrected cite" sent the model round the same refusal. When every expression
// the writer names is an earlier one, not this call's, the refusal names that passage, its item and the repair instead.
function olderPassageRepair(context: ToolContext, message: string, mine: Set<string>): string | undefined {
	const named = [...new Set([...message.matchAll(/expression '([^']+)'/g)].map((match) => match[1]))];
	if (named.length === 0 || named.some((passageId) => mine.has(passageId))) return undefined;
	const passages = recordRows(recordDocument(context, 'data/source-expressions.json').expressions);
	const items = recordRows(recordDocument(context, 'data/evidence-bundle.json').items);
	const shaFor = new Map<string, Set<string>>();
	for (const passageId of named) {
		const passage = passages.find((row) => String(row.id) === passageId);
		if (passage && typeof passage.original_artifact_sha256 === 'string') shaFor.set(String(passage.original_evidence_bundle_id ?? ''), (shaFor.get(String(passage.original_evidence_bundle_id ?? '')) ?? new Set()).add(passage.original_artifact_sha256));
	}
	const repairs = named.map((passageId) => {
		const passage = passages.find((row) => String(row.id) === passageId);
		const itemId = passage ? String(passage.original_evidence_bundle_id ?? '') : '';
		const item = items.find((row) => String(row.id) === itemId);
		const sha = typeof passage?.original_artifact_sha256 === 'string' ? passage.original_artifact_sha256 : undefined;
		// The hash repair only when the item's file still has the bytes the passage recorded (a harness capture, unchanged since) and the
		// named passages agree on that item's hash; otherwise the writer's own message stands (Codex P2).
		const intact = typeof item?.raw_path === 'string' && registeredCapture(context, item.raw_path)?.sha256 === sha;
		if (item && sha && intact && shaFor.get(itemId)?.size === 1 && message.includes(`expression '${passageId}': original_artifact_sha256 does not match evidence bundle`)) {
			return `recorded passage ${passageId} no longer matches its evidence item ${itemId}, whose sha256 is ${typeof item.sha256 === 'string' ? item.sha256 : 'missing'}; put back "sha256": "${sha}" on item ${itemId} in data/evidence-bundle.json (the hash of ${String(item.raw_path ?? 'its capture')} that ${passageId} recorded) and leave its raw_path as it is`;
		}
		return `recorded passage ${passageId}${itemId ? ` (evidence item ${itemId})` : ''} is the invalid one; repair it as the writer's message says`;
	});
	return `This cite is not the cause: ${repairs.join('; ')}. Then call record_finding again with the same claim and cite, omitting id; the finding keeps its id.`;
}

// step2-design.md section 3 (TUNE W1-W4 for the fact-checker): one question
// per finding. The fact-checker answers supports, partly, doesnt or cant_tell
// with a one-line reason; code writes the claim through record_assessment.
// Verified needs Tom's rule (2026-11-03, plan C8): the harness re-fetch of the
// same source, under the fact-checker's receipt and unchanged since, still
// holds the passage that supports the finding. Otherwise the claim is
// unverified with the reason; partly adds the partial_support flag. A second
// source stays on record_assessment, whose quote path records its passage.
// Confidence is paperwork from the same rule (review-simp.md #1, plan C13):
// medium for a verified row (one source, re-fetched), low for every other
// answer. Without it Ingest's builder refuses the row and the report caps it.
// contradicts (Tom, 2026-11-04): the same re-fetch holds a line that
// contradicts the finding; the fact-checker quotes it and the claim is
// disputed, low, with that line against it. false needs a second source and
// stays on record_assessment.
const ANSWERS = ['supports', 'partly', 'doesnt', 'cant_tell', 'contradicts'] as const;

// The lines of `text` that hold `quote`: as written first, else with spacing
// normalized (the re-check's own comparison), then that window as the file has it.
function locate(text: string, quote: string): { line_start: number; line_end: number; quote: string } | undefined {
	const lines = sourceLines(text);
	const wanted = quote.trim();
	if (!wanted) return undefined;
	const at = lines.join('\n').indexOf(wanted);
	if (at >= 0) {
		const line_start = lines.join('\n').slice(0, at).split('\n').length;
		return { line_start, line_end: line_start + wanted.split('\n').length - 1, quote: wanted };
	}
	// Codex review of simp-3 (P2): a window as long as the passage needs, and more.
	const most = Math.min(lines.length, Math.max(20, 2 * wanted.split('\n').length + 2));
	for (let size = 1; size <= most; size++) {
		for (let start = 0; start + size <= lines.length; start++) {
			const window = lines.slice(start, start + size).join('\n');
			if (flatText(linkText(window)).includes(flatText(wanted))) return { line_start: start + 1, line_end: start + size, quote: window.trim() };
		}
	}
	return undefined;
}

type Recheck = Record<string, { status?: string; acquisition?: { path?: string; url?: string; accessed?: string; sha256?: string } } | string>;
function taskRecheck(context: ToolContext): Recheck {
	const path = context.env.SPOTLIGHT_TASK_RECHECK;
	if (typeof path !== 'string' || !/^data\/harness-checks\/fact-checker\/recheck-[0-9a-f]{64}\.json$/.test(path)) return {};
	try { return JSON.parse(readFileSync(join(context.caseDir, path), 'utf8')) as Recheck; } catch { return {}; }
}

// The finding's supporting passage found in the harness re-fetch of its
// source, as an evidence item quotes.ts records; or the reason there is none.
// The finding's evidence items, by its refs or their claim links.
function findingItems(context: ToolContext, findingId: string): RecordRow[] {
	const finding = recordRows(recordDocument(context, 'data/findings.json').findings).find((row) => row.id === findingId);
	const refs = new Set(Array.isArray(finding?.evidence_bundle_refs) ? finding.evidence_bundle_refs.map(String) : []);
	return recordRows(recordDocument(context, 'data/evidence-bundle.json').items).filter((item) => refs.has(String(item.id)) || recordRows(item.claim_links).some((link) => link.finding_id === findingId));
}

// The harness re-fetch of `url` for this fact-checker, unchanged since and on
// that URL, with its text; or why there is none.
function refetchOf(context: ToolContext, url: string, recheck: Recheck): { path: string; accessed: string; text: string } | { status: string } {
	const check = recheck[url];
	const acquisition = typeof check === 'object' ? check.acquisition : undefined;
	const capture = typeof acquisition?.path === 'string' ? factCheckerCapture(context, acquisition.path) : undefined;
	// The re-fetch must have landed on the item's own URL, not a redirect (Codex review of simp-3, P1).
	if (capture && capture.url !== url) return { status: `${url}: the re-fetch landed on ${capture.url}, not the cited source` };
	if (!capture || capture.sha256 !== acquisition?.sha256) return { status: `${url}: ${typeof check === 'object' && check.status ? check.status : typeof check === 'string' ? check : 'no harness re-fetch'}` };
	try {
		return { path: acquisition!.path!, accessed: capture.accessed, text: readFileSync(readTarget(context.caseDir, acquisition!.path!, context.scope), 'utf8') };
	} catch {
		return { status: `${url}: the re-fetched copy cannot be read` };
	}
}

const itemURL = (item: RecordRow) => typeof item.source_url === 'string' ? item.source_url : typeof item.url === 'string' ? item.url : undefined;

// A finding's source as the case holds it: the harness re-fetch and the capture the item cites.
function sourcePages(context: ToolContext, item: RecordRow, recheck: Recheck): string[] {
	const url = itemURL(item);
	const page = url ? refetchOf(context, url, recheck) : undefined;
	let capture = '';
	// Only a file inside the case; an unreadable one is simply not a source, so the claim check never fails the call.
	try { if (typeof item.raw_path === 'string') capture = readFileSync(readTarget(context.caseDir, item.raw_path), 'utf8'); } catch { capture = ''; }
	return [page && 'text' in page ? page.text : '', capture];
}

function refetchedSupport(context: ToolContext, findingId: string): { item?: RecordRow; status: string; passages?: string; pages?: string } {
	const items = findingItems(context, findingId);
	const passages = recordRows(recordDocument(context, 'data/source-expressions.json').expressions).filter((passage) =>
		recordRows(passage.lifecycle_events).at(-1)?.event === 'activated'
		&& recordRows(passage.finding_links).some((link) => link.finding_id === findingId && link.relation === 'supports'));
	const recheck = taskRecheck(context);
	const statuses: string[] = [];
	for (const item of items) {
		const url = itemURL(item);
		const own = passages.filter((passage) => passage.original_evidence_bundle_id === item.id && typeof passage.text === 'string');
		if (!url || !own.length) continue;
		const refetch = refetchOf(context, url, recheck);
		if ('status' in refetch) { statuses.push(refetch.status); continue; }
		let unsupported = false;
		for (const passage of own) {
			const found = locate(refetch.text, String(passage.text));
			if (found && !sameSourceSupport(context, url, findingId, found.quote)) { unsupported = true; continue; }
			if (found) return { status: 'found', passages: passages.map((row) => String(row.text ?? '')).join('\n'), pages: items.flatMap((other) => sourcePages(context, other, recheck)).join('\n'), item: { description: `Re-fetch by the harness for the fact-checker of ${url} at ${refetch.accessed}; it still contains the supporting passage. Same source as the investigation, not a second source.`, source: url, quote: found.quote, source_ref: { path: refetch.path, line_start: found.line_start, line_end: found.line_end } } };
		}
		statuses.push(unsupported ? `${url}: the investigation records no direct or indirect support from this source for the finding` : `${url}: re-fetched, but the supporting passage is no longer on the page`);
	}
	if (!statuses.length) return { status: passages.length ? 'no harness re-fetch of its sources' : 'no recorded supporting passage to compare' };
	return { status: statuses.join('; ') };
}

// The fact-checker's quote found in the harness re-fetch of one of the
// finding's sources, as an evidence item against it; or the reason it is not.
function refetchedContradiction(context: ToolContext, findingId: string, quote: string): { item?: RecordRow; status: string } {
	const recheck = taskRecheck(context);
	const statuses: string[] = [];
	for (const url of new Set(findingItems(context, findingId).map(itemURL).filter((url): url is string => !!url))) {
		const refetch = refetchOf(context, url, recheck);
		if ('status' in refetch) { statuses.push(refetch.status); continue; }
		const found = locate(refetch.text, quote);
		if (found) return { status: 'found', item: { description: `Re-fetch by the harness for the fact-checker of ${url} at ${refetch.accessed}; this line contradicts the finding. Same source as the investigation.`, source: url, quote: found.quote, source_ref: { path: refetch.path, line_start: found.line_start, line_end: found.line_end } } };
		statuses.push(`${url}: the quote is not in its re-fetch ${refetch.path}`);
	}
	return { status: statuses.length ? statuses.join('; ') : 'no harness re-fetch of its sources' };
}

// B20 (K4 Tor P3: 13 record_assessment calls, 0 judge_finding, every
// verified row refused for want of a passage ref): on the local tiers a
// fact-checker gets judge_finding only, and record_assessment once it has a
// second source, a capture of its own of a URL none of its batch's findings
// cites, made since this worker started (the registry keeps earlier workers'
// captures; Codex review of simp-b20). The harness re-fetch of a cited source is not one.
export function secondSourceFetched(context: ToolContext, batch: string[], since: number): boolean {
	// No batch (B20b): the worker answers for every finding, so all their sources are cited.
	const findings = recordRows(recordDocument(context, 'data/findings.json').findings).filter((row) => !batch.length || batch.includes(String(row.id)));
	const ids = new Set(findings.map((row) => String(row.id)));
	const refs = new Set(findings.flatMap((row) => Array.isArray(row.evidence_bundle_refs) ? row.evidence_bundle_refs.map(String) : []));
	const cited = new Set(recordRows(recordDocument(context, 'data/evidence-bundle.json').items)
		.filter((item) => refs.has(String(item.id)) || recordRows(item.claim_links).some((link) => ids.has(String(link.finding_id))))
		.map((item) => item.source_url ?? item.url).filter((url): url is string => typeof url === 'string'));
	const rechecks = new Set(Object.values(taskRecheck(context)).map((check) => typeof check === 'object' ? check.acquisition?.path : undefined).filter((path): path is string => typeof path === 'string'));
	return factCheckerSourceURLs(context, rechecks, since).some((url) => !cited.has(url));
}

function judgeTool(context: ToolContext, assess: ToolDefinition, batch: string[], filled: string | undefined): ToolDefinition {
	const parameters = Type.Object({
		finding_id: Type.Optional(Type.String({ minLength: 1 })),
		answer: Type.Union(ANSWERS.map((answer) => Type.Literal(answer))),
		reason: Type.String({ minLength: 1, maxLength: 1000 }),
		quote: Type.Optional(Type.String({ minLength: 1, maxLength: 2000 })),
	}, { additionalProperties: false });
	return {
		name: 'judge_finding',
		label: 'Judge finding',
		parameters,
		executionMode: 'sequential',
		description: `Answer one question for one finding: does its source support it? answer is supports, partly, doesnt, cant_tell or contradicts, with a one-line reason; code writes the claim from the harness re-fetch (partly adds a partial_support flag). contradicts also needs quote, the exact line of the re-fetch that contradicts the finding; the claim is then disputed.${filled ? ` Your batch holds one finding, ${filled}: omit finding_id.` : batch.length ? ` finding_id is one of ${batch.join(', ')}.` : ''}`,
		async execute(id, raw, signal, ...rest) {
			const input = checkedParams<{ finding_id?: string; answer: typeof ANSWERS[number]; reason: string; quote?: string }>('judge_finding', parameters, raw);
			const findingId = input.finding_id ?? filled;
			if (!findingId) throw new ToolArgumentError(`judge_finding: finding_id is required${batch.length ? `: one of ${batch.join(', ')}` : ''}.`);
			if (batch.length && !batch.includes(findingId)) throw new ToolArgumentError(`judge_finding: ${findingId} is not in your batch (${batch.join(', ')}).`);
			const reason = input.reason.trim();
			const evidence: RecordRow[] = [];
			let status = '';
			if (input.answer === 'supports') {
				const support = refetchedSupport(context, findingId);
				if (support.item) evidence.unshift(support.item);
				else status = support.status;
				// B20b: a run with no harness re-fetch (no batch) cannot verify; "supports" must not turn a claim unverified.
				const existing = recordRows(recordDocument(context, 'data/fact-check.json').claims).find((row) => row.finding_id === findingId);
				if (!evidence.length && !context.env.SPOTLIGHT_TASK_RECHECK && existing) return textResult(`${findingId} keeps its ${String(existing.verdict)} claim: this run has no harness re-fetch to check it against.`);
				// The claim check downgrades and never refuses: the same claim is written, unverified, with the value named.
				const claim = recordRows(recordDocument(context, 'data/findings.json').findings).find((row) => row.id === findingId)?.claim;
				const missing = support.item ? missingValues(String(claim ?? ''), support.passages ?? '', support.pages) : [];
				if (missing.length) {
					evidence.length = 0;
					status = `the finding's sources do not contain ${missing.join(', ')}`;
				}
			}
			const against: RecordRow[] = [];
			if (input.answer === 'contradicts') {
				if (!input.quote?.trim()) throw new ToolArgumentError(`judge_finding: contradicts needs quote, the exact line of the harness re-fetch of ${findingId}'s source that contradicts it. Copy it from the re-fetch, or answer doesnt.`);
				const contradiction = refetchedContradiction(context, findingId, input.quote);
				if (!contradiction.item) throw new ToolArgumentError(`judge_finding: ${findingId} is not recorded: ${contradiction.status}. Copy the contradicting line exactly from the re-fetch, or answer doesnt.`);
				against.push(contradiction.item);
			}
			const verdict = input.answer === 'supports' && evidence.length ? 'verified' : against.length ? 'disputed' : 'unverified';
			const assessment: RecordRow = {
				finding_id: findingId,
				verdict,
				confidence: verdict === 'verified' ? 'medium' : 'low',
				evidence_for: evidence,
				evidence_against: against,
				notes: status ? `${reason} (Harness re-check: ${status}.)` : reason,
				...(input.answer === 'partly' ? { flags: [{ code: 'partial_support', reason }] } : {}),
			};
			const result = await assess.execute(id, { assessment }, signal, ...rest);
			const said = verdict === 'unverified' ? `${findingId} recorded as unverified${status ? `: ${status}` : ''}.` : `${findingId} recorded as ${verdict}.`;
			return { ...result, content: [...result.content, { type: 'text' as const, text: said }] };
		},
	};
}

const claimKey = (claim: unknown) => String(claim).normalize('NFKC').toLowerCase().replace(/[\s\p{P}]+/gu, ' ').trim();
const sameClaim = (a: unknown, b: unknown) => claimKey(a) === claimKey(b);
const hasPassage = (context: ToolContext, findingId: string) => recordRows(recordDocument(context, 'data/source-expressions.json').expressions).some((passage) => recordRows(passage.finding_links).some((link) => link.finding_id === findingId));

export function recordTools(tools: ToolDefinition[], context: ToolContext): ToolDefinition[] {
	const role = context.env.SPOTLIGHT_ROLE;
	if (role !== 'investigator' && role !== 'fact-checker') return [];
	const finding = role === 'investigator';
	const name = finding ? 'record_finding' : 'record_assessment';
	const key = finding ? 'finding' : 'assessment';
	const file = finding ? 'findings.json' : 'fact-check.json';
	// Code owns IDs (Phase B, stock LFM3: 6 of 6 assessments sent without
	// finding_id and refused). A fact-checker whose batch holds one finding may
	// omit finding_id and code fills it; with any other batch the schema
	// requires it and a call without it is refused naming the batch.
	const batch = finding ? [] : (context.env.SPOTLIGHT_FC_BATCH ?? '').split(',').map((item) => item.trim()).filter(Boolean);
	const filled = batch.length === 1 ? batch[0] : undefined;
	const parameters = recordSchema(
		finding ? 'findings.schema.json' : 'fact-check.schema.json',
		finding ? 'findings' : 'claims',
		finding ? ['id', 'finding_fingerprint'] : ['claim_text'],
		finding || filled ? [] : ['finding_id'],
		finding,
	);
	const batchNote = finding ? '' : filled ? ` Your batch holds one finding, ${filled}: omit finding_id and code fills it.` : batch.length ? ` finding_id is required inside assessment: one of ${batch.join(', ')} (your batch).` : ' finding_id is required inside assessment.';
	const write = tools.find((tool) => tool.name === 'write_file');
	const expressions = tools.find((tool) => tool.name === 'source_expressions');
	if (!write) return [];
	const tool: ToolDefinition = {
		name,
		label: finding ? 'Record finding' : 'Record assessment',
		parameters,
		executionMode: 'sequential',
		description: finding
			? 'Record one finding as a structured object. Supply claim and confidence with any relevant grounding fields, and cite the lines that support it: cite [{path, line_start, line_end}] of a capture you fetched (lines as read_file numbers them). Code then copies those lines as the evidence, lists the capture as the source, adds its evidence item linking the finding (support_type direct, or say support_type indirect) and records the passage; you need not retype a quote. Without cite, supply evidence and sources yourself. Omit id for a stable generated ID; supply an existing id to correct it. Code supplies the file, contract, project and fingerprint. Other findings remain unchanged. Archive URLs require an actual capture. You choose the claim, the lines and the judgment.'
			: `Record one independent assessment as a structured object: assessment holds finding_id, verdict, confidence and your evidence/grounding assessment; finding_id goes inside assessment, not beside it. Omit claim_text to copy the exact finding. Exact quotes and source_ref locators use the same acquired-capture and passage checks as a fact-check write. Other assessments remain unchanged. You choose the verdict and evidence; code supplies the canonical file and references.${batchNote}`,
		// Pi validates arguments against the schema before execute, after this.
		prepareArguments: (args: unknown) => (finding ? applyCites(context, args) : foldFindingId(args, batch, filled)),
		async execute(id, raw, signal, ...rest) {
			const proposed = (raw as Record<string, unknown> | undefined)?.[key] as RecordRow | undefined;
			if (!finding && !filled && proposed && typeof proposed === 'object' && proposed.finding_id === undefined) {
				throw new ToolArgumentError(`record_assessment: finding_id is required.${batch.length ? ` Your batch: ${batch.join(', ')}; record one assessment per finding with its finding_id.` : ' Use the assigned finding\'s id from the next-to-check list.'}`);
			}
			const input = checkedParams<Record<string, RecordRow>>(name, parameters, raw);
			const row = structuredClone(input[key]);
			const supplied = !finding && filled !== undefined && row.finding_id === undefined;
			if (supplied) row.finding_id = filled;
			const findings = recordDocument(context, 'data/findings.json');
			const rows = recordRows(findings.findings);
			const existing = recordDocument(context, `data/${file}`);
			const contract = recordDocument(context, 'data/case-contract.json');
			const active = contract.current_contract_version === '1.1';
			if (finding) {
				if (active && !expressions) throw new FileRefused('record_finding: this runtime is missing the canonical passage/fingerprint writer; no finding was written.');
				if (active && row.confidence === 'disputed') throw new ToolArgumentError('record_finding: activated findings use confidence high, medium or low; record uncertainty in the claim and grounding.');
				if (!row.id) {
					const prefix = context.env.SPOTLIGHT_DIRECTION ? `${context.env.SPOTLIGHT_DIRECTION}-F` : 'F';
					// Local tiers (u9mac-rd3b81 F2, F3): a claim differing only in case or punctuation is the same claim, and
					// once it holds a passage a second cite would overwrite good lines, so the first stands. Without a passage
					// this is the repair route, which keeps the id. Same direction only: an empty direction is dispatched again.
					const local = localTier(context.env);
					const same = rows.find((item) => typeof item.id === 'string' && item.id.startsWith(prefix) && (local ? sameClaim(item.claim, row.claim) : item.claim === row.claim));
					if (same && local && active && hasPassage(context, String(same.id))) return textResult(`This claim is already recorded as ${String(same.id)}, so nothing was written. Record a different claim, or finish.`);
					let next = 1;
					const taken = new Set(rows.map((item) => item.id));
					while (taken.has(`${prefix}${next}`)) next++;
					row.id = same?.id ?? `${prefix}${next}`;
				}
				delete row.finding_fingerprint;
			} else {
				const source = rows.find((item) => item.id === row.finding_id);
				if (!source || typeof source.claim !== 'string') throw new FileRefused(`record_assessment: unknown finding_id ${JSON.stringify(row.finding_id)}. Read data/findings.json and use the assigned finding's id.`);
				if (row.claim_text === undefined) row.claim_text = source.claim;
				if (active && !['verified', 'unverified', 'disputed', 'false', 'not_checkable'].includes(String(row.verdict))) throw new ToolArgumentError('record_assessment: activated cases use verdict verified, unverified, disputed, false or not_checkable.');
			}
			const cites = finding && Array.isArray(input.cite) ? resolveCites(context, input.cite as unknown as Cite[]) : [];
			const project = existing.project ?? contract.project ?? findings.project ?? recordDocument(context, 'data/methodology.json').project;
			if (typeof project !== 'string' || !project.trim()) throw new FileRefused(`${name}: the approved case has no project label; repair the methodology through its owner before recording results.`);
			// How the cited lines support the claim: the model's word (support_type, or its
			// grounding's support_type, so a non-supporting grounding never becomes a
			// direct link: Codex review of simp-link), else direct.
			const grounded = ((row.grounding ?? {}) as RecordRow).support_type;
			const support = ((input as Record<string, unknown>).support_type ?? (['direct', 'indirect', 'inferred', 'contradicted', 'insufficient'].includes(String(grounded)) ? grounded : 'direct')) as ClaimLink['support_type'];
			// A corrected claim keeps this role's links in step: their claim_text is the claim (validate-case).
			const before = finding ? rows.find((item) => item.id === row.id) : undefined;
			if (before && typeof before.claim === 'string' && before.claim !== row.claim) {
				const direction = context.env.SPOTLIGHT_DIRECTION;
				const stale = recordRows(recordDocument(context, 'data/evidence-bundle.json').items).filter((item) => item.created_by !== 'fact-checker' && (!direction || String(item.id).startsWith(`${direction}-`)) && recordRows(item.claim_links).some((link) => link.finding_id === row.id));
				if (stale.length) await write.execute(`${id}-relink`, { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: stale.map((item) => ({ ...item, claim_links: recordRows(item.claim_links).map((link) => (link.finding_id === row.id ? { ...link, claim_text: row.claim } : link)) })) }) }, signal, undefined, undefined as never);
			}
			const items = cites.length ? await citeItems(context, write, cites, id, signal, { finding_id: String(row.id), claim_text: String(row.claim), support_type: support }) : undefined;
			if (items) row.evidence_bundle_refs = [...new Set([...(Array.isArray(row.evidence_bundle_refs) ? row.evidence_bundle_refs.map(String) : []), ...items.itemOf.values()])];
			const content = JSON.stringify({
				project,
				...(!active && existing.schema_version ? { schema_version: existing.schema_version } : {}),
				[finding ? 'findings' : 'claims']: [row],
			});
			const result = await write.execute(id, { path: `data/${file}`, content }, signal, ...rest);
			if (finding && active && expressions) {
				try {
					// The sole passage writer refreshes fingerprints and affected links.
					await expressions.execute(`${id}-fingerprint`, { action: 'fingerprint_findings' }, signal, ...rest);
				} catch (error) {
					throw new Error(`record_finding: data/findings.json was written, but fingerprint refresh failed. Do not recreate the finding. Repair the fingerprint step with source_expressions action fingerprint_findings before continuing. ${(error as Error).message}`);
				}
			}
			if (supplied) return { ...result, content: [...result.content, { type: 'text' as const, text: `finding_id ${filled} was filled by the harness: it is the one finding in your batch.` }] };
			const cited = [
				...(items?.added.length ? [`Evidence item(s) ${items.added.join(', ')} added for the cited capture(s).`] : []),
				...(items && active && expressions ? [await citePassages(context, expressions, row, cites, items.itemOf, id, signal)] : []),
			];
			return finding ? { ...result, content: [...result.content, { type: 'text' as const, text: [`Finding id: ${String(row.id)}.`, ...cited].join(' ') }] } : result;
		},
	};
	return finding ? [tool] : [tool, judgeTool(context, tool, batch, filled)];
}
