// Plan step 4 (reports/step4-routing-design.md): the code router's rule at its
// edges. The end-to-end route is in launcher.test.ts ("with the code router, a
// model that only writes text does too").
import assert from 'node:assert/strict';
import test from 'node:test';
import { ROUTER_MODEL, routeMember, routeWithoutMember, routerOn, routerState } from './router.ts';

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
