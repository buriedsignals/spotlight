#!/usr/bin/env node
// Spotlight harness launcher: one long-lived `pi --mode rpc` orchestrator per
// chat. Stdin and stdout pass through untouched (stdout carries only Pi's RPC
// records); the launcher itself writes only to stderr. Closing stdin shuts the
// chat down in order, SIGTERM is the fallback, and no pi child outlives it.
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { ConfigError, RouteRefused, checkNewsroomInstall, checkRouteSecrets, childEnv, parseLaunchArgs, piSettings, readConfig, researchEnv, routeFor, resolverEnv, secretValues } from './config.ts';
import {
	CASE_REFERENCE_FILE,
	CaseRecordError,
	EditorialCredentialError,
	EditorialToolError,
	KNOWLEDGE_TOOLS,
	NEWSROOM_UNAVAILABLE,
	editorialClient,
	ensureCaseRegistered,
	isAllowedOrigin,
	pendingSubmission,
	runChecks,
	stopsTheNewsroomCase,
	submitCase,
} from './editorial.ts';
import type { CheckKind, EditorialClient, RunPackage } from './editorial.ts';
import { openInBrowser } from '../../review/session.mjs';
import { LEGACY_MARKERS } from './tools/case-files.ts';
import { newsroomData } from './tools/editorial.ts';
import { DESKTOP_ENV, capped, programEnv, runFixed, runPython } from './tools/run.ts';
import type { HarnessConfig, LaunchArgs } from './config.ts';
import { CaseHeldError, acquireCaseLock } from './lock.ts';
import type { CaseLock } from './lock.ts';
import { redactingLines, redactor, stderrLogger } from './log.ts';
import { piArguments, piCliPath, writePiSettings } from './pi-process.ts';
import { runResearchRound } from './lanes.ts';
import type { ResearchPlan, RoundSummary } from './lanes.ts';
import { discardIncompleteLanes } from './resume.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, deliverableFor, factCheckChecklist, isolationRefusal, nextCheckBatch, readCheckDocs, loadComposition, skillsFor, spawnRefusal, spawnableWorkers, systemPrompt } from './roles.ts';
import type { Composition, Deliverable, WorkerContext } from './roles.ts';
import { approvedIndicatorFramework } from './tools/hosted-skill.ts';
import { BRIEF_PENDING_REFUSAL, GATE_DECIDED_REFUSAL, GATE_READY_REFUSAL, WorkerQueue, runWorker } from './subagents.ts';
import { withCasePassages, withWikiPassages } from './case-search.ts';
import { decideAnomaly, withAnomalyBlock } from './anomaly-decision.ts';
import { directionBlock, directionCallBudget, directionDeliverable, directionsToRun, planDirections } from './directions.ts';
import type { CancelRequest, EditorialRequest, ResearchRequest, SpawnRequest, SpawnResult } from './subagents.ts';
import { bindActiveCase, nextAction, pythonAdapter, runsResearchRound } from './tools/spotlight.ts';
import type { ActiveCase } from './tools/spotlight.ts';
import { PLANNING_PHASES, validateAfterWorker } from './validators.ts';

const STDIN_CLOSE_GRACE_MS = 15_000;
const KILL_GRACE_MS = 5_000;

// Newsroom edition: `signIn`, the newsroom token was revoked or expired;
// `newsroom`, the Editorial MCP could not be used.
const EXIT = { config: 2, held: 3, failed: 1, signIn: 4, newsroom: 5 } as const;

type Harness = {
	config: HarnessConfig;
	args: LaunchArgs;
	active: ActiveCase;
	composition: Composition;
	lock: CaseLock;
	log: (message: string) => void;
	redact: (text: string) => string;
	children: Set<ChildProcess>;
	// Newsroom edition: the only Editorial MCP client, holding the token.
	editorial?: EditorialClient;
	// The newsroom case reference, validated at the start.
	caseRef?: string;
	// Why the knowledge batch cannot be built for a reason no agent can fix;
	// no analyst runs again in this chat once it is set.
	batchBlocked?: string;
};

// A revoked or expired newsroom token, or an Editorial MCP that cannot be
// used, stops the whole harness with a plain message: a newsroom case does
// not go on without the newsroom's knowledge and checks, and nothing falls
// back to another route or service.
function newsroomStop(error: unknown): { code: number; message: string } {
	if (error instanceof EditorialCredentialError) return { code: EXIT.signIn, message: error.message };
	if (error instanceof CaseRecordError) return { code: EXIT.newsroom, message: `${error.message}. Spotlight stopped; resume the case to finish.` };
	return { code: EXIT.newsroom, message: NEWSROOM_UNAVAILABLE };
}

let stoppingForNewsroom = false;
function stopForNewsroom(harness: Harness, error: unknown): void {
	if (stoppingForNewsroom) return;
	stoppingForNewsroom = true;
	const { code, message } = newsroomStop(error);
	harness.log(`stopped: ${message}`);
	if (!(error instanceof EditorialCredentialError)) harness.log(`the Editorial MCP: ${harness.redact((error as Error).message)}`);
	// Let the refusal reach the process that asked, then stop everything.
	setTimeout(() => {
		for (const child of harness.children) child.kill('SIGTERM');
		process.exit(code);
	}, 200);
}

