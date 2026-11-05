// Plan step 4 (reports/step4-routing-design.md, approved 2026-11-03): on the
// local tiers code makes the orchestrator's routing moves. It reads the
// request's transcript the way the d1route and step3a replays did (the last
// spotlight_resolve result, the calls since it, a pending member message) and
// either names a move code makes (spotlight_resolve, open_review, or the
// investigator's correction carrying a member's change at the methodology
// review: never a transition or an approval) or hands the request to the model, which keeps
// every member-facing word. A member message is read by the decision model
// (stock D1, intent only); when it cannot answer, the model decides, as before.
// The router never refuses; it adds no cap. It stops a turn in one case only:
// a repeated refused spawn with no move code can prove (routeRefusedSpawn).

export const ROUTER_MODEL = 'spotlight-code-router';
// Every router call's id starts with this, so a record of the request history
// (which keeps tool-call ids) still shows the call was code-made.
export const ROUTER_CALL_PREFIX = 'router-';

export type Intent = 'change_request' | 'question' | 'approval_talk' | 'other';
export type RouterMove = { kind: 'call'; tool: 'spotlight_resolve' | 'open_review'; reason: string } | { kind: 'call'; tool: 'spawn_agent'; arguments: CodeSpawn; reason: string } | { kind: 'stop'; text: string; reason: string } | { kind: 'model'; reason: string };
export type CodeSpawn = { agent: string; task: string };
export type RouterState = {
	resolved?: Record<string, unknown>;
	// Tool calls since the last spotlight_resolve result (all calls when there is none).
	callsSince: number;
	member?: string;
	lastTool?: string;
	// The last assistant message was the router's own call of this tool.
	lastRouted?: string;
};

type Part = { type?: string; text?: string; name?: string };
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type Msg = { role?: string; content?: unknown; toolName?: string; responseModel?: string };

// Turns the harness writes into the chat, not the member (step1 intents_build HARNESS).
const HARNESS_TURN = /^(The conversation history before this point was compacted|Your turn ended with text)/;
// review-simp.md #4: the empty-turn and runaway-turn nudges (extension.ts), matched
// whole so a member message that merely starts the same way stays the member's.
const HARNESS_NUDGES = ['Your last turn was empty. Call the next tool, or give your answer.', 'Your last turn ran on without a tool call and was cut off. Call the next tool, or give your answer in a few sentences.'];

// Whether a user-role turn is the harness's own (a nudge or the compaction note), not the member's.
export const harnessTurn = (said: string) => HARNESS_TURN.test(said) || HARNESS_NUDGES.includes(said);

const parts = (content: unknown): Part[] => (Array.isArray(content) ? (content as Part[]) : typeof content === 'string' ? [{ type: 'text', text: content }] : []);
const text = (content: unknown) => parts(content).filter((part) => part.type === 'text').map((part) => part.text ?? '').join('\n');

export function routerState(messages: readonly unknown[]): RouterState {
	const list = messages as Msg[];
	let resolvedAt = -1;
	let resolved: Record<string, unknown> | undefined;
	for (let index = list.length - 1; index >= 0; index--) {
		const message = list[index];
		if (message.role !== 'toolResult' || message.toolName !== 'spotlight_resolve') continue;
		try {
			const value = JSON.parse(text(message.content));
			if (value && typeof value === 'object' && !Array.isArray(value)) { resolved = value; resolvedAt = index; break; }
		} catch {
			// A refused resolve is not a resolved state.
		}
	}
	const callsSince = list.slice(resolvedAt + 1).filter((message) => message.role === 'assistant').flatMap((message) => parts(message.content).filter((part) => part.type === 'toolCall')).length;
	const last = [...list].reverse().find((message) => message.role === 'user' || message.role === 'assistant' || message.role === 'toolResult');
	const said = last?.role === 'user' ? text(last.content).trim() : '';
	const member = said && !HARNESS_TURN.test(said) && !HARNESS_NUDGES.includes(said) ? said : undefined;
	const lastTool = [...list].reverse().find((message) => message.role === 'toolResult')?.toolName;
	const lastAssistant = [...list].reverse().find((message) => message.role === 'assistant');
	const lastRouted = lastAssistant?.responseModel === ROUTER_MODEL ? parts(lastAssistant.content).find((part) => part.type === 'toolCall')?.name : undefined;
	return { resolved, callsSince, member: member || undefined, lastTool, lastRouted };
}

