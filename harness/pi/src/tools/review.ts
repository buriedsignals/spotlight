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
// Why the page must not open yet, or undefined when it may.
type GateCheck = () => Promise<string | undefined>;

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

// With nothing left to write, the resolver's `missing` at the Investigation
// gate is only the approval itself.
const GATE_APPROVAL = 'Investigation gate approval';

export function gateCheck(active: ActiveCase, runAdapter: AdapterRunner, context: ToolContext): GateCheck {
	return async () => {
		const resolved = await resolveCase(active, runAdapter);
		const phase = String(resolved.phase ?? '');
		// r3/r4 12B (A2 northwind): the page opened before the summaries were
		// written, and the gate refused the member's approval with a 422.
		const unwritten = phase === 'investigation_gate_approval' && Array.isArray(resolved.missing) ? resolved.missing.filter((item) => item !== GATE_APPROVAL) : [];
		if (unwritten.length > 0) {
			return `the Investigation gate still needs: ${unwritten.join(', ')}. Write summary.md and data/summary.json as phase-investigation-gate "Generate summary" says, then call spotlight_resolve and open_review again.`;
		}
		const checks = GATE_CHECKS[phase]?.(context) ?? [];
		for (const { file, args } of checks) {
			const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', args[0]), ...args.slice(1)], { keys: [RESEARCH_SWITCH], timeoutMs: 300_000 });
			if (result.code !== 0) {
				const output = `${result.stdout}\n${result.stderr}`.trim();
				return `${file} does not pass ${args[0]}. Fix it and call open_review again; nothing is rendered or recorded until the member decides on the page.\n${output.slice(-6000)}`;
			}
		}
		return undefined;
	};
}

// LFM3 e2 kde stages 2 and 3 on Osaurus (327ec248): the orchestrator opened
// the Report page with no draft and the Ingest page with no knowledge batch.
// The page then offers only the decline (the approval is disabled with its
// reason), so the page still opens, and the orchestrator is told what the
// member cannot approve yet and the step that makes it possible.
export async function missingForApproval(caseDir: string, resolved: Record<string, unknown>): Promise<string | undefined> {
	const has = (path: string) => existsSync(join(caseDir, path));
	if (resolved.phase === 'report' && !has('data/report-draft.json')) {
		return 'No report draft exists, so the member can only decline the report on the page. If the member wants the report, write data/report-draft.json as report-drafting says, then call open_review again.';
	}
	const ingest = resolved.ingest as { resume_at?: unknown } | undefined;
	if (resolved.phase === 'ingest' && ingest?.resume_at === 'decision' && !has('data/knowledge-batch.json') && !has('data/knowledge-batch-draft.json')) {
		return 'No knowledge batch exists, so the member can only decline ingestion on the page. If the member wants to ingest, call spawn_agent with agent analyst (phase-ingest step 1); the harness builds the batch when the analyst finishes. Then call open_review again.';
	}
	return undefined;
}

export function openReviewTool(caseDir: string, casesRoot: string, opener?: Opener, check?: GateCheck, note?: () => Promise<string | undefined>): ToolDefinition {
	const open: Opener = opener ?? createReviewOpener({ casesRoot });
	return {
		name: 'open_review',
		label: 'Open review page',
		description:
			'Open the review page for the gate this case is waiting on, in the member\'s browser. Call it when a phase skill reaches a gate, then end your turn; the member decides on the page.',
		parameters: Type.Object({}),
		async execute() {
			const failed = await check?.();
			if (failed) throw new Error(`The review page was not opened: ${failed}`);
			const result = await open({ caseDir });
			const missing = result.status === 'opened' ? await note?.() : undefined;
			return { content: [{ type: 'text', text: missing ? `${describeOpenResult(result)}\n${missing}` : describeOpenResult(result) }], details: undefined };
		},
	};
}