// Serves one process's newsroom tool calls. The operation must be one of the
// tools that process's role holds in this edition.
function serveEditorial(harness: Harness, child: ChildProcess, role: string, running: Map<string, AbortController>): void {
	child.on('message', (message: unknown) => {
		const request = message as EditorialRequest | CancelRequest;
		if (request?.kind === 'spotlight-cancel') {
			running.get(request.id)?.abort();
			return;
		}
		if (request?.kind !== 'spotlight-editorial') return;
		const controller = new AbortController();
		running.set(request.id, controller);
		const reply = (result: Omit<SpawnResult, 'kind' | 'id'>) => {
			if (child.connected) child.send({ kind: 'spotlight-result', id: request.id, ...result });
		};
		editorialOperation(harness, role, request.op, request.args ?? {}, controller.signal)
			.then((text) => reply({ ok: true, text }))
			.catch((error: Error) => {
				if (stopsTheNewsroomCase(error)) {
					reply({ ok: false, error: `stopped: ${newsroomStop(error).message}` });
					stopForNewsroom(harness, error);
					return;
				}
				// A tool's own error text comes from the Editorial MCP.
				if (error instanceof EditorialToolError) {
					reply({ ok: false, error: `the newsroom answered ${request.op} with an error:\n${newsroomData(capped(harness.redact(error.message)))}` });
					return;
				}
				reply({ ok: false, error: harness.redact(error.message) });
			})
			.finally(() => running.delete(request.id));
	});
}

async function editorialOperation(harness: Harness, role: string, op: string, args: Record<string, unknown>, signal: AbortSignal): Promise<string> {
	const client = harness.editorial;
	if (!client) throw new Error(`${op} is a newsroom edition tool`);
	const spec = agentSpec(harness.composition, role);
	if (!allowedTools(spec, harness.config.sensitive, harness.config.edition).includes(op)) throw new Error(`${op} is not a tool of the ${role}`);
	if ((KNOWLEDGE_TOOLS as readonly string[]).includes(op)) return JSON.stringify(await client.call(op, args, signal));
	const { config, active } = harness;
	if (op === 'run_checks') {
		const kind = args.kind === 'report' ? 'report' : args.kind === 'findings' ? 'findings' : undefined;
		if (!kind) throw new Error('run_checks: kind must be "findings" or "report"');
		return JSON.stringify(await runChecks(client, harness.caseRef!, active.caseDir, kind as CheckKind, packageRunner(harness), { signal }));
	}
	if (op === 'newsroom_submit') {
		const status = await caseStatus(harness, true);
		const step = (status.ingest as { resume_at?: string } | undefined)?.resume_at;
		if (status.phase !== 'ingest' || step !== 'ingest') {
			return JSON.stringify({ skipped: `the case is at ${String(status.phase ?? '') || 'an unknown phase'}${step ? ` (${step})` : ''}; Submit runs only after the member requested ingestion on the page` });
		}
		return JSON.stringify(await submit(harness, client, signal));
	}
	throw new Error(`unknown newsroom operation ${op}`);
}

// scripts/newsroom-package.py: builds the Submit package and is the only
// writer of the newsroom case files, under the case lock.
function packageRunner(harness: Harness): RunPackage {
	const { config } = harness;
	return (args, input) =>
		runFixed(config.python, [resolve(CHECKOUT_ROOT, 'scripts/newsroom-package.py'), ...args], {
			env: programEnv(process.env, [], { SPOTLIGHT_CONFIG: config.spotlightConfig }),
			cwd: CHECKOUT_ROOT,
			timeoutMs: 300_000,
			input: input === undefined ? undefined : JSON.stringify(input),
		});
}

// The newsroom's review page opens in the member's browser, as open_review
// opens the local one; its address goes nowhere else, and only an address on
// one of the newsroom's own endpoints is opened.
function reviewOpener(harness: Harness): (url: string) => Promise<boolean> {
	return async (url) => {
		if (!isAllowedOrigin(harness.config.newsroomOrigins, url)) {
			harness.log('the newsroom review address is not on one of the newsroom\'s endpoints; it was not opened');
			return false;
		}
		// The opener sees the system basics and the desktop session, never the
		// launcher's tokens, whatever their variable names.
		const env = programEnv(process.env, DESKTOP_ENV);
		if (!(await openInBrowser(url, { env })).ok) throw new Error('the newsroom review page could not be opened; the submission stays pending and is resumed at the next start');
		return true;
	};
}

