// Validators the harness runs itself at fixed points, instead of asking the
// model to run them through a shell: when a worker finishes, the checks for
// what that worker just wrote run against the active case, and their outcome
// is attached to the worker's answer for the orchestrator. After the analyst,
// the fixed point is the batch build from its proposal.
import { randomUUID } from 'node:crypto';
import { localTier, writeConfined } from './tools/case-files.ts';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { RESEARCH_SWITCH } from './config.ts';
import { CHECKOUT_ROOT } from './roles.ts';
import { RECEIPTS, runPython } from './tools/run.ts';
import type { ToolContext } from './tools/run.ts';

type Check = { name: string; args: string[] };

// Phases the case is in once an investigator planning task ends: still
// planning, or waiting for methodology approval because methodology.json now
// exists. Either way the investigator wrote a methodology, not findings.
export const PLANNING_PHASES = new Set(['brief', 'methodology', 'methodology_approval']);
// At Research the investigator works in AMENDMENT mode: suggested marks, an
// amendment or a stall round, which the resolver and its transitions check
// (the suggestions' schema, D8, D1). It wrote no findings to validate.
const RESEARCH_PHASES = new Set(['research', 'research_approval']);

function checksAfter(role: string, phase: string, producerModel: string, context: ToolContext): Check[] {
	const script = (name: string) => `${CHECKOUT_ROOT}/scripts/${name}`;
	const caseCheck = { name: 'validate-case.py', args: [script('validate-case.py'), context.caseDir] };
	if (role === 'investigator' && PLANNING_PHASES.has(phase)) {
		return [
			{
				name: 'validate-methodology-navigator.py',
				args: [script('validate-methodology-navigator.py'), context.caseDir, '--config', context.configPath],
			},
		];
	}
	if (role === 'investigator' && RESEARCH_PHASES.has(phase)) return [];
	// The Indicator Framework file, when a worker wrote one: ids, references
	// and the no-score rule.
	const indicatorCheck = existsSync(join(context.caseDir, 'data/indicator-framework.json'))
		? [{ name: 'validate-indicator-framework.py', args: [script('validate-indicator-framework.py'), context.caseDir] }]
		: [];
	if (role === 'investigator') return [caseCheck, ...indicatorCheck];
	if (role === 'fact-checker') {
		// Before the checks, code repairs evidence line ranges the fact-checker
		// miscounted (a quote found once in its cited file) and fills in the full
		// refs for passages cited by bare id, the fingerprints a model must never
		// copy by hand.
		const resolveRefs = { name: 'write-source-expressions.py --resolve-fact-check-refs', args: [script('write-source-expressions.py'), context.caseDir, '--resolve-fact-check-refs'] };
		return [resolveRefs, caseCheck, { name: 'validate-fact-check.py', args: [script('validate-fact-check.py'), context.caseDir] }, ...indicatorCheck];
	}
	// The analyst's gate summary is checked by the case validator (it checks
	// data/summary.json against the findings and the fact-check), its report
	// draft by the draft's own check.
	if (role === 'analyst' && phase === 'investigation_gate_approval') return [caseCheck];
	if (role === 'analyst' && phase === 'report') return [{ name: 'validate-report-draft.py', args: [script('validate-report-draft.py'), context.caseDir] }];
	if (role === 'analyst' && phase === 'ingest') {
		// --review writes the draft for the ingest page, and the final batch when
		// no flag awaits a disposition. The producer models are needed on the
		// first build only; the builder keeps them in its id map.
		return [
			{
				name: 'build-knowledge-batch.py',
				args: [
					script('build-knowledge-batch.py'),
					context.caseDir,
					'--review',
					'--model', `investigator=${producerModel}`,
					'--model', `fact-checker=${producerModel}`,
				],
			},
		];
	}
	return [];
}

export type ValidationOutcome = { ran: string[]; passed: boolean; report: string };

