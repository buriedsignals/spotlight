// What the launcher tests (launcher.part*.test.ts) share: case fixtures, model
// scripts and readers of a chat's output. No tests live here.
import assert from 'node:assert/strict';
import { execFile, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { chmod, copyFile, mkdir, readFile, readdir, realpath, rm, unlink, writeFile } from 'node:fs/promises';
import { cpSync, existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { dirname, join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { isWorkerRequest, startFakeOpenAI, systemText, toolNames, toolResults } from './test-support/fake-openai.ts';
import type { ChatRequest, Reply } from './test-support/fake-openai.ts';
import { baseEnv, caseAtIngest, launch, newsroomEnv, requestNewsroomIngest, settledPrompt, waitUntil, workspace } from './test-support/launch.ts';
import { DECISION_RECORDED } from './router.ts';
import { NEXT_BRIEF } from './tools/spotlight.ts';
import { reviewRefusalStop } from './extension.ts';
import { AMENDMENT_STOP, PAGE_NOT_OPENED, REVIEW_HOLD_NOTICE } from './tools/review.ts';
import { startFakeEditorial } from './test-support/fake-editorial.ts';
import { registerCapture } from './test-support/receipts.ts';
import type { Chat, Workspace } from './test-support/launch.ts';
import { CHECKOUT_ROOT, FACT_CHECK_TASK, agentSpec, loadComposition, deliverableFor } from './roles.ts';
import { withCasePassages } from './case-search.ts';
import { BRIEF_PENDING_REFUSAL, FACT_CHECKERS_SPENT, GATE_DECIDED_REFUSAL, GATE_READY_REFUSAL } from './subagents.ts';
import { ASSESSMENT_HELD, FC_BATCH_DONE, FC_BATCH_DUE, FC_BATCH_NOTICE, FC_BATCH_STOP, registerFetchedPages } from './extension.ts';
import { CONTINUE_AFTER_FOLD } from './subagents.ts';
import { blockedStop, budgetedWebResult, CALL_BUDGET_SPENT, FACT_CHECK_DUE, FACT_CHECK_DUE_NOTICE, FACT_CHECK_DUE_REPEAT, FACT_CHECK_STOP, GATE_BOUND, GATE_BOUND_REFUSAL, LOOP_NOTICE_CAP, ORCHESTRATOR_LOOP_STOP, PROPOSAL_DONE, readAlreadyShown, repeatsItself, REVIEW_OPEN_STOP, REVIEW_REFUSAL_CAP, skillAlreadyLoaded, UNPRODUCTIVE_NEXT, UNPRODUCTIVE_STOP } from './extension.ts';
import { GATE_APPROVAL_REFUSAL, NEXT_METHODOLOGY_APPROVAL, NEXT_METHODOLOGY_FAILING } from './tools/spotlight.ts';
import { TASK_AFTER_FOLD, taskAfterFold } from './checkpoint.ts';
import { maxOutputTokens } from './config.ts';

export const FORBIDDEN_TOOL = /^(bash|read|edit|write|ls|find|grep|powershell|codemode|tool_search)$|^mcp__/;

export function isAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch {
		return false;
	}
}

export async function lockRecord(space: Workspace, caseId = 'case-a') {
	return JSON.parse(await readFile(join(space.dataDir, 'locks', `${caseId}.lock`), 'utf8')) as {
		pid: number;
		children: number[];
	};
}

// The orchestrator resolves on its first turn; later orchestrator turns end.
export function resolveThenStop(request: ChatRequest): Reply {
	return toolResults(request).length === 0
		? { toolCalls: [{ name: 'spotlight_resolve', arguments: {} }] }
		: { text: 'Waiting at the gate.' };
}

// The last spotlight_resolve result the chat reported on stdout.
export function lastResolution(chat: Chat): Record<string, unknown> {
	const ends = chat.lines
		.map((line) => JSON.parse(line) as { type?: string; toolName?: string; result?: { content: Array<{ text: string }> } })
		.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'spotlight_resolve');
	assert.ok(ends.length > 0, 'the chat resolved the case');
	return JSON.parse(ends[ends.length - 1].result!.content[0].text) as Record<string, unknown>;
}

// A case whose methodology is approved, so the next worker task is Investigation.
// The member approved the brief: the case is at Methodology, where the
// investigator plans. Before it no investigator starts (refusalFor).
export async function briefApproved(space: Workspace, caseId = 'case-a'): Promise<void> {
	const caseDir = join(space.casesRoot, caseId);
	await mkdir(caseDir, { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), 'Who owns the fixture company?\n');
}

export async function caseAtInvestigation(space: Workspace, brief = 'Who owns the fixture company?', methodology: object = {}): Promise<string> {
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(join(caseDir, 'data'), { recursive: true });
	await writeFile(join(caseDir, 'brief-directions.txt'), `${brief}\n`);
	await writeFile(join(caseDir, 'data', 'methodology.json'), `${JSON.stringify(methodology)}\n`);
	await new Promise<void>((done, fail) =>
		execFile(
			process.env.SPOTLIGHT_PYTHON ?? 'python3',
			[
				join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'),
				'approve', 'methodology',
				'--approved-by', 'journalist:fixture',
				'--approved-at', '2026-08-23T12:00:00Z',
				'--authorized-cases-root', space.casesRoot,
				caseDir,
			],
			(error) => (error ? fail(error) : done()),
		),
	);
	return caseDir;
}

// Full case metadata matters when the harness validates each partial batch.
export async function factCheckCase(space: Workspace, findings: Array<{ id: string; claim: string }>): Promise<string> {
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--clean']);
	const path = join(caseDir, 'data', 'findings.json');
	const document = JSON.parse(await readFile(path, 'utf8'));
	document.findings = findings.map((finding) => ({ ...structuredClone(document.findings[0]), ...finding }));
	await writeFile(path, JSON.stringify(document));
	await unlink(join(caseDir, 'data', 'fact-check.json'));
	return caseDir;
}

// 12B r3 A2 cc seq 4 on ad768b54: the 12b ended a turn inside its reasoning with no text
// and no tool call, and the run ended with nothing for the member.
export const NUDGE = 'Your last turn was empty. Call the next tool, or give your answer.';
export const nudges = (request: ChatRequest) => request.messages.filter((message) => message.role === 'user' && JSON.stringify(message.content ?? '').includes(NUDGE)).length;

// Base 12B with no reasoning budget (step-eval, 2026-10-28) ran turns to the
// 10,240-token cap: the orchestrator repeating "<channel|><|channel>thought",
// a fact-checker cycling three sentences. Each sample is its repeated tail.
export const DEGENERATE_SAMPLES = {
	channel: '<channel|><|channel>thought\n'.repeat(400),
	sentences: 'Actually, I\'ll do the main one first.\n\nActually, I\'ll do them all.\n\nWait, I\'ll start.\n\n'.repeat(120),
};

// Progress watchdog (constellation-20261102, reports/watchdog.md): a worker is
// stopped after 16 model turns with no accepted record, or 6 after its last
// one, on every tier; its answer is the stop line and a JSON receipt.
export async function watchdogRun(t: TestContext, tier: string, worker: (turn: number, caseDir: string) => Reply) {
	let caseDir = '';
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'investigator')) return worker(toolResults(request).length, caseDir);
		return toolResults(request).length === 0 ? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'investigator', task: 'Investigate.' } }] } : { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	caseDir = await caseAtInvestigation(space);
	await settledPrompt(launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_MODEL_TIER: tier }), 'Continue.');
	const orchestrator = fake.requests.filter((request) => !isWorkerRequest(request, 'investigator'));
	const answer = toolResults(orchestrator[orchestrator.length - 1]).join('\n');
	const receipt = answer.match(/\[progress-watchdog receipt\] (\{.*\})/)?.[1];
	return { turns: fake.requests.filter((request) => isWorkerRequest(request, 'investigator')).length, answer, receipt: receipt ? JSON.parse(receipt.replace(/\\"/g, '"')) : undefined };
}
export const search = (turn: number): Reply => ({ toolCalls: [{ name: 'grep_files', arguments: { pattern: `absent text ${turn}`, path: 'data' } }] });
// Each record adds a finding, so the file's content changes (review W3).
export const record = (turn: number, path = 'data/findings.json'): Reply => ({ toolCalls: [{ name: 'write_file', arguments: { path, content: JSON.stringify({ project: 'case-a', findings: [{ id: `F${turn}`, claim: `Claim ${turn}.` }] }) } }] });

// Review W2: an AMENDMENT investigator's deliverable is its research step's
// file (roles.ts deliverableFor), here data/research-suggestions.json.
// step1-routing.md (fz1s1:10, :19): at research resume_at suggestions the move
// is fixed, so resolving launches that investigator; the orchestrator only
// resolves, and the resolver then reaches research_approval (the page is next).
// B18 (briefs/b18.md): the write commit step's request.
export const isWriteCommit = (request: ChatRequest) => JSON.stringify(request.messages.at(-1) ?? {}).includes('Commit step (from the harness, not the member): write ');
// The worker's write_file calls as its last request carries them: [id, path, result].
export const writeCalls = (request: ChatRequest) => {
	const results = new Map(request.messages.filter((message) => message.role === 'tool').map((message) => [message.tool_call_id, JSON.stringify(message.content)]));
	return request.messages.flatMap((message) => (message.tool_calls ?? []) as Array<{ id: string; function: { name: string; arguments: string } }>)
		.filter((call) => call.function.name === 'write_file')
		.map((call) => [call.id, JSON.parse(call.function.arguments).path, results.get(call.id) ?? ''] as const);
};

// LFM3 and the base 12B on Osaurus (7513e421) never planned Anomaly for a case
// with a payments table. On a local tier the harness asks the decision model
// when a PLANNING investigator finishes; on yes the plan gets the section,
// and the next EXECUTION task names the table and the call.
export async function anomalyStandIn(dir: string): Promise<string> {
	const home = join(dir, 'anomaly');
	const script = `import importlib.util, sys; from pathlib import Path
spec = importlib.util.spec_from_file_location("c", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.StandIn(Path(sys.argv[2]))`;
	await new Promise<void>((done, fail) =>
		execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', ['-c', script, join(CHECKOUT_ROOT, 'tests', 'anomaly-check.py'), home], (error) => (error ? fail(error) : done())),
	);
	return home;
}

export async function fakeDecisions(probability: number, choose: string | ((message: string) => string) = 'other') {
	const { createServer } = await import('node:http');
	const states: unknown[] = [];
	const server = createServer((request, response) => {
		let body = '';
		request.on('data', (chunk) => (body += chunk));
		request.on('end', () => {
			const parsed = JSON.parse(body) as { state: unknown; questions: Record<string, unknown> };
			states.push(parsed.state);
			const choice = typeof choose === 'string' ? choose : choose(String((parsed.state as { member_message?: unknown }).member_message ?? ''));
			const answers = Object.fromEntries(Object.entries(parsed.questions).map(([key, question]) => [key, (question as { type?: string }).type === 'choice' ? { choice, probabilities: { [choice]: probability } } : { noul: probability }]));
			response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ model: 'd1-test', answers }));
		});
	});
	await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
	const { port } = server.address() as { port: number };
	return { url: `http://127.0.0.1:${port}/v1/systemone`, states, close: () => new Promise<void>((done) => server.close(() => done())) };
}

