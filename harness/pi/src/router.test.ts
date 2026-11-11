// Plan step 4 (reports/step4-routing-design.md): the code router's rule at its
// edges. The end-to-end route is in the launcher tests ("with the code router, a
// model that only writes text does too").
import assert from 'node:assert/strict';
import test from 'node:test';
import { ROUTER_MODEL, memberHasTheMove, noteRefusedSpawn, routeMember, routeRefusedSpawn, routeWithoutMember, routerOn, routerState, spawnRefusals } from './router.ts';
import { INPUTS_REFUSED, refusedSpawnMove } from './tools/spotlight.ts';
import { FACT_CHECKERS_SPENT } from './subagents.ts';
import { EMPTY_TURN_NUDGE, RUNAWAY_TURN_NUDGE } from './extension.ts';

const resolved = (state: Record<string, unknown>) => ({ role: 'toolResult', toolName: 'spotlight_resolve', content: [{ type: 'text', text: JSON.stringify(state) }] });
const call = (name: string, routed = false) => ({ role: 'assistant', content: [{ type: 'toolCall', name }], ...(routed ? { responseModel: ROUTER_MODEL } : {}) });
const result = (toolName: string) => ({ role: 'toolResult', toolName, content: [{ type: 'text', text: 'ok' }] });
const member = (text: string) => ({ role: 'user', content: text });

test('the code router resolves after any event, follows open_review, and otherwise hands the turn to the model', () => {
	assert.deepEqual(routeWithoutMember(routerState([])), { kind: 'call', tool: 'spotlight_resolve', reason: 'no resolve yet' });
	const atGate = [call('spotlight_resolve', true), resolved({ phase: 'methodology_approval', next_action: 'Next: call open_review so the member can approve the methodology.' })];
	assert.equal((routeWithoutMember(routerState(atGate)) as { tool?: string }).tool, 'open_review');
	// After the page opened, the model ends the turn with its words.
	assert.equal(routeWithoutMember(routerState([...atGate, call('open_review', true), result('open_review')])).kind, 'model');
	// A worker spawn since the last resolve: resolve again.
	assert.equal((routeWithoutMember(routerState([...atGate, call('spawn_agent'), result('spawn_agent')])) as { tool?: string }).tool, 'spotlight_resolve');
	// Nothing for code to do: the model.
	assert.equal(routeWithoutMember(routerState([call('spotlight_resolve', true), resolved({ phase: 'brief', next_action: 'Next: restate the lead.' })])).kind, 'model');
});

test('a stop at Research after a failed round waits for the member, whose message runs it again once', () => {
	const stopped = resolved({ phase: 'blocked', status: 'blocked', blocked: { phase: 'research', exhausted_attempt: 'research-lane', gap: 'OpenRouter key rejected\nmore' }, next_action: 'Next: tell the member. Once they say so, call open_review.' });
	const inTurn = [{ role: 'user', content: 'Continue.' }, call('spawn_agent'), result('spawn_agent'), call('spotlight_resolve', true), stopped];
	const stop = routeWithoutMember(routerState(inTurn));
	assert.equal(stop.kind, 'stop', 'the turn that failed does not run again by itself');
	assert.match((stop as { text: string }).text, /failed three times \(OpenRouter key rejected\)[\s\S]*send a message here[\s\S]*runs the research again/);
	assert.ok(memberHasTheMove((stop as { text: string }).text));
	const answered = [{ role: 'user', content: 'The key is fixed.' }, call('spotlight_resolve', true), stopped];
	assert.equal((routeWithoutMember(routerState(answered)) as { tool?: string }).tool, 'open_review');
	assert.equal((routeWithoutMember(routerState([...answered, call('open_review'), result('open_review')])) as { tool?: string }).tool, 'spotlight_resolve', 'then the case goes on');
});

