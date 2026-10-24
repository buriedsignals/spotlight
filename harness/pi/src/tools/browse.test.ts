// The request guard inside the browse script must hold every request the
// harness's own egress guard would refuse. The script is the contract with
// dev-browser, so it is run here as written against a stand-in browser that
// records the routes it installs; each probe URL goes through those routes the
// way Playwright would hand a request to them. The last test runs the script in
// the real browser browse launches, behind the egress proxy.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import dns from 'node:dns';
import type { LookupAddress } from 'node:dns';
import { createServer } from 'node:http';
import type { Server } from 'node:http';
import { syncBuiltinESMExports } from 'node:module';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { browseScript, chromiumExecutable, runGuardedBrowse } from './browse.ts';
import { NOT_PUBLIC_V4, isPublicAddress } from './egress.ts';

type Route = { request: () => { url: () => string }; abort: () => void; continue: () => void; fallback: () => void };
type Handler = (route: Route) => unknown;

async function installedRoutes(): Promise<Array<[RegExp | string, Handler]>> {
	const routes: Array<[RegExp | string, Handler]> = [];
	const page = {
		route: async (matcher: RegExp | string, handler: Handler) => void routes.push([matcher, handler]),
		goto: async () => null,
		url: () => 'https://example.com/',
		title: async () => '',
		evaluate: async () => '',
	};
	const browser = { newPage: async () => page };
	const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
	const run = new AsyncFunction('browser', 'saveScreenshot', 'console', 'setTimeout', browseScript('https://example.com/', 'text', 0, 'shot.png'));
	await run(browser, async () => '', { log: () => {} }, setTimeout);
	return routes;
}

async function blocked(routes: Array<[RegExp | string, Handler]>, url: string): Promise<boolean> {
	for (const [matcher, handler] of routes) {
		const matches = matcher === '**/*' ? true : matcher instanceof RegExp ? matcher.test(url) : assert.fail(`unexpected matcher ${matcher}`);
		if (!matches) continue;
		let outcome: 'abort' | 'continue' | 'fallback' | undefined;
		await handler({
			request: () => ({ url: () => url }),
			abort: () => void (outcome = 'abort'),
			continue: () => void (outcome = 'continue'),
			fallback: () => void (outcome = 'fallback'),
		});
		if (outcome === 'abort') return true;
		if (outcome === 'continue') return false;
	}
	return false;
}

function ipv4(value: number): string {
	return [24, 16, 8, 0].map((shift) => Math.floor(value / 2 ** shift) % 256).join('.');
}

test('the browser request guard holds every IPv4 address the egress guard refuses, and no public one', async () => {
	const routes = await installedRoutes();
	const probes = new Set(['8.8.8.8', '93.184.215.14', '1.1.1.1']);
	for (const [network, prefix] of NOT_PUBLIC_V4) {
		const start = network.split('.').reduce((total, part) => total * 256 + Number(part), 0);
		const end = start + 2 ** (32 - prefix) - 1;
		for (const value of [start - 1, start, start + 1, Math.floor((start + end) / 2), end - 1, end, end + 1]) {
			if (value >= 0 && value < 2 ** 32) probes.add(ipv4(value));
		}
	}
	for (const address of probes) {
		assert.equal(await blocked(routes, `http://${address}:8080/x.js`), !isPublicAddress(address), address);
	}
});

test('the browser request guard holds local and single-label names and IPv6 literals, not public names', async () => {
	const routes = await installedRoutes();
	for (const url of [
		'http://localhost:4173/review',
		'http://app.localhost/',
		'http://printer.local/',
		'http://metadata.google.internal/computeMetadata/v1/',
		'http://router.lan/',
		'http://nas.home.arpa/',
		'http://intranet/',
		'http://NAS.LOCAL./',
		'ws://[::1]:4173/',
		'https://user@169.254.169.254/latest/',
	]) {
		assert.equal(await blocked(routes, url), true, url);
	}
	for (const url of ['https://example.com/', 'https://cdn.example.org/app.js?next=http://127.0.0.1/', 'data:text/plain,hi']) {
		assert.equal(await blocked(routes, url), false, url);
	}
});

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