// LFM3 e1 on Osaurus (7513e421): a planning investigator loaded these 11
// skills in one turn (99,184 characters), the fold could not cut inside that
// turn, and the next request had a 1-token completion budget.
export const SEQ7_SKILLS = ['integrations', 'osint', 'investigation-methodology', 'epistemic-grounding', 'acquisition-graduation', 'web-archiving', 'content-access', 'shell-safety', 'follow-the-money', 'social-media-intelligence', 'technical-investigation'];

// Tools whose effects stay on this machine (case files, the local vault, its
// wiki write and the tool index, the Anomaly lookup and runner, the passage writer, the resolver, the loopback review page), plus the two checks
// that refuse their remote step themselves when SPOTLIGHT_SENSITIVE is set.
// Any other tool a sensitive process declares is a possible way out.
export const LOCAL_TOOLS = new Set([
	'record_finding',
	'record_assessment',
	'judge_finding',
	'spotlight_resolve',
	'spotlight_transition',
	'spawn_agent',
	'invoke_skill',
	'open_review',
	'read_file',
	'write_file',
	'edit_file',
	'list_files',
	'grep_files',
	'hash_files',
	'build_review_html',
	'query_vault',
	'osint_tools',
	'exiftool',
	'provenance_manifest',
	'anomaly_methods',
	'anomaly_run',
	'wiki_destination',
	'source_expressions',
]);

