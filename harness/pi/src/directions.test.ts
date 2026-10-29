import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { directionBlock, planDirections, requestedDirection } from './directions.ts';

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
