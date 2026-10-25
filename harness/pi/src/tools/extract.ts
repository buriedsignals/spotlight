// Document extraction with NuExtract3 (numind, Apache 2.0), a small vision
// model on an endpoint the operator runs (Osaurus). It fills a JSON template
// from one case file: text, or a scan or page image. Its values are leads to
// check against the source, never evidence: on printed documents it reads about
// nine fields in ten right, and it can misread a digit or a date without saying so.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { extname, relative, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { readTarget } from './case-files.ts';
import { checkedParams, jsonResult } from './run.ts';
import type { ToolContext } from './run.ts';

const DEFAULT_MODEL = 'nuextract3-mlx-nvfp4';
const IMAGE_TYPES: Record<string, string> = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_CHARS = 60_000;
// The cap is also the repetition stop: a run that reaches it was looping.
const MAX_TOKENS = 2500;
const TIMEOUT_MS = 120_000;
const NOTE = 'Leads only: check every value against the source before using it; this model can misread a digit or a date. Never cite this output as evidence.';

export function extractDocumentTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{
			path: Type.String({ minLength: 1, maxLength: 512 }),
			template: Type.Record(Type.String(), Type.Unknown(), { minProperties: 1 }),
		},
		{ additionalProperties: false },
	);
	return {
		name: 'extract_document',
		label: 'Extract document',
		description:
			'Fill a JSON template from one case file with the local extraction model: a text file, or a scan or page image (.png, .jpg, .webp). The template names the fields and their types, for example {"issuer": "string", "date": "date-time", "total": "number", "items": [{"name": "string", "amount": "number"}]}. For printed or typed documents only, not handwriting. The result is leads tied to the file and its hash: check every value against the source before using it, and never cite it as evidence.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ path: string; template: Record<string, unknown> }>('extract_document', params, raw);
			const env = context.env;
			const base = env.SPOTLIGHT_EXTRACT_OPENAI_BASE_URL?.trim().replace(/\/$/, '');
			if (!base) return jsonResult({ available: false, reason: 'no extraction model is configured on this install' });
			if (env.SPOTLIGHT_EXTRACT_INFERENCE !== 'own') {
				throw new Error('extract_document: the extraction endpoint is not confirmed as one you run yourself; refusing to send case material to it.');
			}
			const target = readTarget(context.caseDir, input.path, context.scope);
			const bytes = readFileSync(target);
			if (bytes.length > MAX_BYTES) throw new Error(`extract_document: ${input.path} is larger than ${MAX_BYTES} bytes`);
			// NuExtract reads its template from chat_template_kwargs, which Osaurus
			// drops; the template markers at the start of the message carry it instead.
			const marker = `【template_start】${JSON.stringify(input.template)}【template_end】`;
			const type = IMAGE_TYPES[extname(target).toLowerCase()];
			const content = type
				? [{ type: 'text', text: marker }, { type: 'image_url', image_url: { url: `data:${type};base64,${bytes.toString('base64')}` } }]
				: `${marker}\n${bytes.toString('utf8').slice(0, MAX_TEXT_CHARS)}`;
			const timeout = AbortSignal.timeout(TIMEOUT_MS);
			const response = await fetch(base.endsWith('/v1') ? `${base}/chat/completions` : `${base}/v1/chat/completions`, {
				method: 'POST',
				headers: {
					'content-type': 'application/json',
					...(env.SPOTLIGHT_EXTRACT_API_KEY ? { authorization: `Bearer ${env.SPOTLIGHT_EXTRACT_API_KEY}` } : {}),
				},
				redirect: 'error',
				signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
				body: JSON.stringify({
					model: env.SPOTLIGHT_EXTRACT_OPENAI_MODEL?.trim() || DEFAULT_MODEL,
					messages: [{ role: 'user', content }],
					max_tokens: MAX_TOKENS,
					temperature: 0.2,
					enable_thinking: false,
				}),
			});
			if (!response.ok) throw new Error(`extract_document: the extraction model answered HTTP ${response.status}`);
			const answer = (await response.json()) as { choices?: Array<{ message?: { content?: string }; finish_reason?: string }> };
			const choice = answer.choices?.[0];
			const source = { path: relative(context.caseDir, target).split(sep).join('/'), sha256: createHash('sha256').update(bytes).digest('hex') };
			if (choice?.finish_reason === 'length') {
				return jsonResult({ source, extracted: null, stopped: 'the model reached its token cap, which on this model means it was repeating; nothing is kept. Read this document yourself instead of retrying.' });
			}
			const text = (choice?.message?.content ?? '').replace(/^\s*```(?:json)?\s*|\s*```\s*$/g, '');
			try {
				return jsonResult({ source, extracted: JSON.parse(text), note: NOTE });
			} catch {
				return jsonResult({ source, extracted: null, stopped: 'the model did not return JSON; read this document yourself.' });
			}
		},
	};
}
