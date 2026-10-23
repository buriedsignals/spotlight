// The newsroom edition's Editorial MCP client. Only the launcher holds the
// newsroom sign-in token and calls the Editorial MCP; pi processes reach it
// through the launcher (IPC), so no model process ever sees the token.
//
// Transport: MCP Streamable HTTP (one POST per JSON-RPC message, a JSON or
// SSE answer, an optional MCP-Session-Id). Every request carrying the token
// goes only to an origin in the launch contract's newsroom allowlist; a
// redirect is never followed. A rejected or expired token (HTTP 401 or 403)
// is a plain stop with no fallback. The contract the newsroom's server
// implements is docs/newsroom-editorial-mcp.md.
import { createHash, randomUUID } from 'node:crypto';
import { closeSync, constants, lstatSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import type { HarnessConfig } from './config.ts';

export const PROTOCOL_VERSION = '2025-11-25';
export const CASE_REFERENCE_FILE = 'data/editorial-case.json';
export const CHECKS_FILE = 'data/newsroom-checks.json';
export const SUBMISSION_FILE = 'data/newsroom-submission.json';
export const RECEIPTS_DIR = 'receipts';

// The Editorial MCP tools the harness calls. Approval is not among them: no
// agent tool approves anything.
export const KNOWLEDGE_TOOLS = ['search_knowledge', 'get_record', 'traverse', 'coverage'] as const;
export type KnowledgeTool = (typeof KNOWLEDGE_TOOLS)[number];

export const SIGN_IN_AGAIN =
	'the newsroom sign-in is no longer valid (revoked or expired). Sign in again through the newsroom extension, then resume the case. Spotlight stopped; nothing was sent elsewhere.';

// HTTP 401 or 403 from the Editorial MCP: the token was revoked or expired.
export class EditorialCredentialError extends Error {
	constructor() {
		super(SIGN_IN_AGAIN);
	}
}
// A request the harness would not send: off the allowlist, or a redirect.
export class EditorialRefused extends Error {}
// Anything else: the server answered with an error, or something unreadable.
export class EditorialError extends Error {}

export function isAllowedOrigin(origins: readonly string[], url: string): boolean {
	let origin: string;
	try {
		origin = new URL(url).origin;
	} catch {
		return false;
	}
	return origins.includes(origin);
}

// A request that would carry a newsroom token goes only to the newsroom's own
// endpoints (its model routes and its Editorial MCP).
export function assertNewsroomDestination(origins: readonly string[], url: string): void {
	if (!isAllowedOrigin(origins, url)) {
		let host = 'an invalid URL';
		try {
			host = new URL(url).host;
		} catch {
			// keep the placeholder
		}
		throw new EditorialRefused(`refused: ${host} is not one of the newsroom's allowed endpoints, and the request would carry the newsroom token`);
	}
}

type Fetch = typeof fetch;

export type EditorialClientOptions = {
	url: string;
	token: string;
	origins: readonly string[];
	timeoutMs?: number;
	fetchImpl?: Fetch;
};

type JsonRpcResponse = { jsonrpc: '2.0'; id?: string | number | null; result?: unknown; error?: { code: number; message: string } };

function parseEventStream(text: string, id: string): JsonRpcResponse | undefined {
	for (const block of text.split(/\r?\n\r?\n/)) {
		const data = block
			.split(/\r?\n/)
			.filter((line) => line.startsWith('data:'))
			.map((line) => line.slice(5).replace(/^ /, ''))
			.join('\n');
		if (!data) continue;
		let message: JsonRpcResponse;
		try {
			message = JSON.parse(data) as JsonRpcResponse;
		} catch {
			continue;
		}
		if (message && message.id === id) return message;
	}
	return undefined;
}

export class EditorialClient {
	readonly #options: Required<Omit<EditorialClientOptions, 'fetchImpl'>> & { fetchImpl: Fetch };
	#session: string | undefined;
	#ready: Promise<void> | undefined;

	constructor(options: EditorialClientOptions) {
		this.#options = { ...options, timeoutMs: options.timeoutMs ?? 120_000, fetchImpl: options.fetchImpl ?? fetch };
		assertNewsroomDestination(options.origins, options.url);
	}

	// The token never appears in what this client reports, even when a server
	// echoes it back.
	#clean(text: string): string {
		const token = this.#options.token;
		return token && token.length >= 4 ? text.split(token).join('[redacted]') : text;
	}

	async #post(message: Record<string, unknown>, signal?: AbortSignal): Promise<{ status: number; body: string; type: string; session?: string }> {
		const { url, token, origins, timeoutMs, fetchImpl } = this.#options;
		assertNewsroomDestination(origins, url);
		const headers: Record<string, string> = {
			authorization: `Bearer ${token}`,
			accept: 'application/json, text/event-stream',
			'content-type': 'application/json',
			'mcp-protocol-version': PROTOCOL_VERSION,
		};
		if (this.#session) headers['mcp-session-id'] = this.#session;
		const timeout = AbortSignal.timeout(timeoutMs);
		let response: Response;
		try {
			response = await fetchImpl(url, {
				method: 'POST',
				headers,
				body: JSON.stringify(message),
				redirect: 'manual',
				signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
			});
		} catch (error) {
			throw new EditorialError(`the Editorial MCP could not be reached: ${this.#clean((error as Error).message)}`);
		}
		if (response.status >= 300 && response.status < 400) {
			throw new EditorialRefused('refused: the Editorial MCP answered with a redirect; Spotlight does not follow it with the newsroom token');
		}
		if (response.status === 401 || response.status === 403) throw new EditorialCredentialError();
		return {
			status: response.status,
			body: await response.text(),
			type: response.headers.get('content-type') ?? '',
			session: response.headers.get('mcp-session-id') ?? undefined,
		};
	}

	async #request(method: string, params: Record<string, unknown>, signal?: AbortSignal, retried = false): Promise<unknown> {
		const id = randomUUID();
		const answer = await this.#post({ jsonrpc: '2.0', id, method, params }, signal);
		// The server ended the session: start a new one, once.
		if (answer.status === 404 && this.#session && !retried && method !== 'initialize') {
			this.#session = undefined;
			this.#ready = undefined;
			await this.#initialize(signal);
			return this.#request(method, params, signal, true);
		}
		if (answer.status < 200 || answer.status >= 300) {
			throw new EditorialError(`the Editorial MCP answered HTTP ${answer.status}: ${this.#clean(answer.body.slice(0, 500))}`);
		}
		if (method === 'initialize' && answer.session) this.#session = answer.session;
		let message: JsonRpcResponse | undefined;
		if (answer.type.includes('text/event-stream')) {
			message = parseEventStream(answer.body, id);
		} else {
			try {
				message = JSON.parse(answer.body) as JsonRpcResponse;
			} catch {
				message = undefined;
			}
		}
		if (!message || (message.result === undefined && message.error === undefined)) {
			throw new EditorialError(`the Editorial MCP gave no readable answer to ${method}`);
		}
		if (message.error) throw new EditorialError(`the Editorial MCP refused ${method}: ${this.#clean(String(message.error.message ?? message.error.code))}`);
		return message.result;
	}

	#initialize(signal?: AbortSignal): Promise<void> {
		this.#ready ??= (async () => {
			await this.#request('initialize', {
				protocolVersion: PROTOCOL_VERSION,
				capabilities: {},
				clientInfo: { name: 'spotlight-harness', version: '1' },
			}, signal);
			const sent = await this.#post({ jsonrpc: '2.0', method: 'notifications/initialized' }, signal);
			if (sent.status !== 202 && (sent.status < 200 || sent.status >= 300)) {
				throw new EditorialError(`the Editorial MCP refused the initialized notification (HTTP ${sent.status})`);
			}
		})().catch((error) => {
			this.#ready = undefined;
			throw error;
		});
		return this.#ready;
	}

	// Opens the MCP session. The launcher does this at every start, so a
	// revoked or expired token stops a resumed case before any work.
	connect(signal?: AbortSignal): Promise<void> {
		return this.#initialize(signal);
	}

	// One tools/call. The answer is the tool's structuredContent (or its text
	// content parsed as JSON); a tool error becomes an EditorialError.
	async call(tool: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> {
		await this.#initialize(signal);
		const result = (await this.#request('tools/call', { name: tool, arguments: args }, signal)) as {
			content?: Array<{ type: string; text?: string }>;
			structuredContent?: unknown;
			isError?: boolean;
		};
		const text = (result?.content ?? []).filter((part) => part.type === 'text').map((part) => part.text ?? '').join('\n');
		if (result?.isError) throw new EditorialError(`${tool}: ${this.#clean(text || 'the Editorial MCP reported an error')}`);
		if (result?.structuredContent && typeof result.structuredContent === 'object') return result.structuredContent as Record<string, unknown>;
		try {
			const parsed = JSON.parse(text) as unknown;
			if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
		} catch {
			// fall through
		}
		throw new EditorialError(`${tool}: the Editorial MCP returned no structured result`);
	}
}

