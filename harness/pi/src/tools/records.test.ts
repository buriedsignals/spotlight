// Typed canonical writes use the same production merge/refusal/quote boundary.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, rm, unlink, writeFile } from 'node:fs/promises';
import { readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { validateToolArguments } from '@earendil-works/pi-ai';
import http from 'node:http';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import { syncBuiltinESMExports } from 'node:module';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT } from '../roles.ts';
import { typedTools } from './index.ts';
import type { ToolContext } from './run.ts';
import { registerCapture, registerFixtureCapture } from '../test-support/receipts.ts';

async function fixture(t: TestContext, role: string, extra: Record<string,string> = {}) {
	const root = await realpath(await mkdtemp(join(tmpdir(),'spotlight-records-')));
	t.after(()=>rm(root,{recursive:true,force:true}));
	const caseDir = join(root,'case');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3',[join(CHECKOUT_ROOT,'tests/fixtures/review_case.py'),caseDir,'investigation_gate','--activated','--clean']);
	const context: ToolContext = {caseDir,writeRoot:caseDir,python:process.env.SPOTLIGHT_PYTHON ?? 'python3',configPath:join(root,'config.json'),env:{PATH:process.env.PATH,SPOTLIGHT_ROLE:role,SPOTLIGHT_HARNESS_DATA:join(root,'harness'),...extra}};
	// The fixture's own fact-checker capture has the receipt its fetch would leave.
	registerFixtureCapture(join(root,'harness'),caseDir);
	const tools = new Map(typedTools(context).map(tool=>[tool.name,tool]));
	const call = async(name:string,args:Record<string,unknown>)=> {
		const tool = tools.get(name);
		assert.ok(tool,`production tools must expose ${name}`);
		return tool.execute('call',args,undefined,undefined,undefined as never);
	};
	// As Pi's agent loop (pi-agent-core agent-loop.js): prepareArguments, then
	// pi-ai's schema validation, then execute.
	const live = async(name:string,args:Record<string,unknown>)=> {
		const tool = tools.get(name)!;
		const prepared = tool.prepareArguments ? tool.prepareArguments(args) : args;
		return tool.execute('call',validateToolArguments(tool as never,{type:'toolCall',id:'call',name,arguments:prepared} as never),undefined,undefined,undefined as never);
	};
	const json = async(name:string)=>JSON.parse(await readFile(join(caseDir,'data',name),'utf8'));
	return {caseDir,context,tools,call,live,json};
}

test('typed finding records supply stable IDs and fingerprints while preserving other rows',async t=>{
	const {call,json,caseDir}=await fixture(t,'investigator',{SPOTLIGHT_DIRECTION:'D2'});
	const before=await json('findings.json');
	const finding={claim:'The original release gives 6.4%.',evidence:'The release states 6.4%.',sources:[],confidence:'medium'};
	const receipt=await call('record_finding',{finding});
	const first=await json('findings.json');
	assert.deepEqual(first.findings.slice(0,before.findings.length),before.findings);
	const added=first.findings.at(-1);
	assert.match(added.id,/^D2-F\d+$/);
	assert.ok(receipt.content.some(part=>part.type==='text' && part.text.includes(added.id)), 'the assigned ID is returned for passage links and later corrections');
	assert.match(added.finding_fingerprint,/^[a-f0-9]{64}$/);
	await call('record_finding',{finding});
	assert.equal((await json('findings.json')).findings.length,first.findings.length,'identical retries keep the same generated ID');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3',[join(CHECKOUT_ROOT,'scripts/write-source-expressions.py'),caseDir,'--fingerprint-findings']);
	assert.equal((await json('findings.json')).findings.at(-1).finding_fingerprint,added.finding_fingerprint,'the canonical writer agrees with the generated fingerprint');
	const saved=await readFile(join(caseDir,'data/findings.json'),'utf8');
	await assert.rejects(call('record_finding',{finding:{...finding,id:added.id,sources:[{archive_url:'https://web.archive.org/web/20261101010101/https://example.org/'}]}}),/archive.*actual|archive.*capture/i);
	assert.equal(await readFile(join(caseDir,'data/findings.json'),'utf8'),saved);
});

test('typed assessment copies exact claim text, preserves other claims, and refuses invalid or unsupported verdicts atomically',async t=>{
	const {call,json,caseDir}=await fixture(t,'fact-checker',{SPOTLIGHT_FC_BATCH:'F1'});
	const existing=await json('fact-check.json');
	const source=await json('findings.json');
	const assessment={finding_id:source.findings[0].id,verdict:'unverified',confidence:'low',evidence_for:[],evidence_against:[]};
	const result=await call('record_assessment',{assessment});
	assert.match((result.content[0] as {text:string}).text,/Every finding in your batch has a claim/);
	let saved=await json('fact-check.json');
	assert.equal(saved.claims.find((row:any)=>row.finding_id===assessment.finding_id).claim_text,source.findings[0].claim);
	for(const row of existing.claims.filter((row:any)=>row.finding_id!==assessment.finding_id)) assert.ok(saved.claims.some((next:any)=>JSON.stringify(next)===JSON.stringify(row)));
	const bytes=await readFile(join(caseDir,'data/fact-check.json'),'utf8');
	for(const verdict of ['certain','partially_verified','verified']) {
		await assert.rejects(call('record_assessment',{assessment:{...assessment,verdict}}),/schema|verdict|passage|verified/);
		assert.equal(await readFile(join(caseDir,'data/fact-check.json'),'utf8'),bytes);
	}
	await assert.rejects(call('record_assessment',{assessment:{...assessment,claim_text:'A different claim.'}}),/claim_text|claim text|does not match/);
	assert.equal(await readFile(join(caseDir,'data/fact-check.json'),'utf8'),bytes);
	assert.equal((await fixture(t,'investigator')).tools.has('record_assessment'),false);
	assert.equal((await fixture(t,'fact-checker')).tools.has('record_finding'),false);
});

