import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { withInvestigationPacket } from './investigation-packet.ts';

async function fixture(t: TestContext, research = true) {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-inv-packet-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	const caseDir = join(root, 'case');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	const write = async (name: string, value: unknown) => writeFile(join(caseDir, 'data', `${name}.json`), JSON.stringify(value));
	await write('methodology', { investigation_plan: [{ direction_id: 'rates', direction: 'Was the August release revised?', questions: ['Distinguish original and revised figures.'], steps: [] }], ...(research ? { research_plan: { round_budget_usd: 0.2, case_budget_usd: 0.5 } } : {}) });
	return { root, caseDir, write, packet: () => withInvestigationPacket('investigator', 'MODE: EXECUTION\nDIRECTION_ID: direction:rates', { caseDir, phase: 'investigation' }) };
}

test('the handoff retains contrary and uncertain Research leads without treating triage as truth', async (t) => {
	const f = await fixture(t);
	const content = 'The August figure was revised from 6.4% to 6.5%.\n';
	await mkdir(join(f.caseDir, 'research/swarm/rates/sources'), { recursive: true });
	await writeFile(join(f.caseDir, 'research/swarm/rates/sources/revision.md'), content);
	await f.write('leads', { spend_usd: 0.04, leads: [{ lead: { lead_id: 'revision', topic_id: 'rates', direction_id: 'rates', status: 'candidate', summary: 'Revision must be checked.', content_ref: 'sources/revision.md', content_sha256: createHash('sha256').update(content).digest('hex'), capture: { captured_at: '2026-11-01T00:00:00Z' }, excerpt: { text: content.trim(), language: 'en' } } }], gaps: [{ direction_id: 'rates', queries_tried: ['original vintage'] }] });
	await f.write('clues', { clues: [{ clue_id: 'C1', type: 'conflict', model_unsure: true, lead_ids: ['revision'], summary: 'Original and revision differ.', direction_ids: ['rates'] }] });
	const packet = f.packet();
	assert.match(packet, /August figure was revised from 6.4% to 6.5%/);
	assert.match(packet, /"model_unsure":true/);
	assert.match(packet, /"research_gap"/);
	assert.match(packet, /"research_spend_usd":0.04/);
	assert.match(packet, /"approved_round_usd":0.2/);
	assert.match(packet, /Capture times are custody times, not publication dates/);
	assert.match(packet, /triage prioritizes work and never verifies a claim/);
	await writeFile(join(f.caseDir, 'research/swarm/rates/sources/revision.md'), 'The capture changed.');
	const changed = f.packet();
	assert.doesNotMatch(changed, /"exact_excerpt"/);
	assert.match(changed, /capture changed or excerpt not verbatim/);
});

test('bounded handoffs disclose incomplete coverage and prioritize conflict clues without cutting quotes', async (t) => {
	const f = await fixture(t);
	await f.write('clues', { clues: [{ clue_id: 'ordinary', type: 'change', summary: 'x'.repeat(20_000) }, { clue_id: 'critical', type: 'conflict', summary: 'Contrary values remain unresolved.', model_unsure: true }] });
	const packet = f.packet();
	assert.ok(packet.length < 12_000);
	assert.match(packet, /Contrary values remain unresolved/);
	assert.match(packet, /"complete":false/);
	assert.match(packet, /"data\/clues.json":1/);
	assert.match(packet, /omitted material may contain contradictions/);
	assert.doesNotMatch(packet, /x{20}/);
});

test('Research-off uses retained captures and does not import stray Research or other roles packets', async (t) => {
	const f = await fixture(t, false);
	await f.write('leads', { leads: [{ lead: { summary: 'STRAY-RESEARCH-SECRET' } }] });
	await f.write('evidence-bundle', { items: [{ id: 'E1', raw_path: 'research/release.md.raw', source_url: 'https://example.org/release', accessed: '2026-11-01T00:00:00Z' }] });
	assert.match(f.packet(), /research\/release.md.raw/);
	assert.doesNotMatch(f.packet(), /STRAY-RESEARCH-SECRET/);
	for (const role of ['fact-checker', 'analyst', 'researcher', 'orchestrator']) assert.equal(withInvestigationPacket(role, 'task', { caseDir: f.caseDir, phase: 'investigation' }), 'task');
	for (const phase of ['methodology', 'research', 'research_approval', 'report']) assert.equal(withInvestigationPacket('investigator', 'task', { caseDir: f.caseDir, phase }), 'task');
	assert.equal(withInvestigationPacket('investigator', 'MODE: STRUCTURAL CORRECTION\nRepair.', { caseDir: f.caseDir, phase: 'investigation' }), 'MODE: STRUCTURAL CORRECTION\nRepair.');
});

