// Shared plumbing for the typed tools: strict argument checks, the one way a
// tool starts a backing program (an argv array, never a shell), and the small
// environment each backing program receives.
import { execFile } from 'node:child_process';
import { relative, sep } from 'node:path';
import { Type } from 'typebox';
import type { TSchema } from 'typebox';
import Value from 'typebox/value';
import { CHECKOUT_ROOT } from '../roles.ts';

export type Env = Record<string, string | undefined>;

export type CaseScope = { reads?: string[]; writes?: string[]; denyReads?: string[]; denyWrites?: string[] };

// What every typed tool is bound to at launch. The model never names these.
export type ToolContext = {
	// The active case folder (canonical, from bindActiveCase).
	caseDir: string;
	// The folder this process may write in. The whole case for today's roles;
	// a narrower folder for a role that must stay inside one subfolder.
	writeRoot: string;
	// The role's case-file scope from harness/composition.json (reads, writes,
	// denyReads, denyWrites): the file tools reach only the named case-relative
	// files, never read the read-denied ones and never write the write-denied ones.
	scope?: CaseScope;
	// Pinned interpreter for every Python seam (SPOTLIGHT_PYTHON).
	python: string;
	// Installed Spotlight config (.spotlight-config.json) passed to scripts that read it.
	configPath: string;
	// The pi process environment: the only source of keys and backing settings.
	env: Env;
};

export class ToolArgumentError extends Error {}

// What a model is told when it repeats a call that already ran twice in a row
// with the same result (the extension's loop guard and the lane loop).
export const REPEAT_REASON = 'This is the same call as the last two. Act on what you already have or take the next phase step.';
// What a 12b worker's run ends with on its third refusal in a row, and what the
// orchestrator then gets as that worker's answer.
export const LOOP_STOP = 'repeated one call and was stopped; its last results stand.';

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

// The most text one tool result hands a model; the rest is marked truncated.
export const RETURNED_CHARS = 20_000;
export function capped(text: string, note = ''): string {
	return text.length > RETURNED_CHARS ? `${text.slice(0, RETURNED_CHARS)}\n[... truncated at ${RETURNED_CHARS} characters${note}]` : text;
}

export const BASE_ENV = ['PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SYSTEMROOT'];

// A backing program sees the system basics plus the named variables, nothing else.
// What the review page's browser opener also gets: the desktop session.
export const DESKTOP_ENV = ['DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'];

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
	options: { keys?: string[]; extra?: Record<string, string>; signal?: AbortSignal; timeoutMs?: number; input?: string } = {},
): Promise<RunResult> {
	return runFixed(context.python, args, {
		env: programEnv(context.env, options.keys, options.extra),
		cwd: CHECKOUT_ROOT,
		signal: options.signal,
		timeoutMs: options.timeoutMs,
		input: options.input,
	});
}

// Case-relative save paths for tools that store what they fetched: a file
// under research/ or evidence/, never the folder itself (no trailing slash),
// or a bare file name (see saveTarget). Written to hold as a full match too
// (see webURL in fetch.ts).
export const savePath = Type.String({ minLength: 1, maxLength: 512, pattern: '^(?:(?:research|evidence)/.*[^/\\\\]|[^/\\\\]+)$' });

// A bare save_as file name goes to the agent's own folder: research/ for a
// case role, the lane folder for a research lane.
export function saveTarget(context: Pick<ToolContext, 'caseDir' | 'writeRoot'>, path: string): string {
	if (/[\\/]/.test(path)) return path;
	const folder = relative(context.caseDir, context.writeRoot).split(sep).join('/');
	return `${folder || 'research'}/${path}`;
}