async function submit(harness: Harness, client: EditorialClient, signal?: AbortSignal) {
	const outcome = await submitCase({ client, caseRef: harness.caseRef!, caseDir: harness.active.caseDir, signal, runPackage: packageRunner(harness), openReview: reviewOpener(harness) });
	harness.log(outcome.state === 'submitted' ? `submitted the case to the newsroom (${outcome.receipts.length} receipt(s) in receipts/)` : 'the newsroom refused the package: integrity checks failed');
	if (outcome.state === 'submitted' && outcome.notice) harness.log(outcome.notice);
	return outcome;
}

function track(harness: Harness, child: ChildProcess): void {
	harness.children.add(child);
	const record = () =>
		harness.lock.setChildren([...harness.children].map((each) => each.pid).filter((pid): pid is number => !!pid));
	record();
	const forget = () => {
		harness.children.delete(child);
		record();
	};
	child.once('exit', forget);
	// A process that never started emits 'error' and may never emit 'exit'.
	child.once('error', () => {
		if (child.pid === undefined) forget();
	});
}

function forwardStderr(harness: Harness, child: ChildProcess): void {
	child.stderr?.pipe(redactingLines(harness.redact)).pipe(process.stderr, { end: false });
}

// Called right before each pi process starts: it also writes that process's
// Pi settings (the local tier's fold point) into its configuration folder.
function processLaunch(harness: Harness, role: string, worker?: WorkerContext, direction?: { tag: string; budget: number }, fold: Record<string, string> = {}) {
	const route = routeFor(harness.config);
	const spec = agentSpec(harness.composition, role);
	const env = childEnv(harness.config, harness.composition, role, process.env);
	if (direction) Object.assign(env, { SPOTLIGHT_DIRECTION: direction.tag, SPOTLIGHT_CALL_BUDGET: String(direction.budget) });
	Object.assign(env, fold);
	writePiSettings(env.PI_CODING_AGENT_DIR, piSettings(harness.config));
	return {
		command: process.execPath,
		args: piArguments({
			systemPrompt: systemPrompt(harness.composition, role, harness.config.modelTier, harness.config.edition, harness.config.research, worker),
			skills: skillsFor(harness.composition, role),
			tools: allowedTools(spec, harness.config.sensitive, harness.config.edition),
			model: route.model,
			// The chat client's system prompt is for the orchestrator only.
			appendSystemPrompt: role === ORCHESTRATOR ? harness.args.appendSystemPrompt : undefined,
		}),
		env,
		cwd: resolve(harness.config.dataDir, 'work', role),
	};
}

function serveWorkers(harness: Harness, orchestrator: ChildProcess): void {
	const queue = new WorkerQueue();
	const running = new Map<string, AbortController>();
	const workers = spawnableWorkers(harness.composition);
	serveEditorial(harness, orchestrator, ORCHESTRATOR, running);
	orchestrator.on('message', (message: unknown) => {
		const request = message as SpawnRequest | CancelRequest | ResearchRequest;
		if (request?.kind === 'spotlight-cancel') {
			running.get(request.id)?.abort();
			return;
		}
		if (request?.kind === 'spotlight-research') {
			const controller = new AbortController();
			running.set(request.id, controller);
			const reply = (result: Omit<SpawnResult, 'kind' | 'id'>) => {
				if (orchestrator.connected) orchestrator.send({ kind: 'spotlight-result', id: request.id, ...result });
			};
			researchRound(harness, request.retry, controller.signal)
				.then((round) => reply({ ok: true, round }))
				.catch((error: Error) => {
					reply({ ok: false, error: stopsTheNewsroomCase(error) ? `stopped: ${newsroomStop(error).message}` : harness.redact(error.message) });
					if (stopsTheNewsroomCase(error)) stopForNewsroom(harness, error);
				})
				.finally(() => running.delete(request.id));
			return;
		}
		if (request?.kind !== 'spotlight-spawn') return;
		const reply = (result: Omit<SpawnResult, 'kind' | 'id'>) => {
			if (orchestrator.connected) orchestrator.send({ kind: 'spotlight-result', id: request.id, ...result });
		};
		if (!workers.includes(request.agent)) {
			reply({ ok: false, error: `unknown worker "${request.agent}"` });
			return;
		}
		const controller = new AbortController();
		running.set(request.id, controller);
		let owed: Deliverable | undefined;
		refusalFor(harness, request.agent, request.task)
			.then((refusal) => {
				if (refusal) throw new Error(`spawn_agent: ${refusal}`);
				// Case search runs inside the queue's admission, so it counts
				// against the worker limit, and a cancel stops it.
				return queue.run(async () => {
					await activateNewCase(harness, request.agent, controller.signal);
					owed = await deliverable(harness, request.agent, request.task);
					const run = async (task: string, due: Deliverable | undefined, direction?: { tag: string; budget: number }) => {
						const worker = await finalTask(harness, request.agent, task, controller.signal, due);
						const kept = keepPassages(harness, request.agent, worker.passages);
						const fold = {
							SPOTLIGHT_TASK_HEADER: worker.header,
							...(worker.start ? { SPOTLIGHT_TASK_START: worker.start } : {}),
							...(kept ? { SPOTLIGHT_TASK_PASSAGES: kept } : {}),
						};
						return runWorker(
							{
								...processLaunch(harness, request.agent, await workerContext(harness, request.agent, request.task), direction, fold),
								onSpawn: (child) => {
									track(harness, child);
									serveEditorial(harness, child, request.agent, running);
								},
								onStderr: (child) => forwardStderr(harness, child),
							},
							worker.text,
							controller.signal,
						);
					};
					// On a local tier, an investigation task on a plan of two or
					// more directions runs one worker per direction (directions.ts).
					// The frontier tier's window holds every direction at once.
					const split = owed?.files.includes('data/findings.json') && harness.config.modelTier !== 'frontier';
					const directions = split ? planDirections(harness.active.caseDir) : [];
					if (directions.length < 2) return run(request.task, owed);
					const todo = directionsToRun(harness.active.caseDir, directions);
					const budget = directionCallBudget(todo.length);
					const answers: string[] = [];
					for (const direction of todo) {
						harness.log(`investigator: direction ${direction.tag} (${direction.ref}), ${budget} calls`);
						let answer: string;
						try {
							answer = await run(`${request.task}\n\n${directionBlock(direction, directions.length)}`, directionDeliverable(direction.tag), { tag: direction.tag, budget });
						} catch (error) {
							if (controller.signal.aborted) throw error;
							answer = (error as Error).message;
						}
						answers.push(`Direction ${direction.tag} (${direction.ref}): ${answer}`);
					}
					return answers.join('\n\n');
				});
			})
			.then((text) => withValidation(harness, request.agent, text))
			.then((text) => reply({ ok: true, text }))
			.catch((error: Error) => reply({ ok: false, error: harness.redact(withRespawn(harness, request.agent, error.message, owed)) }))
			.finally(() => running.delete(request.id));
	});
}

