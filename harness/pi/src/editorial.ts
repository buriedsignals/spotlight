// The newsroom edition's Editorial MCP client. Only the launcher holds the
// newsroom sign-in token and calls the Editorial MCP; pi processes reach it
// through the launcher (IPC), so no model process ever sees the token.
//
// Transport: MCP Streamable HTTP (one POST per JSON-RPC message, a JSON or
// SSE answer, an optional MCP-Session-Id). Every request carrying the token
// goes only to an origin in the launch contract's newsroom allowlist; a
// redirect is never followed. A rejected or expired token (HTTP 401 or 403)
// is a plain stop with no fallback, and so is a service that cannot be used
// (unreachable, a redirect, an HTTP or protocol error, an unreadable answer):
// a newsroom case never goes on without the newsroom's knowledge and checks.
// The contract the newsroom's server implements is
// docs/newsroom-editorial-mcp.md.
//
// The launcher writes nothing into the case itself: scripts/newsroom-package.py
// records every answer under the case lock (RunPackage below).
import { createHash, randomUUID } from 'node:crypto';
import { lstatSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { HarnessConfig } from './config.ts';
import { redactor } from './log.ts';

export const PROTOCOL_VERSION = '2025-11-25';
export const CASE_REFERENCE_FILE = 'data/editorial-case.json';
const RECEIPTS_DIR = 'receipts';

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
export const NEWSROOM_UNAVAILABLE =
	'the newsroom service could not be used, so Spotlight stopped rather than go on without the newsroom\'s knowledge and checks. Nothing more was sent. Resume the case when the service is back';

// The service cannot be used: unreachable, an HTTP or protocol error, an
// unreadable answer. It stops the harness, like a revoked sign-in.
export class EditorialError extends Error {}
// A request the harness would not send: off the allowlist, or a redirect.
export class EditorialRefused extends EditorialError {}
// The tool itself answered with an error (isError): a normal outcome the
// model reads, such as an unknown record id. The service is working.
export class EditorialToolError extends Error {}

// The case could not record what the newsroom answered (a check, a
// submission): the case stops rather than go on without the record; a
// pending submission resumes at the next start.
export class CaseRecordError extends Error {}

export function stopsTheNewsroomCase(error: unknown): boolean {
	return error instanceof EditorialCredentialError || error instanceof EditorialError || error instanceof CaseRecordError;
}

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
	// Every other secret the launcher holds (route tokens, tool keys): scrubbed
	// from every answer, like the token.
	secrets?: Iterable<string | undefined>;
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

// The largest answer read, JSON or SSE; past it the read is cancelled.
const MAX_ANSWER_BYTES = 16 * 1024 * 1024;

async function readCapped(response: Response): Promise<string> {
	const reader = response.body?.getReader();
	const chunks: Uint8Array[] = [];
	let size = 0;
	for (let part = await reader?.read(); part && !part.done; part = await reader!.read()) {
		size += part.value.byteLength;
		if (size > MAX_ANSWER_BYTES) {
			await reader!.cancel();
			throw new EditorialError(`the Editorial MCP's answer is larger than ${MAX_ANSWER_BYTES} bytes`);
		}
		chunks.push(part.value);
	}
	return Buffer.concat(chunks).toString('utf8');
}

function untilAborted<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
	if (!signal) return promise;
	return new Promise((done, fail) => {
		const abort = () => fail(signal.reason);
		if (signal.aborted) return abort();
		signal.addEventListener('abort', abort, { once: true });
		promise.then(done, fail).finally(() => signal.removeEventListener('abort', abort));
	});
}

export class EditorialClient {
	readonly #options: Required<Omit<EditorialClientOptions, 'fetchImpl' | 'secrets'>> & { fetchImpl: Fetch };
	readonly #redact: (text: string) => string;
	#session: string | undefined;
	#ready: Promise<void> | undefined;

	constructor(options: EditorialClientOptions) {
		this.#options = { ...options, timeoutMs: options.timeoutMs ?? 120_000, fetchImpl: options.fetchImpl ?? fetch };
		this.#redact = redactor([options.token, ...(options.secrets ?? [])]);
		assertNewsroomDestination(options.origins, options.url);
	}

