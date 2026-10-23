// The egress proxy that fetch's browser runs behind: every connection the
// browser makes passes the public-address guard, loopback included.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import dns from 'node:dns';
import type { LookupAddress } from 'node:dns';
import { existsSync } from 'node:fs';
import { createServer, request } from 'node:http';
import type { Server } from 'node:http';
import { syncBuiltinESMExports } from 'node:module';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { CHECKOUT_ROOT } from '../roles.ts';
import { isPublicAddress } from './egress.ts';
import { startEgressProxy } from './proxy.ts';

// A server on a loopback address that records every request it receives.
async function countingServer(t: TestContext, host: string, handler?: (path: string) => { status: number; headers?: Record<string, string>; body?: string }) {
	const hits: string[] = [];
	const server: Server = createServer((incoming, response) => {
		hits.push(incoming.url ?? '');
		const answer = handler?.(incoming.url ?? '') ?? { status: 200, body: 'loopback secret' };
		response.writeHead(answer.status, { 'content-type': 'text/html', ...answer.headers }).end(answer.body ?? '');
	});
	server.on('upgrade', (incoming, socket) => {
		hits.push(incoming.url ?? '');
		socket.destroy();
	});
	await new Promise<void>((resolve) => server.listen(0, host, resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	const { port } = server.address() as AddressInfo;
	return { port, hits };
}

async function proxy(t: TestContext, allowAddress?: (address: string) => boolean) {
	const started = await startEgressProxy(allowAddress);
	t.after(() => started.close());
	return new URL(started.url);
}

// One request through the proxy: a plain absolute-form GET, or a CONNECT.
function throughProxy(proxyURL: URL, method: 'GET' | 'CONNECT', target: string): Promise<number> {
	const { promise, resolve, reject } = Promise.withResolvers<number>();
	const sent = request({ host: proxyURL.hostname, port: proxyURL.port, method, path: target, agent: false });
	sent.once('response', (response) => {
		response.resume();
		resolve(response.statusCode ?? 0);
	});
	sent.once('connect', (response, socket) => {
		socket.destroy();
		resolve(response.statusCode ?? 0);
	});
	sent.once('error', reject);
	sent.end();
	return promise;
}

const serverOnly = (address: string) => address === '::1' || isPublicAddress(address);

test('the proxy refuses plain and CONNECT requests to loopback addresses and local names', async (t) => {
	const secret = await countingServer(t, '127.0.0.1');
	const proxyURL = await proxy(t);

	assert.equal(await throughProxy(proxyURL, 'GET', `http://127.0.0.1:${secret.port}/plain`), 403);
	assert.equal(await throughProxy(proxyURL, 'CONNECT', `127.0.0.1:${secret.port}`), 403);
	assert.equal(await throughProxy(proxyURL, 'GET', `http://localhost:${secret.port}/name`), 403);
	assert.equal(await throughProxy(proxyURL, 'CONNECT', `localhost:${secret.port}`), 403);
	assert.equal(await throughProxy(proxyURL, 'GET', `http://[::ffff:7f00:1]:${secret.port}/mapped`), 403);
	assert.deepEqual(secret.hits, []);
});

test('the proxy forwards plain and CONNECT requests to an allowed address', async (t) => {
	const allowed = await countingServer(t, '::1');
	const proxyURL = await proxy(t, serverOnly);

	assert.equal(await throughProxy(proxyURL, 'GET', `http://[::1]:${allowed.port}/plain`), 200);
	assert.equal(await throughProxy(proxyURL, 'CONNECT', `[::1]:${allowed.port}`), 200);
	assert.deepEqual(allowed.hits, ['/plain']);
});

test('a public-looking name that resolves to loopback is refused when the proxy connects', async (t) => {
	const secret = await countingServer(t, '127.0.0.1');
	t.mock.method(dns, 'lookup', (_hostname: string, _options: unknown, callback: (error: Error | null, addresses: LookupAddress[]) => void) => {
		callback(null, [{ address: '127.0.0.1', family: 4 }]);
	});
	syncBuiltinESMExports();
	t.after(() => {
		t.mock.restoreAll();
		syncBuiltinESMExports();
	});
	const proxyURL = await proxy(t);

	assert.equal(await throughProxy(proxyURL, 'GET', `http://rebind.example.com:${secret.port}/plain`), 403);
	assert.equal(await throughProxy(proxyURL, 'CONNECT', `rebind.example.com:${secret.port}`), 403);
	assert.deepEqual(secret.hits, []);
});

// The real browser behind fetch: Crawl4AI's Chromium, through the scraping
// seam, with the proxy the fetch tool starts. Runs where Crawl4AI is installed
// (SPOTLIGHT_CRAWL4AI_PYTHON, or the uv tool the seam itself uses).
function crawl4aiPython(): string | undefined {
	const override = process.env.SPOTLIGHT_CRAWL4AI_PYTHON;
	if (override) return existsSync(override) ? override : undefined;
	const home = process.env.HOME ?? '';
	for (const candidate of [
		join(home, '.local', 'share', 'uv', 'tools', 'crawl4ai', 'bin', 'python'),
		join(home, 'AppData', 'Roaming', 'uv', 'tools', 'crawl4ai', 'Scripts', 'python.exe'),
	]) {
		if (existsSync(candidate)) return candidate;
	}
	return undefined;
}

const python = crawl4aiPython();

test('a page fetched by the seam browser cannot reach a loopback server through subresources, fetch(), redirects or WebSockets', { skip: python ? false : 'Crawl4AI is not installed', timeout: 240_000 }, async (t) => {
	const secret = await countingServer(t, '127.0.0.1');
	const s = secret.port;
	const page = await countingServer(t, '::1', (path) => {
		if (path === '/redirect') return { status: 302, headers: { location: `http://127.0.0.1:${s}/redirect` } };
		if (path !== '/') return { status: 404 };
		return {
			status: 200,
			body: `<html><body><h1>Public page body</h1><p>Ordinary article text for the scraper.</p>
<img src="http://127.0.0.1:${s}/img"><img src="http://localhost:${s}/name">
<iframe src="/redirect"></iframe>
<script>
fetch('http://127.0.0.1:${s}/fetch').catch(() => {});
try { new WebSocket('ws://127.0.0.1:${s}/ws'); } catch (e) {}
</script></body></html>`,
		};
	});
	const proxyURL = await proxy(t, serverOnly);

	const { promise, resolve } = Promise.withResolvers<{ code: number; stdout: string; stderr: string }>();
	execFile(
		python as string,
		['-m', 'integrations.scraping', '--json', '--no-escalate', '--no-tor', '--', `http://[::1]:${page.port}/`],
		{ cwd: CHECKOUT_ROOT, timeout: 200_000, env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '', SPOTLIGHT_BROWSER_PROXY: proxyURL.href.replace(/\/$/, '') } },
		(error, stdout, stderr) => resolve({ code: error ? Number(error.code ?? 1) : 0, stdout, stderr }),
	);
	const run = await promise;
	// Give late subrequests time to arrive before counting.
	await new Promise((done) => setTimeout(done, 1000));

	assert.equal(run.code, 0, run.stderr.slice(-2000));
	assert.match(JSON.parse(run.stdout).markdown, /Public page body/);
	assert.ok(page.hits.includes('/'), 'the page loaded');
	assert.deepEqual(secret.hits, []);
});
