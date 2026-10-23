// A fake Editorial MCP for network-free tests: MCP Streamable HTTP on
// 127.0.0.1 (initialize, notifications/initialized, tools/call) with the
// tool shapes of docs/newsroom-editorial-mcp.md. It records every tool call
// and the Authorization header it came with. Each tool's answer is the test's
// handler, or the default below. Like the contract's server, it answers a
// repeated submit_case_review idempotency key with the submission it staged
// for that key, and answers 404 on a session it ended.
import { createServer } from 'node:http';
import type { IncomingMessage, Server, ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

// `httpStatus`: answer the call with that HTTP status; `unstructured`: a text
// result with no structuredContent.
export type ToolAnswer = Record<string, unknown> | { isError: true; text: string } | { httpStatus: number } | { unstructured: string };
export type ToolHandler = (args: Record<string, unknown>, index: number) => ToolAnswer;

export type FakeEditorial = {
	url: string;
	token: string;
	calls: Array<{ tool: string; args: Record<string, unknown>; authorization: string | undefined }>;
	// Every HTTP request (initialize and notifications included).
	requests: Array<{ method: string; authorization: string | undefined }>;
	callsOf(tool: string): Array<Record<string, unknown>>;
	// From now on the token is refused with HTTP 401.
	revoke(): void;
	// Ends every open MCP session: the next request on one gets HTTP 404.
	endSessions(): void;
	// The idempotency keys of the submissions staged, one per staged revision.
	staged: string[];
	close(): Promise<void>;
};

export const FAKE_CASE_REF = 'nr-case-1';

const DEFAULTS: Record<string, ToolHandler> = {
	register_case: () => ({ case_ref: FAKE_CASE_REF }),
	search_knowledge: (args) => ({
		records: [{ record_id: 'claim:prior-1', kind: 'claim', title: 'Prior reporting on Acme', summary: `Approved record for "${String(args.query)}": Acme paid Doe in 2024.` }],
	}),
	get_record: (args) => ({ record: { record_id: args.record_id, kind: 'claim', proposition: 'Acme paid Doe.' } }),
	traverse: (args) => ({ root: args.record_id, nodes: [], edges: [] }),
	coverage: (args) => ({ record_id: args.record_id, coverage: { articles: [] } }),
	run_checks: (args) =>
		args.job_id
			? { job_id: args.job_id, state: 'done', flags: [{ target: 'finding:F1', question: 'partial_support', reason: 'The excerpt names the payment but not its date.' }] }
			: { job_id: 'job-1', state: 'running' },
	submit_case_review: () => ({ status: 'staged', submission_id: 'sub-1', revision: 1, review_id: 'rev-1', review_url: 'https://editorial.newsroom.example/review/rev-1' }),
	get_review_status: (args) => ({ review_id: args.review_id, state: 'pending', receipt: { review_id: args.review_id, state: 'pending', digest: 'f'.repeat(64) } }),
};

async function readBody(request: IncomingMessage): Promise<string> {
	const parts: Buffer[] = [];
	for await (const part of request) parts.push(part as Buffer);
	return Buffer.concat(parts).toString('utf8');
}

export async function startFakeEditorial(
	options: { token?: string; handlers?: Record<string, ToolHandler>; sse?: boolean; redirectTo?: string; answerId?: string } = {},
): Promise<FakeEditorial> {
	const token = options.token ?? 'newsroom-editorial-token-123';
	const calls: FakeEditorial['calls'] = [];
	const staged: string[] = [];
	const requests: FakeEditorial['requests'] = [];
	let revoked = false;
	const counts = new Map<string, number>();
	let sessions = 0;
	const ended = new Set<string>();
	const stagedByKey = new Map<string, Record<string, unknown>>();
	const server: Server = createServer(async (request: IncomingMessage, response: ServerResponse) => {
		const raw = await readBody(request);
		const authorization = request.headers.authorization;
		let message: { id?: string; method?: string; params?: { name?: string; arguments?: Record<string, unknown> } };
		try {
			message = JSON.parse(raw);
		} catch {
			response.writeHead(400).end();
			return;
		}
		requests.push({ method: String(message.method), authorization });
		if (options.redirectTo) {
			response.writeHead(307, { location: options.redirectTo }).end();
			return;
		}
		if (revoked || authorization !== `Bearer ${token}`) {
			response.writeHead(401, { 'content-type': 'application/json', 'www-authenticate': 'Bearer error="invalid_token"' });
			response.end(JSON.stringify({ error: `token ${authorization ?? ''} is not valid` }));
			return;
		}
		const session = request.headers['mcp-session-id'];
		if (typeof session === 'string' && ended.has(session) && message.method !== 'initialize') {
			response.writeHead(404).end();
			return;
		}
		if (message.id === undefined) {
			response.writeHead(202).end();
			return;
		}
		let result: unknown;
		if (message.method === 'initialize') {
			result = { protocolVersion: '2025-11-25', capabilities: { tools: {} }, serverInfo: { name: 'fake-editorial', version: '1' } };
		} else if (message.method === 'tools/call') {
			const tool = String(message.params?.name);
			const args = message.params?.arguments ?? {};
			calls.push({ tool, args, authorization });
			const index = counts.get(tool) ?? 0;
			counts.set(tool, index + 1);
			const handler = options.handlers?.[tool] ?? DEFAULTS[tool];
			const key = tool === 'submit_case_review' ? String(args.idempotency_key) : undefined;
			let answer: ToolAnswer = key && stagedByKey.has(key) ? stagedByKey.get(key)! : handler ? handler(args, index) : { isError: true as const, text: `unknown tool ${tool}` };
			if (key && !stagedByKey.has(key) && (answer as { status?: unknown }).status === 'staged') {
				stagedByKey.set(key, answer as Record<string, unknown>);
				staged.push(key);
			}
			if ('httpStatus' in answer && typeof answer.httpStatus === 'number') {
				response.writeHead(answer.httpStatus, { 'content-type': 'text/plain' }).end('the service is unavailable');
				return;
			}
			if ('unstructured' in answer && typeof answer.unstructured === 'string') answer = { unstructured: answer.unstructured };
			result = 'isError' in answer && answer.isError === true
				? { content: [{ type: 'text', text: answer.text }], isError: true }
				: 'unstructured' in answer
					? { content: [{ type: 'text', text: answer.unstructured }] }
					: { content: [{ type: 'text', text: JSON.stringify(answer) }], structuredContent: answer };
		} else {
			result = {};
		}
		// `answerId`: answer tool calls under another request's id.
		const body = JSON.stringify({ jsonrpc: '2.0', id: message.method === 'tools/call' && options.answerId ? options.answerId : message.id, result });
		const headers: Record<string, string> = message.method === 'initialize' ? { 'mcp-session-id': `fake-session-${(sessions += 1)}` } : {};
		if (options.sse) {
			response.writeHead(200, { ...headers, 'content-type': 'text/event-stream' });
			response.end(`id: 1\ndata:\n\nid: 2\ndata: ${body}\n\n`);
		} else {
			response.writeHead(200, { ...headers, 'content-type': 'application/json' });
			response.end(body);
		}
	});
	await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
	const { port } = server.address() as AddressInfo;
	return {
		url: `http://127.0.0.1:${port}/mcp`,
		token,
		calls,
		requests,
		callsOf: (tool) => calls.filter((call) => call.tool === tool).map((call) => call.args),
		revoke: () => {
			revoked = true;
		},
		endSessions: () => {
			for (let n = 1; n <= sessions; n += 1) ended.add(`fake-session-${n}`);
		},
		staged,
		close: () => new Promise<void>((done) => server.close(() => done())),
	};
}
