// Typed tools for the remaining fixed programs the skills used to start through
// a shell: exiftool, the Navigator CLI, decision checks, the provenance
// manifest and signing, the editorial review artifact, RLM analysis, the
// integration status check, the solo Ingest wiki write and the passage
// writer. Each
// is bound to the active case; the model picks only the documented options.
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { Type } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { engineEnv } from '../case-search.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { FileRefused, readTarget, writeConfined } from './case-files.ts';
import { NAVIGATOR_ENV } from './hosted-skill.ts';
import { ToolArgumentError, checkedParams, jsonResult, programEnv, programOutput, runFixed, runPython, textResult } from './run.ts';
import type { ToolContext } from './run.ts';

const script = (path: string) => `${CHECKOUT_ROOT}/${path}`;
const caseDataFile = Type.String({ pattern: '^data/[A-Za-z0-9._-]+\\.json$' });

function exiftoolTool(context: ToolContext): ToolDefinition {
	const params = Type.Object({ path: Type.String({ minLength: 1, maxLength: 1024 }) }, { additionalProperties: false });
	return {
		name: 'exiftool',
		label: 'Exiftool',
		description: 'Read the embedded metadata (author, creation and modification dates, software, GPS) of one file in the case with exiftool. Returns exiftool JSON.',
		parameters: params,
		async execute(_id, raw, signal) {
			const { path } = checkedParams<{ path: string }>('exiftool', params, raw);
			const target = readTarget(context.caseDir, path, context.scope);
			const result = await runFixed('exiftool', ['-json', '-G', target], {
				env: programEnv(context.env),
				cwd: context.caseDir,
				signal,
			});
			return textResult(programOutput('exiftool', result));
		},
	};
}

function navigatorTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{
			action: Type.Union([Type.Literal('find'), Type.Literal('show')]),
			value: Type.String({ minLength: 1, maxLength: 500 }),
		},
		{ additionalProperties: false },
	);
	return {
		name: 'navigator_tools',
		label: 'Navigator tools',
		description:
			'Search the OSINT Navigator tool catalogue: {action: "find", value: "<what you need, in plain words>"} or {action: "show", value: "<tool id>"}. Needs the Navigator CLI installed and signed in.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ action: 'find' | 'show'; value: string }>('navigator_tools', params, raw);
			const result = await runFixed('navigator', ['tools', input.action, '--json', '--', input.value], {
				env: programEnv(context.env, NAVIGATOR_ENV),
				cwd: context.caseDir,
				signal,
			});
			return textResult(programOutput('navigator_tools', result));
		},
	};
}

function decisionSignalsTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{
			phase: Type.Union([Type.Literal('investigation_gate'), Type.Literal('report')]),
			check: Type.Optional(Type.Boolean()),
			case_opt_in: Type.Optional(Type.Boolean()),
		},
		{ additionalProperties: false },
	);
	return {
		name: 'decision_signals',
		label: 'Decision signals',
		description:
			'Run the optional decision checks (scripts/decision-signals.py) for the active case at a phase: investigation_gate or report. check: true reports readiness without any request; case_opt_in: true records the user\'s agreement on the first run. Refuses itself when not enabled or in sensitive mode.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ phase: string; check?: boolean; case_opt_in?: boolean }>('decision_signals', params, raw);
			const args = [script('scripts/decision-signals.py'), context.caseDir, '--phase', input.phase, '--config', context.configPath];
			if (input.check) args.push('--check');
			if (input.case_opt_in) args.push('--case-opt-in');
			const result = await runPython(context, args, { keys: ['OPENROUTER_API_KEY', 'SPOTLIGHT_SENSITIVE'], signal, timeoutMs: 600_000 });
			return textResult(programOutput('decision_signals', result));
		},
	};
}

function provenanceTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{
			action: Type.Union([Type.Literal('build'), Type.Literal('check_current'), Type.Literal('sign')]),
			artifact: Type.Optional(Type.String({ pattern: '^(?!.*\\.\\.)[A-Za-z0-9._/-]{1,256}$' })),
		},
		{ additionalProperties: false },
	);
	return {
		name: 'provenance_manifest',
		label: 'Provenance manifest',
		description:
			'Build the case provenance manifest (action "build"), check it still matches the case inputs ("check_current"), or build and sign it through the configured Noosphere C2PA signer ("sign", optional artifact such as "review.html").',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ action: string; artifact?: string }>('provenance_manifest', params, raw);
			const args = [script('scripts/build-provenance-manifest.py'), context.caseDir];
			if (input.action === 'check_current') args.push('--check-current');
			if (input.action === 'sign') {
				const endpoint = context.env.NOOSPHERE_C2PA_URL;
				const credential = context.env.NOOSPHERE_C2PA_CREDENTIAL_ID;
				if (!endpoint || !credential) {
					throw new Error('provenance_manifest: signing is not configured (NOOSPHERE_C2PA_URL and NOOSPHERE_C2PA_CREDENTIAL_ID)');
				}
				args.push('--sign-endpoint', endpoint, '--credential-id', credential);
				if (input.artifact) args.push('--artifact', input.artifact);
			}
			const result = await runPython(context, args, {
				keys: ['NOOSPHERE_PROVENANCE_API_KEY', 'SPOTLIGHT_SENSITIVE'],
				signal,
				timeoutMs: 300_000,
			});
			return textResult(programOutput('provenance_manifest', result));
		},
	};
}

// The script builds the page; the harness writes it, so review.html passes the
// same write confinement as every other case write.
function reviewHtmlTool(context: ToolContext): ToolDefinition {
	const params = Type.Object({}, { additionalProperties: false });
	return {
		name: 'build_review_html',
		label: 'Build review.html',
		description:
			'Build the editorial review artifact review.html in the case (scripts/build-review-html.py): joins data/findings.json with data/fact-check.json and adds source expressions, the provenance manifest and data/summary.json, serialized safely into the template. Returns the path, bytes and SHA-256.',
		parameters: params,
		executionMode: 'sequential',
		async execute(_id, raw, signal) {
			checkedParams('build_review_html', params, raw);
			const result = await runPython(context, [script('scripts/build-review-html.py'), context.caseDir, '--stdout'], { signal });
			const html = programOutput('build_review_html', result);
			const written = writeConfined(context.caseDir, context.writeRoot, 'review.html', html);
			const bytes = Buffer.from(html, 'utf8');
			return jsonResult({ written, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
		},
	};
}

function rlmTool(context: ToolContext): ToolDefinition {
	const params = Type.Object({ request: caseDataFile }, { additionalProperties: false });
	return {
		name: 'run_rlm',
		label: 'Run RLM',
		description: 'Run the approved RLM analysis (integrations/rlm/run_rlm.py) on a request file directly under data/, normally data/rlm-request.json. Output is lead-routing context, never evidence.',
		parameters: params,
		async execute(_id, raw, signal) {
			const { request } = checkedParams<{ request: string }>('run_rlm', params, raw);
			// The script reads and writes the case its request names under the
			// cases root, so the request must name the active case. It runs on a
			// private copy of the checked request, so a later edit cannot redirect it.
			const bytes = readFileSync(readTarget(context.caseDir, request));
			let project: unknown;
			try {
				project = (JSON.parse(bytes.toString('utf8')) as { project?: unknown } | null)?.project;
			} catch {
				throw new Error(`run_rlm: ${request} is not valid JSON`);
			}
			const active = basename(context.caseDir);
			if (project !== active) throw new FileRefused(`refused: run_rlm request must name the active case ("project": "${active}")`);
			const copyDir = mkdtempSync(join(tmpdir(), 'spotlight-rlm-'));
			try {
				const copy = join(copyDir, 'request.json');
				writeFileSync(copy, bytes, { mode: 0o600 });
				const result = await runPython(context, [script('integrations/rlm/run_rlm.py'), copy], {
					keys: ['SPOTLIGHT_RLM_MODE', 'SPOTLIGHT_RLM_OPENAI_BASE_URL', 'SPOTLIGHT_RLM_OPENAI_MODEL', 'SPOTLIGHT_RLM_MODEL', 'SPOTLIGHT_RLM_API_KEY'],
					extra: { SPOTLIGHT_CASES_ROOT: dirname(context.caseDir) },
					signal,
					timeoutMs: 1_800_000,
				});
				return textResult(programOutput('run_rlm', result));
			} finally {
				rmSync(copyDir, { recursive: true, force: true });
			}
		},
	};
}

function isPreflightReport(stdout: string): boolean {
	try {
		const report = JSON.parse(stdout) as { integrations?: unknown; summary?: unknown } | null;
		return Array.isArray(report?.integrations) && typeof report.summary === 'object' && report.summary !== null;
	} catch {
		return false;
	}
}

function integrationStatusTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{ model_tier: Type.Optional(Type.String({ pattern: '^[a-z][a-z0-9_-]{0,31}$' })) },
		{ additionalProperties: false },
	);
	return {
		name: 'integration_status',
		label: 'Integration status',
		description: 'Report which external integrations are ready (integrations/preflight.py --json): green, yellow, red or unconfigured per integration. Optional model_tier from the Spotlight config.',
		parameters: params,
		async execute(_id, raw, signal) {
			const { model_tier } = checkedParams<{ model_tier?: string }>('integration_status', params, raw);
			const args = [script('integrations/preflight.py'), '--json'];
			if (model_tier) args.push('--model-tier', model_tier);
			const result = await runPython(context, args, { signal });
			// Exit 1 with a report means "every integration is red": still a report,
			// not a tool failure. Exit 1 without one is a crash.
			if (result.code === 1 && isPreflightReport(result.stdout)) return textResult(result.stdout);
			return textResult(programOutput('integration_status', result));
		},
	};
}

