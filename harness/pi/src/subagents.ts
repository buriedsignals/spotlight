// Workers (investigator, fact-checker, later researchers and the analyst) run
// as their own `pi --mode rpc` processes, one per delegated task, adapted from
// Pi's subagent example. The orchestrator's `spawn_agent` tool does not start
// them itself: it asks the launcher over the IPC channel, so the launcher alone
// supervises every process and hands each one only its own role's keys.
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { onJsonlRecords } from './pi-process.ts';

export type SpawnRequest = { kind: 'spotlight-spawn'; id: string; agent: string; task: string };
export type CancelRequest = { kind: 'spotlight-cancel'; id: string };
export type SpawnResult = { kind: 'spotlight-result'; id: string; ok: boolean; text?: string; error?: string };

export const MAX_CONCURRENT_WORKERS = 4;

// ── Orchestrator side ────────────────────────────────────────────────────────

export function spawnAgentTool(workers: string[]): ToolDefinition {
	const pending = new Map<string, (result: SpawnResult) => void>();
	let listening = false;
	const listen = () => {
		if (listening) return;
		listening = true;
		process.on('message', (message: unknown) => {
			const result = message as SpawnResult;
			if (result?.kind !== 'spotlight-result') return;
			pending.get(result.id)?.(result);
			pending.delete(result.id);
		});
	};
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
export async function runWorker(launch: WorkerLaunch, task: string, signal: AbortSignal): Promise<string> {
	mkdirSync(launch.cwd, { recursive: true, mode: 0o700 });
	const child = spawn(launch.command, launch.args, {
		cwd: launch.cwd,
		env: launch.env,
		stdio: ['pipe', 'pipe', 'pipe'],
	});
	launch.onSpawn(child);
	launch.onStderr(child);
	const exited = new Promise<number | null>((resolve) => child.once('exit', (code) => resolve(code)));
	const answer = Promise.withResolvers<string>();
	const send = (command: Record<string, unknown>) => {
		if (child.stdin.writable) child.stdin.write(`${JSON.stringify(command)}\n`);
	};
	let started = false;
	// Opt-in debugging: the worker's RPC records (which carry case text) go to a
	// folder the operator names, never to stderr or the chat.
	const transcriptDir = process.env.SPOTLIGHT_WORKER_TRANSCRIPT_DIR;
	const transcript = transcriptDir ? join(transcriptDir, `worker-${Date.now()}.jsonl`) : '';
	onJsonlRecords(child.stdout, (record) => {
		if (transcript) appendFileSync(transcript, `${JSON.stringify(record)}\n`, { mode: 0o600 });
		if (record.type === 'response' && record.id === 'prompt') {
			if (!record.success) answer.reject(new Error(`worker refused the task: ${String(record.error)}`));
			started = (record.data as { disposition?: string } | undefined)?.disposition !== 'handled';
			if (!started) send({ id: 'last', type: 'get_last_assistant_text' });
		} else if (record.type === 'agent_settled' && started) {
			started = false;
			send({ id: 'last', type: 'get_last_assistant_text' });
		} else if (record.type === 'response' && record.id === 'last') {
			answer.resolve(String((record.data as { text?: string | null } | undefined)?.text ?? ''));
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
