#!/usr/bin/env node
// Spotlight harness launcher: one long-lived `pi --mode rpc` orchestrator per
// chat. Stdin and stdout pass through untouched (stdout carries only Pi's RPC
// records); the launcher itself writes only to stderr. Closing stdin shuts the
// chat down in order, SIGTERM is the fallback, and no pi child outlives it.
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ConfigError, RouteRefused, childEnv, piSettings, readConfig, routeFor, secretValues } from './config.ts';
import type { HarnessConfig } from './config.ts';
import { CaseHeldError, acquireCaseLock } from './lock.ts';
import type { CaseLock } from './lock.ts';
import { redactingLines, redactor, stderrLogger } from './log.ts';
import { piArguments, piCliPath, writePiSettings } from './pi-process.ts';
import { runResearchRound } from './lanes.ts';
import type { ResearchPlan, RoundSummary } from './lanes.ts';
import { discardIncompleteLanes } from './resume.ts';
import { ORCHESTRATOR, agentSpec, allowedTools, isolationRefusal, loadComposition, skillsFor, spawnRefusal, spawnableWorkers, systemPrompt } from './roles.ts';
import type { Composition } from './roles.ts';
import { WorkerQueue, runWorker } from './subagents.ts';
import { withCasePassages } from './case-search.ts';
import type { CancelRequest, ResearchRequest, SpawnRequest, SpawnResult } from './subagents.ts';
import { bindActiveCase, pythonAdapter, runsResearchRound } from './tools/spotlight.ts';
import type { ActiveCase } from './tools/spotlight.ts';
import { validateAfterWorker } from './validators.ts';

const STDIN_CLOSE_GRACE_MS = 15_000;
const KILL_GRACE_MS = 5_000;

const EXIT = { config: 2, held: 3, failed: 1 } as const;

type Harness = {
	config: HarnessConfig;
	active: ActiveCase;
	composition: Composition;
	lock: CaseLock;
	log: (message: string) => void;
	redact: (text: string) => string;
	children: Set<ChildProcess>;
};

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
function processLaunch(harness: Harness, role: string) {
	const route = routeFor(harness.config);
	const spec = agentSpec(harness.composition, role);
	const env = childEnv(harness.config, harness.composition, role, process.env);
	writePiSettings(env.PI_CODING_AGENT_DIR, piSettings(harness.config));
	return {
		command: process.execPath,
		args: piArguments({
			systemPrompt: systemPrompt(harness.composition, role, harness.config.modelTier),
			skills: skillsFor(harness.composition, role),
			tools: allowedTools(spec, harness.config.sensitive),
			model: route.model,
		}),
		env,
		cwd: resolve(harness.config.dataDir, 'work', role),
	};
}

function serveWorkers(harness: Harness, orchestrator: ChildProcess): void {
	const queue = new WorkerQueue();
	const running = new Map<string, AbortController>();
	const workers = spawnableWorkers(harness.composition);
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
				.catch((error: Error) => reply({ ok: false, error: harness.redact(error.message) }))
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
		refusalFor(harness, request.agent, request.task)
			.then((refusal) => {
				if (refusal) throw new Error(`spawn_agent: ${refusal}`);
				return withCasePassages(request.agent, request.task, {
					caseDir: harness.active.caseDir,
					python: harness.config.python,
					env: process.env,
					log: harness.log,
				});
			})
			.then((task) =>
				queue.run(() =>
					runWorker(
						{
							...processLaunch(harness, request.agent),
							onSpawn: (child) => track(harness, child),
							onStderr: (child) => forwardStderr(harness, child),
						},
						task,
						controller.signal,
					),
				),
			)
			.then((text) => withValidation(harness, request.agent, text))
			.then((text) => reply({ ok: true, text }))
			.catch((error: Error) => reply({ ok: false, error: harness.redact(error.message) }))
			.finally(() => running.delete(request.id));
	});
}

async function caseStatus(harness: Harness): Promise<Record<string, unknown>> {
	const { config, active } = harness;
	try {
		const status = await pythonAdapter(config.python)(['status', '--json', '--authorized-cases-root', active.casesRoot, active.caseDir]);
		return JSON.parse(status) as Record<string, unknown>;
	} catch (error) {
		harness.log(`could not resolve the case phase: ${harness.redact((error as Error).message)}`);
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
		});
		harness.log(`research round ${summary.round}: ${summary.ran.length} topic(s) ran, ${summary.left_out.length} left out${summary.stopped ? `, stopped (${summary.stopped.code})` : ''}`);
		return summary;
	})().finally(() => {
		roundInFlight = undefined;
	});
	return roundInFlight;
}

// A worker with spawn conditions in harness/composition.json (the analyst:
// Ingest, solo edition) starts only when the case meets them.
async function refusalFor(harness: Harness, role: string, task: string): Promise<string | undefined> {
	const isolation = isolationRefusal(role, task);
	if (isolation) return isolation;
	const spec = agentSpec(harness.composition, role);
	if (!spec.phases && !spec.editions) return undefined;
	return spawnRefusal(spec, role, spec.phases ? await casePhase(harness) : '', harness.config.edition);
}

// Fixed point: when a worker finishes, the harness runs the validators for
// what that worker writes and hands their outcome to the orchestrator with the
// worker's answer. The resolver's phase tells planning from investigation.
async function withValidation(harness: Harness, role: string, text: string): Promise<string> {
	const { config, active } = harness;
	const phase = await casePhase(harness);
	// Every Pi process runs on the one route the launcher chose, so the
	// investigator and the fact-checker ran on this model.
	const outcome = await validateAfterWorker(role, phase, routeFor(config).model, {
		caseDir: active.caseDir,
		writeRoot: active.caseDir,
		python: config.python,
		configPath: config.spotlightConfig,
		env: process.env,
	});
	if (outcome.ran.length === 0) return text;
	const verdict = outcome.passed ? 'all passed' : 'FAILED: send the worker the correction the phase skill describes';
	return `${text}\n\n[Harness checks after the ${role} finished: ${verdict}]\n${outcome.report}`;
}

function terminate(harness: Harness): void {
	for (const child of harness.children) child.kill('SIGTERM');
	setTimeout(() => {
		for (const child of harness.children) child.kill('SIGKILL');
	}, KILL_GRACE_MS).unref();
}

function start(harness: Harness): void {
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
	let config: HarnessConfig;
	let composition: Composition;
	try {
		config = readConfig(process.env);
		composition = loadComposition();
		routeFor(config);
	} catch (error) {
		if (error instanceof ConfigError || error instanceof RouteRefused) {
			plain(`cannot start: ${error.message}`);
			process.exit(EXIT.config);
		}
		throw error;
	}
	const redact = redactor(secretValues(config, composition, process.env));
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
	const discarded = discardIncompleteLanes(config.caseDir);
	if (discarded.length > 0) log(`discarded ${discarded.length} unfinished research lane(s); they will rerun`);
	start({ config, active, composition, lock, log, redact, children: new Set() });
}

main();