test('packet preparation refuses case metadata symlinks outside the case', async (t) => {
	const f = await fixture(t);
	await writeFile(join(f.root, 'private.json'), '{"leads":[{"lead":{"summary":"secret"}}]}');
	await symlink(join(f.root, 'private.json'), join(f.caseDir, 'data/leads.json'));
	assert.throws(f.packet, /outside the case files this agent may read/);
});

test('capture references cannot launder denied FC diagnostics or a different direction into the packet', async (t) => {
	const f = await fixture(t);
	const text = 'PRIVATE-FC-JUDGMENT';
	await mkdir(join(f.caseDir, 'data/harness-checks/fact-checker'), { recursive: true });
	await mkdir(join(f.caseDir, 'research/swarm/rates/sources'), { recursive: true });
	await writeFile(join(f.caseDir, 'data/harness-checks/fact-checker/private.txt'), text);
	await symlink(join(f.caseDir, 'data/harness-checks/fact-checker/private.txt'), join(f.caseDir, 'research/swarm/rates/sources/link.txt'));
	for (const content_ref of ['sources/link.txt', '../../../data/harness-checks/fact-checker/private.txt']) {
		await f.write('leads', { leads: [{ lead: { lead_id: 'bad', topic_id: 'rates', direction_id: 'rates', content_ref, content_sha256: createHash('sha256').update(text).digest('hex'), excerpt: { text } } }] });
		assert.doesNotMatch(f.packet(), /PRIVATE-FC-JUDGMENT/);
		assert.match(f.packet(), /capture[^\n]+refused/);
	}
	await f.write('evidence-bundle', { items: [{ id: 'D1-E1', notes: 'ASSIGNED-CAPTURE' }, { id: 'D2-E1', notes: 'OTHER-DIRECTION-CAPTURE' }, { id: 'E0', notes: 'GENERAL-CONTEXT' }] });
	await f.write('source-expressions', { expressions: [{ id: 'P1', text: 'ASSIGNED-PASSAGE', finding_links: [{ finding_id: 'D1-F1' }], lifecycle_events: [{ event: 'activated' }] }, { id: 'P2', text: 'OTHER-DIRECTION-PASSAGE', finding_links: [{ finding_id: 'D2-F1' }], lifecycle_events: [{ event: 'activated' }] }] });
	const packet = f.packet();
	assert.match(packet, /ASSIGNED-CAPTURE/);
	assert.match(packet, /ASSIGNED-PASSAGE/);
	assert.match(packet, /GENERAL-CONTEXT/);
	assert.doesNotMatch(packet, /OTHER-DIRECTION/);
	assert.match(packet, /"excluded_other_direction_captures":1/);
});

test('a relevant conflict retains its linked cross-direction counterevidence as context', async (t) => {
	const f = await fixture(t);
	await f.write('leads', { leads: [{ lead: { lead_id: 'current', direction_id: 'rates', summary: 'Original figure was 6.4%.' } }, { lead: { lead_id: 'revision', direction_id: 'revision-history', summary: 'COUNTEREVIDENCE: revised figure is 6.5%.' } }, { lead: { lead_id: 'unrelated', direction_id: 'people', summary: 'UNRELATED-PERSON-TASK' } }] });
	await f.write('clues', { clues: [{ clue_id: 'conflict', type: 'conflict', direction_ids: ['rates'], lead_ids: ['current', 'revision'], summary: 'Versions disagree.', model_unsure: true }] });
	const packet = f.packet();
	assert.match(packet, /COUNTEREVIDENCE: revised figure is 6.5%/);
	assert.match(packet, /"direction_id":"revision-history"[^\n]+"context_only":true/);
	assert.match(packet, /Cross-direction leads are context for that question, not additional task assignments/);
	assert.doesNotMatch(packet, /UNRELATED-PERSON-TASK/);
});
