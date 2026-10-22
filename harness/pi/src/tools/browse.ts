// browse: render one public page in a real browser and capture its text, HTML
// or a screenshot. Backed by the dev-browser script runner (`dev-browser
// --headless run <script>`), which `integrations/dev-browser` documents; the
// `integrations/browse` folder is only a manifest for the optional browse.sh
// CLI and backs nothing here.
//
// The model supplies a URL, never a script: the harness writes the whole
// script, so the browser opens exactly one guarded page. Egress: the harness
// walks the URL's redirect chain under the public-address guard first; inside
// the browser a route guard holds every request to a literal loopback,
// private, link-local or bracketed IPv6 address, or a localhost/.local name;
// the page's final URL must pass the guard again before anything is returned.
// A public hostname that the browser later resolves to a private address is
// not caught by the in-browser guard (the browser does its own DNS).
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { assertPublicDestination, publicDestination } from './egress.ts';
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

// Requests the in-browser guard holds (it cannot resolve names itself).
const BLOCKED_REQUEST =
	'^[a-z][a-z0-9+.-]*://(?:[^/@]*@)?(?:localhost|[^/:?#]*\\.localhost|[^/:?#]*\\.local|\\[[^\\]]*\\]|(?:0|10|127)\\.\\d+\\.\\d+\\.\\d+|169\\.254\\.\\d+\\.\\d+|192\\.168\\.\\d+\\.\\d+|172\\.(?:1[6-9]|2\\d|3[01])\\.\\d+\\.\\d+|100\\.(?:6[4-9]|[7-9]\\d|1[01]\\d|12[0-7])\\.\\d+\\.\\d+)\\.?(?:[:/?#]|$)';

function script(url: string, capture: string, waitMs: number, screenshotName: string): string {
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
			const capture = input.capture ?? 'text';
			if (capture === 'screenshot' && !input.save_as) throw new Error('browse: a screenshot needs save_as');
			const destination = await publicDestination(input.url, signal);
			const screenshotName = `spotlight-${randomUUID()}.png`;
			const scriptPath = join(tmpdir(), `spotlight-browse-${randomUUID()}.js`);
			writeFileSync(scriptPath, script(destination, capture, input.wait_ms ?? 1000, screenshotName), { mode: 0o600 });
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
