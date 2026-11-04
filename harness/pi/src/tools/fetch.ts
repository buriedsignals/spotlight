// fetch: scrape one public web page to Markdown through the checkout's
// scraping seam (`python -m integrations.scraping`: Crawl4AI, with the optional
// Firecrawl fallback when FIRECRAWL_API_KEY is set).
//
// Egress: the harness first walks the URL's redirect chain itself under the
// public-address guard and hands the seam only the final URL. The seam's
// browser then runs behind the harness's egress proxy (proxy.ts), so every
// connection it makes (subresources, scripts, redirects, WebSockets) passes
// the same guard, loopback included. The URL it reports ending on must pass
// the guard as well, or the content is discarded. The optional Firecrawl
// fallback fetches from Firecrawl's servers, not from this machine.
//
// Tor mode (SPOTLIGHT_ANONYMIZE_FETCH): the harness itself makes no connection
// and no DNS query for the target. It checks the URL without the network
// (scheme, credentials, literal addresses, local names) and hands it to the
// seam with --tor; Chromium resolves names through a SOCKS5 proxy, so the name
// is resolved behind Tor. The URL the seam reports ending on is checked the
// same way before anything is returned.
//
// Local tier (lead distillation): every pi process runs on a route the
// operator runs (config.ts routeFor), so the local tier is any process the
// launcher gave an RLM endpoint, which it passes only after confirming it is
// own (SPOTLIGHT_RLM_INFERENCE=own). There, after the final URL passed the
// guard, the page goes to the RLM, `save_as` receives the compact leads and
// `save_as`.raw the page itself; the agent gets the leads, never the page.
// Without an RLM endpoint fetch returns the page as it always did.
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { engineEnv } from '../case-search.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { LEAD_MARKER, readTarget, writeConfined } from './case-files.ts';
import { anonymizedFetch, assertPublicDestination, checkURL, publicDestination } from './egress.ts';
import { startEgressProxy } from './proxy.ts';
import { ToolArgumentError, checkedParams, jsonResult, programOutput, runPython, savePath, saveTarget } from './run.ts';
import type { ToolContext } from './run.ts';

const MAX_RETURNED_CHARS = 30_000;
// Variables the seam reads: the optional fallback key, the Tor posture, and
// where Engine's runtime pack keeps Crawl4AI's Chromium and cache (Ninja U46).
export const SEAM_ENV = ['FIRECRAWL_API_KEY', 'SPOTLIGHT_ANONYMIZE_FETCH', 'TOR_SOCKS', 'SCRAPE_PROVIDER', 'PLAYWRIGHT_BROWSERS_PATH', 'CRAWL4_AI_BASE_DIRECTORY'];

// Patterns are written to hold as full matches too: some providers compile
// `pattern` into a whole-value grammar, and a bare prefix then lets the model
// emit only "https://". The egress guard still checks the URL itself.
export const webURL = Type.String({ minLength: 1, maxLength: 2048, pattern: '^[Hh][Tt][Tt][Pp][Ss]?://.+$' });

const params = Type.Object({ url: webURL, save_as: Type.Optional(savePath) }, { additionalProperties: false });

// The lead-distillation request the scraping seam's --rlm path also uses.
type LeadRequest = { default_model: string; max_input_chars: number; max_tokens: number; temperature: number; prompt: string; response_format: Record<string, unknown> };
const LEAD_REQUEST = resolve(CHECKOUT_ROOT, 'integrations/scraping/rlm-leads.json');
const RLM_TIMEOUT_MS = 120_000;

// The RLM endpoint for this process, or undefined off the local tier. An
// endpoint the launcher did not confirm as own is refused, never used.
export function rlmEndpoint(env: ToolContext['env']): string | undefined {
	const url = env.SPOTLIGHT_RLM_OPENAI_BASE_URL?.trim();
	if (!url) return undefined;
	if (env.SPOTLIGHT_RLM_INFERENCE !== 'own') {
		throw new Error('SPOTLIGHT_RLM_OPENAI_BASE_URL is not confirmed as an endpoint you run yourself; refusing to send case material to it.');
	}
	return url.replace(/\/$/, '');
}

// The RLM model: SPOTLIGHT_RLM_OPENAI_MODEL, else the lead request's default.
export function rlmModel(env: ToolContext['env']): string {
	return env.SPOTLIGHT_RLM_OPENAI_MODEL?.trim() || (JSON.parse(readFileSync(LEAD_REQUEST, 'utf8')) as LeadRequest).default_model;
}

