// browse: render one public page in a real browser and capture its text, HTML
// or a screenshot. Backed by the dev-browser script runner (`dev-browser run
// <script>`), which `integrations/dev-browser` documents; the
// `integrations/browse` folder is only a manifest for the optional browse.sh
// CLI and backs nothing here.
//
// The model supplies a URL, never a script: the harness writes the whole
// script, so the browser opens exactly one guarded page. Egress: the harness
// walks the URL's redirect chain under the public-address guard first. For each
// call it then starts the egress proxy (proxy.ts) and its own headless Chromium
// behind it, with loopback proxying forced on (`--proxy-bypass-list=<-loopback>`),
// and runs the script with `dev-browser --connect` to that Chromium. dev-browser
// takes no proxy or launch flags for the browsers it launches itself, so this is
// the only way to put its browser behind the proxy. Every connection the page
// makes (subresources, scripts' fetch(), redirects, WebSockets, service
// workers) passes the same guard, and the proxy resolves hostnames itself, so a
// public name that resolves to a private address is refused too. Inside the
// browser a route guard still holds every request whose host is an IPv4
// literal in egress.ts's non-public table, any bracketed IPv6 literal, a
// single-label name, or a local name (localhost, .local, .internal, .lan,
// .home.arpa), as defence in depth; the page's final URL must pass the guard
// again before anything is returned. WebRTC (UDP) is not covered by an HTTP
// proxy. In Tor mode browse refuses: the browser would connect from the
// operator's address.
//
// Chromium: SPOTLIGHT_BROWSE_CHROMIUM when set, otherwise the Chromium that
// `dev-browser install` downloads, located through dev-browser's own
// playwright-core (in ~/.dev-browser), so a member who has dev-browser has it.
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { LOCAL_NAME_SUFFIXES, NOT_PUBLIC_V4, assertPublicDestination, isPublicAddress, publicDestination, refuseDirectWhenAnonymized } from './egress.ts';
import type { AddressPolicy } from './egress.ts';
import { webURL } from './fetch.ts';
import { startEgressProxy } from './proxy.ts';
import { checkedParams, jsonResult, programEnv, programOutput, runFixed, savePath } from './run.ts';
import type { Env, ToolContext } from './run.ts';

const MAX_RETURNED_CHARS = 30_000;
const MARKER = 'SPOTLIGHT_BROWSE_RESULT ';

const params = Type.Object(
	{
		url: webURL,
		capture: Type.Optional(Type.Union([Type.Literal('text'), Type.Literal('html'), Type.Literal('screenshot')])),
		wait_ms: Type.Optional(Type.Integer({ minimum: 0, maximum: 15_000 })),
		save_as: Type.Optional(savePath),
	},
	{ additionalProperties: false },
);

type Input = { url: string; capture?: 'text' | 'html' | 'screenshot'; wait_ms?: number; save_as?: string };

// Inside the browser a route guard holds requests by URL alone. In the
// dev-browser sandbox a handler that calls route.continue() or fallback(), or a
// function matcher, stalls or breaks the navigation, so the guard is one
// RegExp whose handler only aborts. The
// pattern is built from egress.ts's non-public IPv4 table and local-name list.
// The browser has already normalised the URL (integer and hex IPv4 forms
// become dotted quads) before a route sees it.
function octets(from: number, to: number): string {
	return Array.from({ length: to - from + 1 }, (_, index) => String(from + index)).join('|');
}

function ipv4Pattern(network: string, prefix: number): string {
	const parts = network.split('.').map(Number);
	const fixed = Math.floor(prefix / 8);
	const span = 2 ** (8 - (prefix % 8));
	return parts
		.map((part, index) => {
			if (index < fixed) return String(part);
			if (index === fixed && prefix % 8 !== 0) return `(?:${octets(part, part + span - 1)})`;
			return '\\d{1,3}';
		})
		.join('\\.');
}

const BLOCKED_HOSTS = [
	'\\[[^\\]]*\\]', // every IPv6 literal
	...NOT_PUBLIC_V4.map(([network, prefix]) => ipv4Pattern(network, prefix)),
	'[^/:?#.@]+', // single-label names
	`(?:[^/:?#@]*\\.)?(?:${LOCAL_NAME_SUFFIXES.map((suffix) => suffix.replace('.', '\\.')).join('|')})`,
];