// B28: the check that says a worker's write-file deliverable is done, by file:
// the same check the harness runs after that worker. The knowledge proposal is
// refused at write time and ends its turn already (registerProposalEnd); the
// research-step files have no check of their own until the resolver applies them.
const DELIVERABLE_CHECK: Record<string, [role: string, phase: string]> = {
	'data/methodology.json': ['investigator', 'methodology'],
	'data/report-draft.json': ['analyst', 'report'],
};
export const hasDeliverableCheck = (file: string) => Object.hasOwn(DELIVERABLE_CHECK, file);
export async function deliverablePasses(file: string, context: ToolContext): Promise<boolean> {
	const [role, phase] = DELIVERABLE_CHECK[file];
	for (const check of checksAfter(role, phase, '', context)) {
		const result = await runPython(context, check.args, { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000 });
		if (result.code !== 0) return false;
	}
	return true;
}

// Before spending a worker on verification, require its investigation inputs
// to validate. Existing fact-check errors remain that worker's repair job.
// Use the validator's full owner-tagged diagnostics, never an abbreviated
// report: an investigator error near the end must still block dispatch.
export async function validateInvestigationInputs(context: ToolContext, signal?: AbortSignal): Promise<ValidationOutcome> {
	const name = 'validate-case.py';
	try {
		const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', name), context.caseDir, '--json'], { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000, signal });
		const data = JSON.parse(result.stdout);
		if (!data || typeof data.ok !== 'boolean' || !Array.isArray(data.errors) || data.errors.some((row: { message?: unknown; owner?: unknown } | null) => !row || typeof row.message !== 'string' || !row.message || (typeof row.owner !== 'string' || !['investigator', 'fact-checker'].includes(row.owner))) || ![0, 1].includes(result.code) || data.ok !== (result.code === 0) || data.ok !== (data.errors.length === 0) || result.stderr.trim()) {
			throw new Error('invalid investigation validator JSON/exit status');
		}
		const passed = data.errors.every((row: { owner: string }) => row.owner === 'fact-checker');
		const report = data.errors.map((row: { message: string; owner: string }) => `  • [${row.owner}] ${row.message}`).join('\n');
		return { ran: [name], passed, report: `${name}: ${passed ? 'inputs ready' : 'FAILED'}\n${report}` };
	} catch (error) {
		signal?.throwIfAborted();
		return { ran: [name], passed: false, report: `${name}: FAILED\n${(error as Error).message}` };
	}
}

export async function validateAfterWorker(role: string, phase: string, producerModel: string, context: ToolContext): Promise<ValidationOutcome> {
	const checks = checksAfter(role, phase, producerModel, context);
	const lines: string[] = [];
	const captured: string[] = [];
	let passed = true;
	for (const check of checks) {
		let text: string;
		let ok: boolean;
		try {
			const result = await runPython(context, check.args, { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000 });
			ok = result.code === 0;
			text = `${result.stdout}\n${result.stderr}`.trim();
		} catch (error) {
			ok = false;
			text = (error as Error).message;
		}
		passed &&= ok;
		captured.push(`${check.name}: ${ok ? 'PASSED' : 'FAILED'}\n${text}`);
		const failures = text.split('\n').filter((line) => /^\s*FAIL\b/.test(line));
		const detail = failures.length ? failures.join('\n') : text;
		lines.push(`${check.name}: ${ok ? 'PASSED' : 'FAILED'}${!ok && detail ? `\n${detail.slice(0, 1800)}${detail.length > 1800 ? `\n[${detail.length - 1800} diagnostic characters omitted; read the full diagnostic]` : ''}` : ''}`);
	}
	const full = captured.join('\n\n');
	let diagnostic: string | undefined;
	if (full.length > 1800) {
		diagnostic = writeConfined(context.caseDir, context.writeRoot, `data/harness-checks/${role}/${randomUUID()}.txt`, full);
		lines.push(`Full diagnostic: ${diagnostic}`);
	}
	return { ran: checks.map((check) => check.name), passed, report: lines.join('\n\n') };
}