	// No secret the launcher holds appears in what this client reports or
	// returns, even when a server echoes it back: this is the one scrub point
	// for answers, errors and the receipts made from them. config.ts refuses
	// short tokens.
	#clean(text: string): string {
		return this.#redact(text);
	}

	// What the harness passes on from an answer (to a model, the case, a
	// receipt): no secret the launcher holds, in keys or values, and no review
	// capability: no review_url key and no address on a newsroom endpoint
	// (where review pages live), nor `address` itself. Other addresses, such
	// as a record's sources, stay. `keepReview` is only for the submit answer,
	// whose review address the harness itself opens.
	sanitize(value: unknown, options: { address?: string; keepReview?: boolean } = {}, depth = 0): unknown {
		if (depth > 64) throw new EditorialError('the Editorial MCP answered with a value nested more than 64 levels deep');
		if (typeof value === 'string') {
			const text = this.#clean(value);
			if (options.keepReview) return text;
			return (options.address ? text.split(options.address).join('[review page]') : text)
				.replace(/\bhttps?:\/\/[^\s"'<>]+/gi, (found) => (isAllowedOrigin(this.#options.origins, found) ? '[address removed]' : found));
		}
		if (Array.isArray(value)) return value.map((item) => this.sanitize(item, options, depth + 1));
		if (value && typeof value === 'object') {
			return Object.fromEntries(
				Object.entries(value)
					.filter(([key]) => options.keepReview || key !== 'review_url')
					.map(([key, item]) => [this.sanitize(key, options) as string, this.sanitize(item, options, depth + 1)]),
			);
		}
		return value;
	}

	// Every error text the client reports passes the same sanitizer: it ends
	// up in logs, lane results and case files.
	#said(text: string): string {
		return this.sanitize(text) as string;
	}

	// A failed request or an unreadable answer stops the case; the caller's own
	// cancellation stays a cancellation and stops only that call.
	#unreachable(error: unknown, signal?: AbortSignal): unknown {
		if (signal?.aborted) return error;
		return new EditorialError(`the Editorial MCP could not be reached: ${this.#said((error as Error).message)}`);
	}

	async #post(message: Record<string, unknown>, signal?: AbortSignal): Promise<{ status: number; body: string; type: string; session?: string; sent?: string }> {
		const { url, token, origins, timeoutMs, fetchImpl } = this.#options;
		assertNewsroomDestination(origins, url);
		const headers: Record<string, string> = {
			authorization: `Bearer ${token}`,
			accept: 'application/json, text/event-stream',
			'content-type': 'application/json',
			'mcp-protocol-version': PROTOCOL_VERSION,
		};
		const sent = this.#session;
		if (sent) headers['mcp-session-id'] = sent;
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
			throw this.#unreachable(error, signal);
		}
		if (response.status >= 300 && response.status < 400) {
			throw new EditorialRefused('refused: the Editorial MCP answered with a redirect; Spotlight does not follow it with the newsroom token');
		}
		if (response.status === 401 || response.status === 403) throw new EditorialCredentialError();
		let body: string;
		try {
			body = await readCapped(response);
		} catch (error) {
			if (error instanceof EditorialError) throw error;
			throw this.#unreachable(error, signal);
		}
		return {
			status: response.status,
			body,
			type: response.headers.get('content-type') ?? '',
			session: response.headers.get('mcp-session-id') ?? undefined,
			sent,
		};
	}

	async #request(method: string, params: Record<string, unknown>, signal?: AbortSignal, retried = false): Promise<unknown> {
		const id = randomUUID();
		const answer = await this.#post({ jsonrpc: '2.0', id, method, params }, signal);
		// The server ended the session: start a new one, once. Concurrent
		// requests share one client, so only the first to see the old session
		// end clears it; all of them then wait on the same re-initialisation
		// and retry once.
		if (answer.status === 404 && answer.sent && !retried && method !== 'initialize') {
			if (this.#session === answer.sent) {
				this.#session = undefined;
				this.#ready = undefined;
			}
			await this.#initialize(signal);
			return this.#request(method, params, signal, true);
		}
		if (answer.status < 200 || answer.status >= 300) {
			throw new EditorialError(`the Editorial MCP answered HTTP ${answer.status}: ${this.#said(answer.body.slice(0, 500))}`);
		}
		if (method === 'initialize' && answer.session) this.#session = answer.session;
		let message: JsonRpcResponse | undefined;
		if (answer.type.includes('text/event-stream')) {
			message = parseEventStream(answer.body, id);
		} else {
			try {
				message = JSON.parse(answer.body) as JsonRpcResponse;
				// Another request's answer is no answer to this one.
				if (message?.id !== id) message = undefined;
			} catch {
				message = undefined;
			}
		}
		if (!message || message.jsonrpc !== '2.0' || (message.result === undefined && message.error === undefined)) {
			throw new EditorialError(`the Editorial MCP gave no readable answer to ${method}`);
		}
		if (message.error) throw new EditorialError(`the Editorial MCP refused ${method}: ${this.#said(String(message.error.message ?? message.error.code))}`);
		return message.result;
	}

	// The session is shared, so its (re-)initialisation runs under the
	// client's own timeout only; each caller's signal ends only its own wait.
	#initialize(signal?: AbortSignal): Promise<void> {
		this.#ready ??= (async () => {
			await this.#request('initialize', {
				protocolVersion: PROTOCOL_VERSION,
				capabilities: {},
				clientInfo: { name: 'spotlight-harness', version: '1' },
			});
			const sent = await this.#post({ jsonrpc: '2.0', method: 'notifications/initialized' });
			if (sent.status !== 202 && (sent.status < 200 || sent.status >= 300)) {
				throw new EditorialError(`the Editorial MCP refused the initialized notification (HTTP ${sent.status})`);
			}
		})().catch((error) => {
			this.#ready = undefined;
			throw error;
		});
		return untilAborted(this.#ready, signal);
	}

	// Opens the MCP session. The launcher does this at every start, so a
	// revoked or expired token stops a resumed case before any work.
	connect(signal?: AbortSignal): Promise<void> {
		return this.#initialize(signal);
	}

	// One tools/call. The answer is the tool's structuredContent (or its text
	// content parsed as JSON) with the token scrubbed out; a tool error becomes
	// an EditorialToolError.
	async call(tool: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> {
		await this.#initialize(signal);
		const result = (await this.#request('tools/call', { name: tool, arguments: args }, signal)) as {
			content?: Array<{ type: string; text?: string }>;
			structuredContent?: unknown;
			isError?: boolean;
		};
		// The envelope is checked here, once: a malformed answer is the service
		// failing, which stops the case like any other.
		const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
		if (!isObject(result) || (result.isError !== undefined && typeof result.isError !== 'boolean') || (result.content !== undefined && !(Array.isArray(result.content) && result.content.every(isObject)))) {
			throw new EditorialError(`${tool}: the Editorial MCP returned a malformed result`);
		}
		const text = (result.content ?? []).filter((part) => part.type === 'text').map((part) => String(part.text ?? '')).join('\n');
		// Error text reaches the model: no address in it, since one may be a
		// review page's capability.
		if (result.isError) {
			throw new EditorialToolError(`${tool}: ${this.#said(text || 'the Editorial MCP reported an error').replace(/\bhttps?:\/\/\S+/gi, '[address removed]')}`);
		}
		const answer = (value: Record<string, unknown>) => {
			const nonEmpty = (field: unknown) => typeof field === 'string' && field.length > 0;
			const malformed =
				(tool === 'run_checks' &&
					(!JOB_STATES.has(String(value.state)) ||
						((value.state === 'queued' || value.state === 'running') && !nonEmpty(value.job_id)) ||
						(value.input_sha256 !== undefined && !(typeof value.input_sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.input_sha256))) ||
						(value.flags !== undefined &&
							!(Array.isArray(value.flags) && value.flags.every((flag) => isObject(flag) && nonEmpty(flag.target) && nonEmpty(flag.reason) && ['string', 'undefined'].includes(typeof flag.question)))))) ||
				(tool === 'submit_case_review' && value.review_url !== undefined && typeof value.review_url !== 'string') ||
				// Knowledge answers: the top-level shapes of schemas/editorial-mcp.
				(tool === 'search_knowledge' && !(Array.isArray(value.records) && value.records.every(isObject))) ||
				(tool === 'get_record' && !isObject(value.record)) ||
				(tool === 'traverse' && !(typeof value.root === 'string' && Array.isArray(value.nodes) && Array.isArray(value.edges))) ||
				(tool === 'coverage' && !(typeof value.record_id === 'string' && isObject(value.coverage)));
			if (malformed) throw new EditorialError(`${tool}: the Editorial MCP answered outside the contract`);
			return this.sanitize(value, { keepReview: tool === 'submit_case_review' }) as Record<string, unknown>;
		};
		if (isObject(result.structuredContent)) {
			return answer(result.structuredContent);
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(text);
		} catch {
			// no structured result
		}
		if (isObject(parsed)) return answer(parsed);
		throw new EditorialError(`${tool}: the Editorial MCP returned no structured result`);
	}
}

