#!/usr/bin/env node
// Spotlight harness launcher: one long-lived `pi --mode rpc` orchestrator per
// chat. Stdin and stdout pass through untouched (stdout carries only Pi's RPC
// records); the launcher itself writes only to stderr. Closing stdin shuts the
// chat down in order, SIGTERM is the fallback, and no pi child outlives it.
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { ConfigError, RouteRefused, childEnv, readConfig, routeFor, secretValues } from './config.ts';
import type { HarnessConfig } from './config.ts';
import { CaseHeldError, acquireCaseLock } from './lock.ts';
import type { CaseLock } from './lock.ts';
import { redactingLines, redactor, stderrLogger } from './log.ts';
import { piArguments, piCliPath } from './pi-process.ts';
import { discardIncompleteLanes } from './resume.ts';
import { ORCHESTRATOR, agentSpec, loadComposition, skillsFor, spawnableWorkers, systemPrompt } from './roles.ts';
import type { Composition } from './roles.ts';
import { WorkerQueue, runWorker } from './subagents.ts';
import type { CancelRequest, SpawnRequest, SpawnResult } from './subagents.ts';
import { bindActiveCase } from './tools/spotlight.ts';

const STDIN_CLOSE_GRACE_MS = 15_000;
const KILL_GRACE_MS = 5_000;

const EXIT = { config: 2, held: 3, failed: 1 } as const;

type Harness = {
	config: HarnessConfig;
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
	child.once('exit', () => {
		harness.children.delete(child);
		record();
	});
}

function forwardStderr(harness: Harness, child: ChildProcess): void {
	child.stderr?.pipe(redactingLines(harness.redact)).pipe(process.stderr, { end: false });
}

function processLaunch(harness: Harness, role: string) {
	const route = routeFor(harness.config, 'general');
	const spec = agentSpec(harness.composition, role);
	return {
		command: process.execPath,
		args: piArguments({
			systemPrompt: systemPrompt(harness.composition, role),
			skills: skillsFor(harness.composition, role),
			tools: spec.tools,
			model: route.model,
		}),
		env: childEnv(harness.config, harness.composition, role, 'general', process.env),
		cwd: resolve(harness.config.dataDir, 'work', role),
	};
}

function serveWorkers(harness: Harness, orchestrator: ChildProcess): void {
	const queue = new WorkerQueue();
	const running = new Map<string, AbortController>();
	const workers = spawnableWorkers(harness.composition);
	orchestrator.on('message', (message: unknown) => {
		const request = message as SpawnRequest | CancelRequest;
		if (request?.kind === 'spotlight-cancel') {
			running.get(request.id)?.abort();
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
		queue
			.run(() =>
				runWorker(
					{
						...processLaunch(harness, request.agent),
						onSpawn: (child) => track(harness, child),
						onStderr: (child) => forwardStderr(harness, child),
					},
					request.task,
					controller.signal,
				),
			)
			.then((text) => reply({ ok: true, text }))
			.catch((error: Error) => reply({ ok: false, error: harness.redact(error.message) }))
			.finally(() => running.delete(request.id));
	});
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
		routeFor(config, 'general');
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
	try {
		mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
		mkdirSync(config.casesRoot, { recursive: true, mode: 0o700 });
		bindActiveCase(config.casesRoot, config.caseDir);
		lock = acquireCaseLock(config.dataDir, config.caseId, piCliPath());
	} catch (error) {
		log(`cannot start: ${(error as Error).message}`);
		process.exit(error instanceof CaseHeldError ? EXIT.held : EXIT.config);
	}
	const discarded = discardIncompleteLanes(config.caseDir);
	if (discarded.length > 0) log(`discarded ${discarded.length} unfinished research lane(s); they will rerun`);
	start({ config, composition, lock, log, redact, children: new Set() });
}

main();