// Only the launcher supplies this pre-worker whitelist. A partial batch may
// leave future findings unassessed; it never excuses a malformed written claim.
export async function validateFactCheckBatch(context: ToolContext, expectedFutureMissing: string[], signal?: AbortSignal): Promise<ValidationOutcome> {
	const future = new Set(expectedFutureMissing.map((id) => `missing:${id}`));
	const ran: string[] = [];
	const lines: string[] = [];
	for (const check of checksAfter('fact-checker', 'investigation', '', context)) {
		signal?.throwIfAborted();
		ran.push(check.name);
		try {
			const json = check.name === 'validate-fact-check.py';
			const result = await runPython(context, [...check.args, ...(json ? ['--json'] : [])], { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000, signal });
			signal?.throwIfAborted();
			let passed = result.code === 0;
			let report = `${result.stdout}\n${result.stderr}`.trim();
			if (json) {
				const data = JSON.parse(result.stdout);
				const rows = data?.verdicts;
				if (!data || typeof data.ok !== 'boolean' || !Array.isArray(rows) || rows.length === 0 || rows.some((row) => !row || typeof row.ok !== 'boolean' || !['string', 'number'].includes(typeof row.id) || typeof row.reason !== 'string' || (row.owner !== undefined && typeof row.owner !== 'string'))) {
					throw new Error('invalid validator JSON: expected ok and non-empty verdicts with id, ok and reason');
				}
				const failed = rows.filter((row) => !row.ok);
				if ((result.code !== 0 && result.code !== 3) || data.ok !== (result.code === 0) || data.ok !== (failed.length === 0)) {
					throw new Error(`invalid validator JSON/exit status combination (exit ${result.code})`);
				}
				// Case 2bd8d15c: a missing claim link stopped the batches at 3 of 28. The fact-checker
				// cannot repair one, and a verdict does not need it, so on a local tier later batches
				// still run; the last one reports it. Any other broken record still stops them.
				const waits = (row: { id: unknown; owner?: unknown }) => (row.owner === 'fact-checker' && future.has(String(row.id))) || (future.size > 0 && localTier(context.env) && row.owner === 'investigator' && String(row.id).startsWith('record:') && /finding link|not linked to finding|structured claim_links/.test(String((row as { reason?: unknown }).reason)));
				const pending = failed.filter(waits);
				const errors = failed.filter((row) => !pending.includes(row));
				passed = errors.length === 0;
				report = errors.map((row) => `FAIL ${row.id}${row.owner ? ` [${row.owner}]` : ''}${row.locator ? ` ${row.locator} (${row.finding_id})` : ''}: ${row.reason}`).join('\n');
				const unassessed = pending.filter((row) => String(row.id).startsWith('missing:'));
				if (unassessed.length) report += `${report ? '\n' : ''}${unassessed.length} future findings remain unassessed: ${unassessed.map((row) => row.id.slice('missing:'.length)).join(', ')}.`;
			}
			lines.push(`${check.name}: ${passed ? 'batch progress valid' : 'FAILED'}${report ? `\n${report}` : ''}`);
			if (!passed) return { ran, passed: false, report: lines.join('\n\n') };
		} catch (error) {
			signal?.throwIfAborted();
			lines.push(`${check.name}: FAILED\n${(error as Error).message}`);
			return { ran, passed: false, report: lines.join('\n\n') };
		}
	}
	return { ran, passed: true, report: lines.join('\n\n') };
}

// The fact-check validator's FAIL lines on the case as it is now, or '' when
// it passes. A correction's defects come from here, not from the launcher's
// memory: after a restart that is empty, and after another batch's check it is
// stale (audit-feedback-loop G1). Read-only: unlike the post-worker checks it
// repairs no refs.
export async function factCheckDefects(context: ToolContext, signal?: AbortSignal): Promise<string> {
	const name = 'validate-fact-check.py';
	const result = await runPython(context, [join(CHECKOUT_ROOT, 'scripts', name), context.caseDir], { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000, signal });
	if (result.code === 0) return '';
	const failures = `${result.stdout}\n${result.stderr}`.split('\n').filter((line) => /^\s*FAIL\b/.test(line));
	return failures.length ? `${name}: FAILED\n${failures.join('\n')}` : '';
}

// Whether the evidence chain validates now: validate-case.py and
// validate-fact-check.py both pass, as the fact-checker's repair loop checks.
export async function evidenceValidates(context: ToolContext): Promise<boolean> {
	for (const name of ['validate-case.py', 'validate-fact-check.py']) {
		const result = await runPython(context, [`${CHECKOUT_ROOT}/scripts/${name}`, context.caseDir], { keys: [RESEARCH_SWITCH, RECEIPTS], timeoutMs: 300_000 });
		if (result.code !== 0) return false;
	}
	return true;
}