export function editorialClient(config: HarnessConfig, env: Record<string, string | undefined>, secrets: Iterable<string>): EditorialClient | undefined {
	if (!config.editorial) return undefined;
	return new EditorialClient({
		url: config.editorial.url,
		token: (env[config.editorial.tokenEnv] ?? '').trim(),
		origins: config.newsroomOrigins,
		secrets,
	});
}

// ── Case files ───────────────────────────────────────────────────────────────
// The harness reads them here; scripts/newsroom-package.py alone writes them,
// under the case lock (case-files.ts keeps every model process off them).

// Only a missing file is absent; one that is a symlink, unreadable or not a
// JSON object stops the case rather than send or keep less than it holds.
function readCaseJson(caseDir: string, path: string): Record<string, unknown> | undefined {
	const target = resolve(caseDir, path);
	let value: unknown;
	try {
		if (lstatSync(target).isSymbolicLink()) throw new Error('it is a symlink');
		value = JSON.parse(readFileSync(target, 'utf8'));
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
		throw new CaseRecordError(`${path} cannot be read: ${(error as Error).message}`);
	}
	if (!value || typeof value !== 'object' || Array.isArray(value)) throw new CaseRecordError(`${path} is not a JSON object`);
	return value as Record<string, unknown>;
}

const now = () => new Date().toISOString();
export const sha256 = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex');