// step2-design.md section 4: with Research on, an investigator that writes
// only the plan's content (directions, steps, topics on d1) gets the
// paperwork from code: the brief, IDs, mode, keys, egress, consent, budgets,
// tools_required and Navigator's skip reason; the plan then passes its check.
export const CONTENT_ONLY_PLAN = JSON.stringify({
	schema_version: '1.0', project: 'case-a',
	investigation_plan: [{ direction: 'Ownership', questions: ['Who owns the fixture company?'], steps: [{ order: 1, action: 'Read the registry entry', tool: 'fetch' }] }],
	research_plan: { topics: [{ direction_id: 'd1', lane_type: 'web', questions: ['Which registry names the owner?'] }] },
	// A 'used' Navigator claim with no receipt (Codex review of simp-4): replaced by what ran.
	navigator: { required: true, used: true, queries: [{ direction: 'Ownership', selected_tools: ['Invented tool'] }] },
});
// An activated case paused at the Ingest decision, built by the review-page
// fixture (tests/fixtures/review_case.py) through the resolver's own seam.
export async function caseDigests(caseDir: string): Promise<Map<string, string>> {
	const digests = new Map<string, string>();
	for (const entry of await readdir(caseDir, { recursive: true, withFileTypes: true })) {
		if (!entry.isFile()) continue;
		const path = join(entry.parentPath, entry.name);
		digests.set(path.slice(caseDir.length + 1), createHash('sha256').update(await readFile(path)).digest('hex'));
	}
	return digests;
}

