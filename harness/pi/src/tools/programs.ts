// Typed tools for the remaining fixed programs the skills used to start through
// a shell: exiftool, the Navigator CLI, the provenance
// manifest and signing, the editorial review artifact, RLM analysis, the
// integration status check, the solo Ingest wiki write and the passage
// writer. Each
// is bound to the active case; the model picks only the documented options.
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { Type } from 'typebox';
import type { Static } from 'typebox';
import type { ToolDefinition } from '@earendil-works/pi-coding-agent';
import { engineEnv } from '../case-search.ts';
import { redactor } from '../log.ts';
import { recordNavigatorLookup } from './navigator-receipts.ts';
import { CHECKOUT_ROOT } from '../roles.ts';
import { FileRefused, oneMergeAtATime, readTarget, writeConfined, writeGateSummary, writeTarget } from './case-files.ts';
import { NAVIGATOR_ENV } from './hosted-skill.ts';
import { HARNESS_PROPOSALS, ToolArgumentError, RETURNED_CHARS, capped, checkedParams, jsonResult, programEnv, programOutput, runFixed, runPython, textResult } from './run.ts';
import type { ToolContext } from './run.ts';
import type { FixedStepRunner } from './spotlight.ts';

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
			'When you need a new OSINT tool or technique, query Navigator with {action: "find", value: "<need, in plain words>"} or inspect {action: "show", value: "<tool id>"}. Code runs the CLI and saves provenance; you choose relevance. Existing search/fetch needs no lookup. Unavailable discovery may use the local osint_tools fallback.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ action: 'find' | 'show'; value: string }>('navigator_tools', params, raw);
			if (context.env.SPOTLIGHT_SENSITIVE === 'true') {
				return jsonResult({ outcome: 'policy-skipped', reason: 'Sensitive mode prohibits Navigator egress.', next_action: 'Use permitted local material or osint_tools.' });
			}
			const redact = redactor([context.env.NAVIGATOR_TOKEN]);
			let result;
			try {
				result = await runFixed('navigator', ['tools', input.action, '--json', '--', input.value], {
					env: programEnv(context.env, NAVIGATOR_ENV), cwd: context.caseDir, signal,
				});
			} catch (error) {
				signal?.throwIfAborted();
				return jsonResult({ outcome: 'unavailable', reason: redact((error as Error).message), next_action: 'Use osint_tools or existing search/fetch; do not invent a successful lookup.' });
			}
			if (result.code !== 0) {
				return jsonResult({ outcome: 'unavailable', reason: redact((result.stderr || result.stdout).trim()).slice(0, 500), next_action: 'Use osint_tools or existing search/fetch; reconnect Navigator outside the model if needed.' });
			}
			let response: unknown;
			try { response = JSON.parse(redact(result.stdout)); }
			catch { throw new Error('navigator_tools: Navigator returned invalid JSON; no lookup receipt was saved. Use osint_tools or existing sources.'); }
			const receipt_path = recordNavigatorLookup(context, input, response);
			return jsonResult(JSON.stringify(response).length <= RETURNED_CHARS
				? { outcome: 'used', receipt_path, response }
				: { outcome: 'used', receipt_path, response_text: capped(JSON.stringify(response), '; full response is in the receipt') });
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
// Anomaly (integrations/anomaly): at Methodology, which data sources and
// detectors fit the case; in Investigation, one chosen detector run on tables
// the investigator fetched. Both run the installed Anomaly checkout with no keys.
function anomalyMethodsTool(context: ToolContext): ToolDefinition {
	const params = Type.Object(
		{ domain: Type.Optional(Type.String({ minLength: 1, maxLength: 100 })), source: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })) },
		{ additionalProperties: false },
	);
	return {
		name: 'anomaly_methods',
		label: 'Anomaly methods',
		description:
			'What Anomaly offers this case (integrations/anomaly/adapter.py context): each domain (a type of data such as lobbying or procurement) with its data sources (access, cost, licence, the SKILL.md that says how to get the data) and the detectors that read it, plus the core detectors that run on any table under any_table. Give domain to see one domain, or source to read one source\'s SKILL.md. available: false with a reason when ANOMALY_HOME is unset or the installed catalogue cannot be used; then plan no Anomaly work.',
		parameters: params,
		async execute(_id, raw, signal) {
			const input = checkedParams<{ domain?: string; source?: string }>('anomaly_methods', params, raw);
			if (input.domain && input.source) throw new ToolArgumentError('anomaly_methods: give domain or source, not both');
			const scope = input.domain ? ['--domain', input.domain] : input.source ? ['--source', input.source] : [];
			const result = await runPython(context, [script('integrations/anomaly/adapter.py'), 'context', ...scope], {
				keys: ['ANOMALY_HOME'],
				extra: { SPOTLIGHT_CONFIG: context.configPath },
				signal,
			});
			return textResult(programOutput('anomaly_methods', result));
		},
	};
}

