// Egress guard for every tool that reaches the web on a URL the model chose.
// Only http(s) URLs whose addresses are public are allowed. A hostname is
// resolved inside the connection's own lookup, so the address that is checked
// is the address that is connected to; every redirect hop is checked again.
import { lookup as dnsLookup } from 'node:dns';
import type { LookupAddress, LookupOptions } from 'node:dns';
import { request as httpRequest } from 'node:http';
import type { IncomingHttpHeaders, IncomingMessage } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { BlockList, isIP } from 'node:net';

export class EgressRefused extends Error {}

const NOT_PUBLIC = new BlockList();
for (const [network, prefix] of [
	['0.0.0.0', 8], // "this" network
	['10.0.0.0', 8], // private
	['100.64.0.0', 10], // carrier-grade NAT
	['127.0.0.0', 8], // loopback
	['169.254.0.0', 16], // link-local, cloud metadata (169.254.169.254)
	['172.16.0.0', 12], // private
	['192.0.0.0', 24], // protocol assignments
	['192.0.2.0', 24], // documentation
	['192.88.99.0', 24], // 6to4 relay
	['192.168.0.0', 16], // private
	['198.18.0.0', 15], // benchmarking
	['198.51.100.0', 24], // documentation
	['203.0.113.0', 24], // documentation
	['224.0.0.0', 4], // multicast
	['240.0.0.0', 4], // reserved, broadcast
] as const) {
	NOT_PUBLIC.addSubnet(network, prefix, 'ipv4');
}
for (const [network, prefix] of [
	['::', 96], // unspecified, loopback, IPv4-compatible
	['64:ff9b::', 96], // NAT64: reaches IPv4 space through a translator
	['64:ff9b:1::', 48],
	['100::', 64], // discard
	['2001:db8::', 32], // documentation
	['2002::', 16], // 6to4: embeds an IPv4 address
	['fc00::', 7], // unique local
	['fe80::', 10], // link-local
	['fec0::', 10], // site-local (deprecated)
	['ff00::', 8], // multicast
] as const) {
	NOT_PUBLIC.addSubnet(network, prefix, 'ipv6');
}

const MAPPED_V4 = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i;

export function isPublicAddress(address: string): boolean {
	const bare = address.replace(/^\[|\]$/g, '').split('%')[0];
	const mapped = MAPPED_V4.exec(bare);
	if (mapped) return isPublicAddress(mapped[1]);
	if (/^::ffff:/i.test(bare)) return false; // hex-form mapped address
	const family = isIP(bare);
	if (family === 0) return false;
	return !NOT_PUBLIC.check(bare, family === 4 ? 'ipv4' : 'ipv6');
}

export type AddressPolicy = (address: string) => boolean;

// Checks everything that can be checked without the network. Returns the
// parsed URL; hostnames are checked again at connection time.
export function checkURL(raw: string, allowAddress: AddressPolicy = isPublicAddress): URL {
	let url: URL;
	try {
		url = new URL(raw);
	} catch {
		throw new EgressRefused(`refused: not a valid URL: ${raw.slice(0, 200)}`);
	}
	if (url.protocol !== 'http:' && url.protocol !== 'https:') {
		throw new EgressRefused(`refused: only http(s) URLs are allowed, not ${url.protocol}`);
	}
	if (url.username || url.password) throw new EgressRefused('refused: URLs with credentials are not allowed');
	const host = url.hostname.replace(/\.$/, '').toLowerCase();
	const literal = host.replace(/^\[|\]$/g, '');
	if (isIP(literal)) {
		if (!allowAddress(literal)) throw new EgressRefused(`refused: ${literal} is not a public address`);
		return url;
	}
	if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || !host.includes('.')) {
		throw new EgressRefused(`refused: ${host} is a local name`);
	}
	return url;
}

function guardedLookup(allowAddress: AddressPolicy) {
	return (
		hostname: string,
		options: LookupOptions,
		callback: (error: NodeJS.ErrnoException | null, address: string | LookupAddress[], family?: number) => void,
	) => {
		dnsLookup(hostname, { ...options, all: true }, (error, addresses) => {
			if (error) return callback(error, []);
			const list = addresses as LookupAddress[];
			const refused = list.find((entry) => !allowAddress(entry.address));
			if (list.length === 0 || refused) {
				return callback(new EgressRefused(`refused: ${hostname} resolves to ${refused?.address ?? 'nothing'}, which is not a public address`), []);
			}
			if (options.all) return callback(null, list);
			callback(null, list[0].address, list[0].family);
		});
	};
}

