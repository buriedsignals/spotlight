// The decision-judged Anomaly step (scripts/anomaly-decision.py). Small
// models rarely plan Anomaly themselves (LFM3 and the base 12B on Osaurus,
// 7513e421), so when a PLANNING investigator finishes on a local tier the
// harness asks the decision model whether Anomaly would help with the case's
// tables; on yes the script writes the plan's anomaly section, which the
// member approves or not. Every EXECUTION task then carries the script's
// ANOMALY block, and the resolver keeps the Investigation gate closed until
// each table has a run. Any failure here changes nothing: the model's own
// plan stands.
import { join } from 'node:path';
import { engineEnv } from './case-search.ts';
import { CHECKOUT_ROOT } from './roles.ts';
import { programEnv, runFixed } from './tools/run.ts';
import type { Env } from './tools/run.ts';

const SCRIPT = join(CHECKOUT_ROOT, 'scripts', 'anomaly-decision.py');
// A local d1 answers in seconds; the cloud route retries twice.
const DECISION_TIMEOUT_MS = 120_000;

type Options = { caseDir: string; python: string; configPath: string; sensitive: boolean; env: Env; log: (message: string) => void; signal?: AbortSignal };

function run(args: string[], options: Options) {
	return runFixed(options.python, [SCRIPT, options.caseDir, ...args], {
		// The Anomaly install and, for the cloud route only, the OpenRouter key;
		// never a route token.
		env: programEnv(options.env, ['ANOMALY_HOME', 'OPENROUTER_API_KEY', ...engineEnv(options.env)], {
			SPOTLIGHT_CONFIG: options.configPath,
			SPOTLIGHT_SENSITIVE: options.sensitive ? 'true' : 'false',
		}),
		cwd: CHECKOUT_ROOT,
		timeoutMs: DECISION_TIMEOUT_MS,
		signal: options.signal,
	});
}

export async function decideAnomaly(options: Options): Promise<void> {
	try {
		const result = await run(['--config', options.configPath], options);
		const line = result.stdout.trim().split('\n').at(-1);
		options.log(result.code === 0 && line ? line : `anomaly-decision: failed (exit ${result.code})`);
	} catch (error) {
		options.signal?.throwIfAborted();
		options.log(`anomaly-decision: could not run (${(error as Error).message})`);
	}
}

// The ANOMALY block for an investigator EXECUTION task, or the task unchanged.
export async function withAnomalyBlock(role: string, task: string, options: Options): Promise<string> {
	if (role !== 'investigator') return task;
	try {
		const result = await run(['--task-block'], options);
		const block = result.code === 0 ? result.stdout.trim() : '';
		return block ? `${task}\n\n${block}` : task;
	} catch {
		options.signal?.throwIfAborted();
		return task;
	}
}