import { isWorkerRequest, startFakeOpenAI, toolNames, toolResults } from '../test-support/fake-openai.ts';
import { baseEnv, launch, settledPrompt, workspace } from '../test-support/launch.ts';
import { FC_BATCH_DUE } from '../extension.ts';

for (const mode of ['budget closeout','rejected repair'] as const) test(`native typed assessment preserves ${mode}`,async t=>{
	const space=await workspace(t);
	const caseDir=join(space.casesRoot,'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3',[join(CHECKOUT_ROOT,'tests/fixtures/review_case.py'),caseDir,'investigation_gate','--activated','--clean']);
	const findings=JSON.parse(await readFile(join(caseDir,'data/findings.json'),'utf8'));
	const finding=findings.findings[0];
	await unlink(join(caseDir,'data/fact-check.json'));
	await writeFile(join(caseDir,'research/rows.txt'),Array.from({length:FC_BATCH_DUE},(_,i)=>`row${i}`).join('\n'));
	let spawns=0;
	let repairPacket='';
	const assessment={finding_id:finding.id,verdict:'unverified',confidence:'low',evidence_for:[],evidence_against:[],grounding_assessment:{support_type:'insufficient',claim_elements_checked:[],missing_assumptions:['Independent corroboration is absent.'],confidence_cap:'low',assessment:'No independent source establishes this finding.'}};
	const fake=await startFakeOpenAI(request=>{
		if(isWorkerRequest(request,'fact-checker')) {
			if(toolResults(request).length===0) {
				spawns++;
				if(mode==='budget closeout') return {toolCalls:Array.from({length:FC_BATCH_DUE},(_,i)=>({name:'read_file',arguments:{path:'research/rows.txt',offset:i+1,limit:1}}))};
				if(spawns===1) return {toolCalls:[{name:'record_assessment',arguments:{assessment:{...assessment,verdict:'verified'}}}]};
				repairPacket=JSON.stringify(request.messages);
				return {toolCalls:[{name:'record_assessment',arguments:{assessment}}]};
			}
			if(mode==='budget closeout') {
				assert.ok(toolNames(request).includes('record_assessment'),'closing acquisition keeps the typed recorder available');
				assert.ok(!toolNames(request).includes('fetch'),'acquisition really is closed');
				return {toolCalls:[{name:'record_assessment',arguments:{assessment}},{name:'write_file',arguments:{path:'research/after-completion.txt',content:'must not execute'}}]};
			}
			return {text:'The requested assessment was refused.'};
		}
		const results=toolResults(request);
		return results.length<(mode==='rejected repair'?2:1) ? {toolCalls:[{name:'spawn_agent',arguments:{agent:'fact-checker',task:'Check the assigned findings.'}}]} : {text:'Done.'};
	});
	t.after(()=>fake.close());
	// record_assessment from the first call is the frontier tier's; a local tier offers judge_finding until a second source (B20).
	const chat=launch(t,{...baseEnv(space,fake.baseURL),SPOTLIGHT_MODEL_TIER:'frontier',SPOTLIGHT_FC_BATCH_SIZE:'1'});
	await settledPrompt(chat,'Continue.');
	const saved=JSON.parse(await readFile(join(caseDir,'data/fact-check.json'),'utf8'));
	assert.equal(saved.claims[0].verdict,'unverified');
	assert.equal(saved.claims[0].claim_text,finding.claim);
	if(mode==='budget closeout') {
		await assert.rejects(readFile(join(caseDir,'research/after-completion.txt'),'utf8'),/ENOENT/);
	} else {
		assert.equal(spawns,2);
		assert.match(repairPacket,/Rejected fact-check|rejected.*write|rejected.*proposal/i,'same-batch rejected typed proposals are carried to the repairing worker');
	}
	assert.match(toolResults(fake.requests.filter(request=>!isWorkerRequest(request,'fact-checker')).at(-1)!).join('\n'),/validate-fact-check.*PASSED/);
});

test('typed finding fingerprint failure reports the accepted write and can be repaired without recreating its ID', async t=>{
	const {call,json,context}=await fixture(t,'investigator');
	const before=(await json('findings.json')).findings.length;
	const python=context.python;
	context.python=join(context.caseDir,'missing-python');
	await assert.rejects(call('record_finding',{finding:{claim:'A new exact claim.',evidence:'Source unavailable.',sources:[],confidence:'low'}}),/was written, but fingerprint refresh failed.*Do not recreate/);
	const partial=(await json('findings.json')).findings;
	assert.equal(partial.length,before+1);
	const id=partial.at(-1).id;
	context.python=python;
	await call('source_expressions',{action:'fingerprint_findings'});
	const recovered=(await json('findings.json')).findings;
	assert.equal(recovered.length,before+1);
	assert.equal(recovered.at(-1).id,id);
	assert.match(recovered.at(-1).finding_fingerprint,/^[a-f0-9]{64}$/);
});

