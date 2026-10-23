// Case semantic search for the fact-checker (D9-safe). Before each fact-checker
// task starts, including follow-up cycles, the launcher asks the case-search
// seam (`integrations/case_search.py`, over Engine's `bsig wiki case-search`)
// for each claim's top passages in the case's own research/ captures, leaving
// out the research swarm's lane folders, and appends them to the task between
// markers, as data. Retrieval only: it sets no verdict. Without Engine, with
// Engine's switch off, or on any failure, the task goes unchanged.
import { CHECKOUT_ROOT } from './roles.ts';
import { programEnv, runFixed } from './tools/run.ts';
import type { Env } from './tools/run.ts';

const CASE_SEARCH_TIMEOUT_MS = 300_000;

export async function withCasePassages(
	role: string,
	task: string,
	options: { caseDir: string; python: string; env: Env; log: (message: string) => void },
): Promise<string> {
	if (role !== 'fact-checker') return task;
	try {
		const result = await runFixed(
			options.python,
			['-m', 'integrations.case_search', '--case', options.caseDir, '--findings', '--for', 'fact-checker', '--format', 'prompt'],
			{ env: programEnv(options.env, ['SPOTLIGHT_BSIG']), cwd: CHECKOUT_ROOT, timeoutMs: CASE_SEARCH_TIMEOUT_MS },
		);
		if (result.code !== 0) {
			options.log(`case search skipped (exit ${result.code})`);
			return task;
		}
		const block = result.stdout.trim();
		return block ? `${task}\n\n${block}` : task;
	} catch (error) {
		options.log(`case search skipped: ${(error as Error).message}`);
		return task;
	}
}
