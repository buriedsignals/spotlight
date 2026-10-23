// Shared plumbing for the typed tools: strict argument checks, the one way a
// tool starts a backing program (an argv array, never a shell), and the small
// environment each backing program receives.
import { execFile } from 'node:child_process';
import { Type } from 'typebox';
import type { TSchema } from 'typebox';
import Value from 'typebox/value';
import { CHECKOUT_ROOT } from '../roles.ts';

export type Env = Record<string, string | undefined>;

export type CaseScope = { reads?: string[]; writes?: string[]; denyReads?: string[] };

// What every typed tool is bound to at launch. The model never names these.
export type ToolContext = {
	// The active case folder (canonical, from bindActiveCase).
	caseDir: string;
	// The folder this process may write in. The whole case for today's roles;
	// a narrower folder for a role that must stay inside one subfolder.
	writeRoot: string;
	// The role's case-file scope from harness/composition.json (reads, writes,
	// denyReads): the file tools reach only the named case-relative files, and
	// never the denied ones.
	scope?: CaseScope;
	// Pinned interpreter for every Python seam (SPOTLIGHT_PYTHON).
	python: string;
	// Installed Spotlight config (.spotlight-config.json) passed to scripts that read it.
	configPath: string;
	// The pi process environment: the only source of keys and backing settings.
	env: Env;
};

export class ToolArgumentError extends Error {}

// Pi validates arguments before it calls a tool; this repeats the check at the
// tool boundary so a tool never acts on input outside its schema, whoever calls it.
export function checkedParams<T>(tool: string, schema: TSchema, params: unknown): T {
	if (Value.Check(schema, params)) return params as T;
	const reasons = [...Value.Errors(schema, params)]
		.slice(0, 5)
		.map((error) => `${error.instancePath || '/'} ${error.message}`);
	throw new ToolArgumentError(`${tool}: arguments outside the schema (${reasons.join('; ')})`);
}

export function textResult(text: string) {
	return { content: [{ type: 'text' as const, text }], details: undefined };
}

export function jsonResult(value: unknown) {
	return textResult(JSON.stringify(value));
}

const BASE_ENV = ['PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT'];

// A backing program sees the system basics plus the named variables, nothing else.
export function programEnv(env: Env, names: string[] = [], extra: Record<string, string> = {}): Record<string, string> {
	const out: Record<string, string> = {};
	for (const name of [...BASE_ENV, ...names]) {
		const value = env[name];
		if (value) out[name] = value;
	}
	return { ...out, ...extra };
}

export type RunResult = { code: number; stdout: string; stderr: string };
export type RunOptions = {
	env: Record<string, string>;
	cwd: string;
	timeoutMs?: number;
	signal?: AbortSignal;
	// Written to the program's stdin, which is then closed.
	input?: string;
};

const MAX_OUTPUT_BYTES = 32 * 1024 * 1024;

// Starts `command` with `args` as a plain argv array. `shell` is pinned off, so
// metacharacters in arguments reach the program as literal text. A non-zero
// exit resolves with its code; a program that cannot start, times out or is
// aborted rejects.
export function runFixed(command: string, args: readonly string[], options: RunOptions): Promise<RunResult> {
	const { promise, resolve, reject } = Promise.withResolvers<RunResult>();
	const child = execFile(
		command,
		[...args],
		{
			shell: false,
			windowsHide: true,
			encoding: 'utf8',
			env: options.env,
			cwd: options.cwd,
			timeout: options.timeoutMs ?? 120_000,
			signal: options.signal,
			maxBuffer: MAX_OUTPUT_BYTES,
		},
		(error, stdout, stderr) => {
			if (!error) return resolve({ code: 0, stdout, stderr });
			if (typeof error.code === 'number') return resolve({ code: error.code, stdout, stderr });
			const reason = error.killed ? 'timed out or was stopped' : error.message;
			reject(new Error(`${command} could not run: ${reason}`));
		},
	);
	if (options.input !== undefined) {
		child.stdin?.on('error', () => {}); // a program that exits early fails through its exit code
		child.stdin?.end(options.input);
	}
	return promise;
}

// The usual outcome of a backing program for the model: stdout on success,
// the program's own message otherwise.
export function programOutput(name: string, result: RunResult): string {
	if (result.code === 0) return result.stdout;
	const detail = (result.stderr.trim() || result.stdout.trim()).slice(-4000);
	throw new Error(`${name} failed (exit ${result.code}): ${detail}`);
}

// Runs one of the checkout's Python seams with the harness's pinned
// interpreter, from the checkout root so `-m integrations.*` resolves.
export function runPython(
	context: ToolContext,
	args: readonly string[],
	options: { keys?: string[]; extra?: Record<string, string>; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<RunResult> {
	return runFixed(context.python, args, {
		env: programEnv(context.env, options.keys, options.extra),
		cwd: CHECKOUT_ROOT,
		signal: options.signal,
		timeoutMs: options.timeoutMs,
	});
}

// Case-relative save paths for tools that store what they fetched.
export const savePath = Type.String({ minLength: 1, maxLength: 512, pattern: '^(research|evidence)/' });