// H-1 (reports/qwen-base.md, Eurostat P3): the capture's sentence held a markdown link, the quote its visible words: 5 refused writes, then the watchdog stopped the case.
for (const [name,line,quote] of [['exact','Acme paid Doe in full.','Acme paid Doe in full.'],['through a link','Acme paid [Doe](https://example.org/doe "https://example.org/doe") in full.','Acme paid Doe in full.']] as const)
test(`typed assessment records a quote ${name} from its independently acquired capture through the canonical passage writer`, async t=>{
	const {call,json,context,caseDir}=await fixture(t,'fact-checker');
	const interpreter=context.python;
	const wrapper=join(caseDir,'python-fetch');
	const url='https://web.archive.org/web/20261001000000/https://registry.example.org/acme';
	const envelope=JSON.stringify({markdown:`Registry record\n${line}\n`,source_url:url,provider:'crawl4ai',status_code:200});
	await writeFile(wrapper,`#!${process.execPath}\nconst {execFileSync}=require('node:child_process');\nconst args=process.argv.slice(2);\nif(args.includes('integrations.scraping')) process.stdout.write(${JSON.stringify(envelope)});\nelse {try{process.stdout.write(execFileSync(${JSON.stringify(interpreter)},args,{stdio:['inherit','pipe','inherit']}));}catch(error){process.stdout.write(error.stdout??'');process.exitCode=error.status??1;}}\n`,{mode:0o755});
	context.python=wrapper;
	context.env.SPOTLIGHT_ANONYMIZE_FETCH='true';
	await call('fetch',{url,save_as:'fc-own.md'});
	const finding=(await json('findings.json')).findings[0];
	await call('record_assessment',{assessment:{finding_id:finding.id,verdict:'verified',confidence:'high',evidence_for:[{description:'The independently retrieved registry states this payment.',source:url,archive_url:url,quote,source_ref:{path:'research/fc-own.md',line_start:2,line_end:2}}],evidence_against:[],grounding_assessment:{support_type:'direct',claim_elements_checked:['payment'],missing_assumptions:[],confidence_cap:'high',assessment:'The registry states the payment.'}}});
	const checked=(await json('fact-check.json')).claims.find((row:any)=>row.finding_id===finding.id);
	const item=(await json('evidence-bundle.json')).items.find((row:any)=>row.raw_path==='research/fc-own.md');
	assert.ok(item,'the fetched capture gets its own evidence item');
	assert.equal(item.created_by,'fact-checker');
	assert.equal(item.source_url,url);
	assert.equal(checked.evidence_for[0].archive_url,url,'an actually acquired archive URL remains usable through the common file boundary');
	assert.equal(checked.evidence_for[0].evidence_bundle_id,item.id);
	assert.ok(checked.evidence_for[0].source_expression_refs.length>0);
	const passage=(await json('source-expressions.json')).expressions.find((row:any)=>row.id===checked.evidence_for[0].source_expression_refs[0]);
	assert.equal(passage.text,line,'the file\'s own line is what is stored');
	assert.equal(passage.anchor_ref.path,'research/fc-own.md');
	assert.equal(passage.created_by,'fact-checker');
	// Continuity: the validator the fact-check gate runs takes the same claim.
	const check=spawnSync(interpreter,[join(CHECKOUT_ROOT,'scripts/validate-fact-check.py'),caseDir],{encoding:'utf8'});
	assert.doesNotMatch(`${check.stdout}${check.stderr}`,/exact quote is not present/);
});

test('typed archive compatibility preserves a historical citation but refuses a changed capture identity', async t=>{
	const {call,json,caseDir}=await fixture(t,'investigator');
	const document=await json('findings.json');
	const finding=document.findings[0];
	finding.sources=[{url:'https://source.example/record',archive_url:'https://web.archive.org/web/20200101000000/https://source.example/record',local_file:'research/manual-history.md',source_ref:{path:'research/manual-history.md',line_start:1,line_end:1}}];
	await writeFile(join(caseDir,'data/findings.json'),JSON.stringify(document));
	await call('record_finding',{finding});
	assert.equal((await json('findings.json')).findings[0].sources[0].archive_url,finding.sources[0].archive_url);
	const before=await readFile(join(caseDir,'data/findings.json'),'utf8');
	await assert.rejects(call('record_finding',{finding:{...finding,sources:[{...finding.sources[0],source_ref:{path:'research/different.md',line_start:1,line_end:1}}]}}),/archive.*actual.*capture/);
	assert.equal(await readFile(join(caseDir,'data/findings.json'),'utf8'),before);
});

