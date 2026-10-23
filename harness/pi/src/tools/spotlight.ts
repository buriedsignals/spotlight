// Resolver and transition tools, bound by the harness to the active case and
// backed by the product orchestration module (scripts/spotlight-orchestration.py).
import { execFile } from 'node:child_process';
import { lstatSync, mkdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { CHECKOUT_ROOT } from '../roles.ts';

const ADAPTER = resolve(CHECKOUT_ROOT, 'scripts/spotlight-orchestration.py');

// The only transitions a model may request. Gate approvals, the report
// decision and the ingest request or decline come from the review page.
export const MODEL_OPERATIONS = ['recordAttempt', 'requestFollowUp', 'sealInvestigationGate', 'decideIngest'] as const;
type ModelOperation = (typeof MODEL_OPERATIONS)[number];
type Payload = Record<string, unknown>;

export type AdapterRunner = (arguments_: string[], signal?: AbortSignal) => Promise<string>;

export class TransitionRefused extends Error {}

export function pythonAdapter(python: string): AdapterRunner {
	return (arguments_, signal) => {
		const { promise, resolve: done, reject } = Promise.withResolvers<string>();
		execFile(python, [ADAPTER, ...arguments_], { encoding: 'utf8', signal }, (error, stdout, stderr) => {
			if (error) {
				reject(new Error(stderr.trim() || stdout.trim() || error.message));
				return;
			}
			done(stdout);
		});
		return promise;
	};
}

function isCaseWithinRoot(casesRoot: string, caseDir: string): boolean {
	const child = relative(casesRoot, caseDir);
	return child !== '' && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child);
}

function ensureRealDirectory(path: string, label: string): void {
	try {
		const metadata = lstatSync(path);
		if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
			throw new Error(`Spotlight ${label} must be a directory`);
		}
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
		mkdirSync(path, { mode: 0o700 });
	}
}

export type ActiveCase = { casesRoot: string; caseDir: string };

// The active case is a capability fixed at launch: a real directory directly
// or indirectly under the cases root, never a symlink, created if absent.
export function bindActiveCase(casesRoot: string, caseDir: string): ActiveCase {
	if (!casesRoot.trim() || !caseDir.trim()) {
		throw new Error('Spotlight tools require SPOTLIGHT_CASES_ROOT and SPOTLIGHT_CASE_DIR');
	}
	const configuredRoot = resolve(casesRoot);
	const configuredCase = resolve(caseDir);
	if (!isCaseWithinRoot(configuredRoot, configuredCase)) {
		throw new Error('Spotlight active case must be a child of the configured cases root');
	}
	const canonicalRoot = realpathSync(configuredRoot);
	if (!statSync(canonicalRoot).isDirectory()) throw new Error('Spotlight cases root must be a directory');
	const canonicalParent = realpathSync(dirname(configuredCase));
	if (canonicalParent !== canonicalRoot && !isCaseWithinRoot(canonicalRoot, canonicalParent)) {
		throw new Error('Spotlight active case parent resolves outside the configured cases root');
	}
	ensureRealDirectory(configuredCase, 'active case path');
	const canonicalCase = realpathSync(configuredCase);
	if (!isCaseWithinRoot(canonicalRoot, canonicalCase)) {
		throw new Error('Spotlight active case resolves outside the configured cases root');
	}
	ensureRealDirectory(resolve(canonicalCase, 'data'), 'active case data path');
	return { casesRoot: canonicalRoot, caseDir: canonicalCase };
}

function stringField(payload: Payload, name: string): string {
	const value = payload[name];
	if (typeof value !== 'string' || value.trim() === '') {
		throw new TransitionRefused(`spotlight_transition payload.${name} must be a non-empty string`);
	}
	return value;
}

// Refuses anything outside the model's operations, whatever the schema let
// through. The refusal names where the decision does come from.
// `role` is the harness's: the process role that holds the tool, never model input.
export function transitionArguments(operation: unknown, payload: Payload, active: ActiveCase, role: string): string[] {
	const boundary = ['--authorized-cases-root', active.casesRoot, active.caseDir];
	if (!MODEL_OPERATIONS.includes(operation as ModelOperation)) {
		throw new TransitionRefused(
			`spotlight_transition refuses "${String(operation)}": gate approvals and the report decision are recorded by the review page, not by the model.`,
		);
	}
	switch (operation as ModelOperation) {
		case 'recordAttempt':
			return ['record-attempt', stringField(payload, 'kind'), '--gap', stringField(payload, 'gap'), ...boundary];
		case 'requestFollowUp':
			// Recorded on the agent channel with the requesting role, never a member name.
			return [
				'request-follow-up',
				'--instructions',
				stringField(payload, 'instructions'),
				'--requested-by-role',
				role,
				...boundary,
			];
		case 'sealInvestigationGate':
			return ['seal-investigation-gate', ...boundary];
		case 'decideIngest': {
			const decision = stringField(payload, 'decision');
			if (decision !== 'completed') {
				throw new TransitionRefused(
					`spotlight_transition refuses decideIngest "${decision}": the ingest request or decline is recorded by the review page; the model may only seal a completed ingest.`,
				);
			}
			return ['decide-ingest', 'completed', ...boundary];
		}
	}
}

async function resolveCase(active: ActiveCase, runAdapter: AdapterRunner, signal?: AbortSignal) {
	const output = await runAdapter(
		['status', '--json', '--authorized-cases-root', active.casesRoot, active.caseDir],
		signal,
	);
	try {
		return JSON.parse(output) as Record<string, unknown>;
	} catch (error) {
		throw new Error(`Spotlight resolver returned invalid JSON: ${(error as Error).message}`);
	}
}

function jsonResult(value: unknown) {
	return { content: [{ type: 'text' as const, text: JSON.stringify(value) }], details: undefined };
}

export function spotlightTools(active: ActiveCase, runAdapter: AdapterRunner, role: string): ToolDefinition[] {
	const resolveTool: ToolDefinition = {
		name: 'spotlight_resolve',
		label: 'Spotlight resolve',
		description:
			'Resolve the durable phase, owner, requirements, attempts, and resume point for the harness-bound active Spotlight case without writing state.',
		parameters: Type.Object({}),
		async execute(_id, _params, signal) {
			return jsonResult(await resolveCase(active, runAdapter, signal));
		},
	};
	const transitionTool: ToolDefinition = {
		name: 'spotlight_transition',
		label: 'Spotlight transition',
		description:
			'Apply one validated transition to the harness-bound active Spotlight case, then return its newly resolved state. Allowed: recordAttempt {kind, gap}, requestFollowUp {instructions}, sealInvestigationGate {}, decideIngest {decision: "completed"}.',
		parameters: Type.Object({
			operation: Type.Union(MODEL_OPERATIONS.map((name) => Type.Literal(name))),
			payload: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
		}),
		executionMode: 'sequential',
		async execute(_id, params, signal) {
			const input = params as { operation: unknown; payload?: Payload };
			const arguments_ = transitionArguments(input.operation, input.payload ?? {}, active, role);
			if (signal?.aborted) throw signal.reason ?? new Error('Spotlight transition aborted before commit');
			// Once started, a transition runs to its durable outcome even if the
			// caller aborts: the resolver write is atomic and must not be orphaned.
			await runAdapter(arguments_);
			return jsonResult(await resolveCase(active, runAdapter));
		},
	};
	return [resolveTool, transitionTool];
}