// What a blocked case says, by the attempt that ran out (transitions.py
// ATTEMPT_LIMITS). The only way on is a changed methodology approved again:
// that approval clears the block (transitions.py approve). Written by code so
// no model blames a file (r3 blamed brief-directions.txt).
export const BLOCKED_STOPS: Record<string, string> = {
	'structural-correction': "Spotlight stopped the investigation. The investigator's files still fail the format check after two corrections, so Spotlight cannot go on by itself. This stop deleted nothing. The fault is in how the files are written, not in your plan, but the only way back is to approve the methodology again: make any small edit to it (reword a question, for example), approve it, and the investigator gets two more tries.",
	'amendment-correction': "Spotlight stopped at Research. The changes you marked could not be written into the plan, and the plan is unchanged. To continue, change the methodology (for example keep a direction you marked to drop) and approve it again; Research will run again.",
};

// The member's closing words at a complete case, from the records alone: the
// findings and the fact-check's own verdicts, counted. Undefined when either
// file is unreadable, so nothing is claimed that the records do not hold.
export function closingMessage(caseDir: string): string | undefined {
	try {
		const read = (name: string) => JSON.parse(readFileSync(resolve(caseDir, 'data', name), 'utf8')) as { findings?: unknown; claims?: unknown };
		const findings = read('findings.json').findings;
		const claims = read('fact-check.json').claims;
		if (!Array.isArray(findings) || !Array.isArray(claims)) return undefined;
		const tally = new Map<string, number>();
		for (const claim of claims) {
			const verdict = (claim as { verdict?: unknown } | null)?.verdict;
			if (typeof verdict === 'string') tally.set(verdict, (tally.get(verdict) ?? 0) + 1);
		}
		const checked = new Set(claims.map((claim) => (claim as { finding_id?: unknown } | null)?.finding_id));
		const unchecked = findings.filter((finding) => !checked.has((finding as { id?: unknown } | null)?.id)).length;
		const verdicts = [...tally].map(([verdict, count]) => `${count} ${verdict.replaceAll('_', ' ')}`);
		if (unchecked) verdicts.push(`${unchecked} without a fact-check verdict`);
		return `Case complete: ${findings.length} ${findings.length === 1 ? 'finding' : 'findings'}${verdicts.length ? `, ${verdicts.join(', ')}` : ''}. The findings and verdicts are in the case files.`;
	} catch {
		return undefined;
	}
}

// The move when no member message is waiting.
export function routeWithoutMember(state: RouterState, closing?: string): RouterMove {
	// The page is open: the turn ends with the model's words (registerEndTurnOnOpen).
	if (state.lastTool === 'open_review') return { kind: 'model', reason: 'the review page was just opened' };
	let move: RouterMove;
	if (!state.resolved || state.callsSince > 0) move = { kind: 'call', tool: 'spotlight_resolve', reason: state.resolved ? `${state.callsSince} call(s) since the last resolve` : 'no resolve yet' };
	else if (typeof state.resolved.next_action === 'string' && state.resolved.next_action.includes('open_review')) move = { kind: 'call', tool: 'open_review', reason: 'the resolver names open_review' };
	else if (state.resolved.status === 'blocked' && BLOCKED_STOPS[String((state.resolved.blocked as { exhausted_attempt?: unknown } | undefined)?.exhausted_attempt)]) {
		const kind = String((state.resolved.blocked as { exhausted_attempt?: unknown }).exhausted_attempt);
		return { kind: 'stop', text: BLOCKED_STOPS[kind], reason: `the case is blocked (${kind}); code says why` };
	} else if (state.resolved.phase === 'complete' && closing) return { kind: 'stop', text: closing, reason: 'the case is complete; code states the counts' };
	else move = { kind: 'model', reason: 'the resolver names no code move' };
	return noLoop(state, move);
}

// The move for a member message, from the decision model's intent (undefined
// when it could not answer). Approval talk at the Brief means the model writes
// the approved brief, but only while that Brief resolve is the latest event
// (step3a-routing.md resolve:r6:29: a stale phase sent it to write_file).
export function routeMember(state: RouterState, intent: Intent | undefined): RouterMove {
	if (!intent) return { kind: 'model', reason: 'no intent from the decision model' };
	// K5 Tor P1 (overnight-summary.md): at the methodology review the member
	// asked twice for a research topic, the model only answered in prose, and
	// the plan was approved without it. A change before the approval goes to
	// the investigator in the member's own words (phase-methodology step 5);
	// the model still answers questions.
	if (intent === 'change_request' && state.resolved?.phase === 'methodology_approval' && state.member) return { kind: 'call', tool: 'spawn_agent', arguments: { agent: 'investigator', task: methodologyRelay(state.member) }, reason: 'member change_request at the methodology review' };
	if (intent === 'question' || intent === 'change_request') return { kind: 'model', reason: `member ${intent}` };
	if (intent === 'approval_talk' && state.resolved?.phase === 'brief' && state.callsSince === 0) return { kind: 'model', reason: 'member approves the brief' };
	return noLoop(state, { kind: 'call', tool: 'spotlight_resolve', reason: `member ${intent}` });
}

