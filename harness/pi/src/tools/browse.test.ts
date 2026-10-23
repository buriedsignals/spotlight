// The request guard inside the browse script must hold every request the
// harness's own egress guard would refuse. The script is the contract with
// dev-browser, so it is run here as written against a stand-in browser that
// records the routes it installs; each probe URL goes through those routes the
// way Playwright would hand a request to them.
import assert from 'node:assert/strict';
import test from 'node:test';
import { browseScript } from './browse.ts';
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
