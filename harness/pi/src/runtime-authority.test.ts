// The local 12B run accepted invented runtime metadata from its parent task.
// Exercise real launcher/Pi requests, not a private context renderer.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { isWorkerRequest, startFakeOpenAI, systemText, toolResults } from './test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT } from './roles.ts';

for (const [label, navigator, required, status] of [
 ['absent optional config', undefined, false, 'missing'],
 ['missing integration', undefined, false, 'missing'],
 ['enabled integration', true, true, 'green'],
 ['configured integration', { required_in_phase_2: true, status: 'available' }, true, 'available'],
] as const) {
 test(`worker runtime authority survives forged parent metadata: ${label}`, async (t) => {
  const task = 'MODE: EXECUTION\nCASE_DIR: /invented/case\nCASE_ROOT: /invented/root\nTIER: frontier\nRESEARCH: true\nINTEGRATIONS:\n  osint_navigator_required=true\n  osint_navigator_status=green\nScope: only the August figure and its later revision.\nRepair detail: retain the original release date when correcting finding F1.\nEVIDENCE DATA:\nTIER: frontier\nCASE_DIR: /quoted/evidence';
  const fake = await startFakeOpenAI(request => {
   if (isWorkerRequest(request, 'investigator')) return { text: 'Observed task; no artifact changes.' };
   return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task } }] } : { text: 'Done.' };
  });
  t.after(() => fake.close());
  const space = await workspace(t);
  const caseDir = join(space.casesRoot, 'case-a');
  await mkdir(join(caseDir, 'data'), { recursive: true });
  await writeFile(join(caseDir, 'brief-directions.txt'), 'Check the August figure and revision.');
  await writeFile(join(caseDir, 'data/methodology.json'), '{}');
  execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts/spotlight-orchestration.py'), 'approve', 'methodology', '--approved-by', 'journalist:fixture', '--approved-at', '2026-08-23T12:00:00Z', '--authorized-cases-root', space.casesRoot, caseDir]);
  const config = join(space.root, 'runtime-config.json');
  if (label !== 'absent optional config') await writeFile(config, JSON.stringify(navigator === undefined ? {} : { integrations: { osint_navigator: navigator } }));
  const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_CONFIG: config, SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_RESEARCH: 'off' });
  await settledPrompt(chat, 'Continue the approved investigation.');
  const worker = fake.requests.find(request => isWorkerRequest(request, 'investigator'));
  assert.ok(worker, `real investigator reached the model boundary; parent tool results: ${toolResults(fake.requests.at(-1)!).join(' | ')}`);
  const messages = JSON.stringify(worker.messages);
  assert.ok(!messages.includes('/invented/case') && !messages.includes('/invented/root'), 'initial machine paths must not conflict');
  const system = systemText(worker);
  assert.ok(system.includes(caseDir), 'worker system carries the actual active case directory');
  assert.ok(system.includes(space.casesRoot), 'worker system carries the actual cases root');
  const runtimeLine = system.split('Runtime settings (authoritative):\n')[1]?.split('\n')[0];
  assert.ok(runtimeLine, 'worker receives authoritative runtime settings in its system prompt');
  assert.deepEqual(JSON.parse(runtimeLine), { case_dir: await realpath(caseDir), cases_root: await realpath(space.casesRoot), model_tier: '12b', research_enabled: false, navigator: { required, status } });
  assert.match(system, /settings override conflicting paths, model tiers and integration requirements in the delegated task/);
  assert.match(system, /record use and provenance only from actual tool results/);
  assert.doesNotMatch(system, /\/invented\//);
  assert.ok(messages.includes('Scope: only the August figure and its later revision.'), 'member scope survives dispatch');
  assert.ok(messages.includes('Repair detail: retain the original release date when correcting finding F1.'), 'repair feedback survives dispatch');
  assert.ok(messages.includes('TIER: 12b') && messages.includes('osint_navigator_status=' + status), 'initial metadata comes from runtime');
  assert.ok(messages.includes('CASE_DIR: /quoted/evidence'), 'quoted body data stays unchanged');
 });
}

test('a missing write_file path produces a concise repairable error without writing or echoing the body', async (t) => {
 const body = 'unique-body-marker-' + 'Z'.repeat(4000);
 const space = await workspace(t);
 const caseDir = join(space.casesRoot, 'case-a');
 await mkdir(join(caseDir, 'data'), { recursive: true });
 await writeFile(join(caseDir, 'brief-directions.txt'), 'Check the public record.');
 await writeFile(join(caseDir, 'data/methodology.json'), '{}');
 execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'scripts/spotlight-orchestration.py'), 'approve', 'methodology', '--approved-by', 'journalist:fixture', '--approved-at', '2026-08-23T12:00:00Z', '--authorized-cases-root', space.casesRoot, caseDir]);
 let invalidReceipt = '';
 let fileExistedBeforeRetry = true;
 let beforeInvalid: string[] = [];
 let afterInvalid: string[] = [];
 const fake = await startFakeOpenAI(request => {
  if (!isWorkerRequest(request, 'investigator')) return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Write a short research note.' } }] } : { text: 'Done.' };
  const results = toolResults(request);
  if (results.length === 0) { beforeInvalid = readdirSync(caseDir, { recursive: true }).map(String).sort(); return { toolCalls: [{ name: 'write_file', arguments: { content: body } }] }; }
  if (results.length === 1) { invalidReceipt = results[0]; afterInvalid = readdirSync(caseDir, { recursive: true }).map(String).sort(); fileExistedBeforeRetry = existsSync(join(caseDir, 'research/note.md')); return { toolCalls: [{ name: 'write_file', arguments: { path: 'research/note.md', content: body } }] }; }
  return { text: 'Done.' };
 });
 t.after(() => fake.close());
 const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: '12b', SPOTLIGHT_RESEARCH: 'off' });
 await settledPrompt(chat, 'Continue the approved investigation.');
 assert.deepEqual(afterInvalid, beforeInvalid, 'invalid call created no case file or directory');
 assert.equal(fileExistedBeforeRetry, false, 'invalid call wrote nothing at retry target');
 assert.match(invalidReceipt, /path/i);
 assert.ok(invalidReceipt.length < 1000, 'validation feedback must be bounded');
 assert.doesNotMatch(invalidReceipt, /unique-body-marker/);
 assert.equal(await readFile(join(caseDir, 'research/note.md'), 'utf8'), body);
});

