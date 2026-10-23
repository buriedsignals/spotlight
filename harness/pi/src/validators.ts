// Validators the harness runs itself at fixed points, instead of asking the
// model to run them through a shell: when a worker finishes, the checks for
// what that worker just wrote run against the active case, and their outcome
// is attached to the worker's answer for the orchestrator.
import { CHECKOUT_ROOT } from './roles.ts';
import { runPython } from './tools/run.ts';
import type { ToolContext } from './tools/run.ts';

type Check = { name: string; args: string[] };

// Phases the case is in once an investigator planning task ends: still
// planning, or waiting for methodology approval because methodology.json now
// exists. Either way the investigator wrote a methodology, not findings.
const PLANNING_PHASES = new Set(['brief', 'methodology', 'methodology_approval']);

function checksAfter(role: string, phase: string, context: ToolContext): Check[] {
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
	if (role === 'investigator') return [caseCheck];
	if (role === 'fact-checker') {
		return [caseCheck, { name: 'validate-fact-check.py', args: [script('validate-fact-check.py'), context.caseDir] }];
	}
	return [];
}

export type ValidationOutcome = { ran: string[]; passed: boolean; report: string };

export async function validateAfterWorker(role: string, phase: string, context: ToolContext): Promise<ValidationOutcome> {
	const checks = checksAfter(role, phase, context);
	const lines: string[] = [];
	let passed = true;
	for (const check of checks) {
		let text: string;
		let ok: boolean;
		try {
			const result = await runPython(context, check.args, { timeoutMs: 300_000 });
			ok = result.code === 0;
			text = `${result.stdout}\n${result.stderr}`.trim();
		} catch (error) {
			ok = false;
			text = (error as Error).message;
		}
		passed &&= ok;
		lines.push(`${check.name}: ${ok ? 'PASSED' : 'FAILED'}${text ? `\n${text.slice(-6000)}` : ''}`);
	}
	return { ran: checks.map((check) => check.name), passed, report: lines.join('\n\n') };
}
