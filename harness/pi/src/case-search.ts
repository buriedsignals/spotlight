// Case semantic search for the fact-checker (D9-safe). Before each fact-checker
// task starts, including follow-up cycles, the launcher asks the case-search
// seam (`integrations/case_search.py`, over Engine's `bsig wiki case-search`)
// for each claim's top passages in the case's own research/ captures, leaving
// out everything D9 hides, and appends them to the task inside one labelled
// untrusted-data block. Retrieval only: it sets no verdict. Without Engine,
// with Engine's switch off, or on any failure, the task goes unchanged. The
// launcher runs this inside the worker queue, and a cancelled spawn stops it.
import { CHECKOUT_ROOT } from './roles.ts';
import { programEnv, runFixed } from './tools/run.ts';
import type { Env } from './tools/run.ts';

const CASE_SEARCH_TIMEOUT_MS = 300_000;
// Where Engine finds its base folder on Windows and Linux; never provider keys.
const ENGINE_BASE_VARS = ['USERPROFILE', 'APPDATA', 'LOCALAPPDATA'];

function engineEnv(env: Env): string[] {
	return ['SPOTLIGHT_BSIG', ...ENGINE_BASE_VARS, ...Object.keys(env).filter((name) => name.startsWith('XDG_'))];
}

export async function withCasePassages(
	role: string,
	task: string,
	options: { caseDir: string; casesRoot: string; python: string; env: Env; log: (message: string) => void; signal?: AbortSignal },
): Promise<string> {
	if (role !== 'fact-checker') return task;
	try {
		const result = await runFixed(
			options.python,
			['-m', 'integrations.case_search', '--case', options.caseDir, '--findings', '--for', 'fact-checker', '--format', 'prompt'],
			{
				env: programEnv(options.env, engineEnv(options.env), { SPOTLIGHT_CASES_ROOT: options.casesRoot, SPOTLIGHT_CASE_DIR: options.caseDir }),
				cwd: CHECKOUT_ROOT,
				timeoutMs: CASE_SEARCH_TIMEOUT_MS,
				signal: options.signal,
			},
		);
		if (result.code !== 0) {
			options.log(`case search skipped (exit ${result.code})`);
			return task;
		}
		const block = result.stdout.trim();
		return block ? `${task}\n\n${block}` : task;
	} catch (error) {
		options.signal?.throwIfAborted();
		options.log('case search skipped: it could not run');
		return task;
	}
}