// scripts/newsroom-package.py, run with the harness's Python; `input` goes to
// its stdin as JSON.
export type RunPackage = (args: string[], input?: unknown) => Promise<{ code: number; stdout: string; stderr: string }>;

// One run of the package script. Every failure (a refusal, a crash, a
// timeout, an unreadable output) stops the case.
async function packageScript<T>(runPackage: RunPackage, args: string[], input: unknown, read: (stdout: string) => T, what: string): Promise<T> {
	try {
		const result = await runPackage(args, input);
		if (result.code !== 0) throw new Error((result.stderr || result.stdout).trim().slice(-1500));
		return read(result.stdout);
	} catch (error) {
		throw new CaseRecordError(`${what}: ${(error as Error).message}`);
	}
}

function recordInCase(runPackage: RunPackage, args: string[], input?: unknown): Promise<Record<string, unknown>> {
	return packageScript(runPackage, args, input, (stdout) => JSON.parse(stdout) as Record<string, unknown>, 'the case could not record the newsroom\'s answer');
}

// ── Step 1: case start ───────────────────────────────────────────────────────

export type CaseReference = { case_ref: string; title: string; custody_mode: string; editorial_origin: string; client_case_id: string };

// register_case once per case: the reference is stored in the case, and a
// resumed case reuses it without calling the Editorial MCP again. Owner and
// organization come from the sign-in, never from here.
export async function ensureCaseRegistered(
	client: EditorialClient,
	config: HarnessConfig,
	runPackage: RunPackage,
	signal?: AbortSignal,
): Promise<{ reference: CaseReference; registered: boolean }> {
	const editorial = config.editorial!;
	const origin = new URL(editorial.url).origin;
	const stored = readCaseJson(config.caseDir, CASE_REFERENCE_FILE);
	if (stored && typeof stored.case_ref === 'string' && stored.case_ref) {
		// The reference belongs to the endpoint that issued it: another endpoint
		// would register the case again and lose it.
		if (stored.editorial_origin !== origin) {
			throw new Error(`this case is registered with another newsroom endpoint (${String(stored.editorial_origin)}), so it was not started; start it with that endpoint`);
		}
		// A case folder copied or restored under another name carries the
		// other case's reference: refuse rather than review under it.
		if (stored.client_case_id !== config.caseId) {
			throw new Error(`this case folder holds the newsroom reference of another case (${String(stored.client_case_id)}), so it was not started; start it as a new case instead`);
		}
		return { reference: stored as unknown as CaseReference, registered: false };
	}
	const answer = await client.call('register_case', {
		title: editorial.caseTitle,
		custody_mode: editorial.custodyMode,
		client_case_id: config.caseId,
	}, signal);
	const caseRef = answer.case_ref;
	if (typeof caseRef !== 'string' || !SAFE_ID.test(caseRef)) {
		throw new EditorialError('register_case returned no usable case_ref');
	}
	const reference: CaseReference = { case_ref: caseRef, title: editorial.caseTitle, custody_mode: editorial.custodyMode, editorial_origin: origin, client_case_id: config.caseId };
	await recordInCase(runPackage, ['register', config.caseDir], reference);
	return { reference, registered: true };
}

