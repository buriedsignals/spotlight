// Case semantic search for the fact-checker (D9-safe). Before each fact-checker
// task starts, including follow-up cycles, the launcher asks the case-search
// seam (`integrations/case_search.py`, over Engine's `bsig wiki case-search`)
// for each claim's top passages in the case's own research/ captures, leaving
// out everything D9 hides, and appends them to the task inside one labelled
// untrusted-data block. Retrieval only: it sets no verdict. Without Engine,
// with Engine's switch off, or on any failure, the task goes unchanged; a
// refusal (exit 2: not the active case) rejects, so the spawn fails and the
// orchestrator gets the reason. The launcher runs this inside the worker
// queue, and a cancelled spawn stops it.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { CHECKOUT_ROOT } from './roles.ts';
import { programEnv, runFixed } from './tools/run.ts';
import type { Env } from './tools/run.ts';

const CASE_SEARCH_TIMEOUT_MS = 300_000;
// Where Engine finds its base folder on Windows and Linux; never provider keys.
const ENGINE_BASE_VARS = ['USERPROFILE', 'APPDATA', 'LOCALAPPDATA'];

// The variables a seam over Engine's `bsig` needs to find it and its base folder.
export function engineEnv(env: Env): string[] {
	return ['SPOTLIGHT_BSIG', ...ENGINE_BASE_VARS, ...Object.keys(env).filter((name) => name.startsWith('XDG_'))];
}

export async function withCasePassages(
	role: string,
	task: string,
	options: { caseDir: string; casesRoot: string; python: string; env: Env; log: (message: string) => void; signal?: AbortSignal; findings?: string[] },
): Promise<string> {
	if (role !== 'fact-checker') return task;
	// A fact-check batch (#274) searches for its own findings' claims only.
	const which = options.findings?.length ? options.findings.flatMap((id) => ['--finding', id]) : ['--findings'];
	let result;
	try {
		result = await runFixed(
			options.python,
			['-m', 'integrations.case_search', '--case', options.caseDir, ...which, '--for', 'fact-checker', '--format', 'prompt'],
			{
				env: programEnv(options.env, engineEnv(options.env), { SPOTLIGHT_CASES_ROOT: options.casesRoot, SPOTLIGHT_CASE_DIR: options.caseDir }),
				cwd: CHECKOUT_ROOT,
				timeoutMs: CASE_SEARCH_TIMEOUT_MS,
				signal: options.signal,
			},
		);
	} catch {
		options.signal?.throwIfAborted();
		options.log('case search skipped: it could not run');
		return task;
	}
	if (result.code === 2) throw new Error(result.stderr.match(/case search refused: .*/)?.[0] ?? 'case search refused');
	if (result.code !== 0) {
		options.log(`case search skipped (exit ${result.code})`);
		return task;
	}
	const block = result.stdout.trim();
	return block ? `${task}\n\n${block}` : task;
}

// Sensitive mode leaves the member wiki as the only source outside the case,
// but the trained worker prompts skip the wiki whenever VAULT_PATH is "none",
// which it always is (gold sens2 on d49a9a01: no investigator called
// query_vault in 2 of 3 cases). So in sensitive mode the launcher searches the
// wiki itself with the approved brief, clause by clause, and appends the hits to the
// investigator's and the fact-checker's task, inside one untrusted-data block
// (scripts/query_vault.py --format prompt). Each note the block shows is also
// saved whole under research/wiki/ (as the cloud audience reads it, so
// protected sources stay out), and the block names that file, so a finding can
// cite and anchor it like any capture. Without a wiki, without Engine or on any
// failure the task goes unchanged.
const WIKI_ROLES = ['investigator', 'fact-checker'];
const BRIEF_QUERY_CHARS = 4000;

export async function withWikiPassages(
	role: string,
	task: string,
	options: { caseDir: string; python: string; configPath: string; env: Env; sensitive: boolean; log: (message: string) => void; signal?: AbortSignal },
): Promise<string> {
	if (!options.sensitive || !WIKI_ROLES.includes(role)) return task;
	let brief: string;
	try {
		brief = readFileSync(join(options.caseDir, 'brief-directions.txt'), 'utf8').replace(/\s+/g, ' ').trim().slice(0, BRIEF_QUERY_CHARS);
	} catch {
		return task;
	}
	if (!brief) return task;
	let result;
	try {
		result = await runFixed(
			options.python,
			[join(CHECKOUT_ROOT, 'scripts/query_vault.py'), '--config', options.configPath, '--case-dir', options.caseDir, '--limit', '6', '--brief', '--format', 'prompt', '--capture-dir', join(options.caseDir, 'research', 'wiki'), '--', brief],
			{ env: programEnv(options.env, engineEnv(options.env)), cwd: CHECKOUT_ROOT, timeoutMs: CASE_SEARCH_TIMEOUT_MS, signal: options.signal },
		);
	} catch {
		options.signal?.throwIfAborted();
		options.log('wiki passages skipped: the wiki search could not run');
		return task;
	}
	if (result.code !== 0) {
		options.log(`wiki passages skipped (exit ${result.code})`);
		return task;
	}
	const block = result.stdout.trim();
	return block ? `${task}\n\n${block}` : task;
}
