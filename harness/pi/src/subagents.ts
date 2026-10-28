// Workers (investigator, fact-checker, later researchers and the analyst) run
// as their own `pi --mode rpc` processes, one per delegated task, adapted from
// Pi's subagent example. The orchestrator's `spawn_agent` tool does not start
// them itself: it asks the launcher over the IPC channel, so the launcher alone
// supervises every process and hands each one only its own role's keys.
import { spawn } from 'node:child_process';
import type { ChildProcess, ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { onJsonlRecords } from './pi-process.ts';
import { LOOP_STOP, REFUSAL_STOP } from './tools/run.ts';

export type SpawnRequest = { kind: 'spotlight-spawn'; id: string; agent: string; task: string };
export type CancelRequest = { kind: 'spotlight-cancel'; id: string };
export type SpawnResult = { kind: 'spotlight-result'; id: string; ok: boolean; text?: string; error?: string; round?: unknown };
// The orchestrator's resolve asks the launcher to run the approved research
// topics; the launcher checks the phase and the plan itself.
export type ResearchRequest = { kind: 'spotlight-research'; id: string; retry: boolean };
// Newsroom edition: a tool asks the launcher, the only holder of the newsroom
// token, to call the Editorial MCP (tools/editorial.ts). The launcher checks
// the operation against the asking process's role.
export type EditorialRequest = { kind: 'spotlight-editorial'; id: string; op: string; args: Record<string, unknown> };

export const MAX_CONCURRENT_WORKERS = 4;

// ── Orchestrator side ────────────────────────────────────────────────────────

const pending = new Map<string, (result: SpawnResult) => void>();
let listening = false;
function listen(): void {
	if (listening) return;
	listening = true;
	process.on('message', (message: unknown) => {
		const result = message as SpawnResult;
		if (result?.kind !== 'spotlight-result') return;
		pending.get(result.id)?.(result);
		pending.delete(result.id);
	});
}

export async function requestResearchRound(retry: boolean, signal?: AbortSignal): Promise<unknown> {
	if (typeof process.send !== 'function') throw new Error('Research needs the Spotlight launcher; this process was started without it');
	listen();
	const id = randomUUID();
	const { promise, resolve } = Promise.withResolvers<SpawnResult>();
	pending.set(id, resolve);
	const onAbort = () => process.send?.({ kind: 'spotlight-cancel', id } satisfies CancelRequest);
	signal?.addEventListener('abort', onAbort, { once: true });
	process.send({ kind: 'spotlight-research', id, retry } satisfies ResearchRequest);
	try {
		const result = await promise;
		if (!result.ok) throw new Error(result.error ?? 'the research round failed');
		return result.round;
	} finally {
		signal?.removeEventListener('abort', onAbort);
	}
}

export async function requestEditorial(op: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
	if (typeof process.send !== 'function') throw new Error('the newsroom tools need the Spotlight launcher; this process was started without it');
	listen();
	const id = randomUUID();
	const { promise, resolve } = Promise.withResolvers<SpawnResult>();
	pending.set(id, resolve);
	const onAbort = () => process.send?.({ kind: 'spotlight-cancel', id } satisfies CancelRequest);
	signal?.addEventListener('abort', onAbort, { once: true });
	process.send({ kind: 'spotlight-editorial', id, op, args } satisfies EditorialRequest);
	try {
		const result = await promise;
		if (!result.ok) throw new Error(result.error ?? `${op} failed`);
		return result.text ?? '';
	} finally {
		signal?.removeEventListener('abort', onAbort);
	}
}

// phase-investigation step 4.5 re-spawns the fact-checker once per cycle for
// evidence correction, then records the attempt. r1 12B looped those re-spawns
// instead. A plain fact-checker spawn (step 3) starts the next cycle.
const EVIDENCE_CORRECTION = /MODE:\s*EVIDENCE CORRECTION/;
export const CORRECTION_SPENT = 'The fact-checker already had its one evidence correction this cycle. Record the failure with spotlight_transition recordAttempt (kind fact-check-evidence-repair, gap: the validator reason), then call spotlight_resolve.';

// r6 nordhaven on dd14e2e5: the orchestrator spawned about 15 fact-checkers in
// 25 minutes, each rewriting fact-check.json against the validator, with no
// recordAttempt in between. A cycle (one plain check, one correction) needs
// two; a successful spotlight_transition starts the next cycle.
export const FACT_CHECKER_CAP = 3;
export const FACT_CHECKERS_SPENT = `The fact-checker has run ${FACT_CHECKER_CAP} times this cycle; do not spawn it again. If the readiness criteria are met, open the Investigation gate with open_review and the current verdicts. Otherwise record the cycle with spotlight_transition recordAttempt (kind investigation-cycle, gap: what is still missing), then call spotlight_resolve.`;

// Fact-checker spawns since the last successful spotlight_transition, shared by
// spawn_agent and the transition tool of one orchestrator process.
export type FactCheckerCycle = { spawns: number };

// What the launcher answers a worker spawn while the Investigation gate is ready (launcher.ts gateReady).
export const GATE_READY_REFUSAL = 'The investigation is complete: call open_review to present the Investigation gate to the member.';

export function spawnAgentTool(workers: string[], cycle: FactCheckerCycle = { spawns: 0 }): ToolDefinition {
	let corrections = 0;
	return {
		name: 'spawn_agent',
		label: 'Spawn agent',
		description: `Delegate one task to a Spotlight worker in its own context and wait for its final answer. Workers: ${workers.join(', ')}.`,
		parameters: Type.Object({
			agent: Type.Union(workers.map((name) => Type.Literal(name))),
			task: Type.String({ minLength: 1 }),
		}),
		async execute(_id, params, signal) {
			const { agent, task } = params as { agent: string; task: string };
			if (!workers.includes(agent)) throw new Error(`spawn_agent: unknown worker "${agent}"`);
			if (agent === 'fact-checker') {
				if (cycle.spawns >= FACT_CHECKER_CAP) throw new Error(FACT_CHECKERS_SPENT);
				if (!EVIDENCE_CORRECTION.test(task)) corrections = 0;
				else if (corrections > 0) throw new Error(CORRECTION_SPENT);
				else corrections += 1;
				cycle.spawns += 1;
			}
			if (typeof process.send !== 'function') {
				throw new Error('spawn_agent needs the Spotlight launcher; this process was started without it');
			}
			listen();
			const id = randomUUID();
			const { promise, resolve } = Promise.withResolvers<SpawnResult>();
			pending.set(id, resolve);
			const onAbort = () => process.send?.({ kind: 'spotlight-cancel', id } satisfies CancelRequest);
			signal?.addEventListener('abort', onAbort, { once: true });
			process.send({ kind: 'spotlight-spawn', id, agent, task } satisfies SpawnRequest);
			try {
				const result = await promise;
				if (!result.ok) throw new Error(result.error ?? `${agent} failed`);
				return { content: [{ type: 'text', text: result.text || '(no output)' }], details: undefined };
			} finally {
				signal?.removeEventListener('abort', onAbort);
			}
		},
	};
}

// ── Launcher side ────────────────────────────────────────────────────────────

export type WorkerLaunch = {
	command: string;
	args: string[];
	env: Record<string, string>;
	cwd: string;
	onSpawn(child: ChildProcess): void;
	onStderr(child: ChildProcess): void;
};

// Runs one task on a fresh worker: send the prompt, wait for `agent_settled`,
// read the last assistant text, then close stdin so Pi shuts down in order.
const CONTROL_TOKENS = /<\|[^<>]*>|<[^<>]*\|>/g;

export async function runWorker(launch: WorkerLaunch, task: string, signal: AbortSignal): Promise<string> {
	// A task cancelled while it waited in the queue never starts a worker.
	signal.throwIfAborted();
	mkdirSync(launch.cwd, { recursive: true, mode: 0o700 });
	const child = spawn(launch.command, launch.args, {
		cwd: launch.cwd,
		env: launch.env,
		// The IPC channel carries a worker's newsroom tool calls to the launcher.
		stdio: ['pipe', 'pipe', 'pipe', 'ipc'],
	}) as ChildProcessWithoutNullStreams;
	launch.onSpawn(child);
	launch.onStderr(child);
	const answer = Promise.withResolvers<string>();
	// A worker that cannot start emits 'error' and may never emit 'exit'.
	const exited = new Promise<number | null>((resolve) => {
		child.once('exit', (code) => resolve(code));
		child.once('error', (error) => {
			answer.reject(new Error(`worker could not start: ${error.message}`));
			if (child.pid === undefined) resolve(null);
		});
	});
	// A worker that exits early closes its stdin: a pending write fails with
	// EPIPE. That is this task's failure; the exit handler reports it.
	child.stdin.on('error', (error) => answer.reject(new Error(`worker stopped reading its input: ${error.message}`)));
	const send = (command: Record<string, unknown>) => {
		if (child.stdin.writable) child.stdin.write(`${JSON.stringify(command)}\n`);
	};
	let started = false;
	// A 12b worker stopped for repeating one call, or for a streak of refusals,
	// ends on that line as its last tool result, with no answer of its own; the
	// line is its answer.
	let stopped = '';
	const transcript = openTranscript();
	onJsonlRecords(child.stdout, (record) => {
		transcript?.(record);
		if (record.type === 'tool_execution_end') {
			const text = (record.result as { content?: Array<{ text?: string }> } | undefined)?.content?.[0]?.text ?? '';
			stopped = text.endsWith(LOOP_STOP) || text.endsWith(REFUSAL_STOP) ? text : '';
		}
		if (record.type === 'response' && record.id === 'prompt') {
			if (!record.success) answer.reject(new Error(`worker refused the task: ${String(record.error)}`));
			started = (record.data as { disposition?: string } | undefined)?.disposition !== 'handled';
			if (!started) send({ id: 'last', type: 'get_last_assistant_text' });
		} else if (record.type === 'agent_settled' && started) {
			started = false;
			send({ id: 'last', type: 'get_last_assistant_text' });
		} else if (record.type === 'response' && record.id === 'last') {
			const text = String((record.data as { text?: string | null } | undefined)?.text ?? '');
			// A worker that ended with nothing, or only a chat-template control
			// token such as <|tool_response>, failed; it did not answer.
			if (stopped) answer.resolve(stopped);
			else if (text.replace(CONTROL_TOKENS, '').trim() === '') answer.reject(new Error('worker ended without an answer'));
			else answer.resolve(text);
		}
	});
	exited.then((code) => answer.reject(new Error(`worker exited before answering (code ${code})`)));
	const kill = () => child.kill('SIGTERM');
	signal.addEventListener('abort', kill, { once: true });
	try {
		send({ id: 'prompt', type: 'prompt', message: task });
		return await answer.promise;
	} finally {
		signal.removeEventListener('abort', kill);
		// Closing stdin asks Pi to dispose the runtime and exit.
		child.stdin.end();
		const timer = setTimeout(() => child.kill('SIGKILL'), 10_000);
		await exited;
		clearTimeout(timer);
	}
}

// Opt-in debugging: the worker's RPC records (which carry case text) go to a
// folder the operator names, never to stderr or the chat. A transcript that
// cannot be written stops being written; it never fails the task.
function openTranscript(): ((record: Record<string, unknown>) => void) | undefined {
	const dir = process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR;
	if (!dir) return undefined;
	const file = join(dir, `worker-${Date.now()}-${randomUUID()}.jsonl`);
	let failed = false;
	const attempt = (write: () => void) => {
		if (failed) return;
		try {
			write();
		} catch (error) {
			failed = true;
			process.stderr.write(`spotlight: worker transcript disabled for this task (${(error as NodeJS.ErrnoException).code ?? 'write failed'})\n`);
		}
	};
	attempt(() => mkdirSync(dir, { recursive: true, mode: 0o700 }));
	return (record) => attempt(() => appendFileSync(file, `${JSON.stringify(record)}\n`, { mode: 0o600 }));
}

export class WorkerQueue {
	#active = 0;
	#waiting: Array<() => void> = [];

	async run<T>(job: () => Promise<T>): Promise<T> {
		if (this.#active >= MAX_CONCURRENT_WORKERS) {
			await new Promise<void>((resolve) => this.#waiting.push(resolve));
		}
		this.#active += 1;
		try {
			return await job();
		} finally {
			this.#active -= 1;
			this.#waiting.shift()?.();
		}
	}
}
