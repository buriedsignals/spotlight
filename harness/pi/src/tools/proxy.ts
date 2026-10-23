// A loopback forward proxy that applies the egress guard to every connection a
// backing browser makes. The browser is started with this proxy and with
// loopback proxying forced on, so page subresources, scripts' fetch(),
// redirects the harness never saw, WebSockets (Chromium tunnels them with
// CONNECT) and service-worker requests all arrive here. Each one is checked
// like the first URL: local names and non-public literals are refused, and a
// hostname is resolved inside the outgoing connection's own lookup, so the
// address that is checked is the address that is connected to (no DNS
// rebinding between check and connect).
//
// The proxy never follows redirects itself; the browser follows them and each
// new request comes back through the proxy. It is never used in Tor mode: the
// browser then goes straight to Tor's SOCKS port and the harness connects to
// nothing.
import { request as httpRequest } from 'node:http';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createServer } from 'node:http';
import { connect } from 'node:net';
import type { AddressInfo, Socket } from 'node:net';
import { EgressRefused, checkURL, guardedLookup, isPublicAddress } from './egress.ts';
import type { AddressPolicy } from './egress.ts';

export type EgressProxy = { url: string; close: () => Promise<void> };

// Headers that describe one hop, not the request (RFC 9110 section 7.6.1).
const HOP_BY_HOP = new Set(['connection', 'keep-alive', 'proxy-connection', 'proxy-authorization', 'proxy-authenticate', 'te', 'trailer', 'upgrade']);

function refusalStatus(error: unknown): number {
	return error instanceof EgressRefused ? 403 : 502;
}

function forwardHeaders(raw: string[]): string[] {
	const out: string[] = [];
	for (let index = 0; index < raw.length; index += 2) {
		if (!HOP_BY_HOP.has(raw[index].toLowerCase())) out.push(raw[index], raw[index + 1]);
	}
	return out;
}

// Plain http:// requests arrive in absolute form ("GET http://host/path").
function forward(allowAddress: AddressPolicy, request: IncomingMessage, response: ServerResponse): void {
	let url: URL;
	try {
		url = checkURL(request.url ?? '', allowAddress);
		if (url.protocol !== 'http:') throw new EgressRefused('refused: only plain http is proxied without CONNECT');
	} catch (error) {
		response.writeHead(refusalStatus(error), { 'content-type': 'text/plain' }).end(String((error as Error).message));
		request.resume();
		return;
	}
	const upstream = httpRequest(url, {
		method: request.method,
		agent: false,
		lookup: guardedLookup(allowAddress) as never,
		headers: forwardHeaders(request.rawHeaders) as never,
	});
	upstream.once('response', (answer) => {
		response.writeHead(answer.statusCode ?? 502, answer.statusMessage, forwardHeaders(answer.rawHeaders) as never);
		answer.pipe(response);
	});
	upstream.once('error', (error) => {
		if (response.headersSent) return response.destroy();
		response.writeHead(refusalStatus(error), { 'content-type': 'text/plain' }).end(error.message);
	});
	request.pipe(upstream);
}

// https:// and WebSocket traffic arrives as "CONNECT host:port". The target is
// checked as a URL with that authority, then connected under the guard.
function tunnel(allowAddress: AddressPolicy, request: IncomingMessage, client: Socket, head: Buffer): void {
	const refuse = (status: number, reason: string) => {
		client.end(`HTTP/1.1 ${status} ${status === 403 ? 'Forbidden' : 'Bad Gateway'}\r\ncontent-type: text/plain\r\nconnection: close\r\n\r\n${reason}`);
	};
	let target: URL;
	try {
		target = checkURL(`https://${request.url ?? ''}/`, allowAddress);
	} catch (error) {
		return refuse(refusalStatus(error), (error as Error).message);
	}
	const upstream = connect({
		host: target.hostname.replace(/^\[|\]$/g, ''),
		port: Number(target.port || 443),
		lookup: guardedLookup(allowAddress) as never,
	});
	let connected = false;
	upstream.once('connect', () => {
		connected = true;
		client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
		if (head.length > 0) upstream.write(head);
		upstream.pipe(client);
		client.pipe(upstream);
	});
	upstream.once('error', (error) => {
		if (connected) client.destroy();
		else refuse(refusalStatus(error), error.message);
	});
	client.once('error', () => upstream.destroy());
	client.once('close', () => upstream.destroy());
}

// Starts the proxy on an ephemeral loopback port. allowAddress defaults to the
// production policy; only tests pass another, as they do for egressGet.
export async function startEgressProxy(allowAddress: AddressPolicy = isPublicAddress): Promise<EgressProxy> {
	const sockets = new Set<Socket>();
	const server = createServer((request, response) => forward(allowAddress, request, response));
	server.on('connect', (request: IncomingMessage, client: Socket, head: Buffer) => tunnel(allowAddress, request, client, head));
	// No 'upgrade' listener: a WebSocket upgrade sent as a plain proxied request
	// is closed by Node rather than forwarded unchecked.
	server.on('connection', (socket: Socket) => {
		sockets.add(socket);
		socket.once('close', () => sockets.delete(socket));
	});
	server.on('clientError', (_error, socket) => socket.destroy());
	const { promise, resolve, reject } = Promise.withResolvers<void>();
	server.once('error', reject);
	server.listen(0, '127.0.0.1', () => resolve());
	await promise;
	const { port } = server.address() as AddressInfo;
	return {
		url: `http://127.0.0.1:${port}`,
		close: () => {
			const closed = new Promise<void>((done) => server.close(() => done()));
			for (const socket of sockets) socket.destroy();
			return closed;
		},
	};
}