// The RLM's context per request, in tokens: SPOTLIGHT_RLM_CONTEXT, else the
// n_ctx a llama.cpp server reports at /props (per slot), else unknown. arms
// on 8699f11d: the e4b served 24,576 per slot while a 98k-window fold sent
// about 57k, and every checkpoint request got HTTP 400.
let knownContext: Promise<number | undefined> | undefined;
export function rlmContext(env: ToolContext['env'], baseURL: string): Promise<number | undefined> {
	const set = Number(env.SPOTLIGHT_RLM_CONTEXT);
	if (Number.isInteger(set) && set > 0) return Promise.resolve(set);
	knownContext ??= (async () => {
		try {
			const response = await fetch(new URL('/props', baseURL), { signal: AbortSignal.timeout(5_000) });
			if (!response.ok) return undefined;
			const props = (await response.json()) as { n_ctx?: unknown; default_generation_settings?: { n_ctx?: unknown } };
			const n = props.default_generation_settings?.n_ctx ?? props.n_ctx;
			return typeof n === 'number' && n > 0 ? n : undefined;
		} catch {
			return undefined;
		}
	})();
	return knownContext;
}

// One chat completion from the RLM; its answer text, trimmed.
export async function rlmComplete(baseURL: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
	const url = baseURL.endsWith('/v1') ? `${baseURL}/chat/completions` : `${baseURL}/v1/chat/completions`;
	const timeout = AbortSignal.timeout(RLM_TIMEOUT_MS);
	const response = await fetch(url, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		redirect: 'error',
		signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
		body: JSON.stringify({ ...body, stream: false }),
	});
	if (!response.ok) throw new Error(`RLM answered HTTP ${response.status}`);
	const answer = (await response.json()) as { choices?: Array<{ message?: { content?: string | null } }> };
	return answer.choices?.[0]?.message?.content?.trim() ?? '';
}

// The helper chooses line ranges only. It never writes the text returned as a lead.
async function distill(baseURL: string, env: ToolContext['env'], markdown: string, signal?: AbortSignal): Promise<{ leads: string; inspected: number; total: number; selections: Array<{ line_start: number; line_end: number }> }> {
	const request = JSON.parse(readFileSync(LEAD_REQUEST, 'utf8')) as LeadRequest;
	const task = env.SPOTLIGHT_TASK_HEADER?.slice(0, 4000);
	const scope = task ? `Task scope (not source evidence):\n${task}\n\n` : '';
	const lines = markdown.split('\n').map((line) => line.endsWith('\r') ? line.slice(0, -1) : line);
	if (lines.at(-1) === '') lines.pop();
	const excerpts: string[] = [];
	const selections: Array<{ line_start: number; line_end: number }> = [];
	let next = 0;
	for (let chunk = 0; chunk < 3 && next < lines.length; chunk++) {
		const start = next;
		let numbered = '';
		while (next < lines.length) {
			const line = `${next + 1}: ${lines[next]}\n`;
			if (numbered.length + line.length > request.max_input_chars) break;
			numbered += line;
			next++;
		}
		if (next === start) break;
		const answer = await rlmComplete(baseURL, { model: rlmModel(env), messages: [{ role: 'user', content: scope + request.prompt + numbered }], response_format: request.response_format, max_tokens: request.max_tokens, temperature: request.temperature }, signal);
		const fenced = answer.trim().match(/^```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```$/i);
		const parsed: unknown = JSON.parse(fenced ? fenced[1] : answer);
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || Object.keys(parsed).join() !== 'ranges') throw new Error('RLM returned invalid line selections');
		const ranges = (parsed as { ranges: unknown }).ranges;
		if (!Array.isArray(ranges)) throw new Error('RLM returned invalid line selections');
		for (const range of ranges) {
			if (!range || typeof range !== 'object' || Array.isArray(range) || Object.keys(range).sort().join() !== 'end,start') throw new Error('RLM returned invalid line selection');
			const { start: first, end: last } = range as { start: unknown; end: unknown };
			if (!Number.isSafeInteger(first) || !Number.isSafeInteger(last) || (first as number) < start + 1 || (last as number) > next || (last as number) < (first as number)) throw new Error('RLM selected lines outside the inspected range');
			selections.push({ line_start: first as number, line_end: last as number });
			excerpts.push(`[raw lines ${first}-${last}]\n${lines.slice((first as number) - 1, last as number).join('\n')}`);
		}
	}
	if (!excerpts.length) throw new Error('RLM returned no extractive leads');
	const leads = excerpts.join('\n\n');
	if (leads.length > MAX_RETURNED_CHARS) throw new Error('RLM selections exceed the output bound');
	return { leads, inspected: next, total: lines.length, selections };
}