function anomalyRunTool(context: ToolContext): ToolDefinition {
	const names = Type.Record(Type.String({ minLength: 1 }), Type.String({ minLength: 1 }), { minProperties: 1, maxProperties: 20 });
	const scalars = {
		method: Type.String({ minLength: 1, maxLength: 200 }),
		table: Type.Optional(Type.String({ minLength: 1 })),
	};
	// The check run on every call.
	const params = Type.Object(
		{ ...scalars, fields: Type.Optional(names), tables: Type.Optional(names), columns: Type.Optional(Type.Record(Type.String({ minLength: 1 }), names)) },
		{ additionalProperties: false },
	);
	// What the model is sent: maps with typed additionalProperties only, which
	// the Gemma 4 chat template renders on Osaurus (see http_get).
	const map = Type.Object({}, { additionalProperties: Type.String() });
	const declared = Type.Object(
		{ ...scalars, fields: Type.Optional(map), tables: Type.Optional(map), columns: Type.Optional(Type.Object({}, { additionalProperties: map })) },
		{ additionalProperties: false },
	);
	return {
		name: 'anomaly_run',
		label: 'Run an Anomaly detector',
		description:
			'Run one Anomaly detector on tables you fetched (CSV, JSONL or Parquet case files under research/ or evidence/), in a case whose approved methodology has an anomaly section. A core detector (anomaly_methods any_table) takes table and fields: {"method": "method:numeric.zscore_outliers@1.1.0", "table": "research/payments.csv", "fields": {"id": "record_id", "amount": "value"}}, each field role mapped to a table column. A domain detector takes tables, one file per contract table it reads, and optional columns ({table: {contract column: file column}}). Code runs the detector; it never edits a table. Each signal is recorded in data/anomaly-signals.json as a lead to check against the source, never a finding. A refusal runs nothing and says why.',
		parameters: declared,
		executionMode: 'sequential',
		async execute(_id, raw, signal) {
			const input = checkedParams<Record<string, unknown>>('anomaly_run', params, raw);
			// The runner reads every named table, so each must be a file this role may read.
			const paths = [input.table, ...Object.values((input.tables as Record<string, string> | undefined) ?? {})];
			for (const path of paths) if (typeof path === 'string') readTarget(context.caseDir, path, context.scope);
			const result = await runPython(context, [script('integrations/anomaly/adapter.py'), 'run', context.caseDir], {
				keys: ['ANOMALY_HOME'],
				extra: { SPOTLIGHT_CONFIG: context.configPath },
				input: JSON.stringify(input),
				signal,
				// The adapter caps one runner call at 120 s (adapter.py RUN_TIMEOUT_SECONDS).
				timeoutMs: 150_000,
			});
			return textResult(programOutput('anomaly_run', result));
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

// The anchor path a proposed passage names: an object's path, or a string
// with an optional ":12-14" suffix, as write-source-expressions.py reads it.
function anchorPath(expression: unknown): string | undefined {
	const anchor = (expression as { anchor_ref?: unknown } | null)?.anchor_ref;
	if (typeof anchor === 'string') return anchor.replace(/:\d+(-\d+)?$/, '');
	const path = (anchor as { path?: unknown } | null)?.path;
	return typeof path === 'string' ? path : undefined;
}

// The evidence files this role may read: the only ones the writer searches
// for a passage without an anchor (--readable).
function readableEvidence(context: ToolContext): string[] {
	let items: unknown;
	try {
		items = (JSON.parse(readFileSync(join(context.caseDir, 'data', 'evidence-bundle.json'), 'utf8')) as { items?: unknown }).items;
	} catch {
		return [];
	}
	const paths = (Array.isArray(items) ? items : []).flatMap((item) => [
		...['raw_path', 'downloaded_document_path', 'path'].map((key) => item?.[key]),
		...(Array.isArray(item?.text_derivatives) ? item.text_derivatives : []).map((entry: { path?: unknown }) => entry?.path),
	]);
	return [...new Set(paths.filter((path): path is string => typeof path === 'string'))].filter((path) => {
		try {
			readTarget(context.caseDir, path, context.scope);
			return true;
		} catch {
			return false;
		}
	});
}

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
			'The only way to record passages and source positions in an activated case. {action: "write", cycle: N, proposal: {expressions, lifecycle, positions, withdraw_positions}}. For each passage give id, text (the exact quote as the file has it), anchor_ref {path} (the evidence item\'s file: its original artifact or a text_derivatives path; for a distilled fetch its .raw file, never the leads file), original_evidence_bundle_id (an item already in data/evidence-bundle.json), language (BCP 47) and finding_links. The writer finds the lines and computes every hash and fingerprint. If no source you acquired contains a passage that supports the finding, do not record one: set the verdict to unverified. Example: {"id": "P1", "text": "<exact quote>", "anchor_ref": {"path": "research/page.md.raw"}, "original_evidence_bundle_id": "E1", "language": "en", "finding_links": [{"finding_id": "F1", "relation": "supports"}]}. {action: "fingerprint_findings"} sets every finding_fingerprint from its claim; run it after writing or merging findings. A refusal changes nothing and says why.',
		parameters: params,
		// A small model sends the proposal as a JSON string of the object (12B r3
		// ia, three times); decoded here, so the declared schema stays as it is.
		prepareArguments: (args) => {
			const proposal = (args as { proposal?: unknown })?.proposal;
			if (typeof proposal !== 'string') return args as Static<typeof params>;
			try {
				const decoded = JSON.parse(proposal);
				if (decoded !== null && typeof decoded === 'object' && !Array.isArray(decoded)) return { ...(args as object), proposal: decoded } as Static<typeof params>;
			} catch {
				// Not JSON: left for the schema to refuse.
			}
			return args as Static<typeof params>;
		},
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
				// It republishes data/findings.json under a flock Node cannot share, so it takes the merge lock too (Codex P2).
				const result = await oneMergeAtATime(writeTarget(context.caseDir, context.writeRoot, 'data/findings.json'), () => runPython(context, [writer, context.caseDir, '--fingerprint-findings'], { signal }));
				return textResult(programOutput('source_expressions', result));
			}
			if (input.cycle === undefined || input.proposal === undefined) {
				throw new ToolArgumentError('source_expressions: action "write" needs cycle and proposal, for example {"action": "write", "cycle": 1, "proposal": {"expressions": [ ... ]}}');
			}
			// The writer reads each named anchor's file, so it must be a file this
			// role may read, and searches only readable evidence files for the
			// rest (an empty --readable= first, so none readable means none): the
			// fact-checker cannot cite the research it is kept from.
			const expressions = (input.proposal as { expressions?: unknown }).expressions;
			for (const expression of Array.isArray(expressions) ? expressions : []) {
				const path = anchorPath(expression);
				if (path !== undefined) readTarget(context.caseDir, path, context.scope);
			}
			const readable = ['--readable=', ...readableEvidence(context).map((path) => `--readable=${path}`)];
			let proposal = input.proposal;
			let dropped = 0;
			if (!HARNESS_PROPOSALS.has(raw as object) && Array.isArray(expressions)) {
				proposal = {
					...proposal,
					expressions: expressions.map((expression) => {
						// false (a translation, a paraphrase) claims nothing and stays.
						if (!expression || typeof expression !== 'object' || !('direct_quote' in expression) || (expression as Record<string, unknown>).direct_quote === false) return expression;
						dropped += 1;
						const { direct_quote: _dropped, ...rest } = expression as Record<string, unknown>;
						return rest;
					}),
				};
			}
			const result = await runPython(context, [writer, context.caseDir, '--actor', actor, '--cycle', String(input.cycle), ...readable], {
				input: JSON.stringify(proposal),
				signal,
			});
			const note = dropped ? `\nNote: direct_quote is set by the harness only, for a quote it finds as written in a fetched capture; removed from ${dropped} passage(s).` : '';
			return textResult(programOutput('source_expressions', result) + note);
		},
	};
}