export function storedCaseRef(caseDir: string): string {
	const stored = readCaseJson(caseDir, CASE_REFERENCE_FILE);
	if (typeof stored?.case_ref !== 'string') throw new Error('the case has no newsroom case reference yet; restart the case so the harness registers it');
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
const REVIEW_STATES = new Set(['pending', 'approved', 'rejected', 'expired']);

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
			if (required) throw new Error(`run_checks: ${path} is missing or unreadable, so there is nothing to check`);
			continue;
		}
		artifact[field] = value;
	}
	return artifact;
}

// The client checked each flag against the contract.
function flagsOf(value: unknown): CheckRecord['flags'] {
	return ((value ?? []) as Array<Record<string, string>>)
		.map((flag) => ({ target: flag.target, question: flag.question ?? '', reason: flag.reason }))
		.slice(0, 500);
}

// run_checks returns a job; the harness polls it with the job id until the
// flags arrive. A flag never blocks. "Unavailable" is recorded as such and
// never read as "no flags".
export async function runChecks(
	client: EditorialClient,
	caseDir: string,
	kind: CheckKind,
	runPackage: RunPackage,
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
		if (answer.job_id !== jobId) throw new EditorialError('run_checks answered a poll with another job\'s result');
	}
	const state = String(answer.state);
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
	await recordInCase(runPackage, ['checks', caseDir], record);
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
	runPackage: RunPackage;
	// Opens the newsroom's review page in the member's browser. The address
	// is the member's capability: it is never returned, stored or shown to a
	// model process. False: refused, not on the newsroom's endpoints. It
	// rejects when the page could not be opened, which leaves the submission
	// pending.
	openReview(url: string): Promise<boolean>;
	signal?: AbortSignal;
};

export type SubmitOutcome =
	| { state: 'blocked'; integrity_failures: Array<Record<string, unknown>> }
	| { state: 'submitted'; submission_id: string; review_id: string; review_state: string; review_opened: boolean; notice?: string; receipts: string[] };

const REVIEW_ADDRESS_REFUSED =
	'the case was submitted, but the newsroom returned a review address outside the newsroom\'s allowed endpoints, so Spotlight did not open it. Open the review in the newsroom\'s own tool.';

const SAFE_ID = /^[A-Za-z0-9._:-]{1,200}$/;
const fileSafe = (id: string) => id.replace(/[^A-Za-z0-9._-]/g, '_');

// A submission a crash left between its recorded intent and its outcome.
export async function pendingSubmission(runPackage: RunPackage, caseDir: string): Promise<boolean> {
	return (await recordInCase(runPackage, ['pending', caseDir])).pending === true;
}

