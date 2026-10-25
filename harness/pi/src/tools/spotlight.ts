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
export const MODEL_OPERATIONS = ['recordAttempt', 'requestFollowUp', 'sealInvestigationGate', 'decideIngest', 'startResearchRound', 'recordAmendment', 'continueWithoutResearch'] as const;
type ModelOperation = (typeof MODEL_OPERATIONS)[number];
type Payload = Record<string, unknown>;

export type AdapterRunner = (arguments_: string[], signal?: AbortSignal) => Promise<string>;

export class TransitionRefused extends Error {}

// `env`: the program's environment (default: this process's).
export function pythonAdapter(python: string, env?: Record<string, string | undefined>): AdapterRunner {
	return (arguments_, signal) => {
		const { promise, resolve: done, reject } = Promise.withResolvers<string>();
		execFile(python, [ADAPTER, ...arguments_], { encoding: 'utf8', signal, env }, (error, stdout, stderr) => {
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
		// Both read case files the model wrote and are checked in code: the
		// round against D1, the amendment against the member's marks (D8).
		case 'startResearchRound':
			return ['start-research-round', ...boundary];
		case 'recordAmendment':
			return ['record-amendment', ...boundary];
		// Only while Research is off has paused the case, and only at the
		// member's request; the member then approves the plan at the Methodology gate.
		case 'continueWithoutResearch':
			return ['continue-without-research', ...boundary];
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

// Code runs the round while the resolver waits for merged lanes or their
// triage; the other Research steps (a stall round's topics, the suggested
// marks, the amendment) are the model's.
export function runsResearchRound(resolved: Record<string, unknown>): boolean {
	const step = (resolved.research as { resume_at?: unknown } | undefined)?.resume_at;
	return resolved.phase === 'research' && (step === 'lanes' || step === 'triage');
}

// Runs the approved research topics in code (the launcher's lane runner) and
// returns the round's outcome. `retry` repeats a round that stopped.
export type ResearchRunner = (retry: boolean, signal?: AbortSignal) => Promise<unknown>;

export function spotlightTools(active: ActiveCase, runAdapter: AdapterRunner, role: string, research?: ResearchRunner): ToolDefinition[] {
	const resolveTool: ToolDefinition = {
		name: 'spotlight_resolve',
		label: 'Spotlight resolve',
		description:
			'Resolve the durable phase, owner, requirements, attempts, and resume point for the harness-bound active Spotlight case. At Research the harness first runs the approved research topics itself and adds the round\'s outcome as research_round. Set retry_research only when the member asks to retry a research round that stopped.',
		parameters: Type.Object({ retry_research: Type.Optional(Type.Boolean()) }),
		async execute(_id, params, signal) {
			const resolved = await resolveCase(active, runAdapter, signal);
			// Approval of the methodology is what starts Research: the resolver
			// reports the phase and code runs the approved topics. The model only
			// resolves; it chooses no topic and starts no swarm.
			if (!research || !runsResearchRound(resolved)) return jsonResult(resolved);
			const round = await research((params as { retry_research?: boolean }).retry_research === true, signal);
			return jsonResult({ ...(await resolveCase(active, runAdapter, signal)), research_round: round });
		},
	};
	const transitionTool: ToolDefinition = {
		name: 'spotlight_transition',
		label: 'Spotlight transition',
		description:
			'Apply one validated transition to the harness-bound active Spotlight case, then return its newly resolved state. Allowed: recordAttempt {kind, gap}, requestFollowUp {instructions}, sealInvestigationGate {}, decideIngest {decision: "completed"}, startResearchRound {} (after writing data/research-round.json), recordAmendment {} (after writing data/methodology-amendment.json), continueWithoutResearch {} (only when the member asks, while Research is off). Gate approvals and the report decision are recorded by the review page, never through this tool.',
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
