// record_indicator_framework against a synthetic bundle served by a stand-in
// `navigator`: every rating, quadrant, created item and the item order come
// from the fetched framework data, never from the harness.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { approve, hostedFixture } from '../test-support/hosted-skill.ts';
import { recordIndicatorFrameworkTool } from './indicator-framework.ts';

const indicator = (id: string, table_row: string, rating: Record<string, string> | undefined, extra: Record<string, unknown> = {}) => ({
	id,
	value: `value of ${id}`,
	found_at: ['page source'],
	collected_via: 'own fetch',
	collected_at: { fetched: '2000-02-01', source_date: null },
	table_row,
	element_of: null,
	...(rating ? { current_rating: rating } : {}),
	movement: [],
	pairing_status: 'open',
	root_decision: 'Hosting',
	branch_id: 'br-1',
	...extra,
});

const item = (item_id: string, kind: string) => ({ item_id, kind, indicator_ids: ['i-tag'], conclusion_ids: ['c-1'], open: `open ${item_id}`, suggested_step: `step ${item_id}` });

const recording = () => ({
	indicators: [
		indicator('i-box', 'container', { fidelity: 'Hi' }, { collected_at: { fetched: '2000-02-01', source_date: '20000103120000' } }),
		indicator('i-addr', 'A1', undefined, { element_of: 'i-box' }),
		indicator('i-tag', 'A2', { fidelity: 'Hi', specificity: 'Mid-Hi' }, { collected_at: { fetched: '2000-02-01', source_date: '946684800' } }),
		indicator('i-odd', 'unlisted', { fidelity: 'Lo', specificity: 'Hi' }, { collected_at: { fetched: '2000-02-01', source_date: '2000-01' } }),
	],
	branches: [{ branch_id: 'br-1', root_decision: 'Hosting', description: 'One host', indicator_ids: ['i-addr', 'i-tag', 'i-odd'], strongest: ['i-tag'] }],
	conclusions: [{ conclusion_id: 'c-1', question: 'link', statement: 'The sites are linked', branches_present: ['br-1'], decisions_not_represented: ['Money'], strengths: [], weaknesses: [] }],
	// m-pairing is the kind i-tag's rating implies, so it keeps its step; i-tag implies no recollect item.
	items: [item('m-dead', 'dead'), item('m-claim', 'claim'), item('m-pairing', 'pairing'), item('m-recollect', 'recollect')],
});

async function record(t: Parameters<typeof hostedFixture>[0], params: unknown) {
	const fixture = await hostedFixture(t);
	await approve(fixture.caseDir, true);
	const tool = recordIndicatorFrameworkTool(fixture.context(), fixture.caseDir);
	const run = () => tool.execute('call', params, undefined, undefined, undefined as never);
	return { ...fixture, run };
}

test('the harness fills starting ratings, quadrants, created items, their order and the temporal summary from the framework data, keeping the model\'s text on an item its ratings imply', async (t) => {
	const { caseDir, run } = await record(t, recording());

	const result = await run();
	assert.match((result.content[0] as { text: string }).text, /4 indicators, 1 branches, 1 conclusions, 5 items \(2 created from your ratings\)\. 1 item\(s\) were dropped/);
	const file = JSON.parse(await readFile(join(caseDir, 'data/indicator-framework.json'), 'utf8'));
	const byId = Object.fromEntries(file.indicators.map((entry: { id: string }) => [entry.id, entry]));
	assert.deepEqual(byId['i-box'].current_rating, { fidelity: 'Hi' }, 'a container is not plotted');
	assert.deepEqual(byId['i-addr'].starting_rating, { fidelity: 'Hi', specificity: 'Lo', quadrant: 'Context' });
	assert.deepEqual(byId['i-tag'].starting_rating, { fidelity: 'Hi', specificity: 'Mid-Hi', quadrant: 'Context/Anchor' }, 'an alias places like its value');
	assert.equal(byId['i-tag'].current_rating.quadrant, 'Context/Anchor');
	assert.equal(byId['i-odd'].starting_rating, null, 'no row, no starting rating');
	assert.equal(byId['i-odd'].current_rating.quadrant, 'Suspect');
	assert.deepEqual(
		file.indicator_gaps.map((entry: { kind: string; item_id: string }) => `${entry.kind}:${entry.item_id}`),
		['no-rating:auto-no-rating-i-addr', 'recollect:auto-recollect-i-odd', 'claim:m-claim', 'pairing:m-pairing', 'dead:m-dead'],
	);
	assert.equal(file.indicator_gaps[3].suggested_step, 'step m-pairing', 'the model\'s case-specific step is kept');
	assert.ok(file.indicator_gaps.every((entry: { assign_to: unknown }) => entry.assign_to === null));
	assert.equal(file.temporal_summary, '2000-01 value of i-odd (i-odd) · 2000-01-01T00:00:00Z value of i-tag (i-tag) · 2000-01-03T12:00:00Z value of i-box (i-box)');
	assert.equal(file.framework_version, '2000-01-01');
	const report = await readFile(join(caseDir, 'data/indicator-framework.md'), 'utf8');
	assert.match(report, /\| i-tag \| value of i-tag \| A2 \| Hi \| Mid-Hi \| Context\/Anchor \| br-1 \|/);
	assert.ok(!JSON.stringify(file).includes('CAVEAT') && !report.includes('CAVEAT') && !report.includes('T4'), 'no framework text in the case');
});

test('a value outside the framework data is refused, every problem named, and nothing is written', async (t) => {
	const params = recording();
	params.indicators[2] = indicator('i-tag', 'Z9', { fidelity: 'Huge', specificity: 'Hi' }, { collected_at: { fetched: '2000-02-01', source_date: 'last spring' } });
	params.items.push(item('m-bogus', 'bogus'));
	const { caseDir, run } = await record(t, params);

	await assert.rejects(run(), (error: Error) =>
		['table_row "Z9" is not a row id', 'fidelity must be one of Hi, Mid, Lo', 'source_date "last spring"', 'item m-bogus: kind must be one of claim, dead'].every((part) => error.message.includes(part)),
	);
	assert.equal(existsSync(join(caseDir, 'data/indicator-framework.json')), false);
	assert.equal(existsSync(join(caseDir, 'data/indicator-framework.md')), false);
});
