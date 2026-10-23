// The egress guard behind fetch, browse and http_get: only http(s) URLs whose
// addresses are public, checked again on every redirect hop.
import assert from 'node:assert/strict';
import dns from 'node:dns';
import type { LookupAddress } from 'node:dns';
import { createServer } from 'node:http';
import { syncBuiltinESMExports } from 'node:module';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { EgressRefused, assertPublicDestination, egressGet, isPublicAddress } from './egress.ts';

test('loopback, private, link-local, metadata, CGNAT and mapped forms are not public; ordinary addresses are', () => {
	for (const address of [
		'127.0.0.1', '127.8.9.10', '10.0.0.1', '172.16.4.1', '192.168.1.1', '169.254.169.254', '100.64.0.1',
		'0.0.0.0', '224.0.0.1', '255.255.255.255', '::1', '::', 'fe80::1', 'fd00::1', '::ffff:127.0.0.1',
		'::ffff:169.254.169.254', '::ffff:7f00:1', '::ffff:a9fe:a9fe', '64:ff9b::a00:1',
	]) {
		assert.equal(isPublicAddress(address), false, address);
	}
	for (const address of ['93.184.215.14', '8.8.8.8', '2606:4700::1111']) {
		assert.equal(isPublicAddress(address), true, address);
	}
});

test('URLs naming loopback, private, metadata or .local hosts are refused before any connection', async () => {
	for (const url of [
		'http://127.0.0.1/',
		'http://10.0.0.1/',
		'http://169.254.169.254/latest/meta-data/',
		'http://[::1]/',
		'http://2130706433/',
		'http://localhost:8080/',
		'http://printer.local/',
		'http://PRINTER.LOCAL./',
		'http://[::ffff:7f00:1]/',
		'http://[::ffff:127.0.0.1]/',
		'http://router.lan/',
		'http://metadata.google.internal/',
		'http://nas.home.arpa/',
		'file:///etc/passwd',
		'ftp://example.com/',
		'http://user:secret@example.com/',
	]) {
		await assert.rejects(egressGet(url), EgressRefused, url);
	}
});

// The only way to observe a redirect without the network is a server on a
// loopback address. The guard under test is unchanged; the policy admits that
// one server address and delegates every other address to isPublicAddress.
async function redirectServer(t: TestContext, location: (path: string) => string): Promise<{ origin: string; hits: string[] }> {
	const hits: string[] = [];
	const server: Server = createServer((request, response) => {
		hits.push(request.url ?? '');
		if (request.url === '/final') {
			response.writeHead(200, { 'content-type': 'text/plain' }).end('public body');
			return;
		}
		response.writeHead(302, { location: location(request.url ?? '') }).end();
	});
	await new Promise<void>((resolve) => server.listen(0, '::1', resolve));
	t.after(() => new Promise<void>((resolve) => server.close(() => resolve())));
	const { port } = server.address() as AddressInfo;
	return { origin: `http://[::1]:${port}`, hits };
}

const serverOnly = (address: string) => address === '::1' || isPublicAddress(address);

test('a redirect to 127.0.0.1, 10.0.0.1 or 169.254.169.254 is refused at that hop', async (t) => {
	for (const target of ['http://127.0.0.1/admin', 'http://10.0.0.1/', 'http://169.254.169.254/latest/meta-data/']) {
		const { origin, hits } = await redirectServer(t, () => target);
		await assert.rejects(egressGet(`${origin}/start`, { allowAddress: serverOnly }), EgressRefused, target);
		assert.deepEqual(hits, ['/start'], target);
	}
});

test('a redirect chain between allowed addresses is followed and reported', async (t) => {
	const { origin, hits } = await redirectServer(t, (path) => (path === '/start' ? '/middle' : '/final'));

	const response = await egressGet(`${origin}/start`, { allowAddress: serverOnly, readBody: true });

	assert.equal(response.status, 200);
	assert.equal(response.finalURL, `${origin}/final`);
	assert.equal(response.body?.toString('utf8'), 'public body');
	assert.deepEqual(hits, ['/start', '/middle', '/final']);
});

// The resolver answers below come from a stand-in for the system resolver
// (node:dns lookup, which the guard calls); the guard itself is unchanged.
function resolveAs(t: TestContext, answers: Record<string, string[]>): string[] {
	const asked: string[] = [];
	t.mock.method(dns, 'lookup', (hostname: string, _options: unknown, callback: (error: Error | null, addresses: LookupAddress[]) => void) => {
		asked.push(hostname);
		const list = answers[hostname];
		if (!list) return callback(Object.assign(new Error(`ENOTFOUND ${hostname}`), { code: 'ENOTFOUND' }), []);
		callback(null, list.map((address) => ({ address, family: address.includes(':') ? 6 : 4 })));
	});
	syncBuiltinESMExports();
	t.after(() => {
		t.mock.restoreAll();
		syncBuiltinESMExports();
	});
	return asked;
}

test('a public-looking name that resolves to loopback, metadata or a mix with them is refused at connection time', async (t) => {
	const asked = resolveAs(t, {
		'loopback.example.com': ['127.0.0.1'],
		'metadata.example.com': ['169.254.169.254'],
		'mapped.example.com': ['::ffff:7f00:1'],
		'mixed.example.com': ['93.184.215.14', '127.0.0.1'],
	});
	for (const host of ['loopback.example.com', 'metadata.example.com', 'mapped.example.com', 'mixed.example.com']) {
		await assert.rejects(egressGet(`http://${host}/`), /not a public address/, host);
		await assert.rejects(assertPublicDestination(`https://${host}/after-render`), /not a public address/, host);
	}
	assert.ok(asked.includes('mixed.example.com'));
});

test('a name is connected to at the address the guard checked', async (t) => {
	const { origin, hits } = await redirectServer(t, () => '/final');
	const port = new URL(origin).port;
	resolveAs(t, { 'allowed.example.com': ['::1'] });

	const response = await egressGet(`http://allowed.example.com:${port}/final`, { allowAddress: serverOnly, readBody: true });

	assert.equal(response.body?.toString('utf8'), 'public body');
	assert.deepEqual(hits, ['/final']);
});

test('a redirect loop stops after the redirect cap', async (t) => {
	const { origin, hits } = await redirectServer(t, (path) => `/hop${Number(path.slice(4) || 0) + 1}`);

	await assert.rejects(egressGet(`${origin}/hop0`, { allowAddress: serverOnly }), /more than 8 redirects/);
	assert.equal(hits.length, 9);
});

test('a body larger than the cap is cut at the cap and reported as truncated', async (t) => {
	const { origin } = await redirectServer(t, () => '/final');

	const response = await egressGet(`${origin}/final`, { allowAddress: serverOnly, readBody: true, maxBytes: 6 });

	assert.equal(response.body?.toString('utf8'), 'public');
	assert.equal(response.truncated, true);
});