const BLOCKED_REQUEST = `^[a-z][a-z0-9+.-]*://(?:[^/@?#]*@)?(?:${BLOCKED_HOSTS.join('|')})\\.?(?:[:/?#]|$)`;

// The whole script dev-browser runs for one browse call.
export function browseScript(url: string, capture: string, waitMs: number, screenshotName: string): string {
	return `const page = await browser.newPage();
await page.route(new RegExp(${JSON.stringify(BLOCKED_REQUEST)}, 'i'), (route) => route.abort());
const response = await page.goto(${JSON.stringify(url)}, { waitUntil: 'domcontentloaded', timeout: 45000 });
await new Promise((done) => setTimeout(done, ${waitMs}));
const result = { final_url: page.url(), status: response ? response.status() : null, title: await page.title() };
if (${JSON.stringify(capture)} === 'text') result.content = await page.evaluate(() => document.body ? document.body.innerText : '');
if (${JSON.stringify(capture)} === 'html') result.content = await page.content();
if (${JSON.stringify(capture)} === 'screenshot') result.screenshot = await saveScreenshot(await page.screenshot({ fullPage: true }), ${JSON.stringify(screenshotName)});
console.log(${JSON.stringify(MARKER)} + JSON.stringify(result));
`;
}

// The Chromium browse launches. A configured path must exist; otherwise the
// one dev-browser installs, as dev-browser's own playwright-core names it.
export function chromiumExecutable(env: Env): string {
	const configured = env.SPOTLIGHT_BROWSE_CHROMIUM;
	if (configured) {
		if (!existsSync(configured)) throw new Error(`browse: SPOTLIGHT_BROWSE_CHROMIUM does not exist: ${configured}`);
		return configured;
	}
	const runtime = join(env.HOME ?? homedir(), '.dev-browser', 'node_modules');
	try {
		const playwright = createRequire(join(runtime, 'index.js'))('playwright-core') as { chromium: { executablePath: () => string } };
		const path = playwright.chromium.executablePath();
		if (existsSync(path)) return path;
	} catch {
		// No dev-browser runtime: fall through to the error below.
	}
	throw new Error('browse: no Chromium found. Run `dev-browser install`, or set SPOTLIGHT_BROWSE_CHROMIUM to a Chromium or Chrome executable.');
}

type GuardedChromium = { endpoint: string; close: () => Promise<void> };

// Starts a headless Chromium whose every connection, loopback included, goes
// through `proxyURL`, with a fresh profile and DevTools on an ephemeral
// loopback port. Resolves with the DevTools WebSocket endpoint.
async function startGuardedChromium(executable: string, proxyURL: string, env: Env, signal?: AbortSignal): Promise<GuardedChromium> {
	const profile = mkdtempSync(join(tmpdir(), 'spotlight-browse-profile-'));
	const child = spawn(
		executable,
		[
			'--headless',
			`--proxy-server=${proxyURL}`,
			'--proxy-bypass-list=<-loopback>',
			'--remote-debugging-port=0',
			`--user-data-dir=${profile}`,
			'--no-first-run',
			'--no-default-browser-check',
			'--disable-background-networking',
			'--disable-component-update',
			'--disable-sync',
			'--use-mock-keychain',
			'--password-store=basic',
			'about:blank',
		],
		{ stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true, env: programEnv(env) },
	);
	const exited = new Promise<void>((done) => child.once('exit', () => done()));
	const close = async () => {
		if (child.exitCode === null && child.signalCode === null) {
			child.kill('SIGTERM');
			const timer = setTimeout(() => child.kill('SIGKILL'), 5_000);
			await exited;
			clearTimeout(timer);
		}
		rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
	};
	let stderr = '';
	const { promise, resolve, reject } = Promise.withResolvers<string>();
	child.stderr.setEncoding('utf8');
	// Keep draining stderr for the browser's whole life so it never blocks on a full pipe.
	child.stderr.on('data', (chunk: string) => {
		stderr = (stderr + chunk).slice(-8_000);
		const listening = /DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/devtools\/browser\/[\w-]+)/.exec(stderr);
		if (listening) resolve(listening[1]);
	});
	child.once('error', reject);
	child.once('exit', (code) => reject(new Error(`Chromium exited (${code}) before it was ready: ${stderr.slice(-2_000)}`)));
	const timer = setTimeout(() => reject(new Error('Chromium did not start within 30 seconds')), 30_000);
	const onAbort = () => reject(new Error('browse was stopped'));
	signal?.addEventListener('abort', onAbort, { once: true });
	try {
		return { endpoint: await promise, close };
	} catch (error) {
		await close();
		throw new Error(`browse: ${(error as Error).message}`);
	} finally {
		clearTimeout(timer);
		signal?.removeEventListener('abort', onAbort);
	}
}

