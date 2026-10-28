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
import { readFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { engineEnv } from '../case-search.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { LEAD_MARKER, writeConfined } from './case-files.ts';
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
type LeadRequest = { default_model: string; max_input_chars: number; max_tokens: number; temperature: number; prompt: string };
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

async function distill(baseURL: string, env: ToolContext['env'], markdown: string, signal?: AbortSignal): Promise<string> {
	const request = JSON.parse(readFileSync(LEAD_REQUEST, 'utf8')) as LeadRequest;
	const leads = await rlmComplete(
		baseURL,
		{
			model: rlmModel(env),
			messages: [{ role: 'user', content: request.prompt + markdown.slice(0, request.max_input_chars) }],
			max_tokens: request.max_tokens,
			temperature: request.temperature,
		},
		signal,
	);
	if (!leads) throw new Error('RLM returned no leads');
	return leads;
}

const sha256 = (text: string) => createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex');

type ScrapeResult = { markdown?: string; source_url?: string; provider?: string; status_code?: number | null; title?: string | null; content_sha256?: string };

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
			const viaTor = anonymizedFetch(context.env);
			const destination = viaTor ? checkURL(input.url).href : await publicDestination(input.url, signal);
			// Outside Tor mode every connection the seam's browser makes goes through
			// the guarded proxy; in Tor mode the browser goes to Tor and the harness
			// connects to nothing.
			const proxy = viaTor ? undefined : await startEgressProxy();
			let output: string;
			try {
				output = programOutput(
					'fetch',
					await runPython(context, ['-m', 'integrations.scraping', '--json', ...(viaTor ? ['--tor'] : []), '--', destination], {
						// Engine's bsig checks the URL for protected sources in the install's wiki.
						keys: [...SEAM_ENV, ...engineEnv(context.env)],
						extra: { SPOTLIGHT_CONFIG: context.configPath, ...(proxy ? { SPOTLIGHT_BROWSER_PROXY: proxy.url } : {}) },
						signal,
						timeoutMs: 180_000,
					}),
				);
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
			let leads: string;
			try {
				leads = await distill(rlm, context.env, markdown, signal);
			} catch (error) {
				// The page stays on disk; its text still never enters the context.
				return jsonResult({
					...page,
					distilled: false,
					rlm_error: (error as Error).message,
					raw_path: rawPath,
					raw_sha256: sha256(markdown),
					note: 'The RLM could not distill this page. Do not load the raw page whole: grep raw_path for what you need and read only those lines.',
				});
			}
			const leadFile = `${LEAD_MARKER} ${page.final_url}\n# (compact; raw source: ${basename(rawPath)})\n\n${leads}\n`;
			const saved = writeConfined(context.caseDir, context.writeRoot, leadPath, leadFile);
			return jsonResult({
				...page,
				distilled: true,
				saved_to: saved,
				leads_sha256: sha256(leadFile),
				raw_path: rawPath,
				raw_sha256: sha256(markdown),
				truncated: leads.length > MAX_RETURNED_CHARS,
				leads: leads.slice(0, MAX_RETURNED_CHARS),
			});
		},
	};
}