// Builds the Submit package (scripts/newsroom-package.py), sends it with
// submit_case_review and reads the review status. The intent is recorded
// first, under the case lock, and only once the member requested ingestion
// on the ingest page: from then on a crash leaves a pending submission that
// resumes with the same idempotency key (the package sha256), which the
// Editorial MCP answers with the submission it already staged. A failed
// integrity check leaves the case at Ingest with the reasons recorded;
// otherwise the receipts, the submission and the completed ingest marker are
// recorded together, and the phase seals them.
export async function submitCase(deps: SubmitDeps): Promise<SubmitOutcome> {
	const { client, caseDir, runPackage } = deps;
	const caseRef = storedCaseRef(caseDir);
	const scratch = mkdtempSync(join(tmpdir(), 'spotlight-package-'));
	let build: PackageBuild;
	try {
		const out = join(scratch, 'package.json');
		build = await packageScript(runPackage, ['build', caseDir, '--out', out], undefined, () => JSON.parse(readFileSync(out, 'utf8')) as PackageBuild, 'the Submit package could not be built');
	} finally {
		rmSync(scratch, { recursive: true, force: true });
	}
	const key = build.package_sha256;
	await recordInCase(runPackage, ['begin', caseDir, '--package-sha256', key]);
	const answer = await client.call('submit_case_review', { case_ref: caseRef, idempotency_key: key, package: build.package, files: build.files }, deps.signal);
	if (answer.status === 'rejected') {
		const failures = Array.isArray(answer.integrity_failures) ? (client.sanitize(answer.integrity_failures.slice(0, 200)) as Array<Record<string, unknown>>) : [];
		const reasons = failures.length > 0 ? failures : [{ check: 'unknown', reason: 'the service rejected the package without a reason' }];
		await recordInCase(runPackage, ['blocked', caseDir, '--package-sha256', key], { integrity_failures: reasons });
		return { state: 'blocked', integrity_failures: reasons };
	}
	const submissionId = String(answer.submission_id ?? '');
	const reviewId = String(answer.review_id ?? '');
	if (answer.status !== 'staged' || !SAFE_ID.test(submissionId) || !SAFE_ID.test(reviewId)) {
		throw new EditorialError('submit_case_review returned neither a rejection nor a staged submission with its ids');
	}
	const address = typeof answer.review_url === 'string' ? answer.review_url : undefined;
	const status = await client.call('get_review_status', { review_id: reviewId }, deps.signal);
	// The status must be this review's, in a state the contract names, before
	// the submission is marked complete; otherwise the intent stays pending.
	if (status.review_id !== reviewId || !REVIEW_STATES.has(String(status.state))) {
		throw new EditorialError('get_review_status answered for another review or without a valid state');
	}
	const reviewState = String(status.state);
	// The address is kept nowhere, so the page opens before the submission is
	// marked complete: if opening fails, the intent stays pending and a retry
	// with the same key gets the address again. An address off the newsroom's
	// endpoints is refused for good, so it completes (pending would hold the
	// case forever) and the member is told where the review is.
	const opened = address ? await deps.openReview(address) : false;
	const refused = address !== undefined && !opened;
	const receipts: Record<string, unknown> = {
		[`submission-${fileSafe(submissionId)}.json`]: {
			tool: 'submit_case_review',
			received_at: now(),
			package_sha256: key,
			response: client.sanitize(answer, { address }),
			...(refused ? { review_address: 'refused: outside the newsroom\'s allowed endpoints, not opened' } : {}),
		},
		[`review-status-${fileSafe(reviewId)}.json`]: { tool: 'get_review_status', received_at: now(), response: client.sanitize(status, { address }) },
	};
	if (status.receipt && typeof status.receipt === 'object') receipts[`review-${fileSafe(reviewId)}.json`] = client.sanitize(status.receipt, { address });
	await recordInCase(
		runPackage,
		['complete', caseDir, '--package-sha256', key, '--submission-id', submissionId, '--review-id', reviewId],
		{ receipts, review_state: reviewState },
	);
	return {
		state: 'submitted',
		submission_id: submissionId,
		review_id: reviewId,
		review_state: reviewState,
		review_opened: opened,
		...(refused ? { notice: REVIEW_ADDRESS_REFUSED } : {}),
		receipts: Object.keys(receipts).map((name) => `${RECEIPTS_DIR}/${name}`).sort(),
	};
}