test('a member message: approval talk writes the brief only while the Brief resolve is the latest event; an unanswered intent goes to the model', () => {
	const brief = [call('spotlight_resolve', true), resolved({ phase: 'brief' })];
	assert.equal(routeMember(routerState([...brief, member('Yes, go ahead.')]), 'approval_talk').kind, 'model');
	// step3a-routing.md resolve:r6:29: calls since the Brief resolve make that phase stale.
	const stale = [...brief, call('write_file'), result('write_file'), member('Okay, I have decided.')];
	assert.equal((routeMember(routerState(stale), 'approval_talk') as { tool?: string }).tool, 'spotlight_resolve');
	assert.equal(routeMember(routerState(stale), 'question').kind, 'model');
	assert.equal(routeMember(routerState(stale), 'change_request').kind, 'model');
	assert.equal(routeMember(routerState(stale), undefined).kind, 'model');
	// A turn the harness wrote is not a member message.
	assert.equal(routerState([...brief, member('Your turn ended with text and no tool call.')]).member, undefined);
	assert.equal(routerState([...brief, member(EMPTY_TURN_NUDGE)]).member, undefined);
	assert.equal(routerState([...brief, member(RUNAWAY_TURN_NUDGE)]).member, undefined);
	assert.equal(routerState([...brief, member('Your last turn was empty. Please also add the 2019 filing.')]).member, 'Your last turn was empty. Please also add the 2019 filing.');
});

// K5 Tor P1: the member's research topic at the methodology review was only
// answered in prose and the plan was approved without it.
test('a member change at the methodology review goes to the investigator as a correction in their words; elsewhere, and questions, stay the model\'s', () => {
	const asked = 'Before I approve, add a research topic on the Tor Project\'s board of directors today.';
	const review = [call('spotlight_resolve', true), resolved({ phase: 'methodology_approval', next_action: 'Next: call open_review.' }), call('open_review', true), result('open_review'), member(asked)];
	const move = routeMember(routerState(review), 'change_request') as { tool?: string; arguments?: { agent: string; task: string } };
	assert.equal(move.tool, 'spawn_agent');
	assert.equal(move.arguments?.agent, 'investigator');
	assert.match(move.arguments!.task, /^MODE: METHODOLOGY CORRECTION\n/);
	assert.ok(move.arguments!.task.includes(`\n${asked}\n`), 'the member\'s words go verbatim');
	assert.equal(routeMember(routerState(review), 'question').kind, 'model');
	const later = [call('spotlight_resolve', true), resolved({ phase: 'investigation' }), member(asked)];
	assert.equal(routeMember(routerState(later), 'change_request').kind, 'model');
});

test('the router hands the turn to the model rather than repeat its own call with nothing new, and is on for the local tiers only', () => {
	// A refused resolve leaves no new state: a second router resolve is not made.
	const refused = [call('spotlight_resolve', true), { role: 'toolResult', toolName: 'spotlight_resolve', content: [{ type: 'text', text: 'resolver failed' }] }];
	assert.equal(routeWithoutMember(routerState(refused)).kind, 'model');
	assert.equal(routerOn({ SPOTLIGHT_MODEL_TIER: '12b' }), true);
	assert.equal(routerOn({ SPOTLIGHT_MODEL_TIER: 'frontier' }), false);
	assert.equal(routerOn({ SPOTLIGHT_MODEL_TIER: 'frontier', SPOTLIGHT_CODE_ROUTER: 'on' }), true);
	assert.equal(routerOn({ SPOTLIGHT_MODEL_TIER: '27b', SPOTLIGHT_CODE_ROUTER: 'off' }), false);
});

// B11: the harness fields of held-sfc and held-kde at the repeat (qwen-u9-loop-state.md).
const SFC = { phase: 'investigation', status: 'active', missing: ['fresh data/fact-check.json after the investigator\'s update'], follow_up: { instructions: 'x' } };
const KDE = { phase: 'investigation', status: 'active', missing: ['data/fact-check.json'] };
const atModel = (state: Record<string, unknown>) => routerState([call('spotlight_resolve', true), resolved(state)]);
const handed = { kind: 'model', reason: 'the resolver names no code move' } as const;
const refusedTwice = (agent: string, refusal: string) => {
	const spawns = spawnRefusals();
	noteRefusedSpawn(spawns, agent, refusal, 'd');
	noteRefusedSpawn(spawns, agent, refusal, 'd');
	return spawns;
};

