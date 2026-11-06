// step3c (reports/step3c-commit-design.md, approved 2026-11-03): on the local
// tiers, when the investigator has been shown capture lines it has not
// recorded, code makes one extra request offering only a narrowed
// record_finding and nothing_found, with tool_choice required. The model
// writes the claim, the confidence and which lines support it; code keeps only
// cites the model was actually shown and hands the call to the real
// record_finding, which checks everything as before; a cite of other lines
// reads them instead. Any other outcome sends
// the model's own request unchanged. Never a gate, cap, stop or refusal; code
// never writes a claim. The model offers no honest "nothing found" (probe E:
// it invents a cite), so the step fires only on shown, unrecorded lines.

import { relative, resolve, sep } from 'node:path';

export const COMMIT_CALL_PREFIX = 'commit-';
// Tool turns the model's own route keeps after the first unrecorded line, and
// the gap between two commit attempts (design: 2 and 4).
export const COMMIT_AFTER_TURNS = 2;
export const COMMIT_GAP_TURNS = 4;
// grep_files prints a line's first 400 characters (case-files.ts); a line that
// long may be cut, so it was not fully shown.
const GREP_LINE_CHARS = 400;
const READ_TRUNCATED = '[truncated: use offset and limit to read the rest]';
const CAPTURE = /^(?:research|evidence)\//;
// A cited path offered for a read; it is the model's text, so no whitespace (Codex R1).
const READABLE = /^(?:research|evidence)\/\S+$/;
// The most lines one read of a dropped cite asks for.
const CITE_READ_LINES = 60;
// case-files.ts readPage's note, the row before READ_TRUNCATED.
const PAGE_NOTE = /\n\[page: lines (\d+)-\d+ of \d+, so the read stays under this model's context fold\.[^\n]*\]\n\[truncated: use offset and limit to read the rest\]$/;

export const COMMIT_TEXT = 'Commit step (from the harness, not the member): record what the lines you have read already support. Call record_finding with the claim in your own words, its confidence, and cite the path and line numbers you read (as grep_files and read_file numbered them). If no line you have read supports a finding yet, call nothing_found with the reason. You keep working after this.';

export const COMMIT_TOOLS = [
	{
		type: 'function',
		function: {
			name: 'record_finding',
			description: 'Record one finding: the claim, its confidence, and the lines you read that support it.',
			parameters: {
				type: 'object',
				additionalProperties: false,
				required: ['finding', 'cite'],
				properties: {
					finding: { type: 'object', additionalProperties: false, required: ['claim', 'confidence'], properties: { claim: { type: 'string', minLength: 1 }, confidence: { type: 'string', enum: ['high', 'medium', 'low'] } } },
					cite: { type: 'array', minItems: 1, maxItems: 10, items: { type: 'object', additionalProperties: false, required: ['path', 'line_start', 'line_end'], properties: { path: { type: 'string', minLength: 1 }, line_start: { type: 'integer', minimum: 1 }, line_end: { type: 'integer', minimum: 1 } } } },
				},
			},
		},
	},
	{ type: 'function', function: { name: 'nothing_found', description: 'No line you have read supports a finding yet.', parameters: { type: 'object', additionalProperties: false, required: ['reason'], properties: { reason: { type: 'string' } } } } },
];

type Part = { type?: string; text?: string; id?: string; name?: string; arguments?: Record<string, unknown> };
type Msg = { role?: string; content?: unknown; toolCallId?: string; toolName?: string; isError?: boolean };
export type Shown = Map<string, Map<number, number>>; // path -> line -> tool turn first shown
export type CommitState = { turns: number; shown: Shown; committed: Set<string>; uncommitted: Array<{ path: string; line: number; turn: number }>; lastCommitTurn?: number };

const parts = (content: unknown): Part[] => (Array.isArray(content) ? (content as Part[]) : typeof content === 'string' ? [{ type: 'text', text: content }] : []);
const text = (content: unknown) => parts(content).filter((part) => part.type === 'text').map((part) => part.text ?? '').join('\n');

// `recorded`: lines this process saw recorded before a fold shortened the
// transcript ("path:line"), so a fold does not make them unrecorded again.
export function commitState(messages: readonly unknown[], recorded: ReadonlySet<string> = new Set()): CommitState {
	const shown: Shown = new Map();
	const committed = new Map<string, Set<number>>();
	const calls = new Map<string, { name?: string; args: Record<string, unknown>; turn: number }>();
	let turns = 0;
	let lastCommitTurn: number | undefined;
	const show = (path: string, line: number, turn: number) => {
		if (!CAPTURE.test(path)) return;
		const lines = shown.get(path) ?? new Map<number, number>();
		if (!lines.has(line)) lines.set(line, turn);
		shown.set(path, lines);
	};
	for (const message of messages as Msg[]) {
		if (message.role === 'assistant') {
			const toolCalls = parts(message.content).filter((part) => part.type === 'toolCall');
			if (!toolCalls.length) continue;
			turns += 1;
			for (const call of toolCalls) {
				calls.set(String(call.id), { name: call.name, args: call.arguments ?? {}, turn: turns });
				if (String(call.id).startsWith(COMMIT_CALL_PREFIX)) lastCommitTurn = turns;
			}
			continue;
		}
		if (message.role !== 'toolResult' || message.isError) continue;
		const call = calls.get(String(message.toolCallId));
		if (!call) continue;
		const body = text(message.content);
		if (call.name === 'grep_files') {
			// A result with any row that is not path:line:text (a path holding a
			// newline could forge one) shows nothing (Codex review of simp-commit, P2).
			const rows = body.split('\n').map((row) => /^([^:\n\r]+):(\d+):(.*)$/.exec(row));
			if (body.trim() !== 'no matches' && rows.every(Boolean)) {
				for (const match of rows) if (match![3].length < GREP_LINE_CHARS) show(match![1], Number(match![2]), call.turn);
			}
		} else if (call.name === 'read_file' && typeof call.args.path === 'string' && (call.args.offset || call.args.limit || PAGE_NOTE.test(body))) {
			// A whole-file read on a small fold point returns a page that names its lines (Codex review of simp-read).
			const from = call.args.offset || call.args.limit ? Number(call.args.offset ?? 1) : Number(PAGE_NOTE.exec(body)![1]);
			const cut = body.endsWith(READ_TRUNCATED);
			const rows = (cut ? body.slice(0, -READ_TRUNCATED.length).replace(/\n$/, '') : body).split('\n');
			// The row before a cut may be partial: only whole lines were shown.
			const whole = cut ? rows.length - 1 : rows.length;
			for (let index = 0; index < whole; index++) show(call.args.path.replace(/^\.\//, ''), from + index, call.turn);
		} else if (call.name === 'fetch') {
			// Leads print the page's own lines under their raw numbers, and the result names them
			// (u9mac-s856: four commit answers citing them were dropped as unseen).
			let selections: unknown;
			try { selections = (JSON.parse(body) as { selections?: unknown }).selections; } catch { continue; }
			for (const pick of (Array.isArray(selections) ? selections : []) as Array<{ path?: unknown; line_start?: unknown; line_end?: unknown }>) {
				if (typeof pick?.path !== 'string' || !Number.isSafeInteger(pick.line_start) || !Number.isSafeInteger(pick.line_end)) continue;
				for (let line = Number(pick.line_start); line <= Number(pick.line_end); line++) show(pick.path, line, call.turn);
			}
		} else if (call.name === 'record_finding' && Array.isArray(call.args.cite)) {
			for (const cite of call.args.cite as Array<{ path?: unknown; line_start?: unknown; line_end?: unknown }>) {
				if (typeof cite?.path !== 'string' || !Number.isInteger(cite.line_start) || !Number.isInteger(cite.line_end)) continue;
				const lines = committed.get(cite.path) ?? new Set<number>();
				for (let line = Number(cite.line_start); line <= Number(cite.line_end); line++) lines.add(line);
				committed.set(cite.path, lines);
			}
		}
	}
	const done = new Set([...recorded, ...[...committed].flatMap(([path, lines]) => [...lines].map((line) => `${path}:${line}`))]);
	const uncommitted = [...shown].flatMap(([path, lines]) => [...lines].filter(([line]) => !done.has(`${path}:${line}`)).map(([line, turn]) => ({ path, line, turn })));
	return { turns, shown, committed: done, uncommitted, lastCommitTurn };
}

// Due when shown lines are unrecorded, the model has had its turns, and no
// commit attempt is recent. `requestsSinceAttempt` counts this process's
// requests since its last attempt, which a fold does not reset.
export function commitDue(state: CommitState, requestsSinceAttempt = Infinity): boolean {
	if (!state.uncommitted.length) return false;
	const first = Math.min(...state.uncommitted.map((entry) => entry.turn));
	if (state.turns - first < COMMIT_AFTER_TURNS) return false;
	if (state.lastCommitTurn !== undefined && state.turns - state.lastCommitTurn < COMMIT_GAP_TURNS) return false;
	return requestsSinceAttempt >= COMMIT_GAP_TURNS;
}

// The model's commit answer, its cites as the lines it was shown and has not
// recorded (runs of consecutive lines become separate cites); or why it is not
// used. A cite reaching lines it was not shown is not clipped to the rest: the
// rest need not support the claim (u9mac-s856 commit 1: a claim about the
// director, cited to navigation lines and to lines it never saw; Codex R1).
// `unseen`: those ranges, for the read.
export type CommitArgs = { finding: { claim: string; confidence: string }; cite: Array<{ path: string; line_start: number; line_end: number }> };
type Range = { path: string; line_start: number; line_end: number };
function acceptCommit(name: string | undefined, args: unknown, shown: Shown, recorded: ReadonlySet<string>): { args: CommitArgs } | { reason: string; unseen?: Range[] } {
	const unseen: Range[] = [];
	if (name === 'nothing_found') return { reason: 'the model answered nothing_found' };
	if (name !== 'record_finding') return { reason: `the model answered ${name ?? 'no tool call'}` };
	const input = args as { finding?: { claim?: unknown; confidence?: unknown }; cite?: unknown };
	const claim = input?.finding?.claim, confidence = input?.finding?.confidence;
	if (typeof claim !== 'string' || !claim.trim() || !['high', 'medium', 'low'].includes(String(confidence))) return { reason: 'the commit answer has no claim or confidence' };
	const cite: Range[] = [];
	let repeated = false;
	for (const entry of Array.isArray(input.cite) ? (input.cite as Array<{ path?: unknown; line_start?: unknown; line_end?: unknown }>) : []) {
		const start = Number(entry?.line_start), end = Number(entry?.line_end);
		if (typeof entry?.path !== 'string' || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end) continue;
		// A leads path with the numbers the leads printed means the page's lines, as record_finding reads it.
		const given = entry.path.replace(/^\.\//, '');
		const path = shown.has(`${given}.raw`) ? `${given}.raw` : given;
		const seen = shown.get(path);
		// Each walk is bounded by the lines shown, not by the range (Codex review of simp-commit, P2).
		let first = start, last = end;
		while (seen?.has(first)) first++;
		while (seen?.has(last)) last--;
		if (first <= last) unseen.push({ path, line_start: first, line_end: last });
		let run: Range | undefined;
		for (const line of [...(seen?.keys() ?? [])].filter((line) => line >= start && line <= end).sort((a, b) => a - b)) {
			if (recorded.has(`${path}:${line}`)) { repeated = true; continue; }
			if (cite.length >= 10 && !(run && run.line_end === line - 1)) break;
			if (run && run.line_end === line - 1) run.line_end = line;
			else { run = { path, line_start: line, line_end: line }; cite.push(run); }
		}
	}
	if (unseen.length) return { reason: cite.length || repeated ? 'the commit answer cites lines the model was not shown' : 'the commit answer cites no line the model was shown', unseen };
	if (!cite.length) return { reason: repeated ? 'the commit answer cites lines already recorded' : 'the commit answer cites no line the model was shown' };
	return { args: { finding: { claim: claim.trim(), confidence: String(confidence) }, cite } };
}

// inv-retest-def8674f.md: LFM3's commit answer carried two record_finding calls
// and the one-call rule dropped both. The first call that passes the cite rules
// is kept; every other call is dropped and named, so the model sees it. With
// none kept, `read` is the first unseen capture range: the commit turn reads
// it, so a dropped cite is never silent.
export function pickCommit(calls: ReadonlyArray<{ name?: string; arguments?: unknown }>, shown: Shown, recorded: ReadonlySet<string> = new Set()): { args: CommitArgs; dropped: string[] } | { reason: string; read?: { path: string; offset: number; limit: number } } {
	const answers = calls.map((call) => acceptCommit(call.name, call.arguments, shown, recorded));
	const kept = answers.findIndex((answer) => 'args' in answer);
	if (kept < 0) {
		const reason = answers.flatMap((answer) => ('reason' in answer ? [answer.reason] : [])).join('; ') || 'the model answered no tool call';
		const range = answers.flatMap((answer) => ('unseen' in answer ? answer.unseen ?? [] : [])).find(({ path }) => READABLE.test(path));
		return range ? { reason, read: { path: range.path, offset: range.line_start, limit: Math.min(range.line_end - range.line_start + 1, CITE_READ_LINES) } } : { reason };
	}
	// Calls are named by number, tool and reason only: the model's own text
	// never reappears as harness prose (Codex review of simp-commit2, P2).
	const dropped = calls.flatMap((call, index) => {
		if (index === kept) return [];
		const answer = answers[index];
		const why = call.name === 'nothing_found' ? 'a finding was recorded'
			: call.name === 'record_finding' && 'args' in answer ? 'one finding per commit step; record it with record_finding'
			: 'reason' in answer ? answer.reason : 'not used';
		return [`call ${index + 1} (${call.name === 'record_finding' || call.name === 'nothing_found' ? call.name : 'another tool'}: ${why})`];
	});
	return { args: (answers[kept] as { args: CommitArgs }).args, dropped };
}

// B18 (fine-tuning briefs/b18.md): the same step for a worker whose deliverable
// is one file it writes with write_file (k2-tor.md, k2-eurostat.md: LFM3
// workers read and never wrote methodology.json, the research-step files, the
// report draft or the knowledge proposal). After WRITE_COMMIT_AFTER_TURNS tool
// turns with no write of that file, code makes one request offering only
// write_file for that path and not_ready. An accepted answer becomes this
// turn's real write_file call, so every validator and refusal runs on it, and
// a refusal reaches the worker as its tool result. Code never writes content;
// not_ready or anything else sends the model's own request. Attempts are
// COMMIT_GAP_TURNS apart, so before the progress watchdog's 16th turn they
// fall at turns 6, 10 and 14.
export const WRITE_COMMIT_AFTER_TURNS = 6;
const WRITE_COMMIT_FILES = new Set([
	'data/methodology.json',
	'data/research-suggestions.json',
	'data/methodology-amendment.json',
	'data/research-round.json',
	'data/report-draft.json',
	'data/knowledge-proposal.json',
]);

// The worker's one write-file deliverable, from the launcher's
// SPOTLIGHT_DELIVERABLE_FILES; none for a findings, fact-check or two-file one.
export function writeCommitFile(deliverables: string | undefined): string | undefined {
	const files = (deliverables ?? '').split(',').filter(Boolean);
	return files.length === 1 && WRITE_COMMIT_FILES.has(files[0]) ? files[0] : undefined;
}

export const writeCommitText = (file: string) => `Commit step (from the harness, not the member): write ${file} now from what you have read, as your task describes. Call write_file with path ${file} and the whole file as content. If you cannot write it yet, call not_ready with the reason. You keep working after this.`;

export const writeCommitTools = (file: string) => [
	{
		type: 'function',
		function: {
			name: 'write_file',
			description: `Write ${file}, the whole file, from what you have read.`,
			parameters: { type: 'object', additionalProperties: false, required: ['path', 'content'], properties: { path: { type: 'string', enum: [file] }, content: { type: 'string', minLength: 1 } } },
		},
	},
	{ type: 'function', function: { name: 'not_ready', description: `You cannot write ${file} yet.`, parameters: { type: 'object', additionalProperties: false, required: ['reason'], properties: { reason: { type: 'string' } } } } },
];

// A path as the worker spelled it, case-relative and resolved as the file tools
// resolve it ("./", "{CASE_DIR}/", "..", or the absolute case path).
export const caseRelative = (path: string, caseDir: string) => relative(caseDir, resolve(caseDir, path.replace(/^\{CASE_DIR\}(?:[\\/]|$)/, ''))).split(sep).join('/');

// Due while the file has no accepted write or edit ("wrote ..."/"edited ..."
// result; `writtenBefore`: one this process saw before a fold shortened the
// transcript), the worker has spent WRITE_COMMIT_AFTER_TURNS tool turns since
// its last try at the file, and no attempt is recent.
export function writeCommitState(messages: readonly unknown[], file: string, caseDir: string): { turns: number; lastTry: number; written: boolean } {
	let turns = 0;
	let lastTry = 0;
	let written = false;
	const tries = new Set<string>();
	for (const message of messages as Msg[]) {
		if (message.role === 'assistant') {
			const toolCalls = parts(message.content).filter((part) => part.type === 'toolCall');
			if (!toolCalls.length) continue;
			turns += 1;
			for (const call of toolCalls) {
				if ((call.name === 'write_file' || call.name === 'edit_file') && typeof call.arguments?.path === 'string' && caseRelative(call.arguments.path, caseDir) === file) {
					lastTry = turns;
					tries.add(String(call.id));
				}
			}
		} else if (message.role === 'toolResult' && !message.isError && tries.has(String(message.toolCallId)) && /^(?:wrote|edited) /.test(text(message.content))) written = true;
	}
	return { turns, lastTry, written };
}
export function writeCommitDue(messages: readonly unknown[], file: string, caseDir: string, requestsSinceAttempt = Infinity, writtenBefore = false): boolean {
	const state = writeCommitState(messages, file, caseDir);
	return !writtenBefore && !state.written && state.turns - state.lastTry >= WRITE_COMMIT_AFTER_TURNS && requestsSinceAttempt >= COMMIT_GAP_TURNS;
}

// The first write_file of the file with content is kept; the other calls are
// named by number and tool only.
export function pickWrite(calls: ReadonlyArray<{ name?: string; arguments?: unknown }>, file: string, caseDir: string): { args: { path: string; content: string }; dropped: string[] } | { reason: string } {
	const kept = calls.findIndex((call) => {
		const args = call.arguments as { path?: unknown; content?: unknown } | undefined;
		return call.name === 'write_file' && typeof args?.path === 'string' && caseRelative(args.path, caseDir) === file && typeof args.content === 'string' && args.content.trim() !== '';
	});
	if (kept < 0) return { reason: calls.some((call) => call.name === 'not_ready') ? 'the model answered not_ready' : `the commit answer has no write_file of ${file} with content` };
	const dropped = calls.flatMap((call, index) => (index === kept ? [] : [`call ${index + 1} (${call.name === 'write_file' || call.name === 'not_ready' ? call.name : 'another tool'}: one write per commit step)`]));
	return { args: { path: file, content: (calls[kept].arguments as { content: string }).content }, dropped };
}
