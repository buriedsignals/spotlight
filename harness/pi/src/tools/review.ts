// open_review: shows the member the pending gate's review page. The review
// server runs locally and the result never carries the page address, so the
// model cannot reach the page or post a decision.
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { createReviewOpener, describeOpenResult } from '../../../review/open.mjs';
import { CHECKOUT_ROOT } from '../roles.ts';
import { runPython } from './run.ts';
import type { ToolContext } from './run.ts';
import { resolveCase } from './spotlight.ts';
import type { ActiveCase, AdapterRunner } from './spotlight.ts';

type Opener = (request: { caseDir: string }) => Promise<{ status: string }>;
// The validator's failures when the page would open on an invalid report draft.
type DraftCheck = () => Promise<string | undefined>;

// At the Report gate the member decides on the orchestrator's own draft, and
// no worker's validators ran on it: the draft is checked before the page opens.
export function reportDraftCheck(active: ActiveCase, runAdapter: AdapterRunner, context: ToolContext): DraftCheck {
	return async () => {
		if (!existsSync(join(active.caseDir, 'data/report-draft.json'))) return undefined;
		if ((await resolveCase(active, runAdapter)).phase !== 'report') return undefined;
		const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts/validate-report-draft.py'), active.caseDir]);
		return result.code === 0 ? undefined : `${result.stdout}\n${result.stderr}`.trim();
	};
}

export function openReviewTool(caseDir: string, casesRoot: string, opener?: Opener, draftCheck?: DraftCheck): ToolDefinition {
	const open: Opener = opener ?? createReviewOpener({ casesRoot });
	return {
		name: 'open_review',
		label: 'Open review page',
		description:
			'Open the review page for the gate this case is waiting on, in the member\'s browser. Call it when a phase skill reaches a gate, then end your turn; the member decides on the page.',
		parameters: Type.Object({}),
		async execute() {
			const problems = await draftCheck?.();
			if (problems) {
				throw new Error(`The review page was not opened: data/report-draft.json does not pass validate-report-draft.py. Fix the draft and call open_review again; nothing is rendered until the member decides on the page.\n${problems.slice(-6000)}`);
			}
			const result = await open({ caseDir });
			return { content: [{ type: 'text', text: describeOpenResult(result) }], details: undefined };
		},
	};
}