for (const role of ['investigator','fact-checker'] as const) for (const writer of ['write_file','edit_file'] as const) test(`${role} canonical ${writer} refuses an unacquired archive without changing the artifact`, async t=>{
	const {call,json,caseDir}=await fixture(t,role);
	const filename=role==='investigator' ? 'findings.json' : 'fact-check.json';
	const path=`data/${filename}`;
	const before=await readFile(join(caseDir,path),'utf8');
	const document=await json(filename);
	const citation={source:'https://source.example/record',url:'https://source.example/record',description:'An unacquired archive must not become evidence.',archive_url:'https://web.archive.org/web/20261101010101/https://source.example/record'};
	if(role==='investigator') document.findings[0].sources=[citation];
	else {
		const finding=(await json('findings.json')).findings[0];
		document.claims=[{finding_id:finding.id,claim_text:finding.claim,verdict:'unverified',confidence:'low',evidence_for:[citation],evidence_against:[]}];
	}
	const content=JSON.stringify(document,null,2);
	const args=writer==='write_file' ? {path,content} : {path,old_text:before,new_text:content};
	await assert.rejects(call(writer,args),/archive.*actual.*capture|archive.*acquir/i,'generic canonical writers must enforce the same acquisition invariant as typed records');
	assert.equal(await readFile(join(caseDir,path),'utf8'),before,'a refused archive must not enter the historical-citation compatibility path');
});

// The tool accepts the IDs its batch prompt supplies; the real post-worker
// writer expands them before persistent-schema validation.
test('typed assessment accepts recorded passage IDs and id-only references without guessing fingerprints', async t=>{
	const {call,json,context,caseDir}=await fixture(t,'fact-checker');
	const initial=(await json('fact-check.json')).claims[0];
	const reference=initial.evidence_for[0].source_expression_refs[0];
	for (const shorthand of [reference.expression_id,{expression_id:reference.expression_id}]) {
		const assessment=structuredClone(initial);
		assessment.evidence_for[0].source_expression_refs=[shorthand];
		await call('record_assessment',{assessment});
		execFileSync(context.python,[join(CHECKOUT_ROOT,'scripts/write-source-expressions.py'),caseDir,'--resolve-fact-check-refs'],{encoding:'utf8'});
		const saved=(await json('fact-check.json')).claims[0];
		assert.deepEqual(saved.evidence_for[0].source_expression_refs,[reference],'the canonical writer fills exactly the selected immutable relation');
		execFileSync(context.python,[join(CHECKOUT_ROOT,'scripts/validate-fact-check.py'),caseDir],{encoding:'utf8'});
	}
	const passage=(await json('source-expressions.json')).expressions.find((row:any)=>row.id===reference.expression_id);
	await call('source_expressions',{action:'write',cycle:1,proposal:{expressions:[{id:'opposing-passage',text:passage.text,anchor_ref:passage.anchor_ref,original_evidence_bundle_id:passage.original_evidence_bundle_id,language:passage.language,finding_links:[{finding_id:initial.finding_id,relation:'contradicts'}]}]}});
	const before=await readFile(join(caseDir,'data/fact-check.json'),'utf8');
	for (const bad of ['unknown-passage','opposing-passage',{expression_id:reference.expression_id,finding_fingerprint:'0'.repeat(64)}]) {
		const assessment=structuredClone(initial);
		assessment.evidence_for[0].source_expression_refs=[bad];
		await assert.rejects(call('record_assessment',{assessment}),/source_expression_refs|passage|support/);
		assert.equal(await readFile(join(caseDir,'data/fact-check.json'),'utf8'),before,'unknown IDs and supplied fingerprint mismatches leave the artifact unchanged');
	}
});

test('evidence-bundle file aliases cannot introduce an unacquired archive', async t=>{
	const {call,json,caseDir}=await fixture(t,'investigator');
	const path='data/evidence-bundle.json';
	const before=await readFile(join(caseDir,path),'utf8');
	const bundle=await json('evidence-bundle.json');
	bundle.items[0].archive_url='https://web.archive.org/web/20261101010101/https://source.example/record';
	const content=JSON.stringify(bundle,null,2);
	for(const [tool,args] of [['write_file',{path,content}],['edit_file',{path,old_text:before,new_text:content}]] as const) {
		await assert.rejects(call(tool,args),/archive.*actual.*capture/);
		assert.equal(await readFile(join(caseDir,path),'utf8'),before);
	}
});

