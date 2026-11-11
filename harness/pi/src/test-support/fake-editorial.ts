// A fake Editorial MCP for network-free tests: MCP Streamable HTTP on
// 127.0.0.1 (initialize, notifications/initialized, tools/call) with the
// tool shapes of docs/newsroom-editorial-mcp.md. It records every tool call
// and the Authorization header it came with. Each tool's answer is the test's
// handler, or the default below. Like the contract's server, it issues every
// id (create_id), refuses a case id or a batch id it already holds, lists its
// tools (tools/list), and answers 404 on a session it ended.
import { createServer } from 'node:http';
import type { IncomingMessage, Server, ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

// `httpStatus`: answer the call with that HTTP status; `unstructured`: a text
// result with no structuredContent; `rejected`: the service refused the batch
// (an error result whose structured content lists the reasons).
export type ToolAnswer = Record<string, unknown> | { isError: true; text: string } | { rejected: string[] } | { httpStatus: number } | { unstructured: string };
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
	// The ids of the batches stored, one per stored batch.
	staged: string[];
	close(): Promise<void>;
};

export const FAKE_REVIEW_URL = 'https://editorial.newsroom.example/cases/case/batches/batch/review';

// What the service holds: the cases registered and the batches stored.
type Held = { cases: Map<string, string>; batches: Map<string, string>; issued: number };

function defaults(held: Held): Record<string, ToolHandler> {
	const missing = (what: string) => ({ isError: true as const, text: `no such ${what}` });
	const caseInfo = (id: string) => ({ id, name: held.cases.get(id), organization_id: '00000000-0000-4000-8000-0000000000aa', batch_infos: [] });
	const batchInfo = (id: string) => ({ id, case_id: held.batches.get(id), status: 'submitted', created_by: 'journalist@newsroom.example', submitted_at: '2026-11-11T10:00:00Z' });
	return {
		create_id: () => ({ result: `00000000-0000-4000-8000-${String((held.issued += 1)).padStart(12, '0')}` }),
		register_case: (args) => {
			const id = String(args.case_id);
			if (held.cases.has(id)) return { isError: true, text: `case ${id} is already registered` };
			held.cases.set(id, String(args.case_name));
			return caseInfo(id);
		},
		get_case_info: (args) => (held.cases.has(String(args.case_id)) ? caseInfo(String(args.case_id)) : missing('case')),
		start_case_review: (args) => {
			const request = args.request as { case_id: string; batch_id: string };
			if (held.batches.has(request.batch_id)) return { rejected: [`batch_id: batch ${request.batch_id} was already submitted`] };
			return {
				batch: { batch_info: { ...batchInfo(request.batch_id), case_id: request.case_id }, records: {} },
				review_url: FAKE_REVIEW_URL,
				assigned_ids: [],
				warnings: [],
				integrity_status: 'passed',
				decision_check_status: 'not_implemented',
			};
		},
		get_knowledge_batch_info: (args) => (held.batches.has(String(args.batch_id)) ? batchInfo(String(args.batch_id)) : missing('batch')),
		search_knowledge: (args) => ({
			records: [{ record_id: 'claim:prior-1', kind: 'claim', title: 'Prior reporting on Acme', summary: `Approved record for "${String(args.query)}": Acme paid Doe in 2024.` }],
		}),
		get_record: (args) => ({ record: { record_id: args.record_id, kind: 'claim', proposition: 'Acme paid Doe.' } }),
		traverse: (args) => ({ root: args.record_id, nodes: [], edges: [] }),
		coverage: (args) => ({ record_id: args.record_id, coverage: { articles: [] } }),
	};
}

async function readBody(request: IncomingMessage): Promise<string> {
	const parts: Buffer[] = [];
	for await (const part of request) parts.push(part as Buffer);
	return Buffer.concat(parts).toString('utf8');
}

export async function startFakeEditorial(
	// `unlisted`: tools the service does not offer (left out of tools/list).
	options: { token?: string; handlers?: Record<string, ToolHandler>; sse?: boolean; redirectTo?: string; answerId?: string; unlisted?: string[] } = {},
): Promise<FakeEditorial> {
	const token = options.token ?? 'newsroom-editorial-token-123';
	const calls: FakeEditorial['calls'] = [];
	const staged: string[] = [];
	const requests: FakeEditorial['requests'] = [];
	let revoked = false;
	const counts = new Map<string, number>();
	let sessions = 0;
	const ended = new Set<string>();
	const held: Held = { cases: new Map(), batches: new Map(), issued: 0 };
	const DEFAULTS = defaults(held);
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
			let answer: ToolAnswer = handler ? handler(args, index) : { isError: true as const, text: `Unknown tool: '${tool}'` };
			// A batch the service accepted is stored under its id, whoever answered.
			const sent = tool === 'start_case_review' ? (args.request as { case_id?: string; batch_id?: string } | undefined) : undefined;
			if (sent?.batch_id && 'batch' in answer && !held.batches.has(sent.batch_id)) {
				held.batches.set(sent.batch_id, String(sent.case_id));
				staged.push(sent.batch_id);
			}
			if ('httpStatus' in answer && typeof answer.httpStatus === 'number') {
				response.writeHead(answer.httpStatus, { 'content-type': 'text/plain' }).end('the service is unavailable');
				return;
			}
			if ('unstructured' in answer && typeof answer.unstructured === 'string') answer = { unstructured: answer.unstructured };
			result = 'rejected' in answer && Array.isArray(answer.rejected)
				? { content: [{ type: 'text', text: JSON.stringify({ errors: answer.rejected }) }], structuredContent: { errors: answer.rejected }, isError: true }
				: 'isError' in answer && answer.isError === true
				? { content: [{ type: 'text', text: answer.text }], isError: true }
				: 'unstructured' in answer
					? { content: [{ type: 'text', text: answer.unstructured }] }
					: { content: [{ type: 'text', text: JSON.stringify(answer) }], structuredContent: answer };
		} else if (message.method === 'tools/list') {
			const names = [...new Set([...Object.keys(DEFAULTS), ...Object.keys(options.handlers ?? {})])].filter((name) => !options.unlisted?.includes(name));
			result = { tools: names.map((name) => ({ name, inputSchema: { type: 'object' } })) };
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