function browserAvailable(): string | false {
	try {
		chromiumExecutable(process.env);
		execFileSync('dev-browser', ['--help'], { stdio: 'ignore' });
		return false;
	} catch {
		return 'dev-browser or its Chromium is not installed';
	}
}

test('a page opened by browse cannot reach a loopback server through subresources, fetch(), redirects, WebSockets or a name that resolves to loopback', { skip: browserAvailable(), timeout: 180_000 }, async (t) => {
	const secret = await countingServer(t, '127.0.0.1');
	const s = secret.port;
	const page = await countingServer(t, '::1', (path) => {
		if (path === '/redirect') return { status: 302, headers: { location: `http://127.0.0.1:${s}/redirect` } };
		if (path !== '/') return { status: 404 };
		return {
			status: 200,
			body: `<html><body><h1>Public page body</h1>
<img src="http://127.0.0.1:${s}/img"><img src="http://rebind.example.com:${s}/rebind-img">
<iframe src="/redirect"></iframe>
<script>
fetch('http://rebind.example.com:${s}/rebind-fetch').catch(() => {});
try { new WebSocket('ws://127.0.0.1:${s}/ws'); } catch (e) {}
try { new WebSocket('ws://rebind.example.com:${s}/rebind-ws'); } catch (e) {}
</script></body></html>`,
		};
	});
	// Stand-in DNS for the proxy, which resolves every name the browser asks for:
	// the page's public-looking name points at the allowed page server, the
	// other name at the loopback secret.
	t.mock.method(dns, 'lookup', (hostname: string, _options: unknown, callback: (error: Error | null, addresses: LookupAddress[]) => void) => {
		if (hostname === 'page.example.com') return callback(null, [{ address: '::1', family: 6 }]);
		callback(null, [{ address: '127.0.0.1', family: 4 }]);
	});
	syncBuiltinESMExports();
	t.after(() => {
		t.mock.restoreAll();
		syncBuiltinESMExports();
	});

	const output = await runGuardedBrowse(browseScript(`http://page.example.com:${page.port}/`, 'text', 2000, 'unused.png'), {
		env: process.env,
		cwd: tmpdir(),
		allowAddress: (address) => address === '::1' || isPublicAddress(address),
	});
	await new Promise((done) => setTimeout(done, 1000));

	assert.match(output, /Public page body/);
	assert.ok(page.hits.includes('/'), 'the page loaded');
	assert.deepEqual(secret.hits, []);
});

test('the browser browse starts gets only the program environment, never a key of the process that runs it', { skip: process.platform === 'win32' ? 'a shell stand-in for Chromium' : false }, async () => {
	const dir = mkdtempSync(join(tmpdir(), 'spotlight-browse-env-'));
	const seen = join(dir, 'env');
	const chromium = join(dir, 'chromium');
	writeFileSync(chromium, `#!/bin/sh\nenv > '${seen}'\necho 'DevTools listening on ws://127.0.0.1:9/devtools/browser/x' >&2\nexec sleep 30\n`, { mode: 0o755 });
	process.env.SPOTLIGHT_PROVIDER_API_KEY = 'provider-key-0123456789';
	try {
		// dev-browser cannot drive the stand-in; only what the browser was started with matters here.
		await runGuardedBrowse('', { env: { PATH: process.env.PATH ?? '', SPOTLIGHT_BROWSE_CHROMIUM: chromium }, cwd: dir }).catch(() => undefined);
	} finally {
		delete process.env.SPOTLIGHT_PROVIDER_API_KEY;
	}
	assert.ok(!readFileSync(seen, 'utf8').includes('provider-key-0123456789'), 'the browser saw the provider key');
});
