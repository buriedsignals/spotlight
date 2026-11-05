// Public prompt contracts for code-owned recording: capability hints must
// describe callable tools and examples must not solicit invented archives.
import assert from 'node:assert/strict';
import test from 'node:test';
import { batchDeliverable, deliverableFor, loadComposition, systemPrompt } from './roles.ts';
import type { WorkerContext } from './roles.ts';

const worker: WorkerContext = { phase: 'investigation', task: 'MODE: EXECUTION', conditions: { vault: false, framework: false, sensitive: false, research: false, expressions: true } };

test('recording instructions follow the runtime tools across every tier without assigning semantic judgments to code', () => {
	for (const tier of ['12b', '27b', 'frontier'] as const) for (const [role, tool] of [['investigator', 'record_finding'], ['fact-checker', 'record_assessment']] as const) {
		const composition = loadComposition();
		const prompt = systemPrompt(composition, role, tier, 'solo', false, worker);
		assert.match(prompt, new RegExp(`submit one structured (?:finding|judgment) through \\x60${tool}\\x60`, 'i'), `${role}/${tier}`);
		assert.match(prompt, /You choose (?:claims, evidence, relations and confidence|verdict, confidence and evidence)/);
		const missing = structuredClone(composition);
		missing[role].tools = missing[role].tools.filter((name) => name !== tool);
		const fallback = systemPrompt(missing, role, tier, 'solo', false, worker);
		assert.ok(!fallback.includes(`through \`${tool}\``), `${role}/${tier}: absent tools get no capability hint`);
		assert.doesNotMatch(prompt, /"archive_url"\s*:\s*"(?:Wayback Machine|https:\/\/web\.archive\.org\/\.\.\.)/);
		assert.match(prompt, /Never invent a snapshot URL or timestamp; omit archive_url unless an actual acquisition established it/);
	}
});

test('execution deliverables name structured record operations while methodology remains file-based', () => {
	assert.match(deliverableFor('investigator', 'investigation', 'MODE: EXECUTION')!.line, /record_finding/);
	assert.match(deliverableFor('fact-checker', 'investigation', 'Check assigned finding.')!.line, /record_assessment/);
	assert.match(deliverableFor('investigator', 'methodology', 'MODE: PLANNING')!.line, /write_file/);
});


test('typed record capabilities suppress only superseded file recipes across tiers', () => {
	for (const tier of ['12b', '27b', 'frontier'] as const) for (const [role, tool, filename] of [['investigator','record_finding','findings'],['fact-checker','record_assessment','fact-check']] as const) {
		const composition=loadComposition();
		const prompt=systemPrompt(composition,role,tier,'solo',false,worker);
		const recipe=`write-file("{CASE_DIR}/data/${filename}.json"`;
		assert.ok(!prompt.includes(recipe),`${role}/${tier}: typed calls replace the canonical file serialization recipe`);
		assert.doesNotMatch(prompt,/In Pi use `write_file` with complete new or corrected claim objects/);
		if(role==='fact-checker') {
			assert.doesNotMatch(prompt,/"source_document": "\{CASE_DIR\}\/data\/findings\.json"/,'the code-owned assessment envelope must not be model work');
			assert.match(prompt,/without usable independent acquisition, investigator-only evidence is never `verified`/,'independent verification is still required');
		} else assert.match(prompt.replace(/\s+/g,' '),/Never invent text, a locator, a hash/,'exact-passage selection safeguards survive recipe removal');
		assert.match(prompt,/Never invent a snapshot URL or timestamp; omit archive_url unless an actual acquisition established it/);
		const fallback=structuredClone(composition);
		fallback[role].tools=fallback[role].tools.filter(name=>name!==tool);
		assert.ok(systemPrompt(fallback,role,tier,'solo',false,worker).includes(recipe),`${role}/${tier}: hosts without the recorder retain a complete file-writing path`);
	}
});

test('fact-check batch templates are individual assessment arguments without code-owned envelopes', () => {
	const docs={findings:{schema_version:'1.1',project:'public-fixture',findings:[{id:'F1',claim:'Public record A.'},{id:'F2',claim:'Public record B.'}]}};
	const batch=batchDeliverable('/private/tmp/spotlight-public-prompt-fixture',docs,['F1','F2']);
	const start=batch.start!;
	const at=start.lastIndexOf('\n[');
	assert.ok(at>=0,'the final template is an array of separate record_assessment arguments');
	const calls=JSON.parse(start.slice(at+1));
	assert.deepEqual(calls.map((call: {assessment: {finding_id: string}})=>call.assessment.finding_id),['F1','F2']);
	for(const call of calls) {
		assert.deepEqual(Object.keys(call),['assessment']);
		assert.ok(!Object.hasOwn(call.assessment,'claim_text'),'the exact claim is copied by the canonical writer');
		assert.ok(Object.hasOwn(call.assessment,'verdict') && Object.hasOwn(call.assessment,'confidence'),'semantic judgments remain model inputs');
	}
	assert.doesNotMatch(batch.line,/If claim_text is blank.*copy its exact claim/);
	assert.match(batch.line,/context is not supporting evidence/);
});