// Read-only: the adapter reads the installed catalogue and makes no network
// request, so the tool stays in sensitive mode (roles.ts EGRESS_TOOLS).
function anomalyMethodsTool(context: ToolContext): ToolDefinition {
	const params = Type.Object({}, { additionalProperties: false });
	return {
		name: 'anomaly_methods',
		label: 'Anomaly methods',
		description:
			'List the Anomaly clue methods a research topic can name in anomaly_methods (integrations/anomaly/adapter.py methods): the single-table methods of the pinned catalogue, each with its required fields, assumptions and known false positives. available: false with a reason, and no methods, when ANOMALY_HOME is unset or the installed catalogue is not the pinned one; then name none.',
		parameters: params,
		async execute(_id, raw, signal) {
			checkedParams('anomaly_methods', params, raw);
			const result = await runPython(context, [script('integrations/anomaly/adapter.py'), 'methods'], {
				keys: ['ANOMALY_HOME'],
				extra: { SPOTLIGHT_CONFIG: context.configPath },
				signal,
			});
			return textResult(programOutput('anomaly_methods', result));
		},
	};
}

// Solo Ingest's write (skills/phase-ingest, `requested` / `ingest`). The script
// does every check itself: an activated case in solo mode, a request recorded
// by the ingest page and bound to this batch, then the write under spotlight/
// of the configured wiki only (no symlink on the way, the wiki and case locks
// held) and the completed receipt the phase seals. It calls no web tool (its
// only other program is Engine's `bsig wiki resolve` on that wiki), so it
// stays in sensitive mode; the newsroom edition does not get it
// (roles.ts, NEWSROOM_TOOL_SWAPS).
function wikiDestinationTool(context: ToolContext): ToolDefinition {
	const params = Type.Object({}, { additionalProperties: false });
	return {
		name: 'wiki_destination',
		label: 'Write the wiki',
		description:
			'Solo Ingest, once the member has requested ingestion on the ingest page (resume_at "ingest"): write the approved batch into the member\'s wiki under spotlight/ and record the completed receipt (scripts/wiki-destination.py). A refusal names what to fix; a re-run after an interruption is safe. Then resolve again and seal.',
		parameters: params,
		executionMode: 'sequential',
		async execute(_id, raw, signal) {
			checkedParams('wiki_destination', params, raw);
			const result = await runPython(context, [script('scripts/wiki-destination.py'), context.caseDir], {
				keys: engineEnv(context.env),
				extra: { SPOTLIGHT_CONFIG: context.configPath },
				signal,
				timeoutMs: 300_000,
			});
			return textResult(programOutput('wiki_destination', result));
		},
	};
}

// The one writer of data/source-expressions.json and data/source-positions.json
// (agents/investigator.md and agents/fact-checker.md): the producers propose
// passages and positions, the script computes every hash and fingerprint,
// validates and publishes under the case lock. No file tool writes either file
// (case-files.ts). The actor is the calling role, never a model argument.
const PROPOSAL_LIST = Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }), { maxItems: 500 }));
const WRITER_ACTORS = ['investigator', 'fact-checker'];

function sourceExpressionsTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{
			action: Type.Union([Type.Literal('write'), Type.Literal('fingerprint_findings')]),
			cycle: Type.Optional(Type.Integer({ minimum: 1, maximum: 1000 })),
			proposal: Type.Optional(
				Type.Object(
					{ expressions: PROPOSAL_LIST, lifecycle: PROPOSAL_LIST, positions: PROPOSAL_LIST, withdraw_positions: PROPOSAL_LIST },
					{ additionalProperties: false },
				),
			),
		},
		{ additionalProperties: false },
	);
	return {
		name: 'source_expressions',
		label: 'Source expressions',
		description:
			'The only way to record passages and source positions in an activated case (scripts/write-source-expressions.py). {action: "write", cycle: N, proposal: {expressions, lifecycle, positions, withdraw_positions}} merges your proposal into data/source-expressions.json and data/source-positions.json as your role; give each passage\'s id, exact text, anchor_ref, original_evidence_bundle_id and finding_links, never hashes or fingerprints. anchor_ref is {path, line_start, line_end} or {path, json_pointer}: a file of the evidence bundle item (its original artifact or a text_derivatives path; for a page fetch distilled, its .raw file, never the leads file), and text must equal exactly what that range selects. original_evidence_bundle_id names an item already in data/evidence-bundle.json: add the item first, then cite it. Example expression: {"id": "P1", "text": "<the exact lines>", "anchor_ref": {"path": "research/page.md.raw", "line_start": 12, "line_end": 13}, "original_evidence_bundle_id": "E1", "language": "en", "direct_quote": true, "finding_links": [{"finding_id": "F1", "relation": "supports"}]}. {action: "fingerprint_findings"} sets every finding_fingerprint in data/findings.json from its claim and refreshes stale passage links; run it after writing or merging findings. A refusal changes nothing and says why.',
		parameters: params,
		executionMode: 'sequential',
		async execute(_id, raw, signal) {
			const input = checkedParams<{ action: string; cycle?: number; proposal?: Record<string, unknown> }>('source_expressions', params, raw);
			const actor = context.env.SPOTLIGHT_ROLE ?? '';
			if (!WRITER_ACTORS.includes(actor)) throw new FileRefused(`refused: source_expressions is for the investigator and the fact-checker, not ${actor || 'this process'}`);
			// A legacy case keeps contract 1.0 and gets no passage files; only an
			// activated case (its contract exists) records passages.
			if (!existsSync(join(context.caseDir, 'data', 'case-contract.json'))) {
				throw new FileRefused('refused: this case is not activated (no data/case-contract.json), so it records no passages; write findings and the fact-check without them');
			}
			const writer = script('scripts/write-source-expressions.py');
			if (input.action === 'fingerprint_findings') {
				const result = await runPython(context, [writer, context.caseDir, '--fingerprint-findings'], { signal });
				return textResult(programOutput('source_expressions', result));
			}
			if (input.cycle === undefined || input.proposal === undefined) {
				throw new ToolArgumentError('source_expressions: action "write" needs cycle and proposal');
			}
			// The writer reads each anchor's file, so an anchor must be a file this
			// role may read: the fact-checker cannot cite the research it is kept from.
			const expressions = (input.proposal as { expressions?: unknown }).expressions;
			for (const expression of Array.isArray(expressions) ? expressions : []) {
				const path = (expression as { anchor_ref?: { path?: unknown } } | null)?.anchor_ref?.path;
				if (typeof path === 'string') readTarget(context.caseDir, path, context.scope);
			}
			const result = await runPython(context, [writer, context.caseDir, '--actor', actor, '--cycle', String(input.cycle)], {
				input: JSON.stringify(input.proposal),
				signal,
			});
			return textResult(programOutput('source_expressions', result));
		},
	};
}

export function programTools(context: ToolContext): ToolDefinition[] {
	return [
		exiftoolTool(context),
		navigatorTool(context),
		decisionSignalsTool(context),
		provenanceTool(context),
		reviewHtmlTool(context),
		rlmTool(context),
		integrationStatusTool(context),
		anomalyMethodsTool(context),
		wikiDestinationTool(context),
		sourceExpressionsTool(context),
	];
}