export function programTools(context: ToolContext): ToolDefinition[] {
	return [
		exiftoolTool(context),
		navigatorTool(context),
		provenanceTool(context),
		reviewHtmlTool(context),
		rlmTool(context),
		integrationStatusTool(context),
		anomalyMethodsTool(context),
		anomalyRunTool(context),
		wikiDestinationTool(context),
		sourceExpressionsTool(context),
	];
}

// The orchestrator's fixed steps (tools/spotlight.ts runFixedSteps), run
// through the same program tools a model once called. Signing is optional:
// when it is configured and fails, the unsigned manifest is built instead
// (phase-investigation-gate). The newsroom edition has no wiki write: its
// Submit step is the orchestrator's newsroom_submit.
export function fixedStepRunner(context: ToolContext, edition: string): FixedStepRunner {
	const tools = programTools(context);
	const run = async (name: string, params: Record<string, unknown>, signal?: AbortSignal) => {
		const result = await tools.find((tool) => tool.name === name)!.execute(`fixed-${name}`, params, signal, undefined, undefined as never);
		return result.content.map((part) => ('text' in part ? part.text : '')).join('\n');
	};
	return async (step, signal) => {
		if (step === 'summary') return writeGateSummary(context.caseDir);
		if (step === 'provenance') {
			if (!context.env.NOOSPHERE_C2PA_URL || !context.env.NOOSPHERE_C2PA_CREDENTIAL_ID) return run('provenance_manifest', { action: 'build' }, signal);
			try {
				return await run('provenance_manifest', { action: 'sign' }, signal);
			} catch (error) {
				return `signing failed, unsigned manifest built: ${(error as Error).message.slice(0, 300)}\n${await run('provenance_manifest', { action: 'build' }, signal)}`;
			}
		}
		if (step === 'review') return run('build_review_html', {}, signal);
		if (edition === 'newsroom') return undefined;
		if (step === 'wiki') return run('wiki_destination', {}, signal);
		return undefined;
	};
}
