import { execFile } from 'node:child_process';
import { lstatSync, mkdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { defineTool } from '@flue/runtime';
import * as v from 'valibot';
import { HARNESS_ROOT } from './roles.ts';

const PYTHON = process.env.SPOTLIGHT_PYTHON ?? process.env.PYTHON ?? 'python3';
const ADAPTER = resolve(HARNESS_ROOT, 'scripts/spotlight-orchestration.py');
const OPERATIONS = [
	'approve',
	'recordAttempt',
	'requestFollowUp',
	'sealGate1',
	'decideReport',
	'decideIngest',
] as const;

type Payload = Record<string, unknown>;
type AdapterRunner = (arguments_: string[], signal?: AbortSignal) => Promise<string>;

type SpotlightToolOptions = {
	activeCaseDir: string;
	casesRoot: string;
	runAdapter?: AdapterRunner;
};

function nativeRunAdapter(arguments_: string[], signal?: AbortSignal): Promise<string> {
	const { promise, resolve: resolveOutput, reject } = Promise.withResolvers<string>();
	execFile(PYTHON, [ADAPTER, ...arguments_], { encoding: 'utf8', signal }, (error, stdout, stderr) => {
		if (error) {
			reject(new Error(stderr.trim() || stdout.trim() || error.message));
			return;
		}
		resolveOutput(stdout);
	});
	return promise;
}

function isCaseWithinRoot(casesRoot: string, caseDir: string): boolean {
	const child = relative(casesRoot, caseDir);
	return child !== '' && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child);
}

function activeCaseCapability(
	casesRoot: string,
	activeCaseDir: string,
): { casesRoot: string; currentCase: () => string } {
	if (!casesRoot.trim() || !activeCaseDir.trim()) {
		throw new Error('Spotlight Flue tools require SPOTLIGHT_CASES_ROOT and SPOTLIGHT_ACTIVE_CASE');
	}
	const configuredRoot = resolve(casesRoot);
	const configuredCase = resolve(activeCaseDir);
	if (!isCaseWithinRoot(configuredRoot, configuredCase)) {
		throw new Error('Spotlight active case must be a child of the configured cases root');
	}
	const canonicalRoot = realpathSync(configuredRoot);
	if (!statSync(canonicalRoot).isDirectory()) {
		throw new Error('Spotlight cases root must be a directory');
	}

	let canonicalCase: string;
	try {
		const metadata = lstatSync(configuredCase);
		if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
			throw new Error('Spotlight active case path must be a directory');
		}
		canonicalCase = realpathSync(configuredCase);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
		const canonicalParent = realpathSync(dirname(configuredCase));
		if (canonicalParent !== canonicalRoot && !isCaseWithinRoot(canonicalRoot, canonicalParent)) {
			throw new Error('Spotlight active case parent resolves outside the configured cases root');
		}
		mkdirSync(configuredCase);
		canonicalCase = realpathSync(configuredCase);
	}
	if (!isCaseWithinRoot(canonicalRoot, canonicalCase)) {
		throw new Error('Spotlight active case resolves outside the configured cases root');
	}

	const dataDirectory = resolve(canonicalCase, 'data');
	try {
		const metadata = lstatSync(dataDirectory);
		if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
			throw new Error('Spotlight active case data path must be a directory');
		}
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
		mkdirSync(dataDirectory);
	}

	return {
		casesRoot: canonicalRoot,
		currentCase: () => canonicalCase,
	};
}

async function resolveCase(
	caseDir: string,
	casesRoot: string,
	runAdapter: AdapterRunner,
	signal?: AbortSignal,
): Promise<Record<string, unknown>> {
	const output = await runAdapter(
		['status', '--json', '--authorized-cases-root', casesRoot, caseDir],
		signal,
	);
	try {
		return JSON.parse(output) as Record<string, unknown>;
	} catch (error) {
		throw new Error(`Spotlight resolver returned invalid JSON: ${(error as Error).message}`);
	}
}

function stringField(payload: Payload, name: string): string {
	const value = payload[name];
	if (typeof value !== 'string' || value.trim() === '') {
		throw new Error(`spotlight_transition payload.${name} must be a non-empty string`);
	}
	return value;
}

function transitionArguments(
	operation: (typeof OPERATIONS)[number],
	caseDir: string,
	casesRoot: string,
	payload: Payload,
): string[] {
	switch (operation) {
		case 'approve':
			return [
				'approve',
				stringField(payload, 'gate'),
				'--approved-by',
				stringField(payload, 'approvedBy'),
				'--approved-at',
				stringField(payload, 'approvedAt'),
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
		case 'recordAttempt':
			return [
				'record-attempt',
				stringField(payload, 'kind'),
				'--gap',
				stringField(payload, 'gap'),
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
		case 'requestFollowUp':
			return [
				'request-follow-up',
				'--instructions',
				stringField(payload, 'instructions'),
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
		case 'sealGate1':
			return [
				'seal-gate1',
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
		case 'decideReport':
			return [
				'decide-report',
				stringField(payload, 'decision'),
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
		case 'decideIngest':
			return [
				'decide-ingest',
				stringField(payload, 'decision'),
				'--authorized-cases-root',
				casesRoot,
				caseDir,
			];
	}
}

const payloadSchema = v.optional(v.record(v.string(), v.unknown()), {});

export function createSpotlightTools(options: SpotlightToolOptions) {
	const activeCase = activeCaseCapability(options.casesRoot, options.activeCaseDir);
	const runAdapter = options.runAdapter ?? nativeRunAdapter;
	const spotlightResolve = defineTool({
		name: 'spotlight_resolve',
		description:
			'Resolve the durable phase, owner, requirements, attempts, and resume point for the host-bound active Spotlight case without writing state.',
		input: v.object({}),
		async run({ signal }) {
			return resolveCase(activeCase.currentCase(), activeCase.casesRoot, runAdapter, signal);
		},
	});
	const spotlightTransition = defineTool({
		name: 'spotlight_transition',
		description:
			'Apply one validated transition to the host-bound active Spotlight case, then return its newly resolved durable state.',
		input: v.object({
			operation: v.picklist(OPERATIONS),
			payload: payloadSchema,
		}),
		async run({ input, signal }) {
			if (signal?.aborted) {
				throw signal.reason ?? new Error('Spotlight transition aborted before commit');
			}
			const caseDir = activeCase.currentCase();
			await runAdapter(
				transitionArguments(
					input.operation,
					caseDir,
					activeCase.casesRoot,
					input.payload,
				),
			);
			return resolveCase(caseDir, activeCase.casesRoot, runAdapter);
		},
	});
	return [spotlightResolve, spotlightTransition];
}
