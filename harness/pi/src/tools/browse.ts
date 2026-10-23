// browse: render one public page in a real browser and capture its text, HTML
// or a screenshot. Backed by the dev-browser script runner (`dev-browser
// --headless run <script>`), which `integrations/dev-browser` documents; the
// `integrations/browse` folder is only a manifest for the optional browse.sh
// CLI and backs nothing here.
//
// The model supplies a URL, never a script: the harness writes the whole
// script, so the browser opens exactly one guarded page. Egress: the harness
// walks the URL's redirect chain under the public-address guard first; inside
// the browser a route guard holds every request whose host is an IPv4 literal
// in egress.ts's non-public table, any bracketed IPv6 literal, a single-label
// name, or a local name (localhost, .local, .internal, .lan, .home.arpa); the
// page's final URL must pass the guard again before anything is returned.
// Limits: the browser does its own DNS, so a public name that resolves to a
// private address is not caught in the browser; WebSocket and service-worker
// requests are not seen by the route guard. In Tor mode browse refuses: the
// browser would connect from the operator's address.
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { LOCAL_NAME_SUFFIXES, NOT_PUBLIC_V4, assertPublicDestination, publicDestination, refuseDirectWhenAnonymized } from './egress.ts';
import { webURL } from './fetch.ts';
import { checkedParams, jsonResult, programEnv, programOutput, runFixed, savePath } from './run.ts';
import type { ToolContext } from './run.ts';

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
			const scriptPath = join(tmpdir(), `spotlight-browse-${randomUUID()}.js`);
			writeFileSync(scriptPath, browseScript(destination, capture, input.wait_ms ?? 1000, screenshotName), { mode: 0o600 });
			let output: string;
			try {
				output = programOutput(
					'browse',
					await runFixed('dev-browser', ['--headless', '--timeout', '90', 'run', scriptPath], {
						env: programEnv(context.env),
						cwd: context.caseDir,
						signal,
						timeoutMs: 120_000,
					}),
				);
			} finally {
				rmSync(scriptPath, { force: true });
			}
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