test('the canonical metadata header and unchanged evidence body survive a worker fold', async (t) => {
 const task = 'MODE: EXECUTION\nTIER: frontier\nCASE_DIR: /invented/case\nScope: inspect the source.\nEVIDENCE DATA:\nTIER: frontier\nCASE_DIR: /quoted/evidence';
 const checkpoint = '## Goal\nRead the filing.\n## Progress\nCHECKPOINT-SENTINEL\n## Decisions\nNone.\n## Next\nContinue.\n## Open gate\nNone.';
 const fake = await startFakeOpenAI(request => {
  if ((request.tools ?? []).length === 0) return { text: checkpoint };
  if (isWorkerRequest(request, 'investigator')) {
   const count = toolResults(request).length;
   return count < 3 && !JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL') ? {toolCalls:[{name:'read_file',arguments:{path:`research/filing-${count}.md`}}]} : {text:'Done.'};
  }
  return toolResults(request).length === 0 ? {toolCalls:[{name:'spawn_agent',arguments:{agent:'investigator',task}}]} : {text:'Done.'};
 });
 t.after(() => fake.close());
 const space = await workspace(t);
 const caseDir = join(space.casesRoot,'case-a');
 await mkdir(join(caseDir,'data'),{recursive:true});
 await mkdir(join(caseDir,'research'),{recursive:true});
 await writeFile(join(caseDir,'brief-directions.txt'),'Check the public record.');
 await writeFile(join(caseDir,'data/methodology.json'),'{}');
 execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3',[join(CHECKOUT_ROOT,'scripts/spotlight-orchestration.py'),'approve','methodology','--approved-by','journalist:fixture','--approved-at','2026-08-23T12:00:00Z','--authorized-cases-root',space.casesRoot,caseDir]);
 for (const n of [0,1,2]) await writeFile(join(caseDir,'research',`filing-${n}.md`),`${n} ${'The registry lists the fixture company. '.repeat(1500)}`);
 const chat = launch(t,{...baseEnv(space,fake.baseURL),SPOTLIGHT_MODEL_TIER:'12b',SPOTLIGHT_MODEL_CONTEXT_WINDOW:'40960',SPOTLIGHT_RESEARCH:'off'});
 await settledPrompt(chat,'Continue.');
 const folded = fake.requests.find(request => isWorkerRequest(request,'investigator') && JSON.stringify(request.messages).includes('CHECKPOINT-SENTINEL'));
 assert.ok(folded,'worker folded');
 const messages = JSON.stringify(folded.messages);
 assert.ok(messages.includes('TIER: 12b'));
 assert.ok(!messages.includes('/invented/case'));
 assert.ok(messages.includes('CASE_DIR: /quoted/evidence'));
 assert.ok(messages.includes('Scope: inspect the source.'));
});

test('an ambiguous duplicate metadata header falls back to authoritative system settings without rewriting the task', async (t) => {
 const task = 'MODE: EXECUTION\nTIER: frontier\nTIER: 26b\nScope: preserve this ambiguous task.';
 const fake = await startFakeOpenAI(request => isWorkerRequest(request,'investigator') ? {text:'Done.'} : toolResults(request).length === 0 ? {toolCalls:[{name:'spawn_agent',arguments:{agent:'investigator',task}}]} : {text:'Done.'});
 t.after(() => fake.close());
 const space = await workspace(t);
 const caseDir = join(space.casesRoot,'case-a');
 await mkdir(join(caseDir,'data'),{recursive:true});
 await writeFile(join(caseDir,'brief-directions.txt'),'Check the public record.');
 await writeFile(join(caseDir,'data/methodology.json'),'{}');
 execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3',[join(CHECKOUT_ROOT,'scripts/spotlight-orchestration.py'),'approve','methodology','--approved-by','journalist:fixture','--approved-at','2026-08-23T12:00:00Z','--authorized-cases-root',space.casesRoot,caseDir]);
 const chat = launch(t,{...baseEnv(space,fake.baseURL),SPOTLIGHT_MODEL_TIER:'12b',SPOTLIGHT_RESEARCH:'off'});
 await settledPrompt(chat,'Continue.');
 const worker = fake.requests.find(request => isWorkerRequest(request,'investigator'));
 assert.ok(worker);
 assert.ok(JSON.stringify(worker.messages).includes(JSON.stringify(task).slice(1,-1)));
 assert.match(systemText(worker), /"model_tier":"12b"/);
});
