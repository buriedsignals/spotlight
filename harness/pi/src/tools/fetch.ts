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
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { anonymizedFetch, assertPublicDestination, checkURL, publicDestination } from './egress.ts';
import { startEgressProxy } from './proxy.ts';
import { checkedParams, jsonResult, programOutput, runPython, savePath } from './run.ts';
import type { ToolContext } from './run.ts';

const MAX_RETURNED_CHARS = 30_000;
// Variables the seam reads: the optional fallback key and the Tor posture.
const SEAM_ENV = ['FIRECRAWL_API_KEY', 'SPOTLIGHT_ANONYMIZE_FETCH', 'TOR_SOCKS', 'SCRAPE_PROVIDER'];

export const webURL = Type.String({ minLength: 1, maxLength: 2048, pattern: '^[Hh][Tt][Tt][Pp][Ss]?://' });

const params = Type.Object({ url: webURL, save_as: Type.Optional(savePath) }, { additionalProperties: false });

type ScrapeResult = { markdown?: string; source_url?: string; provider?: string; status_code?: number | null; title?: string | null; content_sha256?: string };

export function fetchTool(context: ToolContext): ToolDefinition {
	return {
		name: 'fetch',
		label: 'Fetch',
		description:
			'Fetch one public web page as Markdown (Crawl4AI). Only public http(s) addresses are allowed. Optional save_as stores the Markdown in the case under research/ or evidence/.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ url: string; save_as?: string }>('fetch', params, raw);
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
						keys: SEAM_ENV,
						extra: proxy ? { SPOTLIGHT_BROWSER_PROXY: proxy.url } : {},
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
			const saved = input.save_as ? writeConfined(context.caseDir, context.writeRoot, input.save_as, markdown) : undefined;
			return jsonResult({
				requested_url: input.url,
				final_url: result.source_url ?? destination,
				provider: result.provider,
				status_code: result.status_code ?? null,
				title: result.title ?? null,
				content_sha256: result.content_sha256,
				saved_to: saved,
				truncated: markdown.length > MAX_RETURNED_CHARS,
				markdown: markdown.slice(0, MAX_RETURNED_CHARS),
			});
		},
	};
}
