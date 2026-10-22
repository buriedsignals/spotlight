// osint_tools: discover OSINT tools in the local index through the checkout's
// scripts/osint-tools.py (`find` and `categories`). The index is a local
// SQLite file: no URL is taken and nothing reaches the web. Building the
// index (`build`, which downloads a dataset) is an install step, not a tool.
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT } from '../roles.ts';
import { checkedParams, programOutput, runPython, textResult } from './run.ts';
import type { ToolContext } from './run.ts';

const SCRIPT = `${CHECKOUT_ROOT}/scripts/osint-tools.py`;

const params = Type.Object(
	{
		action: Type.Union([Type.Literal('find'), Type.Literal('categories')]),
		query: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
		category: Type.Optional(Type.String({ pattern: '^[a-z][a-z0-9_-]{0,63}$' })),
		limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
	},
	{ additionalProperties: false },
);

type Input = { action: 'find' | 'categories'; query?: string; category?: string; limit?: number };

export function osintToolsTool(context: ToolContext): ToolDefinition {
	return {
		name: 'osint_tools',
		label: 'OSINT tools',
		description:
			'Find OSINT tools in the local index. {action: "find", query: "<entity type + geography + task keywords>", category?, limit?} or {action: "categories"}.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<Input>('osint_tools', params, raw);
			let args: string[];
			if (input.action === 'categories') {
				args = [SCRIPT, 'categories'];
			} else {
				if (!input.query) throw new Error('osint_tools: find needs a query');
				args = [SCRIPT, 'find', '--json', '--limit', String(input.limit ?? 8)];
				if (input.category) args.push('--category', input.category);
				args.push('--', input.query);
			}
			return textResult(programOutput('osint_tools', await runPython(context, args, { signal })));
		},
	};
}