// `strict`: a resolver failure is an error, never an unknown phase.
async function caseStatus(harness: Harness, strict = false): Promise<Record<string, unknown>> {
	const { config, active } = harness;
	try {
		const status = await pythonAdapter(config.python, resolverEnv({ ...process.env, ...researchEnv(config) }, harness.composition, config.routes))(['status', '--json', '--authorized-cases-root', active.casesRoot, active.caseDir]);
		return JSON.parse(status) as Record<string, unknown>;
	} catch (error) {
		const message = `could not resolve the case phase: ${harness.redact((error as Error).message)}`;
		if (strict) throw new Error(message);
		harness.log(message);
		return {};
	}
}

async function casePhase(harness: Harness): Promise<string> {
	return String((await caseStatus(harness)).phase ?? '');
}

// Research runs in code, never on a model's decision: only while the resolver
// reports the Research phase (so the approved methodology covers the plan
// read here), one round at a time.
let roundInFlight: Promise<RoundSummary | { skipped: string }> | undefined;
function researchRound(harness: Harness, retry: boolean, signal: AbortSignal): Promise<RoundSummary | { skipped: string }> {
	roundInFlight ??= (async () => {
		const status = await caseStatus(harness);
		if (!runsResearchRound(status)) {
			const step = (status.research as { resume_at?: string } | undefined)?.resume_at;
			return { skipped: `the case is at ${String(status.phase ?? '') || 'an unknown phase'}${step ? ` (${step})` : ''}, not waiting for research lanes` };
		}
		const { config, active } = harness;
		const methodology = JSON.parse(readFileSync(resolve(active.caseDir, 'data/methodology.json'), 'utf8')) as { research_plan?: ResearchPlan };
		if (!methodology.research_plan) return { skipped: 'the approved methodology has no research plan' };
		const route = routeFor(config);
		const summary = await runResearchRound({
			caseDir: active.caseDir,
			plan: methodology.research_plan,
			config,
			local: { baseURL: route.baseURL, model: route.model, key: route.keyEnv ? process.env[route.keyEnv] : undefined },
			env: process.env,
			retry,
			signal,
			// Newsroom edition: the prior-knowledge lane reads search_knowledge.
			knowledge: harness.editorial
				? async (query, laneSignal) => {
					try {
						return JSON.stringify(await harness.editorial!.call('search_knowledge', { query, limit: 20 }, laneSignal));
					} catch (error) {
						if (stopsTheNewsroomCase(error)) stopForNewsroom(harness, error);
						throw error;
					}
				}
				: undefined,
		});
		harness.log(`research round ${summary.round}: ${summary.ran.length} topic(s) ran, ${summary.left_out.length} left out${summary.stopped ? `, stopped (${summary.stopped.code})` : ''}`);
		return summary;
	})().finally(() => {
		roundInFlight = undefined;
	});
	return roundInFlight;
}

// What the worker's persona is cut to (roles.ts slicePersona), read at the
// spawn, after a new case was activated.
async function workerContext(harness: Harness, role: string, task: string): Promise<WorkerContext> {
	const { config, active } = harness;
	return {
		phase: await casePhase(harness),
		task,
		conditions: {
			vault: allowedTools(agentSpec(harness.composition, role), config.sensitive, config.edition).includes('query_vault'),
			framework: approvedIndicatorFramework(active.caseDir),
			sensitive: config.sensitive,
			research: config.research,
			expressions: existsSync(resolve(active.caseDir, 'data/case-contract.json')) || /^SOURCE_EXPRESSION_MODE:\s*pilot\s*$/m.test(task),
		},
	};
}