test('a spawn refused twice with the same words: code starts the fact-checker the resolver owes, once, then stops for the member', () => {
	const spawns = refusedTwice('analyst', 'spawn_agent: the analyst runs only at investigation_gate_approval, report, ingest; the case is at investigation.');
	const move = routeRefusedSpawn(atModel(SFC), handed, spawns, 'd', refusedSpawnMove) as { tool?: string; arguments?: { agent: string; task: string } };
	assert.equal(move.tool, 'spawn_agent');
	assert.deepEqual({ agent: move.arguments?.agent, task: move.arguments?.task }, { agent: 'fact-checker', task: '' });
	const again = routeRefusedSpawn(atModel(SFC), handed, spawns, 'd', refusedSpawnMove) as { kind: string; text?: string };
	assert.equal(again.kind, 'stop');
	assert.match(again.text!, /the analyst was refused 2 times/);
	assert.ok(again.text!.includes(SFC.missing[0]));
	assert.equal(spawns.stopped, true);
	// Codex review of B11, P2: a later cycle, with changed records, owes the same move.
	noteRefusedSpawn(spawns, 'analyst', 'no', 'later');
	noteRefusedSpawn(spawns, 'analyst', 'no', 'later');
	assert.equal(routeRefusedSpawn(atModel(SFC), handed, spawns, 'later', refusedSpawnMove).kind, 'call');
});

test('a refused fact-checker: code sends the investigator the input errors the refusal carries, and never restarts the refused worker', () => {
	const refusal = `spawn_agent: ${INPUTS_REFUSED}\n  • [investigator] evidence-bundle.json: item D1-E1 has no extraction_confidence`;
	const repair = routeRefusedSpawn(atModel(KDE), handed, refusedTwice('fact-checker', refusal), 'd', refusedSpawnMove) as { arguments?: { agent: string; task: string } };
	assert.equal(repair.arguments?.agent, 'investigator');
	assert.match(repair.arguments!.task, /^MODE: STRUCTURAL CORRECTION\n[\s\S]*D1-E1/);
	assert.equal(routeRefusedSpawn(atModel(KDE), handed, refusedTwice('fact-checker', FACT_CHECKERS_SPENT), 'd', refusedSpawnMove).kind, 'stop');
});

test('one refusal, changed records and the router\'s own call all leave the turn as it was', () => {
	const once = spawnRefusals();
	noteRefusedSpawn(once, 'analyst', 'no', 'd');
	assert.deepEqual(routeRefusedSpawn(atModel(KDE), handed, once, 'd', refusedSpawnMove), handed);
	const twice = refusedTwice('analyst', 'no');
	assert.deepEqual(routeRefusedSpawn(atModel(KDE), handed, twice, 'changed', refusedSpawnMove), handed);
	noteRefusedSpawn(twice, 'analyst', 'no', 'changed');
	assert.deepEqual(routeRefusedSpawn(atModel(KDE), handed, twice, 'changed', refusedSpawnMove), handed, 'the count starts again after a change');
	const resolve = { kind: 'call', tool: 'spotlight_resolve', reason: 'fresh state first' } as const;
	assert.deepEqual(routeRefusedSpawn(atModel(SFC), resolve, refusedTwice('analyst', 'no'), 'd', refusedSpawnMove), resolve);
});

// D1 loads on demand: a triage it could not answer ends on code's line, not the model's retelling (qwen-no-d1.md: asked the same question four times).
test('a round held at triage stops on code\'s words, naming the failure and both ways on', () => {
	const held = routerState([call('spotlight_resolve', true), resolved({ phase: 'research', research: { resume_at: 'triage' }, research_round: { triage: { ok: false, error: 'traceback\nresearch-triage: no triage written: local unavailable: HTTP 503' } } })]);
	const move = routeWithoutMember(held) as { kind: string; text: string };
	assert.equal(move.kind, 'stop');
	assert.match(move.text, /could not triage[\s\S]*HTTP 503[\s\S]*Send a message here to try again[\s\S]*turn Research off/);
	assert.doesNotMatch(move.text, /traceback/);
});
