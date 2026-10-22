// http_get: one plain HTTP GET to a public registry, archive or API endpoint
// (company registries, Wayback CDX, Unpaywall, OpenAlex, WHOIS/RDAP...). It is
// the typed replacement for the skills' `curl` calls. Every hop of the request,
// redirects included, passes the public-address guard.
import { createHash } from 'node:crypto';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { egressGet } from './egress.ts';
import { webURL } from './fetch.ts';
import { checkedParams, jsonResult, savePath } from './run.ts';
import type { ToolContext } from './run.ts';

const MAX_RETURNED_CHARS = 60_000;

const params = Type.Object(
	{
		url: webURL,
		accept: Type.Optional(Type.Union([Type.Literal('json'), Type.Literal('text'), Type.Literal('any')])),
		save_as: Type.Optional(savePath),
	},
	{ additionalProperties: false },
);

const ACCEPT = { json: 'application/json', text: 'text/plain, text/html;q=0.9, */*;q=0.1', any: '*/*' } as const;

export function httpGetTool(context: ToolContext): ToolDefinition {
	return {
		name: 'http_get',
		label: 'HTTP GET',
		description:
			'GET one public http(s) URL (registry, archive or API endpoint) and return status, content type and body. Put query parameters in the URL. Optional save_as stores the raw body in the case under research/ or evidence/.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ url: string; accept?: keyof typeof ACCEPT; save_as?: string }>('http_get', params, raw);
			const response = await egressGet(input.url, {
				readBody: true,
				signal,
				headers: { accept: ACCEPT[input.accept ?? 'any'] },
			});
			const body = response.body ?? Buffer.alloc(0);
			const saved = input.save_as ? writeConfined(context.caseDir, context.writeRoot, input.save_as, body) : undefined;
			const text = body.toString('utf8');
			return jsonResult({
				final_url: response.finalURL,
				redirects: response.redirects,
				status: response.status,
				content_type: response.headers['content-type'] ?? null,
				sha256: createHash('sha256').update(body).digest('hex'),
				bytes: body.length,
				body_truncated_at_download: response.truncated,
				saved_to: saved,
				truncated: text.length > MAX_RETURNED_CHARS,
				body: text.slice(0, MAX_RETURNED_CHARS),
			});
		},
	};
}