// The role's deliverable for this spawn (roles.ts deliverableFor), read at the spawn.
async function deliverable(harness: Harness, role: string, task: string): Promise<Deliverable | undefined> {
	const status = await caseStatus(harness);
	const step = (status.research as { resume_at?: unknown } | undefined)?.resume_at;
	const owed = deliverableFor(role, String(status.phase ?? ''), task, typeof step === 'string' ? step : undefined);
	const start = role === 'fact-checker' ? factCheckStart(harness.active.caseDir) : '';
	return owed && start ? { ...owed, start } : owed;
}

// U3 census (84674fd9): fact-checkers rebuilt the file's structure by hand
// and wrote fact-check.json late or never. A first fact-check task carries
// its checklist (roles.ts factCheckChecklist). Only the task text carries it:
// a file the harness wrote would read to the resolver as the fact-checker's
// own work.

// arms on 8699f11d (ds-spectator seq 96): a fact-checker re-spawned with 16 of
// 22 findings still without a claim had no checklist, made 250 calls without
// writing, and the case blocked. Any fact-checker task, a correction too,
// carries the next findings while some have no claim.
function factCheckStart(caseDir: string): string {
	try {
		const docs = readCheckDocs(caseDir);
		if (existsSync(resolve(caseDir, 'data/fact-check.json')) && nextCheckBatch(docs).left === 0) return '';
		return factCheckChecklist(docs);
	} catch {
		return '';
	}
}

// Findings with no claim after a fact-checker run are its unfinished work, not
// an evidence error: the next step is a fact-checker that continues.
export function unfinishedNote(caseDir: string): string {
	const { left, total } = nextCheckBatch(readCheckDocs(caseDir));
	if (left === 0 || !existsSync(resolve(caseDir, 'data/fact-check.json'))) return '';
	return `\n[${left} of ${total} findings have no claim yet. That is unfinished work, not an evidence error: spawn the fact-checker again with a task to continue; it gets the next findings to check.]`;
}

// A worker that ended without an answer: when a file it had to write is still
// missing, the orchestrator is told which, and to spawn it again.
function withRespawn(harness: Harness, role: string, message: string, owed: Deliverable | undefined): string {
	if (!owed || !/worker ended without an answer/.test(message)) return message;
	const missing = owed.files.filter((file) => !existsSync(resolve(harness.active.caseDir, file)));
	if (missing.length === 0) return message;
	return `${message}. ${missing.join(' and ')} ${missing.length === 1 ? 'is' : 'are'} still missing. Next: spawn the ${role} again with the same task.`;
}

// The task the worker gets: the requested one plus, for the fact-checker, the
// case passages, and in a sensitive solo case the wiki passages for the
// investigator and the fact-checker. D9 is checked again on that final text;
// passages that would name what the fact-checker may not see are left out and
// the task goes as asked.
// What a worker gets, and what comes back after a fold (checkpoint.ts): the
// task's own lines without the case or wiki passages, the deliverable's
// structure apart, and the passages, which the launcher keeps in a case file.
type WorkerTask = { text: string; header: string; start?: string; passages?: string };

async function finalTask(harness: Harness, role: string, task: string, signal: AbortSignal, owed?: Deliverable): Promise<WorkerTask> {
	const line = owed ? `\n\n${owed.line}` : '';
	const start = owed?.start ? `\n${owed.start}` : '';
	const withCase = await withCasePassages(role, task, {
		caseDir: harness.active.caseDir,
		casesRoot: harness.active.casesRoot,
		python: harness.config.python,
		env: process.env,
		log: harness.log,
		signal,
	});
	const withPassages = await withWikiPassages(role, withCase, {
		caseDir: harness.active.caseDir,
		python: harness.config.python,
		configPath: harness.config.spotlightConfig,
		env: process.env,
		sensitive: harness.config.sensitive && harness.config.edition !== 'newsroom',
		log: harness.log,
		signal,
	});
	if (withPassages !== task && isolationRefusal(role, withPassages)) {
		harness.log('case passages left out: they name material the fact-checker works without');
		return { text: `${task}${line}${start}`, header: `${task}${line}`, start: owed?.start };
	}
	const withAnomaly = await withAnomalyBlock(role, withPassages, anomalyOptions(harness, signal));
	// Each step appends its block, so the passages and the anomaly block are the added text.
	const passages = withPassages.startsWith(task) ? withPassages.slice(task.length).trim() : '';
	const anomaly = withAnomaly.startsWith(withPassages) ? withAnomaly.slice(withPassages.length) : '';
	return { text: `${withAnomaly}${line}${start}`, header: `${task}${anomaly}${line}`, start: owed?.start, passages: passages || undefined };
}