// Phase B (reports/phaseb-lfm3.md): stock LFM3's fact-checker sent 6 of 6
// assessments without finding_id, which the schema did not require, and each
// was refused. Code owns IDs: a one-finding batch gets its id filled and the
// result says so; any other batch's schema requires it, and the refusal names
// the batch.
test('record_assessment fills finding_id for a one-finding batch and otherwise requires it inside assessment, naming the batch; a copy beside assessment is folded in',async t=>{
	const single=await fixture(t,'fact-checker',{SPOTLIGHT_FC_BATCH:'F1'});
	const source=await single.json('findings.json');
	const result=await single.call('record_assessment',{assessment:{verdict:'unverified',confidence:'low',evidence_for:[],evidence_against:[]}});
	assert.ok(result.content.some((part:any)=>part.type==='text' && part.text.includes('finding_id F1 was filled by the harness')),'the result records that code filled the id');
	const saved=(await single.json('fact-check.json')).claims.find((row:any)=>row.finding_id==='F1');
	assert.equal(saved.verdict,'unverified');
	assert.equal(saved.claim_text,source.findings[0].claim);

	const several=await fixture(t,'fact-checker',{SPOTLIGHT_FC_BATCH:'F1,F2'});
	const schema=several.tools.get('record_assessment')!.parameters as any;
	assert.ok(schema.properties.assessment.required.includes('finding_id'),'the schema requires finding_id for a batch of several');
	assert.match(several.tools.get('record_assessment')!.description,/finding_id is required inside assessment: one of F1, F2/);
	const bytes=await readFile(join(several.caseDir,'data/fact-check.json'),'utf8');
	await assert.rejects(several.call('record_assessment',{assessment:{verdict:'unverified',confidence:'low',evidence_for:[],evidence_against:[]}}),/finding_id is required\. Your batch: F1, F2/);
	assert.equal(await readFile(join(several.caseDir,'data/fact-check.json'),'utf8'),bytes,'nothing was written');

	// fz2-fix9 seq 58/59 (Gemma 26B, two-finding batch): finding_id right inside
	// assessment and repeated beside it, refused by Pi's schema check before
	// execute. The recorded shape, with this fixture's ids and an unverified
	// verdict, through the same path as a live call.
	assert.match(several.tools.get('record_assessment')!.description,/finding_id goes inside assessment, not beside it/);
	const grounding={assessment:'The official page lists the role; a second record gives the full name.',claim_elements_checked:['name','role','organization'],confidence_cap:'low',missing_assumptions:['The two records name the same person.'],support_type:'direct'};
	const verdict=async()=>(await several.json('fact-check.json')).claims.find((row:any)=>row.finding_id==='F1')?.verdict;
	await several.live('record_assessment',{assessment:{confidence:'low',evidence_for:[],evidence_against:[],finding_id:'F1',grounding_assessment:grounding,verdict:'unverified'},finding_id:'F1'});
	assert.equal(await verdict(),'unverified','the recorded shape is saved: the copy beside assessment is dropped');
	await several.live('record_assessment',{assessment:{confidence:'medium',evidence_for:[],evidence_against:[],verdict:'disputed'},finding_id:'F1'});
	assert.equal(await verdict(),'disputed','a finding_id given only beside assessment is moved into it and saved');
	const after=await readFile(join(several.caseDir,'data/fact-check.json'),'utf8');
	await assert.rejects(several.live('record_assessment',{assessment:{finding_id:'F1',verdict:'unverified',confidence:'low',evidence_for:[],evidence_against:[]},finding_id:'F2'}),/finding_id "F2" beside assessment differs from "F1" inside it; give finding_id once, inside assessment\. Your batch: F1, F2\./);
	assert.equal(await readFile(join(several.caseDir,'data/fact-check.json'),'utf8'),after,'a mismatch writes nothing');
});

// Phase B (Gemma12): a tool's own schema refusal on an enum lists the allowed values.
test('a record refused for an enum value names the allowed values',async t=>{
	const {call}=await fixture(t,'fact-checker',{SPOTLIGHT_FC_BATCH:'F1'});
	await assert.rejects(call('record_assessment',{assessment:{finding_id:'F1',verdict:'certain',confidence:'low',evidence_for:[],evidence_against:[]}}),/verdict[^;]*\(allowed: [^)]*"verified"[^)]*"not_checkable"/);
});

// e2e r5 resume (31B, D3-F1): items citing the investigator's passages AND a
// source_ref into the fact-checker's own registered capture got no evidence
// item, and validate-case.py refused the verdict. Anchored inside the
// fact-checker's capture they count; anchored only in the investigator's
// material they are refused with the missing field named.
test('a verified item whose quote is in the fact-checker\'s own capture counts even when it also cites investigator passages; one without that quote, or anchored only in investigator material, is refused',async t=>{
	for (const variant of ['quote','lines','investigator-only','http_get']) await t.test(variant, async t=>{
		const {call,json,context,caseDir}=await fixture(t,'fact-checker',{SPOTLIGHT_FC_BATCH:'F1'});
		const interpreter=context.python;
		const wrapper=join(caseDir,'python-fetch');
		const url='https://registry.example.org/acme';
		if (variant==='http_get') {
			// Codex re-review of fix11: a capture saved with http_get (or browse) is
			// the fact-checker's acquisition too, with the same fetch receipt. The
			// transport is a stand-in, as in programs.test.ts; nothing leaves.
			t.mock.method(http,'request',()=>{
				const request=Object.assign(new EventEmitter(),{end(){const response=Object.assign(Readable.from([Buffer.from('Registry record\nAcme paid Doe in full.\n')]),{statusCode:200,headers:{'content-type':'text/plain'}});queueMicrotask(()=>request.emit('response',response));},destroy(){}});
				return request;
			});
			syncBuiltinESMExports();
			t.after(()=>{t.mock.restoreAll();syncBuiltinESMExports();});
			await call('http_get',{url:'http://registry.example.org/acme',save_as:'fc-own.md'});
		} else {
		const envelope=JSON.stringify({markdown:'Registry record\nAcme paid Doe in full.\n',source_url:url,provider:'crawl4ai',status_code:200});
		await writeFile(wrapper,`#!${process.execPath}\nconst {execFileSync}=require('node:child_process');\nconst args=process.argv.slice(2);\nif(args.includes('integrations.scraping')) process.stdout.write(${JSON.stringify(envelope)});\nelse {try{process.stdout.write(execFileSync(${JSON.stringify(interpreter)},args,{stdio:['inherit','pipe','inherit']}));}catch(error){process.stdout.write(error.stdout??'');process.exitCode=error.status??1;}}\n`,{mode:0o755});
		context.python=wrapper;
		context.env.SPOTLIGHT_ANONYMIZE_FETCH='true';
		await call('fetch',{url,save_as:'fc-own.md'});
		}
		const passages=(await json('source-expressions.json')).expressions.filter((row:any)=>(row.finding_links??[]).some((link:any)=>link.finding_id==='F1'&&link.relation==='supports')).map((row:any)=>row.id);
		assert.ok(passages.length>0,'the investigator has a passage for F1');
		const path=variant==='investigator-only'?'research/filing.txt':'research/fc-own.md';
		const item={description:'The registry states the payment.',source:url,source_ref:{path,line_start:2,line_end:2},source_expression_refs:passages,...(variant==='quote'||variant==='http_get'?{quote:'Acme paid Doe in full.'}:{})};
		const assessment={finding_id:'F1',verdict:'verified',confidence:'high',evidence_for:[item],evidence_against:[],grounding_assessment:{support_type:'direct',claim_elements_checked:['payment'],missing_assumptions:[],confidence_cap:'high',assessment:'The registry states the payment.'}};
		// No quote: an existing line number in the fact-checker's capture is not independent support (N1).
		if (variant==='investigator-only' || variant==='lines') {
			await assert.rejects(call('record_assessment',{assessment}),/F1 has a verified verdict with no case-local evidence anchor[\s\S]*Cite a capture you acquired[\s\S]*passages alone are not an anchor/);
			return;
		}
		await call('record_assessment',{assessment});
		const checked=(await json('fact-check.json')).claims.find((row:any)=>row.finding_id==='F1');
		const own=(await json('evidence-bundle.json')).items.find((row:any)=>row.raw_path==='research/fc-own.md');
		assert.ok(own && own.created_by==='fact-checker','the fact-checker\'s capture has its own evidence item');
		assert.equal(checked.evidence_for[0].evidence_bundle_id,own.id);
		// r12-ingest-audit A2: its registered capture was read in full, which code
		// knows and stamps on the item and on the row, so the Batch 1.1 builder
		// counts it as re_fetched at Ingest.
		assert.equal(own.access_method,'full_text');
		assert.equal(checked.evidence_for[0].access_method,'full_text');
		assert.deepEqual(checked.evidence_for[0].source_expression_refs.slice(0,passages.length).map((ref:any)=>typeof ref==='string'?ref:ref.expression_id),passages);
	});
});

