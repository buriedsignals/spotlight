// Plan step 4 (reports/step4-routing-design.md, approved 2026-11-03): on the
// local tiers code makes the orchestrator's routing moves. It reads the
// request's transcript the way the d1route and step3a replays did (the last
// spotlight_resolve result, the calls since it, a pending member message) and
// either names a move code makes (spotlight_resolve, open_review, or the
// investigator's correction carrying a member's change at the methodology
// review: never a transition or an approval) or hands the request to the model, which keeps
// every member-facing word. A member message is read by the decision model
// (stock D1, intent only); when it cannot answer, the model decides, as before.
// The router never refuses and never stops; it adds no cap.

export const ROUTER_MODEL = 'spotlight-code-router';
// Every router call's id starts with this, so a record of the request history
// (which keeps tool-call ids) still shows the call was code-made.
export const ROUTER_CALL_PREFIX = 'router-';

export type Intent = 'change_request' | 'question' | 'approval_talk' | 'other';
export type RouterMove = { kind: 'call'; tool: 'spotlight_resolve' | 'open_review'; reason: string } | { kind: 'call'; tool: 'spawn_agent'; arguments: { agent: 'investigator'; task: string }; reason: string } | { kind: 'model'; reason: string };
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

// The move when no member message is waiting.
export function routeWithoutMember(state: RouterState): RouterMove {
	// The page is open: the turn ends with the model's words (registerEndTurnOnOpen).
	if (state.lastTool === 'open_review') return { kind: 'model', reason: 'the review page was just opened' };
	let move: RouterMove;
	if (!state.resolved || state.callsSince > 0) move = { kind: 'call', tool: 'spotlight_resolve', reason: state.resolved ? `${state.callsSince} call(s) since the last resolve` : 'no resolve yet' };
	else if (typeof state.resolved.next_action === 'string' && state.resolved.next_action.includes('open_review')) move = { kind: 'call', tool: 'open_review', reason: 'the resolver names open_review' };
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

// Router on for the local tiers, off for frontier; SPOTLIGHT_CODE_ROUTER forces it.
export function routerOn(env: Record<string, string | undefined>): boolean {
	const forced = env.SPOTLIGHT_CODE_ROUTER?.trim();
	if (forced === 'on') return true;
	if (forced === 'off') return false;
	return (env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') !== 'frontier';
}