// The passages a worker started with, kept once so a fold can point at them
// instead of carrying them (wave 1 on 52bf6183: about 15k of the
// fact-checker's 21.8k-character task). The file sits in the harness data
// folder, never in the case, so nothing that lists or hashes the case sees it;
// read_file reads exactly that one file (tools/case-files.ts).
let passagesWritten = 0;
function keepPassages(harness: Harness, role: string, passages: string | undefined): string | undefined {
	if (!passages) return undefined;
	const folder = resolve(harness.config.dataDir, 'work', role, 'passages');
	const path = join(folder, `${role}-${Date.now()}-${++passagesWritten}.md`);
	try {
		mkdirSync(folder, { recursive: true, mode: 0o700 });
		writeFileSync(path, `${passages}\n`, { mode: 0o600 });
		return path;
	} catch (error) {
		harness.log(`the ${role}'s case passages could not be kept: ${(error as Error).message}`);
		return undefined;
	}
}

function anomalyOptions(harness: Harness, signal?: AbortSignal) {
	return {
		caseDir: harness.active.caseDir,
		python: harness.config.python,
		configPath: harness.config.spotlightConfig,
		sensitive: harness.config.sensitive,
		env: process.env,
		log: harness.log,
		signal,
	};
}

// A new case starts activated (phase-investigation, "Source-expression release
// mode", step 2): before the first investigator task at Investigation, with no
// contract and no findings yet, the harness runs case-contract.py activate-new
// itself. No role may write data/case-contract.json (tools/case-files.ts). The
// script refuses a case that already has investigation files; that case stays
// legacy, as the skill's step 3 says, and the investigator still starts.

// One activation at a time, so two investigators starting together on a new
// case do not race the one-shot script.
let activationInFlight: Promise<void> | undefined;
function activateNewCase(harness: Harness, role: string, signal: AbortSignal): Promise<void> {
	if (role !== 'investigator') return Promise.resolve();
	const run = (activationInFlight ?? Promise.resolve()).catch(() => {}).then(() => activateOnce(harness, signal));
	activationInFlight = run;
	return run;
}

async function activateOnce(harness: Harness, signal: AbortSignal): Promise<void> {
	const { config, active } = harness;
	const exists = (path: string) => existsSync(resolve(active.caseDir, path));
	// Activated only when both artifacts exist; a contract without its passages
	// file (an older publish order) is repaired by activate-new, and a malformed
	// one is refused by it.
	if ((exists('data/case-contract.json') && exists('data/source-expressions.json')) || LEGACY_MARKERS.some(exists)) return;
	// Strict: a resolver failure must not let a new case start unactivated.
	if (String((await caseStatus(harness, true)).phase ?? '') !== 'investigation') return;
	const result = await runPython(
		{ caseDir: active.caseDir, writeRoot: active.caseDir, python: config.python, configPath: config.spotlightConfig, env: process.env },
		[resolve(CHECKOUT_ROOT, 'scripts/case-contract.py'), 'activate-new', active.caseDir],
		{ signal, timeoutMs: 60_000 },
	);
	// A new case never runs unactivated: the investigator would write contract
	// 1.1 artifacts that no validator accepts without the contract.
	if (result.code !== 0) throw new Error(`the case could not be activated, so the investigator did not start: ${`${result.stderr}${result.stdout}`.trim()}`);
}

// A worker with spawn conditions in harness/composition.json (the analyst:
// Ingest, solo edition) starts only when the case meets them.
// LFM3 e2 stage 1 on Osaurus (bc87c716): right after the fact-check was
// accepted, the orchestrator spawned another fact-checker instead of opening
// the Investigation gate. On the 12b, a worker spawn while the resolver offers
// the gate with nothing missing is refused with the next step.

async function gateReady(harness: Harness): Promise<boolean> {
	const status = await caseStatus(harness);
	// At a ready gate the only thing missing is the member's approval itself.
	const missing = Array.isArray(status.missing) ? status.missing : [];
	return status.next_phase === 'investigation_gate_approval' && missing.every((item) => item === 'Investigation gate approval');
}

// LFM3 e2 kde stage 2 on Osaurus (03f008b0): at Report, ten investigator
// spawns rewrote the findings the member had approved, and the approved
// fact-check no longer validated. A follow-up (requestFollowUp, which the
// member asks for) returns the case to Investigation first, so after the gate
// no investigator runs, on every tier.
const GATE_DECIDED_PHASES = new Set(['report', 'ingest', 'complete']);