export function editorialClient(config: HarnessConfig, env: Record<string, string | undefined>, fetchImpl?: Fetch): EditorialClient | undefined {
	if (!config.editorial) return undefined;
	return new EditorialClient({
		url: config.editorial.url,
		token: env[config.editorial.tokenEnv] ?? '',
		origins: config.newsroomOrigins,
		fetchImpl,
	});
}

// ── Case files the launcher writes ───────────────────────────────────────────

function insideCase(caseDir: string, path: string): boolean {
	const child = relative(caseDir, path);
	return child !== '' && child !== '..' && !child.startsWith(`..${sep}`);
}

// Writes a case file the model may not write (case-files.ts protects these
// paths): never through a symlink, through a temporary file and a rename.
export function writeHarnessFile(caseDir: string, path: string, content: string | Buffer): void {
	const target = resolve(caseDir, path);
	if (!insideCase(caseDir, target)) throw new Error(`refused: ${path} is outside the case`);
	let current = caseDir;
	for (const part of relative(caseDir, dirname(target)).split(sep).filter(Boolean)) {
		current = resolve(current, part);
		let stats;
		try {
			stats = lstatSync(current);
		} catch {
			mkdirSync(current, { mode: 0o700 });
			continue;
		}
		if (stats.isSymbolicLink() || !stats.isDirectory()) throw new Error(`refused: ${relative(caseDir, current)} is not a real folder`);
	}
	const temp = `${target}.${randomUUID()}.tmp`;
	const fd = openSync(temp, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
	try {
		writeSync(fd, typeof content === 'string' ? Buffer.from(content, 'utf8') : content);
	} finally {
		closeSync(fd);
	}
	try {
		renameSync(temp, target);
	} catch (error) {
		rmSync(temp, { force: true });
		throw error;
	}
}

function readCaseJson(caseDir: string, path: string): Record<string, unknown> | undefined {
	try {
		const target = resolve(caseDir, path);
		if (lstatSync(target).isSymbolicLink()) return undefined;
		const value = JSON.parse(readFileSync(target, 'utf8')) as unknown;
		return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
	} catch {
		return undefined;
	}
}

const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
const now = () => new Date().toISOString();
export const sha256 = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex');

// ── Step 1: case start ───────────────────────────────────────────────────────

export type CaseReference = { case_ref: string; title: string; custody_mode: string; editorial_origin: string; registered_at: string };

// register_case once per case: the reference is stored in the case, and a
// resumed case reuses it without calling the Editorial MCP again. Owner and
// organization come from the sign-in, never from here.
export async function ensureCaseRegistered(client: EditorialClient, config: HarnessConfig, signal?: AbortSignal): Promise<{ reference: CaseReference; registered: boolean }> {
	const editorial = config.editorial!;
	const origin = new URL(editorial.url).origin;
	const stored = readCaseJson(config.caseDir, CASE_REFERENCE_FILE);
	if (stored && typeof stored.case_ref === 'string' && stored.case_ref && stored.editorial_origin === origin) {
		return { reference: stored as unknown as CaseReference, registered: false };
	}
	const answer = await client.call('register_case', {
		title: editorial.caseTitle,
		custody_mode: editorial.custodyMode,
		client_case_id: config.caseId,
	}, signal);
	const caseRef = answer.case_ref;
	if (typeof caseRef !== 'string' || !/^[A-Za-z0-9._:-]{1,200}$/.test(caseRef)) {
		throw new EditorialError('register_case returned no usable case_ref');
	}
	const reference: CaseReference = {
		case_ref: caseRef,
		title: editorial.caseTitle,
		custody_mode: editorial.custodyMode,
		editorial_origin: origin,
		registered_at: now(),
	};
	writeHarnessFile(config.caseDir, CASE_REFERENCE_FILE, json({ schema_version: '1.0', ...reference }));
	return { reference, registered: true };
}

export function storedCaseRef(caseDir: string): string {
	const stored = readCaseJson(caseDir, CASE_REFERENCE_FILE);
	if (typeof stored?.case_ref !== 'string') throw new EditorialError('the case has no newsroom case reference yet; restart the case so the harness registers it');
	return stored.case_ref;
}

// ── Step 3: decision checks at the Investigation gate and the Report ─────────

export type CheckKind = 'findings' | 'report';
const CHECK_INPUTS: Record<CheckKind, Array<[string, string, boolean]>> = {
	// [field, case file, required]
	findings: [['findings', 'data/findings.json', true], ['passages', 'data/source-expressions.json', false], ['fact_check', 'data/fact-check.json', false]],
	report: [['report', 'data/report-draft.json', true], ['findings', 'data/findings.json', true]],
};
const JOB_STATES = new Set(['queued', 'running', 'done', 'failed', 'unavailable']);

export type CheckRecord = {
	kind: CheckKind;
	state: 'done' | 'failed' | 'unavailable';
	job_id?: string;
	input_sha256: string;
	flags: Array<{ target: string; question: string; reason: string }>;
	reason?: string;
	checked_at: string;
};

function checkArtifact(caseDir: string, kind: CheckKind): Record<string, unknown> {
	const artifact: Record<string, unknown> = {};
	for (const [field, path, required] of CHECK_INPUTS[kind]) {
		const value = readCaseJson(caseDir, path);
		if (!value) {
			if (required) throw new EditorialError(`run_checks: ${path} is missing or unreadable, so there is nothing to check`);
			continue;
		}
		artifact[field] = value;
	}
	return artifact;
}

function flagsOf(value: unknown): CheckRecord['flags'] {
	if (!Array.isArray(value)) return [];
	return value
		.filter((flag): flag is Record<string, unknown> => !!flag && typeof flag === 'object')
		.map((flag) => ({ target: String(flag.target ?? ''), question: String(flag.question ?? ''), reason: String(flag.reason ?? '') }))
		.filter((flag) => flag.target && flag.reason)
		.slice(0, 500);
}

// run_checks returns a job; the harness polls it with the job id until the
// flags arrive. A flag never blocks. "Unavailable" is recorded as such and
// never read as "no flags".
export async function runChecks(
	client: EditorialClient,
	caseDir: string,
	kind: CheckKind,
	options: { signal?: AbortSignal; pollMs?: number; deadlineMs?: number } = {},
): Promise<CheckRecord> {
	const caseRef = storedCaseRef(caseDir);
	const artifact = checkArtifact(caseDir, kind);
	const inputSha = sha256(JSON.stringify(artifact));
	let answer = await client.call('run_checks', { case_ref: caseRef, kind, artifact, artifact_sha256: inputSha }, options.signal);
	const jobId = typeof answer.job_id === 'string' ? answer.job_id : undefined;
	const deadline = Date.now() + (options.deadlineMs ?? 600_000);
	while (jobId && (answer.state === 'queued' || answer.state === 'running')) {
		if (Date.now() > deadline) {
			answer = { state: 'unavailable', reason: 'the decision checks did not finish in time' };
			break;
		}
		await new Promise((done) => setTimeout(done, options.pollMs ?? 1000));
		options.signal?.throwIfAborted();
		answer = await client.call('run_checks', { case_ref: caseRef, job_id: jobId }, options.signal);
	}
	const state = typeof answer.state === 'string' && JOB_STATES.has(answer.state) ? answer.state : 'unavailable';
	const record: CheckRecord = {
		kind,
		state: state === 'done' ? 'done' : state === 'failed' ? 'failed' : 'unavailable',
		input_sha256: inputSha,
		flags: state === 'done' ? flagsOf(answer.flags) : [],
		checked_at: now(),
	};
	if (jobId) record.job_id = jobId;
	if (state === 'done' && typeof answer.input_sha256 === 'string' && answer.input_sha256 !== inputSha) {
		record.state = 'failed';
		record.flags = [];
		record.reason = 'the service checked a different input than the one sent';
	} else if (state !== 'done') {
		record.reason = String(answer.reason ?? answer.error ?? `the decision checks ended ${state}`);
	}
	const existing = readCaseJson(caseDir, CHECKS_FILE);
	const checks = { ...((existing?.checks as Record<string, unknown> | undefined) ?? {}), [kind]: record };
	writeHarnessFile(caseDir, CHECKS_FILE, json({ schema_version: '1.0', checks }));
	return record;
}

// ── Step 5: Submit ───────────────────────────────────────────────────────────

export type PackageBuild = {
	package: Record<string, unknown>;
	files: Array<Record<string, unknown>>;
	package_sha256: string;
};

export type SubmitDeps = {
	client: EditorialClient;
	caseDir: string;
	// scripts/newsroom-package.py, run with the harness's Python.
	runPackage(args: string[]): Promise<{ code: number; stdout: string; stderr: string }>;
	signal?: AbortSignal;
};

export type SubmitOutcome =
	| { state: 'blocked'; integrity_failures: Array<Record<string, unknown>> }
	| { state: 'submitted'; submission_id: string; review_id: string; review_url?: string; review_state: string; receipts: string[] };

const SAFE_ID = /^[A-Za-z0-9._:-]{1,200}$/;
const fileSafe = (id: string) => id.replace(/[^A-Za-z0-9._-]/g, '_');

// Builds the Submit package (scripts/newsroom-package.py), sends it with
// submit_case_review and reads the review status. A failed integrity check
// leaves the case at Ingest with the reasons recorded; otherwise the
// receipts are copied to {case}/receipts/ and the seam records the completed
// ingest marker, which the phase seals.
export async function submitCase(deps: SubmitDeps): Promise<SubmitOutcome> {
	const { client, caseDir } = deps;
	const caseRef = storedCaseRef(caseDir);
	const built = await deps.runPackage(['build', caseDir]);
	if (built.code !== 0) throw new EditorialError(`the Submit package could not be built: ${(built.stderr || built.stdout).trim().slice(-1500)}`);
	const build = JSON.parse(built.stdout) as PackageBuild;
	const answer = await client.call('submit_case_review', {
		case_ref: caseRef,
		idempotency_key: build.package_sha256,
		package: build.package,
		files: build.files,
	}, deps.signal);
	if (answer.status === 'rejected') {
		const failures = Array.isArray(answer.integrity_failures) ? (answer.integrity_failures as Array<Record<string, unknown>>).slice(0, 200) : [];
		const reasons = failures.length > 0 ? failures : [{ check: 'unknown', reason: 'the service rejected the package without a reason' }];
		writeHarnessFile(caseDir, SUBMISSION_FILE, json({ schema_version: '1.0', state: 'blocked', package_sha256: build.package_sha256, integrity_failures: reasons, at: now() }));
		return { state: 'blocked', integrity_failures: reasons };
	}
	const submissionId = String(answer.submission_id ?? '');
	const reviewId = String(answer.review_id ?? '');
	if (answer.status !== 'staged' || !SAFE_ID.test(submissionId) || !SAFE_ID.test(reviewId)) {
		throw new EditorialError('submit_case_review returned neither a rejection nor a staged submission with its ids');
	}
	const receipts: string[] = [];
	const keep = (name: string, value: unknown) => {
		const path = `${RECEIPTS_DIR}/${name}`;
		writeHarnessFile(caseDir, path, json(value));
		receipts.push(path);
	};
	keep(`submission-${fileSafe(submissionId)}.json`, { tool: 'submit_case_review', received_at: now(), package_sha256: build.package_sha256, response: answer });
	const status = await client.call('get_review_status', { review_id: reviewId }, deps.signal);
	keep(`review-status-${fileSafe(reviewId)}.json`, { tool: 'get_review_status', received_at: now(), response: status });
	if (status.receipt && typeof status.receipt === 'object') keep(`review-${fileSafe(reviewId)}.json`, status.receipt);
	const completed = await deps.runPackage(['complete', caseDir, '--package-sha256', build.package_sha256, '--submission-id', submissionId, '--review-id', reviewId]);
	if (completed.code !== 0) throw new EditorialError(`the submission was staged but the case could not record it: ${(completed.stderr || completed.stdout).trim().slice(-1500)}`);
	const reviewState = String(status.state ?? 'pending');
	writeHarnessFile(caseDir, SUBMISSION_FILE, json({
		schema_version: '1.0',
		state: 'submitted',
		package_sha256: build.package_sha256,
		submission_id: submissionId,
		review_id: reviewId,
		review_url: typeof answer.review_url === 'string' ? answer.review_url : undefined,
		review_state: reviewState,
		receipts,
		at: now(),
	}));
	return { state: 'submitted', submission_id: submissionId, review_id: reviewId, review_url: answer.review_url as string | undefined, review_state: reviewState, receipts };
}