// K3 Eurostat P3 rerun on 40b77272 (records e2e-eurostat-unemployment-s0-resume, B24/B25): a cite saved D1-F1 and code wrote its
// evidence item with the capture's sha256 (seq 23); at seq 24 the investigator rewrote data/evidence-bundle.json and the merge took its
// version of that item without sha256 and with an invented accessed time, so passage D1-Q1 no longer matched its item. Every later cite
// then saved its finding but not its passage, and the refusal told the model to correct the cite it had just made.
// gold-asf on 3767f400 (fact-checker-9, seq 70-79): the receipt repair ran over every merged item, so a fact-checker's add
// "changed" investigator items it never touched, and the bundle guard refused it for good: the finding stayed unjudged.
test('a fact-checker\'s bundle add leaves investigator items it did not touch, even when their accessed differs from the receipt', async t => {
	const { caseDir, context, call, json } = await fixture(t, 'fact-checker');
	const capture = 'research/asf-leadership.md.raw';
	await writeFile(join(caseDir, capture), 'Officers\nPresident: Ruth Suehle\n');
	registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, capture, 'https://www.apache.org/foundation/leadership', 'investigator');
	const receipt = JSON.parse(readFileSync(join(context.env.SPOTLIGHT_HARNESS_DATA!, 'work', 'fetched', readdirSync(join(context.env.SPOTLIGHT_HARNESS_DATA!, 'work', 'fetched'))[0]), 'utf8'))[capture];
	const bundle = await json('evidence-bundle.json');
	const stale = { ...bundle.items[0], id: 'D3-E1', created_by: 'investigator', raw_path: capture, sha256: receipt.sha256, accessed: '2026-11-04T22:16:17Z' };
	await writeFile(join(caseDir, 'data', 'evidence-bundle.json'), JSON.stringify({ ...bundle, items: [...bundle.items, stale] }));
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...bundle.items[0], id: 'FC-E9', created_by: 'fact-checker', claim_links: [] }] }) });
	const after = (await json('evidence-bundle.json')).items;
	assert.ok(after.some((entry: { id: string }) => entry.id === 'FC-E9'), 'the fact-checker\'s item is saved');
	assert.equal(after.find((entry: { id: string }) => entry.id === 'D3-E1').accessed, '2026-11-04T22:16:17Z', 'an item this write did not touch stays as stored');
	// Continuity: the next add goes through too, and an item a write does change still gets the receipt's values, so B24 holds.
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...bundle.items[0], id: 'FC-E10', created_by: 'fact-checker', claim_links: [] }] }) });
	assert.ok((await json('evidence-bundle.json')).items.some((entry: { id: string }) => entry.id === 'FC-E10'));
	await assert.rejects(call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...stale, raw_path: 'research/other.md' }] }) }), /changes or removes D3-E1/);
});