async function refusalFor(harness: Harness, role: string, task: string): Promise<string | undefined> {
	const isolation = isolationRefusal(role, task);
	if (isolation) return isolation;
	if ((role === 'investigator' || role === 'fact-checker') && harness.config.modelTier === '12b' && (await gateReady(harness))) return GATE_READY_REFUSAL;
	if (role === 'investigator') {
		const phase = await casePhase(harness);
		if (GATE_DECIDED_PHASES.has(phase)) return GATE_DECIDED_REFUSAL;
		// r3 held-kde run (ecbcd23e): at brief, with no brief-directions.txt,
		// an investigator ran 69 calls of research before any methodology.
		if (phase === 'brief') return BRIEF_PENDING_REFUSAL;
	}
	if (role === 'analyst' && harness.batchBlocked) return `${BATCH_BLOCKED} ${harness.batchBlocked}`;
	const spec = agentSpec(harness.composition, role);
	if (!spec.phases && !spec.editions) return undefined;
	const status = await caseStatus(harness);
	const refusal = spawnRefusal(spec, role, spec.phases ? String(status.phase ?? '') : '', harness.config.edition);
	// U3 census (84674fd9): a refused spawn was asked again 3 to 5 times; the
	// refusal now carries the step the case is waiting on.
	const next = refusal ? nextAction(status, (relative) => existsSync(resolve(harness.active.caseDir, relative))) : undefined;
	return refusal && next ? `${refusal}. ${next}` : refusal;
}

// Fixed point: when a worker finishes, the harness runs the validators for
// what that worker writes and hands their outcome to the orchestrator with the
// worker's answer. The resolver's phase tells planning from investigation.
// LFM3 r3 kde stage 3 on Osaurus (ecbcd23e): the builder refused, the
// orchestrator spawned analysts that could not fix it, and they read and
// grepped until stopped. When the proposal passes every check the builder
// makes, the refusal is the case's or the harness's: the orchestrator is told
// so, and no analyst runs again in this chat.
const BATCH_BLOCKED = 'The knowledge batch cannot be built for a reason no agent can fix, so no analyst runs again. Tell the member this in plain words and stop. The builder said:';

async function proposalNotAtFault(harness: Harness): Promise<boolean> {
	let proposal: string;
	try {
		proposal = readFileSync(join(harness.active.caseDir, 'data', 'knowledge-proposal.json'), 'utf8');
	} catch {
		return false;
	}
	const result = await runPython(
		{ caseDir: harness.active.caseDir, writeRoot: harness.active.caseDir, python: harness.config.python, configPath: harness.config.spotlightConfig, env: process.env },
		[join(CHECKOUT_ROOT, 'scripts', 'build-knowledge-batch.py'), harness.active.caseDir, '--check-proposal'],
		{ input: proposal, timeoutMs: 120_000 },
	);
	return result.code === 0 || result.code === 3;
}

async function withValidation(harness: Harness, role: string, text: string): Promise<string> {
	const { config, active } = harness;
	let phase = await casePhase(harness);
	// On a local tier the decision model may add the plan's anomaly section
	// before the methodology is checked (anomaly-decision.ts).
	if (role === 'investigator' && PLANNING_PHASES.has(phase) && config.modelTier !== 'frontier') {
		await decideAnomaly(anomalyOptions(harness));
		phase = await casePhase(harness);
	}
	// Every Pi process runs on the one route the launcher chose, so the
	// investigator and the fact-checker ran on this model.
	const outcome = await validateAfterWorker(role, phase, routeFor(config).model, {
		caseDir: active.caseDir,
		writeRoot: active.caseDir,
		python: config.python,
		configPath: config.spotlightConfig,
		env: { ...process.env, ...researchEnv(config) },
	});
	if (outcome.ran.length === 0) return text;
	if (role === 'analyst' && !outcome.passed && (await proposalNotAtFault(harness))) {
		harness.batchBlocked = outcome.report.split('\n').slice(1).join(' ').replace(/\s+/g, ' ').trim().slice(0, 800);
		harness.log(`the knowledge batch cannot be built for a reason no agent can fix: ${harness.batchBlocked}`);
		return `${text}\n\n[Harness checks after the analyst finished: ${BATCH_BLOCKED} ${harness.batchBlocked}]`;
	}
	const verdict = outcome.passed ? 'all passed' : 'FAILED: send the worker the correction the phase skill describes';
	const unfinished = role === 'fact-checker' ? unfinishedNote(active.caseDir) : '';
	return `${text}\n\n[Harness checks after the ${role} finished: ${verdict}]\n${outcome.report}${unfinished}`;
}

function terminate(harness: Harness): void {
	for (const child of harness.children) child.kill('SIGTERM');
	setTimeout(() => {
		for (const child of harness.children) child.kill('SIGKILL');
	}, KILL_GRACE_MS).unref();
}

