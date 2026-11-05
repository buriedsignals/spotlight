// open_review: shows the member the pending gate's review page. The review
// server runs locally and the result never carries the page address, so the
// model cannot reach the page or post a decision.
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { createReviewOpener, describeOpenResult } from '../../../review/open.mjs';
import { RESEARCH_SWITCH } from '../config.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { REVIEW_DECIDED } from '../router.ts';
import { RECEIPTS, runPython } from './run.ts';
import type { ToolContext } from './run.ts';
import { NEXT_METHODOLOGY_FAILING, draftWritten, resolveCase } from './spotlight.ts';
import type { ActiveCase, AdapterRunner, DispatchRunner } from './spotlight.ts';

// open_review's refusal starts with this; the refusal cap in extension.ts counts it.
export const PAGE_NOT_OPENED = 'The review page was not opened: ';

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
	// The member may decline without a draft; a draft with prose written is checked.
	report: (context) => (draftWritten(context.caseDir) ? [{ file: 'data/report-draft.json', args: ['validate-report-draft.py', context.caseDir] }] : []),
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
			const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', args[0]), ...args.slice(1)], { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000 });
			if (result.code !== 0) {
				const output = `${result.stdout}\n${result.stderr}`.trim();
				// The plan's own errors are code's to correct (spotlight.ts
				// withPlanCheck), so the refusal says what resolve says (review-fix6 F1).
				if (phase === 'methodology_approval' && /^FAIL\s+methodology\.json/m.test(output)) return `${file} does not pass ${args[0]}. ${NEXT_METHODOLOGY_FAILING}\n${output.slice(-6000)}`;
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
// Codex review of fix11 (P2): once the launcher has found that no batch can be
// built (BATCH_BLOCKED), it refuses every analyst, so the page must not tell
// the orchestrator to spawn one. The launcher records the reason with the
// fact-check's hash; a changed fact-check makes the record stale.
export function batchBlockedFile(dataDir: string, caseDir: string): string {
	return join(dataDir, 'work', 'batch-blocked', `${createHash('sha256').update(caseDir).digest('hex').slice(0, 16)}.json`);
}
export function factCheckHash(caseDir: string): string {
	try {
		return createHash('sha256').update(readFileSync(join(caseDir, 'data', 'fact-check.json'))).digest('hex');
	} catch {
		return '';
	}
}
export function batchBlocked(dataDir: string | undefined, caseDir: string): string | undefined {
	if (!dataDir) return undefined;
	try {
		const record = JSON.parse(readFileSync(batchBlockedFile(dataDir, caseDir), 'utf8')) as { reason?: unknown; fact_check_sha256?: unknown };
		return typeof record.reason === 'string' && record.fact_check_sha256 === factCheckHash(caseDir) ? record.reason : undefined;
	} catch {
		return undefined;
	}
}

export async function missingForApproval(caseDir: string, resolved: Record<string, unknown>, blocked?: string): Promise<string | undefined> {
	const has = (path: string) => existsSync(join(caseDir, path));
	if (resolved.phase === 'report' && !draftWritten(caseDir)) {
		return 'No report draft is written yet, so the member can only decline the report on the page. If the member wants the report, spawn_agent with agent analyst to write the draft\'s prose, then call open_review again.';
	}
	const ingest = resolved.ingest as { resume_at?: unknown } | undefined;
	// A blocked batch cannot be built from any draft either.
	if (resolved.phase === 'ingest' && ingest?.resume_at === 'decision' && !has('data/knowledge-batch.json') && blocked) {
		return `No knowledge batch can be built for this case, and no analyst can change that: ${blocked} The member can decline ingestion on the page, or request a follow-up to reopen Investigation so the fact-check can be corrected. Do not spawn the analyst; tell the member this.`;
	}
	// The newsroom edition has no local batch and no analyst; its page is the decision (Codex review of simp-next).
	if (resolved.phase === 'ingest' && ingest?.resume_at === 'decision' && process.env.SPOTLIGHT_EDITION !== 'newsroom' && !has('data/knowledge-batch.json') && !has('data/knowledge-batch-draft.json')) {
		return 'No knowledge batch exists, so the member can only decline ingestion on the page. If the member wants to ingest, call spawn_agent with agent analyst (phase-ingest step 1); the harness builds the batch when the analyst finishes. Then call open_review again.';
	}
	return undefined;
}

// Option D (paperwork-p5-option-d.md): members expect the case to go on once
// they decide in the browser, but Osaurus shows nothing outside a turn, so on a
// local tier open_review holds the turn until the decision lands. The time limit
// stays under the page's 45 idle minutes; past it the turn ends as it always did.
export const REVIEW_HOLD_MS = 30 * 60_000;
const HOLD_POLL_MS = 2_000;
export const REVIEW_HOLD_NOTICE = 'Waiting for your decision on the review page. The case carries on here once you decide. To use this chat now, press Stop: the page stays open.';
export type ReviewHold = { ms: number; say?: (text: string) => void; resolved: () => Promise<Record<string, unknown>>; decided?: () => void };

// Whether the gate moved within the limit. Decisions write data/orchestration.json,
// so the resolver runs only when that file changes. The starting point is taken
// before the page opens, so a quick decision is not part of it (Codex R3).
const orchestrationRecord = (caseDir: string) => {
	try { return readFileSync(join(caseDir, 'data/orchestration.json'), 'utf8'); } catch { return ''; }
};
async function decisionLanded(caseDir: string, hold: ReviewHold, start: { seen: string; before: string }, signal?: AbortSignal): Promise<boolean> {
	const record = () => orchestrationRecord(caseDir);
	let { seen } = start;
	const { before } = start;
	const deadline = Date.now() + hold.ms;
	for (let first = true; Date.now() < deadline && !signal?.aborted; first = false) {
		if (!first) await new Promise<void>((done) => {
			const timer = setTimeout(done, Math.min(HOLD_POLL_MS, Math.max(deadline - Date.now(), 0)));
			signal?.addEventListener('abort', () => { clearTimeout(timer); done(); }, { once: true });
		});
		if (record() === seen) continue;
		seen = record();
		if (JSON.stringify(await hold.resolved()) !== before) return true;
	}
	return false;
}

export function openReviewTool(caseDir: string, casesRoot: string, opener?: Opener, check?: GateCheck, note?: () => Promise<string | undefined>, hold?: ReviewHold): ToolDefinition {
	const open: Opener = opener ?? createReviewOpener({ casesRoot });
	return {
		name: 'open_review',
		label: 'Open review page',
		description:
			'Open the review page for the gate this case is waiting on, in the member\'s browser. Call it when a phase skill reaches a gate, then end your turn; the member decides on the page.',
		parameters: Type.Object({}),
		async execute(_id, _params, signal) {
			const failed = await check?.();
			if (failed) throw new Error(`${PAGE_NOT_OPENED}${failed}`);
			const start = hold?.ms ? { seen: orchestrationRecord(caseDir), before: JSON.stringify(await hold.resolved()) } : undefined;
			const result = await open({ caseDir });
			const missing = result.status === 'opened' ? await note?.() : undefined;
			// A page with nothing to approve needs the chat, so it holds nothing.
			if (hold && start && result.status === 'opened' && !missing) {
				hold.say?.(REVIEW_HOLD_NOTICE);
				if (await decisionLanded(caseDir, hold, start, signal)) {
					hold.decided?.();
					return { content: [{ type: 'text', text: REVIEW_DECIDED }], details: { opened: false, decided: true } };
				}
			}
			return { content: [{ type: 'text', text: missing ? `${describeOpenResult(result)}\n${missing}` : describeOpenResult(result) }], details: { opened: result.status === 'opened' } };
		},
	};
}

// Auto-dispatch at resolve (tools/spotlight.ts runDispatches): spawns go
// through the orchestrator's own spawn_agent tool, so its caps, the launcher's
// refusals, cancel on abort and each worker's watchdog apply unchanged; the
// Methodology gate check is the page's own check; the owner's phase skill is
// attached once per orchestrator process.
export function dispatchRunner(spawn: ToolDefinition, invokeSkill: ToolDefinition, check: GateCheck): DispatchRunner {
	const loaded = new Set<string>();
	const text = (result: { content: Array<{ type: string; text?: string }> }) => result.content.map((part) => part.text ?? '').join('\n');
	return {
		spawn: async (agent, task, signal) => {
			return text(await spawn.execute(`auto-${randomUUID()}`, { agent, task }, signal, undefined, undefined as never));
		},
		methodologyCheck: () => check(),
		phaseSkill: async (skill) => {
			if (loaded.has(skill)) return undefined;
			loaded.add(skill);
			// The resolver's owner is always one of the orchestrator's skills; an
			// owner that is not (none at complete) attaches nothing.
			try {
				return text(await invokeSkill.execute(`auto-${randomUUID()}`, { skill }, undefined, undefined, undefined as never));
			} catch (error) {
				if (/is not one of this agent's skills/.test((error as Error).message)) return undefined;
				throw error;
			}
		},
	};
}