// Runs one harness-written script in a Chromium behind a fresh egress proxy
// and returns dev-browser's stdout. Proxy, browser and profile live only for
// this call. allowAddress defaults to the production policy; only tests pass
// another, as they do for startEgressProxy.
export async function runGuardedBrowse(
	script: string,
	options: { env: Env; cwd: string; signal?: AbortSignal; allowAddress?: AddressPolicy },
): Promise<string> {
	const executable = chromiumExecutable(options.env);
	const scriptPath = join(tmpdir(), `spotlight-browse-${randomUUID()}.js`);
	const proxy = await startEgressProxy(options.allowAddress ?? isPublicAddress);
	let chromium: GuardedChromium | undefined;
	try {
		writeFileSync(scriptPath, script, { mode: 0o600 });
		chromium = await startGuardedChromium(executable, proxy.url, options.env, options.signal);
		return programOutput(
			'browse',
			await runFixed('dev-browser', ['--browser', 'spotlight-browse', '--connect', chromium.endpoint, '--timeout', '90', 'run', scriptPath], {
				env: programEnv(options.env),
				cwd: options.cwd,
				signal: options.signal,
				timeoutMs: 120_000,
			}),
		);
	} finally {
		await chromium?.close();
		await proxy.close();
		rmSync(scriptPath, { force: true });
	}
}

type BrowseResult = { final_url: string; status: number | null; title: string; content?: string; screenshot?: string };

export function browseTool(context: ToolContext): ToolDefinition {
	return {
		name: 'browse',
		label: 'Browse',
		description:
			'Open one public http(s) page in a headless browser (dev-browser) and capture its rendered text (default), HTML, or a full-page screenshot. Use when fetch returns an incomplete JavaScript-rendered page. A screenshot needs save_as (under evidence/ or research/).',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<Input>('browse', params, raw);
			refuseDirectWhenAnonymized('browse', context.env);
			const capture = input.capture ?? 'text';
			if (capture === 'screenshot' && !input.save_as) throw new Error('browse: a screenshot needs save_as');
			const destination = await publicDestination(input.url, signal);
			const screenshotName = `spotlight-${randomUUID()}.png`;
			const output = await runGuardedBrowse(browseScript(destination, capture, input.wait_ms ?? 1000, screenshotName), {
				env: context.env,
				cwd: context.caseDir,
				signal,
			});
			const line = output.split('\n').find((each) => each.startsWith(MARKER));
			if (!line) throw new Error('browse: the browser returned no result');
			const result = JSON.parse(line.slice(MARKER.length)) as BrowseResult;
			await assertPublicDestination(result.final_url, signal);
			let bytes: Buffer | undefined;
			if (capture === 'screenshot') {
				// dev-browser saves screenshots in its own temp folder; copy the bytes into the case.
				const shots = resolve(context.env.HOME ?? homedir(), '.dev-browser', 'tmp');
				const shot = resolve(result.screenshot ?? '');
				if (!shot.startsWith(shots + sep)) throw new Error('browse: unexpected screenshot location');
				bytes = readFileSync(shot);
				rmSync(shot, { force: true });
			} else if (input.save_as) {
				bytes = Buffer.from(result.content ?? '', 'utf8');
			}
			const saved = bytes && input.save_as ? writeConfined(context.caseDir, context.writeRoot, input.save_as, bytes) : undefined;
			const content = result.content ?? '';
			return jsonResult({
				requested_url: input.url,
				final_url: result.final_url,
				status: result.status,
				title: result.title,
				saved_to: saved,
				sha256: bytes ? createHash('sha256').update(bytes).digest('hex') : undefined,
				truncated: content.length > MAX_RETURNED_CHARS,
				content: capture === 'screenshot' ? undefined : content.slice(0, MAX_RETURNED_CHARS),
			});
		},
	};
}