export const PROPOSAL = {
	interface_version: '1',
	model: 'stub-model',
	claims: [
		{
			key: 'c1',
			finding_id: 'F1',
			origin_expression_id: 'SX1',
			proposition: 'Acme paid Doe.',
			language: 'en',
			attributed: false,
			statement: { subject: { text: 'Acme' }, predicate: 'paid', object: { text: 'Doe' } },
		},
	],
};

// The orchestrator delegates once to the analyst, then stops.
export function delegateToAnalyst(analyst: (request: ChatRequest) => Reply, task = 'Propose the knowledge batch.') {
	return (request: ChatRequest): Reply => {
		if (isWorkerRequest(request, 'analyst')) return analyst(request);
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'analyst', task } }] }
			: { text: 'Done.' };
	};
}

// Option D (paperwork-p5-option-d.md): members expect the case to go on once they decide in the browser.
export const atGate = (space: Workspace) => {
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(process.env.SPOTLIGHT_PYTHON ?? 'python3', [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	return caseDir;
};
export const holdScript = (request: ChatRequest): Reply => (request.messages.some((message) => message.role === 'tool') ? { text: 'Done.' } : { toolCalls: [{ name: 'open_review', arguments: {} }] });
export const notices = (chat: { lines: string[] }) => chat.lines.map((line) => JSON.parse(line) as { method?: string; message?: string }).filter((record) => record.method === 'notify').map((record) => record.message ?? '');
// What Osaurus shows: its codec reads text only from text_delta (PiRPCAdapter.swift decodeMessageUpdate).
export const shownText = (chat: { lines: string[] }) => chat.lines.map((line) => JSON.parse(line) as { type?: string; assistantMessageEvent?: { type?: string; delta?: string } }).filter((record) => record.type === 'message_update' && record.assistantMessageEvent?.type === 'text_delta').map((record) => record.assistantMessageEvent!.delta).join('');
export const settles = (chat: { lines: string[] }) => chat.lines.filter((line) => line.includes('"type":"agent_settled"')).length;

// An [investigator] fault code cannot repair (a link it can): a cited row with no stored artifact.
export type Row = { id: string; raw_path?: string; sha256?: string; text_derivatives?: unknown };
export const unrepairable = (items: Row[], id = 'E9') => {
	const { raw_path: _path, sha256: _hash, text_derivatives: _text, ...row } = structuredClone(items.find((item) => item.id === 'E1')!);
	items.push({ ...row, id });
};
export const withArtifact = (path: string) => {
	const items = JSON.parse(readFileSync(path, 'utf8')).items as Row[];
	const { raw_path, sha256 } = items.find((item) => item.id === 'E1')!;
	return items.map((item) => (item.id === 'E9' ? { ...item, raw_path, sha256 } : item));
};

export const stopCase = async (caseDir: string, stop: Record<string, unknown>) => {
	const path = join(caseDir, 'data', 'orchestration.json');
	const state = JSON.parse(await readFile(path, 'utf8'));
	await writeFile(path, JSON.stringify({ ...state, attempts: { [String(stop.exhausted_attempt)]: 1 }, blocked: { ...stop, attempts: {} } }));
	return path;
};

// The shared stand-in Engine `bsig` (tests/fixtures/case_search_bsig.py),
// answering `wiki case-search` with the NDJSON result contract, or refusing as
// Engine does while its switch is off. Its scenario sits next to it.
export async function standInBsig(dir: string, scenario: Record<string, unknown> = {}): Promise<string> {
	const path = join(dir, `bsig-${Math.random().toString(36).slice(2)}`);
	await copyFile(join(CHECKOUT_ROOT, 'tests', 'fixtures', 'case_search_bsig.py'), path);
	await chmod(path, 0o755);
	await writeFile(`${path}.json`, JSON.stringify({
		passages: [
			{ path: 'research/swarm/web/sources/s1.md', chunk: 0, text: 'Swarm capture on {claim}', score: 9 },
			{ path: 'research/registry.md', chunk: 0, text: 'Registry contradicts: {claim}', score: 8 },
		],
		...scenario,
	}));
	return path;
}

// Engine only indexes files that exist: the captures the stand-in's passages name.
export async function researchFiles(caseDir: string): Promise<void> {
	await mkdir(join(caseDir, 'research'), { recursive: true });
	for (const name of ['registry', 'filing', 'notes']) await writeFile(join(caseDir, 'research', `${name}.md`), 'capture');
}

export function userText(request: ChatRequest): string {
	const message = request.messages.find((each) => each.role === 'user');
	const content = message?.content;
	if (typeof content === 'string') return content;
	return Array.isArray(content) ? content.map((part) => (part as { text?: string }).text ?? '').join('') : '';
}

// The fact-checker's task with case passages, for one spawn whose stand-in
// Engine answers with `scenario`.
export async function factCheckerTask(t: TestContext, scenario: Record<string, unknown>): Promise<string> {
	const fake = await startFakeOpenAI((request) => {
		if (isWorkerRequest(request, 'fact-checker')) return { text: 'Checked.' };
		return toolResults(request).length === 0
			? { toolCalls: [{ name: 'spawn_agent', arguments: { agent: 'fact-checker', task: 'Check data/findings.json.' } }] }
			: { text: 'Done.' };
	});
	t.after(() => fake.close());
	const space = await workspace(t);
	const caseDir = await factCheckCase(space, [{ id: 'F1', claim: 'Acme paid Doe' }]);
	await researchFiles(caseDir);
	const chat = launch(t, { ...baseEnv(space, fake.baseURL), SPOTLIGHT_BSIG: await standInBsig(space.root, scenario) });
	await settledPrompt(chat, 'Continue.');
	const worker = fake.requests.find((request) => isWorkerRequest(request, 'fact-checker'));
	assert.ok(worker, 'the fact-checker ran');
	return userText(worker);
}

export const newsroomRoute = (baseURL: string) => [{ name: 'newsroom-model', base_url: baseURL, model: 'stub-model', inference: 'own' }];

// The orchestrator makes these calls in order, one per turn, then stops.
export function orchestratorScript(calls: Array<{ name: string; arguments: Record<string, unknown> }>, worker?: (request: ChatRequest) => Reply) {
	return (request: ChatRequest): Reply => {
		if (worker && isWorkerRequest(request, 'investigator')) return worker(request);
		const next = calls[toolResults(request).length];
		return next ? { toolCalls: [next] } : { text: 'Done.' };
	};
}

export const SEAM = join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py');
export const PYTHON = process.env.SPOTLIGHT_PYTHON ?? 'python3';
export const DISPATCH_TOOLS = ['invoke_skill', 'open_review', 'read_file', 'spawn_agent', 'spotlight_resolve', 'spotlight_transition', 'write_file'];

export function caseAtGate(space: Workspace): string {
	const caseDir = join(space.casesRoot, 'case-a');
	execFileSync(PYTHON, [join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'investigation_gate', '--activated', '--clean']);
	return caseDir;
}

// The member approves the gate on the review page; the seam records the same.
export function memberApprovesGate(caseDir: string): void {
	execFileSync(PYTHON, [SEAM, 'approve', 'investigation_gate', '--approved-by', 'journalist:fixture', '--approved-at', '2026-08-23T12:00:00Z', caseDir]);
}

export const REPORT_DRAFT = {
	schema_version: '1.0', title: 'Report', deck: 'What the case found.',
	framing_finding_ids: ['F1'], finding_order: ['F1'],
	finding_treatments: [{ finding_id: 'F1', headline: 'The filing names the payer', summary: 'One filing.', why_it_matters: 'It is primary.' }],
	caveats: [{ text: 'Only one filing.', finding_ids: ['F1'] }],
	next_steps: [{ text: 'Ask the payer.', finding_ids: ['F1'] }],
};

// e2e r1 (31B): the orchestrator wrote its own "MODE: METHODOLOGY AMENDMENT"
// task and misread the persisting missing amendment file. At the amendment
// step the investigator's task is code-built and code applies recordAmendment
// once the worker finishes.
// A researched case whose Research gate the member approved: the resolver
// is at research/amendment.
export function caseAtAmendment(space: Workspace, dismissClues = false, mark = 'dismiss'): string {
	const caseDir = join(space.casesRoot, 'case-a');
	const prepare = [
		'import importlib.util, json, sys',
		'from pathlib import Path',
		`spec = importlib.util.spec_from_file_location("rpc", ${JSON.stringify(join(CHECKOUT_ROOT, 'tests', 'research-phase-check.py'))})`,
		'm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)',
		'case = Path(sys.argv[1]); (case / "data").mkdir(parents=True)',
		'(case / "brief-directions.txt").write_text("Verify the offline fixture.\\n", encoding="utf-8")',
		'm.write_json(case / "data/methodology.json", m.research_methodology())',
		'import subprocess',
		'seam = lambda *a: subprocess.run([sys.executable, str(m.SEAM), *a, str(case)], check=True, capture_output=True, env={"PATH": "", "PYTHONNOUSERSITE": "1", "SPOTLIGHT_RESEARCH_SWARMS": "on"})',
		'seam("approve", "methodology", *m.APPROVER)',
		'm.write_json(case / "data/leads.json", json.loads(m.LEADS_SAMPLE.read_text(encoding="utf-8")))',
		'm.write_json(case / "data/clues.json", json.loads(m.CLUES_SAMPLE.read_text(encoding="utf-8")))',
		'm.prepare_research_gate(case)',
		...(dismissClues ? [`marks = {f"clue:{clue[\'clue_id\']}": "${mark}" for clue in json.loads(m.CLUES_SAMPLE.read_text(encoding="utf-8"))["clues"]}`, 'm.write_json(case / "data/research-review.json", {"schema_version": "spotlight-review/v1", "gate": "research", "reviewer": "journalist:fixture", "items": {t: {"target": t, "final": mark} for t, mark in marks.items()}})'] : []),
		'seam("approve", "research", *m.APPROVER)',
	].join('\n');
	execFileSync(PYTHON, ['-c', prepare, caseDir]);
	return caseDir;
}

// step2-design.md section 4: the amendment may also be only the directions it
// changes; code completes it from the approved methodology and the marks.
// r2-k1.md: at a stalled investigation's research round the resolver named no
// next step and the orchestrator improvised. Resolving now launches the
// investigator with the code-built TASK: ROUND, code starts the round when the
// worker has written it, and the case leaves round_topics for the round.
export function caseAtRoundTopics(space: Workspace): { caseDir: string; round: string } {
	const caseDir = join(space.casesRoot, 'case-a');
	const prepare = [
		'import importlib.util, json, sys',
		'from pathlib import Path',
		`spec = importlib.util.spec_from_file_location("rgc", ${JSON.stringify(join(CHECKOUT_ROOT, 'tests', 'research-gate-check.py'))})`,
		'm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)',
		'g = m.ResearchGate("test_a_stall_goes_to_a_research_round_once_then_stops_with_a_gaps_report")',
		'case = Path(sys.argv[1]); (case / "data").mkdir(parents=True)',
		'(case / "brief-directions.txt").write_text("Who owns Northwind Holdings?\\n", encoding="utf-8")',
		'm.write_json(case / "data/methodology.json", m.researched_methodology())',
		'g.case = case',
		'g.through_amendment()',
		'for cycle in (1, 2, 3): g.fail_cycle(cycle)',
		'print(json.dumps(m.ROUND))',
	].join('\n');
	const round = execFileSync(PYTHON, ['-c', prepare, caseDir], { env: { ...process.env, SPOTLIGHT_RESEARCH_SWARMS: 'on' }, encoding: 'utf8' }).trim().split('\n').at(-1)!;
	return { caseDir, round };
}

// The install config under which the conformance methodology passes the gate.
export async function gateConfig(space: Workspace): Promise<string> {
	const config = join(space.root, 'spotlight-config.json');
	await writeFile(config, JSON.stringify({ model_tier: 'frontier', integrations: { osint_navigator: { status: 'red', required_in_phase_2: false } } }));
	return config;
}

export const resolveResults = (chat: Chat) => chat.lines
	.map((line) => JSON.parse(line) as { type?: string; toolName?: string; result?: { content: Array<{ text: string }> } })
	.filter((record) => record.type === 'tool_execution_end' && record.toolName === 'spotlight_resolve' && record.result!.content[0].text.startsWith('{'))
	.map((record) => JSON.parse(record.result!.content[0].text) as Record<string, unknown>);
