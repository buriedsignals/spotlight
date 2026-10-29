// Starts the real launcher as a chat client would, and speaks RPC to it.
import { execFile, spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT } from '../roles.ts';

const LAUNCHER = resolve(fileURLToPath(import.meta.url), '../../launcher.ts');

export type Workspace = { root: string; casesRoot: string; dataDir: string };

export async function workspace(t: TestContext): Promise<Workspace> {
	const root = await mkdtemp(join(tmpdir(), 'spotlight-pi-'));
	t.after(() => rm(root, { recursive: true, force: true }));
	return { root, casesRoot: join(root, 'cases'), dataDir: join(root, 'harness-data') };
}

export function baseEnv(space: Workspace, modelBaseURL: string, caseId = 'case-a'): Record<string, string> {
	return {
		PATH: process.env.PATH ?? '',
		HOME: process.env.HOME ?? '',
		SPOTLIGHT_MODEL_BASE_URL: modelBaseURL,
		SPOTLIGHT_MODEL_ID: 'stub-model',
		SPOTLIGHT_CASES_ROOT: space.casesRoot,
		SPOTLIGHT_CASE_ID: caseId,
		SPOTLIGHT_HARNESS_DATA: space.dataDir,
		// The fact-check re-check would fetch the fixtures' source URLs (#274); tests reach no network.
		SPOTLIGHT_RECHECK: 'off',
		...(process.env.SPOTLIGHT_PYTHON ? { SPOTLIGHT_PYTHON: process.env.SPOTLIGHT_PYTHON } : {}),
	};
}

// The newsroom edition's launch contract: allowed routes, the Editorial MCP
// and its token, and an install config in newsroom mode.
export function newsroomEnv(
	space: Workspace,
	routes: object[],
	editorial: { url: string; token: string },
	extra: Record<string, string> = {},
): Record<string, string> {
	const config = join(space.root, 'newsroom-config.json');
	writeFileSync(config, JSON.stringify({ knowledge_destination: { mode: 'newsroom' } }));
	const env: Record<string, string> = {
		...baseEnv(space, ''),
		SPOTLIGHT_EDITION: 'newsroom',
		SPOTLIGHT_ROUTES: JSON.stringify(routes),
		SPOTLIGHT_CONFIG: config,
		SPOTLIGHT_EDITORIAL_MCP_URL: editorial.url,
		SPOTLIGHT_EDITORIAL_MCP_TOKEN: editorial.token,
		...extra,
	};
	if (!extra.SPOTLIGHT_MODEL_BASE_URL) delete env.SPOTLIGHT_MODEL_BASE_URL;
	return env;
}

export type Chat = {
	process: ChildProcessWithoutNullStreams;
	lines: string[];
	stderr(): string;
	send(record: Record<string, unknown>): void;
	waitFor(predicate: (record: Record<string, unknown>) => boolean, from?: number, timeoutMs?: number): Promise<Record<string, unknown>>;
	exited: Promise<{ code: number | null; signal: NodeJS.Signals | null }>;
};

export function launch(t: TestContext, env: Record<string, string>, args: string[] = []): Chat {
	const child = spawn(process.execPath, [LAUNCHER, ...args], { env, stdio: ['pipe', 'pipe', 'pipe'] });
	const lines: string[] = [];
	let partial = '';
	let stderr = '';
	child.stdout.setEncoding('utf8');
	child.stdout.on('data', (chunk: string) => {
		partial += chunk;
		let newline: number;
		while ((newline = partial.indexOf('\n')) !== -1) {
			lines.push(partial.slice(0, newline));
			partial = partial.slice(newline + 1);
		}
	});
	child.stderr.on('data', (chunk) => {
		stderr += chunk;
	});
	const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((done) =>
		child.once('exit', (code, signal) => done({ code, signal })),
	);
	t.after(async () => {
		if (child.exitCode === null && child.signalCode === null) {
			child.kill('SIGTERM');
			await exited;
		}
	});
	return {
		process: child,
		lines,
		stderr: () => stderr,
		send(record) {
			child.stdin.write(`${JSON.stringify(record)}\n`);
		},
		async waitFor(predicate, from = 0, timeoutMs = 30_000) {
			const deadline = Date.now() + timeoutMs;
			let seen = from;
			while (Date.now() < deadline) {
				for (; seen < lines.length; seen += 1) {
					const record = JSON.parse(lines[seen]) as Record<string, unknown>;
					if (predicate(record)) return record;
				}
				if (child.exitCode !== null) break;
				await new Promise((done) => setTimeout(done, 25));
			}
			throw new Error(`no matching RPC record; stderr:\n${stderr}`);
		},
		exited,
	};
}

export async function settledPrompt(chat: Chat, message: string): Promise<void> {
	const before = chat.lines.length;
	chat.send({ id: `p-${before}`, type: 'prompt', message });
	await chat.waitFor((record) => record.type === 'agent_settled', before);
}

export function waitUntil(check: () => boolean, timeoutMs = 10_000): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	return new Promise((done, fail) => {
		const poll = () => {
			if (check()) return done();
			if (Date.now() > deadline) return fail(new Error('condition not reached in time'));
			setTimeout(poll, 50);
		};
		poll();
	});
}

function python(args: string[], env: Record<string, string> = {}): Promise<string> {
	return new Promise((done, fail) =>
		execFile(process.env.SPOTLIGHT_PYTHON ?? 'python3', args, { env: { ...process.env, ...env } }, (error, stdout, stderr) =>
			error ? fail(new Error(stderr || error.message)) : done(stdout),
		),
	);
}

// A case at Ingest (tests/fixtures/review_case.py): activated and clean.
export async function caseAtIngest(space: Workspace, fixtureEnv: Record<string, string> = {}): Promise<string> {
	const caseDir = join(space.casesRoot, 'case-a');
	await mkdir(space.casesRoot, { recursive: true });
	await python([join(CHECKOUT_ROOT, 'tests', 'fixtures', 'review_case.py'), caseDir, 'ingest', '--activated', '--clean'], fixtureEnv);
	return caseDir;
}

// The member requests ingestion on the ingest page (the page's seam and its
// one-time nonce), in a newsroom-mode install.
export async function requestNewsroomIngest(caseDir: string, env: Record<string, string>): Promise<void> {
	const seam = (args: string[]) => python([join(CHECKOUT_ROOT, 'scripts', 'spotlight-orchestration.py'), ...args], { SPOTLIGHT_CONFIG: env.SPOTLIGHT_CONFIG });
	const view = JSON.parse(await seam(['review-view', 'ingest', caseDir])) as { digest: string };
	const nonce = (JSON.parse(await seam(['issue-review-nonce', 'ingest', caseDir])) as { nonce: string }).nonce;
	await seam(['decide-ingest', 'requested', '--nonce', nonce, '--digest', view.digest, '--decided-by', 'journalist:fixture', '--decided-at', '2026-10-23T12:00:00Z', caseDir]);
}
