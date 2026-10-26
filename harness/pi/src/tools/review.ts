// open_review: shows the member the pending gate's review page. The review
// server runs locally and the result never carries the page address, so the
// model cannot reach the page or post a decision.
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { createReviewOpener, describeOpenResult } from '../../../review/open.mjs';
import { RESEARCH_SWITCH } from '../config.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { runPython } from './run.ts';
import type { ToolContext } from './run.ts';
import { resolveCase } from './spotlight.ts';
import type { ActiveCase, AdapterRunner } from './spotlight.ts';

type Opener = (request: { caseDir: string }) => Promise<{ status: string }>;
// Why the page must not open yet: the failing validator's name and output.
type GateCheck = () => Promise<{ file: string; validator: string; output: string } | undefined>;

// The files the member decides on at each gate, and the validator for them. The
// review page refuses an invalid file only when the member submits, which the
// model never sees, so they are checked before the page opens.
const GATE_CHECKS: Record<string, (context: ToolContext) => { file: string; args: string[] }[]> = {
	methodology_approval: (context) => [{ file: 'data/methodology.json', args: ['validate-methodology-navigator.py', context.caseDir, '--config', context.configPath] }],
	investigation_gate_approval: (context) => [
		{ file: 'data/findings.json', args: ['validate-case.py', context.caseDir] },
		{ file: 'data/fact-check.json', args: ['validate-fact-check.py', context.caseDir] },
	],
	// The member may decline without a draft; a draft that exists is checked.
	report: (context) => (existsSync(join(context.caseDir, 'data/report-draft.json')) ? [{ file: 'data/report-draft.json', args: ['validate-report-draft.py', context.caseDir] }] : []),
};

export function gateCheck(active: ActiveCase, runAdapter: AdapterRunner, context: ToolContext): GateCheck {
	return async () => {
		const checks = GATE_CHECKS[String((await resolveCase(active, runAdapter)).phase ?? '')]?.(context) ?? [];
		for (const { file, args } of checks) {
			const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', args[0]), ...args.slice(1)], { keys: [RESEARCH_SWITCH], timeoutMs: 300_000 });
			if (result.code !== 0) return { file, validator: args[0], output: `${result.stdout}\n${result.stderr}`.trim() };
		}
		return undefined;
	};
}

export function openReviewTool(caseDir: string, casesRoot: string, opener?: Opener, check?: GateCheck): ToolDefinition {
	const open: Opener = opener ?? createReviewOpener({ casesRoot });
	return {
		name: 'open_review',
		label: 'Open review page',
		description:
			'Open the review page for the gate this case is waiting on, in the member\'s browser. Call it when a phase skill reaches a gate, then end your turn; the member decides on the page.',
		parameters: Type.Object({}),
		async execute() {
			const failed = await check?.();
			if (failed) {
				throw new Error(`The review page was not opened: ${failed.file} does not pass ${failed.validator}. Fix it and call open_review again; nothing is rendered or recorded until the member decides on the page.\n${failed.output.slice(-6000)}`);
			}
			const result = await open({ caseDir });
			return { content: [{ type: 'text', text: describeOpenResult(result) }], details: undefined };
		},
	};
}
