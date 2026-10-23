// open_review: shows the member the pending gate's review page. The review
// server runs locally and the result never carries the page address, so the
// model cannot reach the page or post a decision.
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { createReviewOpener, describeOpenResult } from '../../../review/open.mjs';

type Opener = (request: { caseDir: string }) => Promise<{ status: string }>;

export function openReviewTool(caseDir: string, casesRoot: string, opener?: Opener): ToolDefinition {
	const open: Opener = opener ?? createReviewOpener({ casesRoot });
	return {
		name: 'open_review',
		label: 'Open review page',
		description:
			'Open the review page for the gate this case is waiting on, in the member\'s browser. Call it when a phase skill reaches a gate, then end your turn; the member decides on the page.',
		parameters: Type.Object({}),
		async execute() {
			const result = await open({ caseDir });
			return { content: [{ type: 'text', text: describeOpenResult(result) }], details: undefined };
		},
	};
}