// Runs once the case may start (in the newsroom edition: signed in,
// registered, any pending submission resolved).
function start(harness: Harness): void {
	const discarded = discardIncompleteLanes(harness.config.caseDir);
	if (discarded.length > 0) harness.log(`discarded ${discarded.length} unfinished research lane(s); they will rerun`);
	const launch = processLaunch(harness, ORCHESTRATOR);
	mkdirSync(launch.cwd, { recursive: true, mode: 0o700 });
	const orchestrator = spawn(launch.command, launch.args, {
		cwd: launch.cwd,
		env: launch.env,
		stdio: ['pipe', 'inherit', 'pipe', 'ipc'],
	});
	track(harness, orchestrator);
	forwardStderr(harness, orchestrator);
	serveWorkers(harness, orchestrator);

	orchestrator.once('error', (error) => {
		if (orchestrator.pid !== undefined) return;
		harness.log(`cannot start: ${harness.redact(error.message)}`);
		process.exit(EXIT.failed);
	});
	// The orchestrator closing its input early (EPIPE) is not a launcher crash;
	// its exit handler below shuts the chat down.
	orchestrator.stdin!.on('error', (error) => harness.log(`the orchestrator stopped reading the chat: ${harness.redact(error.message)}`));

	let stdinWatchdog: NodeJS.Timeout | undefined;
	process.stdin.pipe(orchestrator.stdin!);
	process.stdin.once('end', () => {
		stdinWatchdog = setTimeout(() => {
			harness.log('chat closed but Spotlight did not stop in time; terminating');
			terminate(harness);
		}, STDIN_CLOSE_GRACE_MS);
	});
	for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP'] as const) {
		process.once(signal, () => terminate(harness));
	}
	process.once('exit', () => {
		for (const child of harness.children) child.kill('SIGKILL');
		harness.lock.release();
	});
	orchestrator.once('exit', (code, signal) => {
		clearTimeout(stdinWatchdog);
		process.stdin.unpipe(orchestrator.stdin!);
		process.stdin.destroy();
		const finish = () => process.exit(code ?? (signal === 'SIGTERM' ? 143 : EXIT.failed));
		if (harness.children.size === 0) return finish();
		terminate(harness);
		const poll = setInterval(() => {
			if (harness.children.size === 0) {
				clearInterval(poll);
				finish();
			}
		}, 50);
	});
}

function main(): void {
	const plain = stderrLogger('spotlight');
	let args: LaunchArgs;
	let config: HarnessConfig;
	let composition: Composition;
	try {
		args = parseLaunchArgs(process.argv.slice(2));
		config = readConfig(process.env, args.sessionId);
		composition = loadComposition();
		routeFor(config);
		checkRouteSecrets(config, composition, process.env);
		checkNewsroomInstall(config);
	} catch (error) {
		if (error instanceof ConfigError || error instanceof RouteRefused) {
			plain(`cannot start: ${error.message}`);
			process.exit(EXIT.config);
		}
		throw error;
	}
	const secrets = secretValues(config, composition, process.env);
	const redact = redactor(secrets);
	const log = stderrLogger('spotlight', redact);
	let lock: CaseLock;
	let active: ActiveCase;
	try {
		mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
		mkdirSync(config.casesRoot, { recursive: true, mode: 0o700 });
		active = bindActiveCase(config.casesRoot, config.caseDir);
		lock = acquireCaseLock(config.dataDir, config.caseId, piCliPath());
	} catch (error) {
		log(`cannot start: ${(error as Error).message}`);
		process.exit(error instanceof CaseHeldError ? EXIT.held : EXIT.config);
	}
	const harness: Harness = { config, args, active, composition, lock, log, redact, children: new Set(), editorial: editorialClient(config, process.env, secrets) };
	if (!harness.editorial) {
		// A case the newsroom holds (registered, or a submission recorded) is
		// never also run as a solo case: that would split its custody.
		if ([CASE_REFERENCE_FILE, 'data/newsroom-submission.json'].some((path) => existsSync(resolve(config.caseDir, path)))) {
			lock.release();
			log('cannot start: this case is registered with a newsroom, so it runs only in the newsroom edition. Start it from the newsroom extension.');
			process.exit(EXIT.config);
		}
		return start(harness);
	}
	// Case start, newsroom edition: the sign-in is checked on every start, and
	// register_case runs once; a resumed case reuses the stored reference. A
	// submission a crash left pending is resolved before anything else runs.
	const client = harness.editorial;
	const runPackage = packageRunner(harness);
	client
		.connect()
		.then(() => ensureCaseRegistered(client, config, runPackage))
		.then(async ({ reference, registered }) => {
			harness.caseRef = reference.case_ref;
			log(registered ? `registered the case with the newsroom (${reference.case_ref})` : `newsroom case ${reference.case_ref}`);
			if (await pendingSubmission(runPackage, config.caseDir)) {
				log('resuming the submission that was pending when Spotlight last stopped');
				// Nothing runs while a submission is unresolved.
				await submit(harness, client).catch((error: Error) => {
					if (stopsTheNewsroomCase(error)) throw error;
					throw new Error(`the submission that was pending could not be resumed, so the case did not start: ${error.message}`);
				});
			}
			start(harness);
		})
		.catch((error: Error) => {
			lock.release();
			if (stopsTheNewsroomCase(error)) {
				const { code, message } = newsroomStop(error);
				log(`cannot start: ${message}`);
				if (!(error instanceof EditorialCredentialError)) log(`the Editorial MCP: ${redact(error.message)}`);
				process.exit(code);
			}
			log(`cannot start: ${redact(error.message)}`);
			process.exit(EXIT.failed);
		});
}

main();