test('a worker\'s bundle write keeps the receipt fields of a cited capture\'s item, and a passage refusal names the passage that is actually broken', async t => {
	const { caseDir, context, call, live, json } = await fixture(t, 'investigator');
	const capture = 'research/eurostat-aug.md.raw';
	await writeFile(join(caseDir, capture), 'Euro indicators\nIn August 2026 the euro area unemployment rate was 6.4%.\nThe EU rate was 6.1%.\n');
	registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, capture, 'https://ec.europa.eu/eurostat/x', 'investigator');
	const cited = await live('record_finding', { finding: { claim: 'The August 2026 euro area unemployment rate was 6.4%.', confidence: 'medium' }, cite: [{ path: capture, line_start: 2, line_end: 2 }] });
	const text = (result: { content: { type: string; text?: string }[] }) => result.content.map((part) => part.text ?? '').join('\n');
	assert.match(text(cited), /Passage\(s\) \S+ recorded/);
	const item = (await json('evidence-bundle.json')).items.find((entry: { raw_path?: string }) => entry.raw_path === capture);
	assert.match(item.sha256, /^[a-f0-9]{64}$/);
	// seq 24: the worker's own version of the item, without sha256 and with another accessed time.
	const { sha256: _dropped, ...rest } = item;
	const rewritten = await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...rest, acquisition_method: 'fetch', accessed: '2026-11-04T10:44:37Z' }] }) });
	const after = (await json('evidence-bundle.json')).items.find((entry: { id: string }) => entry.id === item.id);
	assert.equal(after.sha256, item.sha256, 'the capture\'s sha256 stays');
	assert.equal(after.accessed, item.accessed, 'the receipt\'s accessed time stays');
	assert.equal(after.raw_path, capture);
	assert.equal(after.acquisition_method, 'fetch', 'fields the worker owns are still its to change');
	assert.match(text(rewritten), new RegExp(`kept ${item.id}'s sha256 and accessed`));
	// B16, B17, B23(b) (K3 Eurostat P3, K3 Tor P3 rerun, K5-3): a new item the worker writes itself: code fills sha256 and accessed from the
	// receipt, whatever the worker typed, and an item naming fetch's leads moves to the registered .raw. The next cite on that page then reuses it.
	const leads = 'research/eurostat-sep.md';
	const page = `${leads}.raw`;
	await writeFile(join(caseDir, leads), '# RLM-distilled leads from https://ec.europa.eu/eurostat/z\n- a lead\n');
	await writeFile(join(caseDir, page), 'Euro indicators\nIn July 2026 the euro area unemployment rate was 6.3%.\n');
	registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, page, 'https://ec.europa.eu/eurostat/z', 'investigator');
	const filled = await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...rest, id: 'E-NEW', raw_path: leads, sha256: 'abc123', accessed: '2026-11-04T10:44:37Z', source_url: 'https://ec.europa.eu/eurostat/z' }] }) });
	const fresh = (await json('evidence-bundle.json')).items.find((entry: { id: string }) => entry.id === 'E-NEW');
	const receipt = JSON.parse(readFileSync(join(context.env.SPOTLIGHT_HARNESS_DATA!, 'work', 'fetched', readdirSync(join(context.env.SPOTLIGHT_HARNESS_DATA!, 'work', 'fetched'))[0]), 'utf8'))[page];
	assert.deepEqual([fresh.raw_path, fresh.sha256, fresh.accessed], [page, receipt.sha256, receipt.accessed]);
	assert.match(text(filled), /Code moved E-NEW's raw_path from research\/eurostat-sep\.md to research\/eurostat-sep\.md\.raw/);
	assert.match(text(filled), /Code filled E-NEW's sha256 and accessed from the fetch receipt/);
	assert.match(text(await live('record_finding', { finding: { claim: 'The July 2026 euro area unemployment rate was 6.3%.', confidence: 'medium' }, cite: [{ path: page, line_start: 2, line_end: 2 }] })), /Passage\(s\) \S+ recorded/);
	// A capture changed since its fetch proves nothing: the item stays as the worker wrote it.
	const changed = 'research/eurostat-jun.md.raw';
	await writeFile(join(caseDir, changed), 'as fetched\n');
	registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, changed, 'https://ec.europa.eu/eurostat/j', 'investigator');
	await writeFile(join(caseDir, changed), 'edited after the fetch\n');
	await call('write_file', { path: 'data/evidence-bundle.json', content: JSON.stringify({ items: [{ ...rest, id: 'E-EDITED', raw_path: changed, sha256: 'abc123', accessed: '2026-11-04T10:44:37Z' }] }) });
	const edited = (await json('evidence-bundle.json')).items.find((entry: { id: string }) => entry.id === 'E-EDITED');
	assert.deepEqual([edited.sha256, edited.accessed], ['abc123', '2026-11-04T10:44:37Z']);
	// An older case already broken that way (written before this fix): the next cite's passage is refused because of the
	// earlier passage, and the refusal names that passage, its item and the sha256 to put back, not this cite.
	const bundle = await json('evidence-bundle.json');
	bundle.items = bundle.items.map((entry: { id: string; sha256?: string }) => (entry.id === item.id ? { ...entry, sha256: undefined } : entry));
	await writeFile(join(caseDir, 'data/evidence-bundle.json'), JSON.stringify(bundle, null, 2));
	const broken = (await json('source-expressions.json')).expressions.find((passage: { original_evidence_bundle_id?: string }) => passage.original_evidence_bundle_id === item.id);
	// As in K3 (D2-F1 cited another capture), the next cite is on a different page, so the broken item is not the one this cite writes.
	const other = 'research/eurostat-oct.md.raw';
	await writeFile(join(caseDir, other), 'Euro indicators\nIn September 2026 the euro area unemployment rate was 6.5%.\n');
	registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, other, 'https://ec.europa.eu/eurostat/y', 'investigator');
	await assert.rejects(live('record_finding', { finding: { claim: 'The September 2026 euro area unemployment rate was 6.5%.', confidence: 'medium' }, cite: [{ path: other, line_start: 2, line_end: 2 }] }),
		(error: Error) => error.message.includes(`passage ${broken.id}`) && error.message.includes(`item ${item.id}`) && error.message.includes(item.sha256) && !error.message.includes('a corrected cite'));
});

