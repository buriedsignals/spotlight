// step3c (reports/step3c-commit-design.md, approved 2026-11-03): on the local
// tiers, when the investigator has been shown capture lines it has not
// recorded, code makes one extra request offering only a narrowed
// record_finding and nothing_found, with tool_choice required. The model
// writes the claim, the confidence and which lines support it; code keeps only
// the cited lines the model was actually shown and hands the call to the real
// record_finding, which checks everything as before. Any other outcome sends
// the model's own request unchanged. Never a gate, cap, stop or refusal; code
// never writes a claim. The model offers no honest "nothing found" (probe E:
// it invents a cite), so the step fires only on shown, unrecorded lines and a
// cite of unseen lines is dropped.

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

// The model's commit answer, with each cite clipped to the lines it was shown
// (runs of consecutive shown lines become separate cites); or why it is not used.
export type CommitArgs = { finding: { claim: string; confidence: string }; cite: Array<{ path: string; line_start: number; line_end: number }> };
export function acceptCommit(name: string | undefined, args: unknown, shown: Shown): { args: CommitArgs } | { reason: string } {
	if (name === 'nothing_found') return { reason: 'the model answered nothing_found' };
	if (name !== 'record_finding') return { reason: `the model answered ${name ?? 'no tool call'}` };
	const input = args as { finding?: { claim?: unknown; confidence?: unknown }; cite?: unknown };
	const claim = input?.finding?.claim, confidence = input?.finding?.confidence;
	if (typeof claim !== 'string' || !claim.trim() || !['high', 'medium', 'low'].includes(String(confidence))) return { reason: 'the commit answer has no claim or confidence' };
	const cite: Array<{ path: string; line_start: number; line_end: number }> = [];
	for (const entry of Array.isArray(input.cite) ? (input.cite as Array<{ path?: unknown; line_start?: unknown; line_end?: unknown }>) : []) {
		const start = Number(entry?.line_start), end = Number(entry?.line_end);
		if (typeof entry?.path !== 'string' || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end) continue;
		const seen = shown.get(entry.path.replace(/^\.\//, ''));
		if (!seen) continue;
		// Only the shown lines inside the range are visited (Codex review of simp-commit, P2).
		let run: { path: string; line_start: number; line_end: number } | undefined;
		for (const line of [...seen.keys()].filter((line) => line >= start && line <= end).sort((a, b) => a - b)) {
			if (cite.length >= 10 && !(run && run.line_end === line - 1)) break;
			if (run && run.line_end === line - 1) run.line_end = line;
			else { run = { path: entry.path.replace(/^\.\//, ''), line_start: line, line_end: line }; cite.push(run); }
		}
	}
	if (!cite.length) return { reason: 'the commit answer cites no line the model was shown' };
	return { args: { finding: { claim: claim.trim(), confidence: String(confidence) }, cite } };
}

// inv-retest-def8674f.md: LFM3's commit answer carried two record_finding calls
// and the one-call rule dropped both. The first call that passes the cite rules
// is kept; every other call is dropped and named, so the model sees it.
export function pickCommit(calls: ReadonlyArray<{ name?: string; arguments?: unknown }>, shown: Shown): { args: CommitArgs; dropped: string[] } | { reason: string } {
	let kept: number | undefined;
	let args: CommitArgs | undefined;
	const reasons = calls.map((call) => {
		const accepted = acceptCommit(call.name, call.arguments, shown);
		return 'args' in accepted ? undefined : accepted.reason;
	});
	for (const [index, call] of calls.entries()) {
		if (reasons[index] !== undefined) continue;
		kept = index;
		args = (acceptCommit(call.name, call.arguments, shown) as { args: CommitArgs }).args;
		break;
	}
	if (kept === undefined || !args) return { reason: reasons.filter(Boolean).join('; ') || 'the model answered no tool call' };
	// Calls are named by number, tool and reason only: the model's own text
	// never reappears as harness prose (Codex review of simp-commit2, P2).
	const dropped = calls.flatMap((call, index) => {
		if (index === kept) return [];
		const why = call.name === 'nothing_found' ? 'a finding was recorded'
			: call.name === 'record_finding' && reasons[index] === undefined ? 'one finding per commit step; record it with record_finding'
			: reasons[index] ?? 'not used';
		return [`call ${index + 1} (${call.name === 'record_finding' || call.name === 'nothing_found' ? call.name : 'another tool'}: ${why})`];
	});
	return { args, dropped };
}