export type EgressOptions = {
	readBody?: boolean;
	maxBytes?: number;
	maxRedirects?: number;
	timeoutMs?: number;
	signal?: AbortSignal;
	headers?: Record<string, string>;
	// Which addresses count as reachable. Production uses isPublicAddress.
	allowAddress?: AddressPolicy;
};

export type EgressResponse = {
	finalURL: string;
	redirects: string[];
	status: number;
	headers: IncomingHttpHeaders;
	body?: Buffer;
	truncated: boolean;
};

function once(url: URL, options: EgressOptions, allowAddress: AddressPolicy): Promise<IncomingMessage> {
	const { promise, resolve, reject } = Promise.withResolvers<IncomingMessage>();
	const send = url.protocol === 'https:' ? httpsRequest : httpRequest;
	const request = send(url, {
		method: 'GET',
		agent: false,
		lookup: guardedLookup(allowAddress) as never,
		headers: { 'user-agent': 'spotlight-harness/1', accept: '*/*', ...options.headers },
		signal: options.signal,
		timeout: options.timeoutMs ?? 30_000,
	});
	request.once('response', resolve);
	request.once('timeout', () => request.destroy(new Error(`timed out fetching ${url.href}`)));
	request.once('error', reject);
	request.end();
	return promise;
}

async function readCapped(response: IncomingMessage, maxBytes: number): Promise<{ body: Buffer; truncated: boolean }> {
	const parts: Buffer[] = [];
	let size = 0;
	for await (const part of response) {
		const chunk = part as Buffer;
		if (size + chunk.length > maxBytes) {
			parts.push(chunk.subarray(0, maxBytes - size));
			response.destroy();
			return { body: Buffer.concat(parts), truncated: true };
		}
		parts.push(chunk);
		size += chunk.length;
	}
	return { body: Buffer.concat(parts), truncated: false };
}

// GET with redirects followed by hand: every hop's URL and resolved address
// pass the same guard as the first. Without readBody the body is discarded,
// which is how fetch and browse learn where a URL really leads.
export async function egressGet(raw: string, options: EgressOptions = {}): Promise<EgressResponse> {
	const allowAddress = options.allowAddress ?? isPublicAddress;
	const maxRedirects = options.maxRedirects ?? 8;
	const redirects: string[] = [];
	let url = checkURL(raw, allowAddress);
	for (;;) {
		const response = await once(url, options, allowAddress);
		const status = response.statusCode ?? 0;
		const location = response.headers.location;
		if (status >= 300 && status < 400 && location) {
			response.resume();
			if (redirects.length >= maxRedirects) throw new EgressRefused(`refused: more than ${maxRedirects} redirects`);
			redirects.push(url.href);
			url = checkURL(new URL(location, url).href, allowAddress);
			continue;
		}
		if (!options.readBody) {
			response.destroy();
			return { finalURL: url.href, redirects, status, headers: response.headers, truncated: false };
		}
		const { body, truncated } = await readCapped(response, options.maxBytes ?? 20 * 1024 * 1024);
		return { finalURL: url.href, redirects, status, headers: response.headers, body, truncated };
	}
}

// For a tool whose backing program does its own fetching: walks the redirect
// chain under the guard and returns the URL the program should be given.
export async function publicDestination(raw: string, signal?: AbortSignal): Promise<string> {
	return (await egressGet(raw, { signal })).finalURL;
}

// After a backing program reports the URL it ended on, that URL must pass the
// guard too, or its content is discarded.
export async function assertPublicDestination(raw: string, signal?: AbortSignal): Promise<void> {
	const url = checkURL(raw);
	const host = url.hostname.replace(/^\[|\]$/g, '');
	if (isIP(host)) return;
	const { promise, resolve, reject } = Promise.withResolvers<void>();
	guardedLookup(isPublicAddress)(host, { all: true, family: 0 }, (error) => (error ? reject(error) : resolve()));
	if (signal?.aborted) throw signal.reason;
	return promise;
}

