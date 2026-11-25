import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { directionBlock, directionsToRun, planDirections, recordDirectionGap, requestedDirection } from './directions.ts';

test('unknown direction is refused with usable approved refs and an unscoped retry', async (t) => {
 const root = await mkdtemp(join(tmpdir(),'spotlight-directions-'));
 t.after(() => rm(root,{recursive:true,force:true}));
 await mkdir(join(root,'data'));
 await writeFile(join(root,'data','methodology.json'),JSON.stringify({investigation_plan:[{direction:'Original release',questions:['What did it say?'],steps:[]},{direction:'Later revision',questions:['What changed?'],steps:[]}]}));
 const directions = planDirections(root);
 const before = JSON.stringify(directions);
 assert.throws(() => requestedDirection('MODE: EXECUTION\nDIRECTION_ID: invented',directions), error => {
  const message = String(error);
  assert.match(message,/refused.*unknown|unknown.*refused/i);
  assert.match(message,/direction:d1/);
  assert.match(message,/Original release/);
  assert.match(message,/direction:d2/);
  assert.match(message,/Later revision/);
  assert.match(message,/omit.*DIRECTION_ID/i);
  return true;
 });
 assert.equal(requestedDirection('MODE: EXECUTION\nScope: preserve this direction.',directions),undefined);
 assert.equal(requestedDirection('DIRECTION_ID: direction:d2',directions)?.ref,'direction:d2');
 assert.equal(JSON.stringify(directions),before);
 assert.match(directionBlock(directions[1],2),/Later revision/);
});

// spotlight-stall 2026-11-25: the watchdog stopped direction d3 before its first record,
// D10 then blocked the case ("direction:d3 has no finding and no explicit gap") and the
// correction rounds could not clear it.
test('a direction left with no finding gets a research gap from code, and the next cycle runs it again', async (t) => {
	const caseDir = await mkdtemp(join(tmpdir(), 'spotlight-direction-gap-'));
	t.after(() => rm(caseDir, { recursive: true, force: true }));
	await mkdir(join(caseDir, 'data'));
	await writeFile(join(caseDir, 'data', 'methodology.json'), JSON.stringify({ investigation_plan: [
		{ direction_id: 'd1', direction: 'Entities' }, { direction_id: 'd2', direction: 'Filings' },
	] }));
	await writeFile(join(caseDir, 'data', 'findings.json'), JSON.stringify({ schema_version: '1.1', findings: [{ id: 'D1-F1', claim: 'A' }] }));
	const [d1, d2] = planDirections(caseDir);
	const stop = 'The investigator made no new accepted record within the progress watchdog limit and was stopped; its last results stand.\n[progress-watchdog receipt] {"rule":"no-first-record"}';

	assert.equal(recordDirectionGap(caseDir, d1, 'done'), undefined, 'a direction with a finding needs no gap');
	assert.match(recordDirectionGap(caseDir, d2, stop) ?? '', /No finding was recorded for this direction in this cycle\. The investigator's last words: The investigator made no new accepted record/);
	const findings = JSON.parse(await readFile(join(caseDir, 'data', 'findings.json'), 'utf8'));
	assert.deepEqual(findings.research_gaps.map((gap: { ref: string }) => gap.ref), [d2.ref]);
	assert.doesNotMatch(findings.research_gaps[0].reason, /receipt/);
	assert.deepEqual(findings.findings, [{ id: 'D1-F1', claim: 'A' }]);
	assert.equal(recordDirectionGap(caseDir, d2, stop), undefined, 'one gap per direction');
	assert.deepEqual(directionsToRun(caseDir, [d1, d2]).map((direction) => direction.ref), [d2.ref]);
});
