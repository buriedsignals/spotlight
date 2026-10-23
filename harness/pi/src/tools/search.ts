// search: web search through the checkout's search seam
// (`python -m integrations.search`: SearXNG at SPOTLIGHT_SEARCH_URL, optional
// Firecrawl union/fallback when FIRECRAWL_API_KEY is set). The search service
// is operator configuration, not a model-chosen URL; result URLs are only text
// until the model fetches one, which goes through fetch's guard.
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { writeConfined } from './case-files.ts';
import { checkedParams, jsonResult, programOutput, runPython, savePath } from './run.ts';
import type { ToolContext } from './run.ts';

const params = Type.Object(
	{
		query: Type.String({ minLength: 1, maxLength: 500 }),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
		categories: Type.Optional(Type.String({ pattern: '^[a-z][a-z ,_-]{0,63}$' })),
		time_range: Type.Optional(Type.Union(['day', 'week', 'month', 'year'].map((value) => Type.Literal(value)))),
		save_as: Type.Optional(savePath),
	},
	{ additionalProperties: false },
);

type Input = { query: string; limit?: number; categories?: string; time_range?: string; save_as?: string };

export function searchTool(context: ToolContext): ToolDefinition {
	return {
		name: 'search',
		label: 'Search',
		description:
			'Search the web (SearXNG). Returns [{url, title, snippet, date, engine}]. Optional limit (default 10), categories (e.g. "news"), time_range, and save_as to store the results in the case under research/ or evidence/.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<Input>('search', params, raw);
			const args = ['-m', 'integrations.search', '--json', '--limit', String(input.limit ?? 10)];
			if (input.categories) args.push('--categories', input.categories);
			if (input.time_range) args.push('--time-range', input.time_range);
			args.push('--', input.query);
			const searchURL = context.env.SPOTLIGHT_SEARCH_URL;
			const output = programOutput(
				'search',
				await runPython(context, args, {
					keys: ['FIRECRAWL_API_KEY'],
					extra: searchURL ? { SEARXNG_URL: searchURL } : {},
					signal,
				}),
			);
			const hits = JSON.parse(output || '[]') as unknown;
			const saved = input.save_as
				? writeConfined(context.caseDir, context.writeRoot, input.save_as, JSON.stringify(hits, null, 2))
				: undefined;
			return jsonResult({ query: input.query, saved_to: saved, results: hits });
		},
	};
}