const sha256 = (text: string) => createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex');

type ScrapeResult = { markdown?: string; source_url?: string; provider?: string; status_code?: number | null; title?: string | null; content_sha256?: string };

// fc-stall scan: a failed fetch came back as crawl4ai's whole log, a
// deprecation warning first and the cause last (195 hits), and the same
// blocked page was fetched again and again (one Reuters URL 43 times). The
// cause comes first and short, and a URL that failed in this case is not
// fetched again: once if a site blocked it, twice otherwise.
export function fetchFailure(detail: string): string {
	const lines = detail.split('\n').map((line) => line.trim()).filter((line) => line && !/Warning\b|warnings\.warn\(|^\[(?:FETCH|SCRAPE|COMPLETE|INIT)\]/.test(line));
	const cause = lines.find((line) => line.startsWith('scrape failed:')) ?? lines.find((line) => line.startsWith('[ERROR]')) ?? lines.at(-1) ?? 'no output';
	return cause.replace(/^scrape failed:\s*/, '').slice(0, 400);
}

const blocked = (cause: string) => /bot-blocked|anti-bot|captcha|\b403\b|access denied/i.test(cause);
type Failures = Record<string, { count: number; cause: string }>;

// One file per case, so two cases' workers never write the same file.
function failuresPath(context: ToolContext): string | undefined {
	const dataDir = context.env?.SPOTLIGHT_HARNESS_DATA;
	return dataDir ? resolve(dataDir, 'work', 'fetch-failures', `${createHash('sha256').update(context.caseDir).digest('hex').slice(0, 16)}.json`) : undefined;
}

function readFailures(path: string | undefined): Failures {
	if (!path) return {};
	try {
		return JSON.parse(readFileSync(path, 'utf8')) as Failures;
	} catch {
		return {};
	}
}

export function priorFailure(context: ToolContext, url: string): string | undefined {
	const seen = readFailures(failuresPath(context))[url];
	if (!seen || (seen.count < 2 && !blocked(seen.cause))) return undefined;
	return `fetch: ${url} already failed in this case (${seen.cause}); it is not fetched again. Try its archived copy (https://web.archive.org/web/${url}) with fetch, open it with browse, or use another source.`;
}

function recordFailure(context: ToolContext, url: string, cause: string): void {
	const path = failuresPath(context);
	if (!path) return;
	const all = readFailures(path);
	all[url] = { count: (all[url]?.count ?? 0) + 1, cause };
	try {
		mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
		writeFileSync(path, JSON.stringify(all), { mode: 0o600 });
	} catch {
		// A failure that cannot be recorded only means the next fetch is tried.
	}
}

// What a fetch saved, kept per case in harness data, so the harness can
// record a fetched file as an evidence item when a quote is cited from it
// (arm2 ds-spectator on ce4f010e: a fact-checker's quote from its own fetch was
// refused three times because no item listed the file, and the fact-checker
// has no tool to add one).
// sha256 is the saved text's hash: a file changed after the fetch is not that
// fetch any more (Codex review of #274b).
export type Fetched = { url: string; accessed: string; provider?: string; sha256: string; acquired_by?: string };
function fetchedPath(context: ToolContext): string | undefined {
	const dataDir = context.env?.SPOTLIGHT_HARNESS_DATA;
	return dataDir ? resolve(dataDir, 'work', 'fetched', `${createHash('sha256').update(context.caseDir).digest('hex').slice(0, 16)}.json`) : undefined;
}
// browse and http_get record their saved captures here too (Codex re-review of
// fix11): a fact-checker's genuine capture by either tool is its acquisition.
export function recordFetched(context: ToolContext, paths: string[], entry: Fetched): void {
	const path = fetchedPath(context);
	if (!path) return;
	let all: Record<string, Fetched> = {};
	try {
		all = JSON.parse(readFileSync(path, 'utf8')) as Record<string, Fetched>;
	} catch {
		all = {};
	}
	for (const saved of paths) all[saved] = { ...entry, acquired_by: context.env?.SPOTLIGHT_ROLE };
	try {
		mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
		writeFileSync(path, JSON.stringify(all), { mode: 0o600 });
	} catch {
		// Without the record the file is cited as before: its item added by hand.
	}
}
export function fetchedFile(context: ToolContext, relative: string): Fetched | undefined {
	const path = fetchedPath(context);
	if (!path) return undefined;
	try {
		return (JSON.parse(readFileSync(path, 'utf8')) as Record<string, Fetched>)[relative];
	} catch {
		return undefined;
	}
}

// Independent acquisition is a runtime fact, never inferred from an fc- name
// or a model-authored bundle row. Older receipts remain readable but untrusted.
export function factCheckerCapture(context: ToolContext, relative: string): Fetched | undefined {
	const capture = registeredCapture(context, relative);
	return capture?.acquired_by === 'fact-checker' ? capture : undefined;
}

// The source URLs of the fact-checker's own captures made since `since` (ms),
// each unchanged since its fetch, leaving out the given paths.
export function factCheckerSourceURLs(context: ToolContext, skip: Set<string>, since: number): string[] {
	const registry = fetchedPath(context);
	if (!registry) return [];
	let captures: Record<string, Fetched>;
	try { captures = JSON.parse(readFileSync(registry, 'utf8')) as Record<string, Fetched>; } catch { return []; }
	return Object.keys(captures).filter((path) => !skip.has(path)).map((path) => factCheckerCapture(context, path)).filter((capture) => capture && Date.parse(capture.accessed) >= since).map((capture) => capture!.url);
}

// A file a harness fetch saved, unchanged since: any role's capture.
export function registeredCapture(context: ToolContext, relative: string): Fetched | undefined {
	const capture = fetchedFile(context, relative);
	if (!capture || typeof capture.url !== 'string' || !Number.isFinite(Date.parse(capture.accessed))) return undefined;
	try {
		checkURL(capture.url);
		const bytes = readFileSync(readTarget(context.caseDir, relative));
		return createHash('sha256').update(bytes).digest('hex') === capture.sha256 ? capture : undefined;
	} catch {
		return undefined;
	}
}

// A repair hint, never a citation rewrite: two separately acquired captures
// can contain identical bytes while retaining distinct acquisition provenance.
export function factCheckerAlternative(context: ToolContext, relative: string): string | undefined {
	const original = fetchedFile(context, relative);
	const registry = fetchedPath(context);
	if (!original || !registry) return undefined;
	try {
		checkURL(original.url);
		if (createHash('sha256').update(readFileSync(readTarget(context.caseDir, relative))).digest('hex') !== original.sha256) return undefined;
		const captures = JSON.parse(readFileSync(registry, 'utf8')) as Record<string, Fetched>;
		return Object.keys(captures).sort().find((path) => {
			const candidate = captures[path];
			return path !== relative && candidate?.url === original.url && candidate.sha256 === original.sha256 && !!factCheckerCapture(context, path);
		});
	} catch {
		return undefined;
	}
}

export function fetchTool(context: ToolContext): ToolDefinition {
	return {
		name: 'fetch',
		label: 'Fetch',
		description:
			'Fetch one public web page as Markdown (Crawl4AI). Only public http(s) addresses are allowed. Optional save_as stores the Markdown in the case (a bare file name goes under research/; or give research/... or evidence/...; not .json: use http_get to keep a JSON body). When the result says distilled: true, save_as holds compact RLM leads (what you get back) and raw_path the page itself, for citations.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ url: string; save_as?: string }>('fetch', params, raw);
			// fetch stores Markdown (or RLM leads), never the response body, so a .json
			// name would hold text no JSON reader or json_pointer anchor can parse.
			if (input.save_as && /\.json$/i.test(input.save_as)) {
				throw new ToolArgumentError('fetch: save_as ends in .json, but fetch saves Markdown. Use http_get to save a JSON response as is, or give save_as a .md name.');
			}
			const rlm = rlmEndpoint(context.env);
			if (rlm && !input.save_as) throw new Error('fetch: on this tier fetch needs save_as, so the raw page is kept beside its leads.');
			const prior = priorFailure(context, input.url);
			if (prior) throw new Error(prior);
			const viaTor = anonymizedFetch(context.env);
			const destination = viaTor ? checkURL(input.url).href : await publicDestination(input.url, signal);
			// Outside Tor mode every connection the seam's browser makes goes through
			// the guarded proxy; in Tor mode the browser goes to Tor and the harness
			// connects to nothing.
			const proxy = viaTor ? undefined : await startEgressProxy();
			let output: string;
			try {
				const run = await runPython(context, ['-m', 'integrations.scraping', '--json', ...(viaTor ? ['--tor'] : []), '--', destination], {
					// Engine's bsig checks the URL for protected sources in the install's wiki.
					keys: [...SEAM_ENV, ...engineEnv(context.env)],
					extra: { SPOTLIGHT_CONFIG: context.configPath, ...(proxy ? { SPOTLIGHT_BROWSER_PROXY: proxy.url } : {}) },
					signal,
					timeoutMs: 180_000,
				});
				if (run.code !== 0) {
					const cause = fetchFailure(run.stderr.trim() || run.stdout.trim());
					recordFailure(context, input.url, cause);
					throw new Error(`fetch failed: ${cause}`);
				}
				output = programOutput('fetch', run);
			} finally {
				await proxy?.close();
			}
			const result = JSON.parse(output) as ScrapeResult;
			if (result.source_url) {
				if (viaTor) checkURL(result.source_url);
				else await assertPublicDestination(result.source_url, signal);
			}
			const markdown = result.markdown ?? '';
			const page = {
				requested_url: input.url,
				final_url: result.source_url ?? destination,
				// The time comes from the harness, so a source's access date is never guessed.
				fetched_at: new Date().toISOString(),
				// No archive capture is made by this harness.
				archive_url: null,
				provider: result.provider,
				status_code: result.status_code ?? null,
				title: result.title ?? null,
				content_sha256: result.content_sha256,
			};
			if (!rlm) {
				const saved = input.save_as ? writeConfined(context.caseDir, context.writeRoot, saveTarget(context, input.save_as), markdown) : undefined;
				if (saved) recordFetched(context, [saved], { url: page.final_url, accessed: page.fetched_at, provider: page.provider, sha256: sha256(markdown) });
				return jsonResult({
					...page,
					distilled: false,
					distillation: 'off: SPOTLIGHT_RLM_OPENAI_BASE_URL is not set, so the page is returned as fetched',
					saved_to: saved,
					truncated: markdown.length > MAX_RETURNED_CHARS,
					markdown: markdown.slice(0, MAX_RETURNED_CHARS),
				});
			}
			const leadPath = saveTarget(context, input.save_as as string);
			const rawPath = writeConfined(context.caseDir, context.writeRoot, `${leadPath}.raw`, markdown);
			recordFetched(context, [rawPath], { url: page.final_url, accessed: page.fetched_at, provider: page.provider, sha256: sha256(markdown) });
			let selected: Awaited<ReturnType<typeof distill>>;
			try {
				selected = await distill(rlm, context.env, markdown, signal);
			} catch (error) {
				// The page stays on disk; its text still never enters the context.
				return jsonResult({
					...page,
					distilled: false,
					rlm_error: (error as Error).message,
					raw_path: rawPath,
					raw_sha256: sha256(markdown),
					note: 'The RLM could not distill this page. No distilled output was written to save_as for this acquisition; use raw_path for the saved page. Do not load the raw page whole: grep raw_path for what you need and read only those lines.',
				});
			}
			const { leads, inspected, total, selections } = selected;
			const leadFile = `${LEAD_MARKER} ${page.final_url}\n# (extracts; raw source: ${basename(rawPath)}; inspected lines 1-${inspected} of ${total})\n\n${leads}\n`;
			const saved = writeConfined(context.caseDir, context.writeRoot, leadPath, leadFile);
			return jsonResult({
				...page,
				distilled: true,
				saved_to: saved,
				leads_sha256: sha256(leadFile),
				inspected_lines: { start: 1, end: inspected, total },
				coverage_complete: inspected === total,
				...(inspected < total ? { coverage_note: `Raw lines ${inspected + 1}-${total} were not inspected by the helper.` } : {}),
				selections: selections.map((selection) => ({ path: rawPath, ...selection })),
				raw_path: rawPath,
				raw_sha256: sha256(markdown),
				truncated: leads.length > MAX_RETURNED_CHARS,
				leads: leads.slice(0, MAX_RETURNED_CHARS),
			});
		},
	};
}