// The correction task for a member's change at the methodology review: their
// message verbatim, and the research rule phase-methodology step 5 gives.
export function methodologyRelay(member: string): string {
	return `MODE: METHODOLOGY CORRECTION\nThe member asked for this change at the methodology review, before approving. Their words, verbatim:\n<<<MEMBER>>>\n${member}\n<<<END MEMBER>>>\nMake that change in data/methodology.json. When it asks for research (a topic, a lane, more research on a direction), write it as a research_plan per the PLANNING example.`;
}

// Two router moves of the same tool in a row with no new event hand the turn
// to the model (orchestrator's condition on the design).
function noLoop(state: RouterState, move: RouterMove): RouterMove {
	return move.kind === 'call' && state.lastRouted === move.tool && state.lastTool === move.tool && !state.member
		? { kind: 'model', reason: `the router already called ${move.tool} with nothing new since` }
		: move;
}

// B11 (qwen-u9-pod.md, qwen-u9-loop-state.md): held-sfc sent one refused analyst
// spawn 416 times and held-kde a refused fact-checker spawn 353 times. The
// router's resolve between two spawns resets the loop guard, so nothing in the
// turn ended it. The second refusal with the same words and the same case
// records hands the move to code: the one the refusal names when code can prove
// it, once per state of the records, otherwise an honest stop. Process memory, because a
// fold drops the transcript this would otherwise be read from.
export const REFUSED_SPAWNS = 2;
type RefusedSpawn = { agent: string; refusal: string; count: number };
export type SpawnRefusals = { digest: string; seen: Map<string, RefusedSpawn>; moved: Set<string>; stopped: boolean };

export const spawnRefusals = (): SpawnRefusals => ({ digest: '', seen: new Map(), moved: new Set(), stopped: false });

export function clearSpawnRefusals(spawns: SpawnRefusals): void {
	Object.assign(spawns, spawnRefusals());
}

// Changed records are progress, so the count starts again.
export function noteRefusedSpawn(spawns: SpawnRefusals, agent: string, refusal: string, digest: string): void {
	if (spawns.digest !== digest) {
		spawns.digest = digest;
		spawns.seen.clear();
	}
	const key = JSON.stringify([agent, refusal]);
	const seen = spawns.seen.get(key);
	spawns.seen.set(key, { agent, refusal, count: (seen?.count ?? 0) + 1 });
}

// The member's line: what was refused, where the case is, what they can do.
export function refusedSpawnStop(agent: string, count: number, resolved: Record<string, unknown> | undefined): string {
	const missing = Array.isArray(resolved?.missing) ? resolved.missing.map(String) : [];
	const where = resolved?.phase ? ` The case is at ${String(resolved.phase)}${missing.length ? ` and still needs: ${missing.join('; ')}` : ''}.` : '';
	return `Spotlight stopped this turn because the ${agent} was refused ${count} times with nothing changed in the case, and code cannot take the next step itself.${where} Say what to change, or ask Spotlight to try again.`;
}

// Applies only where the turn would go to the model, so the router's own
// resolve comes first and `prove` reads the case as it is now.
export function routeRefusedSpawn(state: RouterState, move: RouterMove, spawns: SpawnRefusals, digest: string, prove: (refused: { agent: string; refusal: string }, resolved: Record<string, unknown>) => CodeSpawn | undefined): RouterMove {
	if (move.kind !== 'model' || spawns.digest !== digest) return move;
	const repeated = [...spawns.seen.values()].find((spawn) => spawn.count >= REFUSED_SPAWNS);
	if (!repeated) return move;
	const proven = state.resolved ? prove(repeated, state.resolved) : undefined;
	// Per records state: a later cycle that owes the same move has made progress.
	const key = proven && JSON.stringify([proven.agent, proven.task, digest]);
	if (proven && key && !spawns.moved.has(key)) {
		spawns.moved.add(key);
		return { kind: 'call', tool: 'spawn_agent', arguments: proven, reason: `the ${repeated.agent} spawn was refused ${repeated.count} times; code takes the move` };
	}
	spawns.stopped = true;
	return { kind: 'stop', text: refusedSpawnStop(repeated.agent, repeated.count, state.resolved), reason: `the ${repeated.agent} spawn was refused ${repeated.count} times and code can prove no move` };
}

// Router on for the local tiers, off for frontier; SPOTLIGHT_CODE_ROUTER forces it.
export function routerOn(env: Record<string, string | undefined>): boolean {
	const forced = env.SPOTLIGHT_CODE_ROUTER?.trim();
	if (forced === 'on') return true;
	if (forced === 'off') return false;
	return (env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') !== 'frontier';
}
