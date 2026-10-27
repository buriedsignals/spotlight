// A scripted OpenAI-compatible chat-completions server for network-free tests.
// It records every request body and answers with whatever the test's responder
// returns: plain text, tool calls, an HTTP error, or a request that never
// completes. A request with `stream: false` gets one JSON body (with the
// reply's usage, as OpenRouter reports it); any other request gets SSE.
import { createServer } from 'node:http';
import type { IncomingMessage, Server, ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

export type ChatRequest = {
	model?: string;
	messages: Array<{ role: string; content?: unknown; tool_calls?: unknown[]; tool_call_id?: string }>;
	tools?: Array<{ type: string; function: { name: string } }>;
	[key: string]: unknown;
};

export type ToolCall = { name: string; arguments: Record<string, unknown> };
export type Usage = { total_tokens?: number; cost?: number };
// `reasoning` streams first as `reasoning_content`, as llama-server sends a model's thinking.
export type Reply = ({ text: string } | { toolCalls: ToolCall[] }) & { usage?: Usage; reasoning?: string } | { status: number } | 'hang';
export type Responder = (request: ChatRequest, index: number) => Reply;

export type FakeOpenAI = {
	baseURL: string;
	requests: ChatRequest[];
	waitForRequests(count: number, timeoutMs?: number): Promise<void>;
	close(): Promise<void>;
};

export function systemText(request: ChatRequest): string {
	return request.messages
		.filter((message) => message.role === 'system' || message.role === 'developer')
		.map((message) => (typeof message.content === 'string' ? message.content : JSON.stringify(message.content)))
		.join('\n');
}

export function isWorkerRequest(request: ChatRequest, role: string): boolean {
	return systemText(request).includes(`Spotlight ${role} worker`);
}

export function toolNames(request: ChatRequest): string[] {
	return (request.tools ?? []).map((tool) => tool.function.name).sort();
}

export function toolResults(request: ChatRequest): string[] {
	return request.messages
		.filter((message) => message.role === 'tool')
		.map((message) => (typeof message.content === 'string' ? message.content : JSON.stringify(message.content)));
}

function chunk(response: ServerResponse, delta: Record<string, unknown>, finish: string | null) {
	const body = {
		id: 'chatcmpl-fake',
		object: 'chat.completion.chunk',
		created: 0,
		model: 'fake',
		choices: [{ index: 0, delta, finish_reason: finish }],
	};
	response.write(`data: ${JSON.stringify(body)}\n\n`);
}

function toolCallsOf(calls: ToolCall[]) {
	return calls.map((call, index) => ({
		index,
		id: `call_${index}_${call.name}`,
		type: 'function',
		function: { name: call.name, arguments: JSON.stringify(call.arguments) },
	}));
}

function answerJSON(response: ServerResponse, reply: Exclude<Reply, 'hang' | { status: number }>) {
	const message = 'text' in reply ? { role: 'assistant', content: reply.text } : { role: 'assistant', content: null, tool_calls: toolCallsOf(reply.toolCalls) };
	response.writeHead(200, { 'content-type': 'application/json' });
	response.end(JSON.stringify({ id: 'chatcmpl-fake', object: 'chat.completion', model: 'fake', choices: [{ index: 0, message, finish_reason: 'text' in reply ? 'stop' : 'tool_calls' }], usage: reply.usage ?? {} }));
}

function answer(response: ServerResponse, reply: Exclude<Reply, 'hang' | { status: number }>) {
	response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
	if (reply.reasoning) chunk(response, { role: 'assistant', reasoning_content: reply.reasoning }, null);
	if ('text' in reply) {
		chunk(response, { role: 'assistant', content: reply.text }, null);
		chunk(response, {}, 'stop');
	} else {
		chunk(
			response,
			{ role: 'assistant', tool_calls: toolCallsOf(reply.toolCalls) },
			null,
		);
		chunk(response, {}, 'tool_calls');
	}
	response.write('data: [DONE]\n\n');
	response.end();
}

async function readBody(request: IncomingMessage): Promise<string> {
	const parts: Buffer[] = [];
	for await (const part of request) parts.push(part as Buffer);
	return Buffer.concat(parts).toString('utf8');
}

export async function startFakeOpenAI(responder: Responder): Promise<FakeOpenAI> {
	const requests: ChatRequest[] = [];
	const waiters: Array<() => void> = [];
	const hanging = new Set<ServerResponse>();
	const server: Server = createServer(async (request, response) => {
		const raw = await readBody(request);
		if (!request.url?.endsWith('/chat/completions')) {
			response.writeHead(404).end();
			return;
		}
		const body = JSON.parse(raw) as ChatRequest;
		const index = requests.push(body) - 1;
		for (const wake of waiters.splice(0)) wake();
		const reply = responder(body, index);
		if (reply === 'hang') {
			hanging.add(response);
			response.writeHead(200, { 'content-type': 'text/event-stream' });
			return;
		}
		if ('status' in reply) {
			response.writeHead(reply.status, { 'content-type': 'application/json' });
			response.end(JSON.stringify({ error: { code: reply.status, message: 'scripted error' } }));
			return;
		}
		if (body.stream === false) answerJSON(response, reply);
		else answer(response, reply);
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	const { port } = server.address() as AddressInfo;
	return {
		baseURL: `http://127.0.0.1:${port}/v1`,
		requests,
		async waitForRequests(count, timeoutMs = 20_000) {
			const deadline = Date.now() + timeoutMs;
			while (requests.length < count) {
				if (Date.now() > deadline) {
					throw new Error(`expected ${count} model requests, saw ${requests.length}`);
				}
				await new Promise<void>((resolve) => {
					waiters.push(resolve);
					setTimeout(resolve, 100);
				});
			}
		},
		async close() {
			for (const response of hanging) response.destroy();
			server.closeAllConnections();
			await new Promise<void>((resolve) => server.close(() => resolve()));
		},
	};
}