// qwen real launch: the investigator cited lines of fetch's short leads file that only the page (X.raw) holds, was refused, and repeated the same cite until the worker ended.
// u9mac-rd3b81 F2 and F3: one claim, differing in case, recorded twice on different lines, and both went to the fact-checker.
test('on the local tiers a claim already recorded with its passage is not recorded again; the worker is told and carries on', async t => {
	const page = 'research/people.md.raw';
	const text = ['Kendra Albert is on the Board of Directors.', 'Navigation.', 'Sarah Gran is on the Board of Directors.'];
	const setup = async (tier: string) => {
		const made = await fixture(t, 'investigator', { SPOTLIGHT_MODEL_TIER: tier });
		await mkdir(join(made.caseDir, 'research'), { recursive: true });
		await writeFile(join(made.caseDir, page), `${text.join('\n')}\n`);
		registerCapture(made.context.env.SPOTLIGHT_HARNESS_DATA!, made.caseDir, page, 'https://example.org/people', 'investigator');
		return made;
	};
	const record = (claim: string, line: number) => ({ finding: { claim, confidence: 'medium' }, cite: [{ path: page, line_start: line, line_end: line }] });
	const said = (done: { content: Array<{ type: string; text?: string }> }) => done.content.map((part) => part.text ?? '').join('\n');
	const rows = async (json: (name: string) => Promise<{ findings: Array<{ id: string; claim: string; evidence: string[] }> }>) => (await json('findings.json')).findings.filter((row) => /albert|gran/i.test(row.claim));

	const local = await setup('12b');
	await local.live('record_finding', record('Kendra Albert is on the Board of Directors for the Tor Project.', 1));
	const [first] = await rows(local.json);
	const passages = (await local.json('source-expressions.json')).expressions.length;
	const again = await local.live('record_finding', record('kendra albert is on the board of  directors for the Tor Project', 2));
	assert.match(said(again), new RegExp(`already recorded as ${first.id}`));
	assert.deepEqual(await rows(local.json), [first], 'the first row and its lines stand');
	assert.equal((await local.json('source-expressions.json')).expressions.length, passages);
	// Continuity: the next claim records as usual.
	await local.live('record_finding', record('Sarah Gran is on the Board of Directors for the Tor Project.', 3));
	assert.equal((await rows(local.json)).length, 2);

	// A row with no passage yet is the repair route: the same claim fills it, under its id.
	const repair = await setup('12b');
	await repair.call('record_finding', { finding: { claim: 'Sarah Gran is on the Board.', evidence: 'x', sources: [], confidence: 'medium' } });
	await repair.live('record_finding', record('Sarah Gran is on the Board.', 3));
	const repaired = await rows(repair.json);
	assert.equal(repaired.length, 1);
	assert.deepEqual(repaired[0].evidence, [text[2]]);

	// Frontier keeps its route: differing text is a new finding.
	const frontier = await setup('frontier');
	await frontier.live('record_finding', record('Kendra Albert is on the Board.', 1));
	await frontier.live('record_finding', record('kendra albert is on the board', 2));
	assert.equal((await rows(frontier.json)).length, 2);
});

test('a cite outside fetch\'s leads file is recorded on its .raw page on the local tiers, and refused on frontier', async t => {
	const page = 'research/craft.co/executives.md';
	const lines = Array.from({ length: 8 }, (_, index) => `line ${index + 1} of the page`);
	lines[5] = 'Isabela Fernandes / Executive Director';
	for (const tier of ['12b', 'frontier']) {
		const { caseDir, context, live, json } = await fixture(t, 'investigator', { SPOTLIGHT_MODEL_TIER: tier });
		await mkdir(join(caseDir, 'research/craft.co'), { recursive: true });
		await writeFile(join(caseDir, page), '# RLM-distilled leads from https://craft.co/x\n- a lead\n');
		await writeFile(join(caseDir, `${page}.raw`), `${lines.join('\n')}\n`);
		registerCapture(context.env.SPOTLIGHT_HARNESS_DATA!, caseDir, `${page}.raw`, 'https://craft.co/x', 'investigator');
		const cite = { finding: { claim: 'Isabela Fernandes is Executive Director.', confidence: 'medium' }, cite: [{ path: page, line_start: 6, line_end: 6 }] };
		if (tier === 'frontier') {
			await assert.rejects(live('record_finding', cite), /lines 6-6 are outside the file \(lines 1-2\)/);
			continue;
		}
		const done = await live('record_finding', cite);
		assert.match(done.content.map((part: { text?: string }) => part.text ?? '').join('\n'), /Passage\(s\) \S+ recorded/);
		const finding = (await json('findings.json')).findings.find((row: { claim: string }) => row.claim.startsWith('Isabela'));
		assert.deepEqual(finding.evidence, ['Isabela Fernandes / Executive Director']);
		assert.equal(finding.sources[0].local_file, `${page}.raw`);
	}
});
