// The Spotlight extension, loaded by path into every Spotlight pi process. It
// registers the model route chosen by the launcher, applies the fixed
// provider request-body policy, and registers exactly the role's tools.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { relative, resolve, sep } from 'node:path';
import { execFile } from 'node:child_process';
import type { ExtensionAPI, ToolDefinition } from '@earendil-works/pi-coding-agent';
import { createAssistantMessageEventStream, getCurrentTools } from '@earendil-works/pi-ai';
import { streamSimple as streamOpenAICompletions } from '@earendil-works/pi-ai/api/openai-completions';
import { takeFirecrawlNotice } from './tools/fetch.ts';
import { DECISION_RECORDED, MEMBER_DECIDED, ROUTER_CALL_PREFIX, decisionHelperNotice, memberHasTheMove, ROUTER_MODEL, clearSpawnRefusals, closingMessage, harnessTurn, noteRefusedSpawn, routeMember, routeRefusedSpawn, routeWithoutMember, routerOn, routerState, spawnRefusals } from './router.ts';
import { COMMIT_CALL_PREFIX, COMMIT_TEXT, COMMIT_TOOLS, commitDue, commitState, pickCommit, caseRelative, pickWrite, writeCommitDue, writeCommitFile, writeCommitState, writeCommitText, writeCommitTools } from './commit.ts';
import type { Intent, RouterMove } from './router.ts';
import { registerCompaction } from './checkpoint.ts';
import { maxOutputTokens, resolverEnv } from './config.ts';
import { applyFixedRequestBody, parseFixedRequestBody, withLocalThinking, withoutHistoryReasoning } from './provider-policy.ts';
import { PROVIDER_ID } from './pi-process.ts';
import { CHECKOUT_ROOT, ORCHESTRATOR, agentSpec, allowedTools, deliverableFor, loadComposition, checkPacket, scopeAt, nextCheckText, readCheckDocs, skillAdapterNote, skillsFor, spawnableWorkers } from './roles.ts';
import { READ_TRUNCATED, canonicalWriteCall, localTier, readTarget, secondSourceFetched, writeConfined } from './tools/case-files.ts';
import { typedTools } from './tools/index.ts';
import { dispatchRunner } from './tools/review.ts';
import { fixedStepRunner } from './tools/programs.ts';
import { invokeSkillTool } from './tools/skills.ts';
import { INDICATOR_FRAMEWORK_ROLES, approvedIndicatorFramework } from './tools/hosted-skill.ts';
import { recordIndicatorFrameworkTool } from './tools/indicator-framework.ts';
import { STALLED_DISPATCHES, bindActiveCase, nextAction, pythonAdapter, recordsDigest, refusedSpawnMove, resolveCase, spotlightTools, withPlanCheck } from './tools/spotlight.ts';
import type { ActiveCase, AdapterRunner, DispatchStall } from './tools/spotlight.ts';
import { CORRECTION_SPENT, FACT_CHECKERS_SPENT, requestEditorial, requestResearchRound, resetFactCheckerCycle, spawnAgentTool, withProgress } from './subagents.ts';
import { editorialTools } from './tools/editorial.ts';
import { PAGE_NOT_OPENED, REVIEW_HOLD_MS, batchBlocked, failureLines, gateCheck, missingForApproval, openReviewTool } from './tools/review.ts';
import { CALL_BUDGET_STOP, FACT_CHECK_DUE_STOP, LOOP_STOP, PROGRESS_STOP, REFUSAL_STOP, REFUSAL_STREAK, REPEAT_REASON } from './tools/run.ts';
import { deliverablePasses, evidenceValidates, hasDeliverableCheck, validateFactCheckBatch } from './validators.ts';

const log = (message: string) => process.stderr.write(`spotlight-extension: ${message}\n`);

function required(name: string): string {
	const value = process.env[name];
	if (!value) throw new Error(`Spotlight extension: ${name} is not set (start Spotlight through its launcher)`);
	return value;
}

// The case as the orchestrator is told it everywhere: resolved, with a plan
// that fails its own check still at Methodology (spotlight.ts withPlanCheck).
function caseForModel(active: ActiveCase, runAdapter: AdapterRunner): Promise<Record<string, unknown>> {
	const context = { caseDir: active.caseDir, writeRoot: active.caseDir, python: process.env.SPOTLIGHT_PYTHON || 'python3', configPath: process.env.SPOTLIGHT_CONFIG || `${CHECKOUT_ROOT}/.spotlight-config.json`, env: process.env };
	return resolveCase(active, runAdapter).then((state) => withPlanCheck(state, gateCheck(active, runAdapter, context)));
}

// The orchestrator's case phase, read now (set in toolsFor). The router's
// methodology relay checks it: the last resolve in the transcript can predate
// an approval the review page recorded (Codex review of simp-relay, P2).
let casePhase: (() => Promise<string>) | undefined;

// Consecutive watchdog-stopped dispatches of one worker task
// (STALLED_DISPATCHES). Process memory, read by the dispatches (toolsFor) and
// the text-turn nudge (spotlightExtension): declared inside toolsFor it was out
// of the nudge's scope, so every nudge threw a ReferenceError from a811d655 on.
const stall: DispatchStall = { key: '', count: 0, digest: '' };
// Refused orchestrator spawns this member turn (router.ts routeRefusedSpawn).
const refusedSpawns = spawnRefusals();
function toolsFor(role: string, pi: ExtensionAPI): ToolDefinition[] {
	const composition = loadComposition();
	const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
	const python = process.env.SPOTLIGHT_PYTHON || 'python3';
	const spec = agentSpec(composition, role);
	const sensitive = process.env.SPOTLIGHT_SENSITIVE === 'true';
	// The Navigator-hosted Indicator Framework, cached per revision in the
	// harness data folder.
	const hosted = INDICATOR_FRAMEWORK_ROLES.includes(role)
		? { caseDir: active.caseDir, dataDir: required('SPOTLIGHT_HARNESS_DATA'), env: process.env, sensitive }
		: undefined;
	const context = {
		caseDir: active.caseDir,
		writeRoot: active.caseDir,
		python,
		configPath: process.env.SPOTLIGHT_CONFIG || `${CHECKOUT_ROOT}/.spotlight-config.json`,
		env: process.env,
		scope: { ...scopeAt(spec, process.env.SPOTLIGHT_PHASE), denyReads: spec.denyReads, denyWrites: spec.denyWrites },
	};
	const tools: ToolDefinition[] = [
		invokeSkillTool(skillsFor(composition, role), hosted, (skill) => skillAdapterNote(skill, spec.tools, role === ORCHESTRATOR)),
		...typedTools(context),
	];
	const edition = process.env.SPOTLIGHT_EDITION === 'newsroom' ? 'newsroom' : 'solo';
	// Newsroom edition: the Editorial MCP tools, served by the launcher. The
	// allowlist below keeps only the ones this role's swaps grant.
	if (edition === 'newsroom') tools.push(...editorialTools(requestEditorial));
	if (role === ORCHESTRATOR) {
		const runAdapter = pythonAdapter(python, resolverEnv(process.env, composition));
		const cycle = { spawns: 0, corrections: 0 };
		const progress: { say?: (text: string) => void; after?: () => void } = {};
		let memberMessages = 0;
		memberNotice = (text) => progress.say?.(text);
		// What a member's message resets. A decision on a held review page counts too:
		// its fresh run fires agent_start but not before_agent_start.
		const memberTurn = (byHarness = false) => {
			resetFactCheckerCycle(cycle);
			// Only the member's own turn lets code start a stalled worker again;
			// a harness nudge does not (Codex review of simp-redispatch, P1).
			if (!byHarness) {
				Object.assign(stall, { key: '', count: 0, digest: '', repaired: new Set<string>() });
				clearSpawnRefusals(refusedSpawns);
			}
			// The launcher's report-repair count (launcher.ts REPORT_REPAIRS) resets
			// on a member's message.
			process.send?.({ kind: 'spotlight-member-turn' });
		};
		pi.on('before_agent_start', (event, ctx) => {
			if (localTier(process.env)) progress.say = (text) => ctx.ui.notify(text, 'info');
			if (event.messageType !== 'user') return;
			const byHarness = harnessTurn(String((event as { prompt?: unknown }).prompt ?? '').trim());
			if (!byHarness) memberMessages += 1;
			memberTurn(byHarness);
		});
		const spawning = new Map<string, string>();
		pi.on('tool_execution_start', (event) => {
			if (event.toolName === 'spawn_agent') spawning.set(event.toolCallId, String((event.args as { agent?: unknown } | undefined)?.agent ?? 'worker'));
		});
		pi.on('tool_execution_end', (event) => {
			const agent = spawning.get(event.toolCallId);
			spawning.delete(event.toolCallId);
			if (agent === undefined || !event.isError) return;
			const said = (event.result?.content ?? []).map((part: { text?: string }) => part.text ?? '').join('\n');
			// A worker that started and failed is not a refusal.
			if (said.startsWith('spawn_agent') || said === FACT_CHECKERS_SPENT || said === CORRECTION_SPENT) noteRefusedSpawn(refusedSpawns, agent, said, recordsDigest(active.caseDir));
		});
		// A worker's or lane's fetch found the Firecrawl key unusable: the member hears it once.
		progress.after = () => {
			const notice = takeFirecrawlNotice({ caseDir: active.caseDir, env: process.env });
			if (notice) progress.say?.(notice);
		};
		const spawnTool = spawnAgentTool(spawnableWorkers(composition), cycle, progress);
		tools.push(...spotlightTools(active, runAdapter, role, (retry, signal) => withProgress(progress.say, 'research round', requestResearchRound(retry, signal)).finally(() => progress.after?.()), () => evidenceValidates(context), () => resetFactCheckerCycle(cycle), fixedStepRunner(context, edition), { ...dispatchRunner(spawnTool, tools[0], gateCheck(active, runAdapter, context)), stall, memberTurns: () => memberMessages }));
		tools.push(spawnTool);
		tools.push(openReviewTool(active.caseDir, required('SPOTLIGHT_CASES_ROOT'), undefined, gateCheck(active, runAdapter, context), async () => missingForApproval(active.caseDir, await resolveCase(active, runAdapter), batchBlocked(process.env.SPOTLIGHT_HARNESS_DATA, active.caseDir)), localTier(process.env) ? { ms: reviewHoldMs(process.env), say: (text) => progress.say?.(text), resolved: () => resolveCase(active, runAdapter), decided: () => { log('the decision on the page counts as the member\'s turn'); memberTurn(); } } : undefined));
		casePhase = async () => String((await resolveCase(active, runAdapter)).phase ?? '');
		tools.splice(0, tools.length, ...tools.map((tool) => keepGateBound(tool, active.caseDir, casePhase!)));
	}
	const allowlist = new Set(allowedTools(spec, sensitive, edition));
	const allowed = tools.filter((tool) => allowlist.has(tool.name));
	// Only a case whose approved methodology turns the framework on gets the
	// tool, so every other case keeps the same tool list.
	if (hosted && approvedIndicatorFramework(active.caseDir)) allowed.push(recordIndicatorFrameworkTool(hosted, context.writeRoot));
	return allowed;
}

// LFM3 e2 kde stage 3 on Osaurus (03f008b0): at Ingest the orchestrator
// rewrote data/summary.json, which the approved Investigation gate and its
// provenance manifest bind, and the case fell back to the gate's finalization.
// Once the gate is approved the orchestrator may not write the files the gate
// and its finalization bind (the rest are in its denyWrites), nor anything in
// the folders the evidence bundle's files live in, which the gate's dependency
// digest hashes too. A follow-up returns the case to Investigation, where it
// may again. launcher.test.ts checks the list against the provenance builder's
// gate dependencies.
export const GATE_BOUND = new Set(['summary.md', 'data/summary.json', 'data/case-contract.json', 'data/provenance-manifest.json', 'review.html']);
const GATE_BOUND_FOLDERS = ['research/', 'evidence/'];
const GATE_APPROVED_PHASES = new Set(['investigation_gate_finalization', 'report', 'ingest', 'complete']);
export const GATE_BOUND_REFUSAL = 'is bound by the approved Investigation gate, so it stays as the member approved it; the file is unchanged. Work from it as it is.';

function keepGateBound(tool: ToolDefinition, caseDir: string, phase: () => Promise<string>): ToolDefinition {
	if (tool.name !== 'write_file' && tool.name !== 'edit_file') return tool;
	return {
		...tool,
		async execute(id, params, ...rest) {
			const path = String((params as { path?: unknown }).path ?? '');
			const name = relative(caseDir, resolve(caseDir, path.replace(/^\{CASE_DIR\}(?:[\\/]|$)/, ''))).split(sep).join('/').toLowerCase();
			if ((GATE_BOUND.has(name) || GATE_BOUND_FOLDERS.some((folder) => name.startsWith(folder))) && GATE_APPROVED_PHASES.has(await phase())) throw new Error(`refused: ${name} ${GATE_BOUND_REFUSAL}`);
			return tool.execute(id, params, ...rest);
		},
	};
}

// Plan step 4 (reports/step4-routing-design.md): the orchestrator's provider,
// wrapped by the code router (router.ts). A code move is answered here as the
// assistant's tool call, marked code-made (responseModel ROUTER_MODEL, its
// reason in responseId) so records, detectors, scoring and training extraction
// never count it as a model decision; every other request goes to the model
// unchanged. Each decision is logged with its reason.
// The orchestrator's notice to the member, for the router below.
let memberNotice: ((text: string) => void) | undefined;
// A line swapped in at message_end never streams, and Osaurus shows deltas only
// (real launch dfdad41e), so the member gets it as a notice too. Workers set no notice.
function shownStop<T extends { message: unknown }>(event: T, text: string) {
	memberNotice?.(text);
	return { message: { ...(event.message as object), content: [{ type: 'text', text }] } as T['message'] };
}
// Past member-intent.py's own 90 s, so the script reports the failure itself.
const INTENT_TIMEOUT_MS = 95_000;
function memberIntent(message: string, signal?: AbortSignal): Promise<{ intent?: Intent; probability?: number; ms: number; failed?: string }> {
	const started = Date.now();
	return new Promise((answered) => {
		const done = (intent?: Intent, probability?: number, failed?: string) => answered({ intent, probability, ms: Date.now() - started, failed });
		const child = execFile(process.env.SPOTLIGHT_PYTHON || 'python3', [resolve(CHECKOUT_ROOT, 'scripts', 'member-intent.py'), ...(process.env.SPOTLIGHT_CONFIG ? ['--config', process.env.SPOTLIGHT_CONFIG] : [])], { timeout: INTENT_TIMEOUT_MS, encoding: 'utf8', signal }, (error, stdout) => {
			if (error) { log(`[router] decision model: ${error.message.slice(0, 200)}`); done(undefined, undefined, signal?.aborted ? undefined : error.killed ? 'it timed out' : 'its check could not run'); return; }
			try {
				const answer = JSON.parse(stdout.trim().split('\n').at(-1) ?? '{}') as { intent?: Intent; probability?: number; unavailable?: string; failed?: boolean };
				if (answer.unavailable) log(`[router] decision model unavailable: ${answer.unavailable.slice(0, 200)}`);
				done(answer.intent, answer.probability, answer.failed ? answer.unavailable : undefined);
			} catch {
				done(undefined, undefined, 'its answer could not be read');
			}
		});
		child.stdin?.end(message);
	});
}

// A message code wrote in the model's place, marked so a record shows it.
function routerMessage<Part>(model: { api: string; provider: string; id: string }, content: Part[], reason: string, stopReason: 'stop' | 'toolUse') {
	return {
		role: 'assistant' as const, content, api: model.api, provider: model.provider, model: model.id,
		responseModel: ROUTER_MODEL, responseId: `router: ${reason}`,
		usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
		stopReason, timestamp: Date.now(),
	};
}

const routedStream: NonNullable<Parameters<ExtensionAPI['registerProvider']>[1]['streamSimple']> = (model, context, options) => {
	const out = createAssistantMessageEventStream();
	void (async () => {
		let move: RouterMove;
		let d1 = '';
		// Only the orchestrator's own turns are routed: a request without its
		// tools (the session-model checkpoint, checkpoint.ts) goes to the model
		// untouched (Codex review of simp-route, P2).
		const routed = getCurrentTools(context.messages).some((tool) => tool.name === 'spotlight_resolve');
		try {
			const state = routerState(context.messages);
			if (!routed) move = { kind: 'model', reason: 'not an orchestrator turn' };
			else if (state.member) {
				const asked = await memberIntent(state.member, options?.signal);
				// r1-router-smoke.md: each decision logs D1's probability and latency.
				d1 = ` [D1 ${asked.intent ?? 'none'}${asked.probability !== undefined ? ` p=${asked.probability.toFixed(2)}` : ''} ${asked.ms} ms]`;
				if (asked.failed) memberNotice?.(decisionHelperNotice(asked.failed));
				move = routeMember(state, asked.intent);
				if (move.kind === 'call' && move.tool === 'spawn_agent' && (await casePhase?.()) !== 'methodology_approval') move = { kind: 'model', reason: 'member change_request, but the case is no longer at the methodology review' };
			} else move = routeRefusedSpawn(state, routeWithoutMember(state, state.resolved?.phase === 'complete' ? closingMessage(required('SPOTLIGHT_CASE_DIR')) : undefined), refusedSpawns, refusedSpawns.seen.size ? recordsDigest(required('SPOTLIGHT_CASE_DIR')) : '', refusedSpawnMove);
		} catch (error) {
			move = { kind: 'model', reason: `router error: ${(error as Error).message.slice(0, 200)}` };
		}
		if (routed) log(`[router] ${move.kind === 'call' ? move.tool : move.kind} (${move.reason})${d1}`);
		if (move.kind === 'model') {
			for await (const event of streamOpenAICompletions(model as never, context, options)) out.push(event);
			out.end();
			return;
		}
		if (move.kind === 'stop') {
			const words = { type: 'text' as const, text: move.text };
			const stop = routerMessage(model, [words], move.reason, 'stop');
			out.push({ type: 'start', partial: { ...stop, content: [] } });
			out.push({ type: 'text_start', contentIndex: 0, partial: { ...stop, content: [{ type: 'text', text: '' }] } });
			// Osaurus shows text from deltas only (PiRPCAdapter.swift), so code's words must stream.
			out.push({ type: 'text_delta', contentIndex: 0, delta: words.text, partial: stop });
			out.push({ type: 'text_end', contentIndex: 0, content: words.text, partial: stop });
			out.push({ type: 'done', reason: 'stop', message: stop });
			out.end(stop);
			return;
		}
		// The call id carries the marker on the wire too (ROUTER_CALL_PREFIX): the
		// request history a capture records keeps tool-call ids, not responseModel.
		const call = { type: 'toolCall' as const, id: `${ROUTER_CALL_PREFIX}${Date.now().toString(36)}`, name: move.tool, arguments: move.tool === 'spawn_agent' ? move.arguments : {} };
		const message = routerMessage(model, [call], move.reason, 'toolUse');
		out.push({ type: 'start', partial: { ...message, content: [] } });
		out.push({ type: 'toolcall_start', contentIndex: 0, partial: message });
		out.push({ type: 'toolcall_end', contentIndex: 0, toolCall: call, partial: message });
		out.push({ type: 'done', reason: 'toolUse', message });
		out.end(message);
	})();
	return out;
};

// step3c (reports/step3c-commit-design.md): the investigator's commit step on
// the local tiers (commit.ts). One extra request with only the narrowed
// record_finding and nothing_found, tool_choice required, through the same
// provider path (the extension's onPayload hooks run first; then the wire tools
// are replaced). An accepted answer becomes this turn's record_finding call,
// marked by its id; anything else sends the model's own request unchanged.
// Process memory across folds: the requests since the last attempt, and the
// lines seen recorded.
let requestsSinceCommit = Infinity;
const recordedLines = new Set<string>();
// B18: this process saw its write-file deliverable accepted (kept across folds).
let deliverableWritten = false;
type StreamFn = NonNullable<Parameters<ExtensionAPI['registerProvider']>[1]['streamSimple']>;
type Answer = Awaited<ReturnType<ReturnType<typeof streamOpenAICompletions>['result']>>;
// One narrowed request: the commit text appended, only `tools` on the wire.
// B19: a provider that refuses tool_choice "required" (any error) gets the
// same request once with "auto".
async function narrowedRequest(model: Parameters<StreamFn>[0], context: Parameters<StreamFn>[1], options: Parameters<StreamFn>[2], text: string, tools: unknown[]): Promise<Answer> {
	const ask = (choice: 'required' | 'auto') => streamOpenAICompletions(model as never, { ...context, messages: [...context.messages, { role: 'user', content: [{ type: 'text', text }], timestamp: Date.now() }] } as typeof context, {
		...options,
		toolChoice: choice,
		onPayload: async (payload, payloadModel) => {
			const prepared = ((await options?.onPayload?.(payload, payloadModel)) ?? payload) as Record<string, unknown>;
			return { ...prepared, tools, tool_choice: choice };
		},
	}).result();
	const first = await ask('required').catch((error: Error) => error);
	if (first instanceof Error || first.stopReason === 'error') {
		log(`[commit] tool_choice required failed (${(first instanceof Error ? first.message : first.errorMessage ?? 'error').slice(0, 200)}); asking with auto`);
		return ask('auto');
	}
	return first;
}
// The accepted call as this turn's answer, marked by its id; the calls not
// kept are named in the turn itself, so the model sees them.
function emitCommit(out: ReturnType<typeof createAssistantMessageEventStream>, answer: Answer, name: string, args: Record<string, unknown>, dropped: string[], noteHead: string, responseId: string): void {
	const toolCall = { type: 'toolCall' as const, id: `${COMMIT_CALL_PREFIX}${Date.now().toString(36)}`, name, arguments: args };
	const note = dropped.length ? [{ type: 'text' as const, text: `${noteHead}; not recorded: ${dropped.join('; ')}.` }] : [];
	const message = { ...answer, content: [...note, toolCall], responseId, stopReason: 'toolUse' as const };
	// Each block is in the partial only from its own event on (Pi's provider contract).
	out.push({ type: 'start', partial: { ...message, content: [] } });
	if (note.length) {
		out.push({ type: 'text_start', contentIndex: 0, partial: { ...message, content: [{ type: 'text', text: '' }] } });
		out.push({ type: 'text_end', contentIndex: 0, content: note[0].text, partial: { ...message, content: [...note] } });
	}
	out.push({ type: 'toolcall_start', contentIndex: note.length, partial: { ...message, content: [...note, { ...toolCall, arguments: {} }] } });
	out.push({ type: 'toolcall_end', contentIndex: note.length, toolCall, partial: message });
	out.push({ type: 'done', reason: 'toolUse', message });
	out.end(message);
}
const commitStream: StreamFn = (model, context, options) => {
	const out = createAssistantMessageEventStream();
	void (async () => {
		// B18: a worker whose deliverable is one write-file gets the write commit step.
		const file = writeCommitFile(process.env.SPOTLIGHT_DELIVERABLE_FILES);
		if (file && getCurrentTools(context.messages).some((tool) => tool.name === 'write_file')) {
			const caseDir = required('SPOTLIGHT_CASE_DIR');
			requestsSinceCommit += 1;
			deliverableWritten ||= writeCommitState(context.messages, file, caseDir).written;
			if (writeCommitDue(context.messages, file, caseDir, requestsSinceCommit, deliverableWritten)) {
				requestsSinceCommit = 0;
				let reason: string;
				try {
					const answer = await narrowedRequest(model, context, options, writeCommitText(file), writeCommitTools(file));
					const calls = answer.content.filter((part) => part.type === 'toolCall') as Array<{ name?: string; arguments?: unknown }>;
					const accepted = answer.stopReason === 'error' || answer.stopReason === 'aborted' ? { reason: `the commit request ended ${answer.stopReason}` } : pickWrite(calls, file, caseDir);
					if ('args' in accepted) {
						log(`[commit] write_file ${file}${accepted.dropped.length ? `; dropped ${accepted.dropped.join('; ')}` : ''}`);
						emitCommit(out, answer, 'write_file', accepted.args, accepted.dropped, 'Commit step: kept the first write', `commit step: write ${file}`);
						return;
					}
					reason = accepted.reason;
				} catch (error) {
					reason = `commit request failed: ${(error as Error).message.slice(0, 200)}`;
				}
				log(`[commit] model's own request (${file}: ${reason})`);
			}
			for await (const event of streamOpenAICompletions(model as never, context, options)) out.push(event);
			out.end();
			return;
		}
		// Lines a recorded passage already anchors count as recorded, whichever process recorded them.
		try {
			const passages = (JSON.parse(readFileSync(resolve(required('SPOTLIGHT_CASE_DIR'), 'data', 'source-expressions.json'), 'utf8')) as { expressions?: Array<{ anchor_ref?: { path?: unknown; line_start?: unknown; line_end?: unknown } }> }).expressions ?? [];
			for (const anchor of passages.map((passage) => passage?.anchor_ref)) {
				if (typeof anchor?.path !== 'string' || !Number.isInteger(anchor.line_start)) continue;
				for (let line = Number(anchor.line_start); line <= Number(Number.isInteger(anchor.line_end) ? anchor.line_end : anchor.line_start); line++) recordedLines.add(`${anchor.path}:${line}`);
			}
		} catch {
			// No passages yet.
		}
		const state = commitState(context.messages, recordedLines);
		for (const line of state.committed) recordedLines.add(line);
		let reason = '';
		// Only an activated case records passages, the durable record of what was
		// committed; a legacy case keeps the model's own route (Codex review of simp-commit, P2).
		const own = getCurrentTools(context.messages).some((tool) => tool.name === 'record_finding') && existsSync(resolve(required('SPOTLIGHT_CASE_DIR'), 'data', 'case-contract.json'));
		if (own) requestsSinceCommit += 1;
		if (own && commitDue(state, requestsSinceCommit)) {
			requestsSinceCommit = 0;
			try {
				const answer = await narrowedRequest(model, context, options, COMMIT_TEXT, COMMIT_TOOLS);
				const calls = answer.content.filter((part) => part.type === 'toolCall') as Array<{ name?: string; arguments?: unknown }>;
				const accepted = answer.stopReason === 'error' || answer.stopReason === 'aborted' ? { reason: `the commit request ended ${answer.stopReason}` } : pickCommit(calls, state.shown, state.committed);
				if ('args' in accepted) {
					log(`[commit] record_finding at tool turn ${state.turns} (${accepted.args.cite.map((cite) => `${cite.path}:${cite.line_start}-${cite.line_end}`).join(', ')})${accepted.dropped.length ? `; dropped ${accepted.dropped.join('; ')}` : ''}`);
					emitCommit(out, answer, 'record_finding', accepted.args, accepted.dropped, 'Commit step: kept the first finding', `commit step: ${state.uncommitted.length} unrecorded line(s) shown`);
					return;
				}
				// The worker sees why its cite was dropped and gets the lines; the next commit step can then keep it.
				if ('read' in accepted && accepted.read && getCurrentTools(context.messages).some((tool) => tool.name === 'read_file')) {
					log(`[commit] read_file at tool turn ${state.turns} (${JSON.stringify(accepted.read)})`);
					// A fixed note: the path is the model's text, and it travels only as the call's argument.
					emitCommit(out, answer, 'read_file', accepted.read, ['the finding, because its cite names lines not shown to you; cite it again once you have read them'], 'Commit step: reading the cited lines', 'commit step: cited lines not shown');
					return;
				}
				reason = accepted.reason;
			} catch (error) {
				reason = `commit request failed: ${(error as Error).message.slice(0, 200)}`;
			}
			log(`[commit] model's own request at tool turn ${state.turns} (${reason})`);
		}
		for await (const event of streamOpenAICompletions(model as never, context, options)) out.push(event);
		out.end();
	})();
	return out;
};

export default function spotlightExtension(pi: ExtensionAPI): void {
	const role = required('SPOTLIGHT_ROLE');
	if (process.env.SPOTLIGHT_ROUTE_INFERENCE !== 'own') {
		// The launcher never starts this combination; refuse here too.
		log('case material on a route the operator does not run; refusing to start');
		process.exit(78);
	}
	const operatorBody = parseFixedRequestBody(process.env.SPOTLIGHT_PROVIDER_REQUEST_BODY);
	const fixedBody = withLocalThinking(operatorBody, process.env.SPOTLIGHT_MODEL_TIER, role === 'fact-checker');
	const contextWindow = Number(process.env.SPOTLIGHT_MODEL_CONTEXT_WINDOW || 32768);
	pi.registerProvider(PROVIDER_ID, {
		...(role === ORCHESTRATOR && routerOn(process.env) ? { streamSimple: routedStream } : (role === 'investigator' || role === 'analyst') && routerOn(process.env) && (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') !== 'frontier' ? { streamSimple: commitStream } : {}),
		name: 'Spotlight',
		baseUrl: required('SPOTLIGHT_MODEL_BASE_URL'),
		api: 'openai-completions',
		// Pi requires a key; a local server without one ignores the placeholder.
		apiKey: process.env.SPOTLIGHT_PROVIDER_API_KEY ? '$SPOTLIGHT_PROVIDER_API_KEY' : 'spotlight-no-key',
		models: [
			{
				id: required('SPOTLIGHT_MODEL_ID'),
				name: required('SPOTLIGHT_MODEL_ID'),
				reasoning: false,
				input: ['text'],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow,
				maxTokens: maxOutputTokens(contextWindow),
			},
		],
	});
	const tier12b = (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') === '12b';
	pi.on('before_provider_request', (event, ctx) => {
		const payload = tier12b ? withoutHistoryReasoning(event.payload) : event.payload;
		const outcome = applyFixedRequestBody(payload, fixedBody);
		if (outcome.blocked) {
			log('provider privacy policy could not apply to this request; it was sent without content and the run is aborted');
			ctx.abort();
		}
		return outcome.payload;
	});
	// The launcher sets the local tier's fold point in Pi's settings; the
	// frontier tier keeps Pi's default threshold and summary.
	const caseDir = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR')).caseDir;
	const fetched = role !== ORCHESTRATOR ? registerFetchedPages(pi) : () => undefined;
	const assigned = (process.env.SPOTLIGHT_FC_BATCH ?? '').split(',').filter(Boolean);
	const remaining = role === 'fact-checker' ? () => {
		const recheckPath = process.env.SPOTLIGHT_TASK_RECHECK;
		const recheck = recheckPath ? JSON.parse(readFileSync(readTarget(caseDir, recheckPath), 'utf8')) : {};
		return assigned.length ? checkPacket(caseDir, readCheckDocs(caseDir), assigned, recheck, recheckPath) : nextCheckText(readCheckDocs(caseDir));
	} : undefined;
	registerCompaction(pi, withLocalThinking(operatorBody, process.env.SPOTLIGHT_MODEL_TIER), process.env.SPOTLIGHT_MODEL_TIER !== 'frontier', process.env, role !== ORCHESTRATOR, (path) => existsSync(resolve(caseDir, path)), remaining, fetched);
	// Every tier since constellation-20261102 (review W4): no recorded DeepSeek
	// or Gemma4 31B worker repeated one call with the same result even twice.
	const guarded = registerLoopGuard(pi, role !== ORCHESTRATOR ? `The ${role} ${LOOP_STOP}` : undefined);
	// Ending the orchestrator would end the case, so only workers are stopped,
	// on every tier: in the recorded runs (watchdog.md) no productive worker had
	// more than four refusals in a row, and no DeepSeek or Gemma4 31B worker two.
	const streakGuarded = role !== ORCHESTRATOR ? registerRefusalStreak(pi, `The ${role} ${REFUSAL_STOP}`) : (tool: ToolDefinition) => tool;
	if (role !== ORCHESTRATOR) registerProgressWatchdog(pi, role, caseDir);
	if (role === ORCHESTRATOR) {
		registerReviewRefusalCap(pi);
		registerEndTurnOnOpen(pi);
		registerOrchestratorLoopBound(pi);
		registerBlockedStop(pi);
		const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
		const runAdapter = pythonAdapter(process.env.SPOTLIGHT_PYTHON || 'python3', resolverEnv(process.env, loadComposition()));
		registerUnproductiveBound(pi, async () => nextAction(await caseForModel(active, runAdapter), (relative) => existsSync(resolve(active.caseDir, relative)), active.caseDir));
	}
	if (role === 'analyst') registerProposalEnd(pi);
	const ownFiles = (process.env.SPOTLIGHT_DELIVERABLE_FILES ?? '').split(',').filter(Boolean);
	const deliverable = writeCommitFile(process.env.SPOTLIGHT_DELIVERABLE_FILES);
	if ((role === 'investigator' || role === 'analyst') && routerOn(process.env) && (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') !== 'frontier' && deliverable && hasDeliverableCheck(deliverable)) registerDeliverableEnd(pi, deliverable, caseDir);
	const factCheckDue = role === 'fact-checker' ? registerFactCheckDue(pi) : (tool: ToolDefinition) => tool;
	if (tier12b) {
		registerReadBudget(pi, bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR')).caseDir);
		registerCompletionFloor(pi);
		registerEmptyTurnNudge(pi);
	}
	registerToolOwners(pi, role);
	if (process.env.SPOTLIGHT_CALL_BUDGET) registerCallBudget(pi, Number(process.env.SPOTLIGHT_CALL_BUDGET));
	const tools = toolsFor(role, pi).map(guarded).map(streakGuarded).map(registerSkillDedup(pi)).map(registerReadDedup(pi)).map(factCheckDue);
	const offered = localAssessmentGate(role, caseDir, assigned);
	if (role === 'fact-checker' && process.env.SPOTLIGHT_FC_BATCH) registerBatchEnd(pi, tools.map((tool) => tool.name), offered);
	// B20b: with no batch, nothing else reopens record_assessment after a fetch brings the second source.
	else if (role === 'fact-checker' && (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') !== 'frontier') pi.on('tool_execution_end', () => pi.setActiveTools(tools.map((tool) => tool.name).filter(offered)));
	if (tier12b) registerHyphenNames(pi, new Set(tools.map((tool) => tool.name)));
	for (const tool of tools) pi.registerTool(tool);
	registerSchemaHints(pi, tools);
	const write = tools.find((tool) => tool.name === 'write_file');
	if (role !== ORCHESTRATOR && write && ownFiles.length === 1 && REPLY_DELIVERABLES.includes(ownFiles[0])) registerReplyDeliverable(pi, write, ownFiles[0]);
	if (role === ORCHESTRATOR && tier12b) {
		const active = bindActiveCase(required('SPOTLIGHT_CASES_ROOT'), required('SPOTLIGHT_CASE_DIR'));
		const runAdapter = pythonAdapter(process.env.SPOTLIGHT_PYTHON || 'python3', resolverEnv(process.env, loadComposition()));
		registerTextTurnNudge(pi, () => caseForModel(active, runAdapter), () => stall.count >= STALLED_DISPATCHES || refusedSpawns.stopped);
	}
	pi.on('session_start', () => pi.setActiveTools(tools.map((tool) => tool.name).filter(offered)));
}

// B20 (case-files.ts secondSourceFetched): a local-tier fact-checker, with a
// batch or without one (B20b, k4r-9ef5.md), is offered record_assessment only once it has fetched a second source; until
// then judge_finding writes its claims. The frontier tier and every other
// role keep all their tools.
export const ASSESSMENT_HELD = 'record_assessment opens once you have fetched a second source, a page none of your findings cites. Until then answer each finding with judge_finding {finding_id, answer: supports, partly, doesnt, cant_tell or contradicts (with quote, the contradicting line of the re-fetch), reason: one line}; code writes its claim.';
const assessmentHeld = { held: false };
function localAssessmentGate(role: string, caseDir: string, batch: string[]): (tool: string) => boolean {
	if (role !== 'fact-checker' || (process.env.SPOTLIGHT_MODEL_TIER?.trim() || '12b') === 'frontier') return () => true;
	const context = { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON || 'python3', configPath: process.env.SPOTLIGHT_CONFIG || `${CHECKOUT_ROOT}/.spotlight-config.json`, env: process.env };
	const started = Date.now();
	let open = false;
	return (tool) => {
		if (tool !== 'record_assessment') return true;
		open ||= secondSourceFetched(context, batch, started);
		assessmentHeld.held = !open;
		return open;
	};
}

// The 12b serves 40,960 tokens per slot, and Pi cannot fold a turn's own tool
// results away when they overflow it: its one compact-and-retry keeps them,
// because a tool result is never a cut point. So on the 12b all the read_file
// results of one turn together return at most 30,000 characters (tool-result
// text runs about 3.1 characters per Gemma token: about 9.6k tokens, which fits
// right after a fold), cut with read_file's own truncation notice. Pi runs a
// turn's tool calls in parallel, so the read that is cut is the one that
// finishes last, not necessarily the one called last.
const TURN_READ_CHARS_12B = 30_000;

// LFM3 on Osaurus (7513e421) wrote tool names with "-" for "_" ("invoke-skill",
// "query-vault"), and each call failed as a tool that does not exist. On the
// 12b a call whose name becomes a real tool by that one swap is renamed and
// logged; no other name is ever mapped.
function registerHyphenNames(pi: ExtensionAPI, names: Set<string>): void {
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string; name?: string }> };
		if (message.role !== 'assistant' || !Array.isArray(message.content)) return undefined;
		let renamed = false;
		const content = message.content.map((part) => {
			if (part.type !== 'toolCall' || !part.name || names.has(part.name)) return part;
			const real = part.name.replaceAll('-', '_');
			if (real === part.name || !names.has(real)) return part;
			log(`tool name ${part.name} read as ${real}`);
			renamed = true;
			return { ...part, name: real };
		});
		return renamed ? { message: { ...event.message, content } as typeof event.message } : undefined;
	});
}

// fc-stall scan (incr-test2 sp98): after a fold, fact-checker threads fetched
// the same Companies House pages again (3x). The pages a worker saved this run,
// by URL, for the text that comes back after a fold.
const FETCHED_SHOWN = 12;
export function registerFetchedPages(pi: ExtensionAPI): () => string | undefined {
	const saved = new Map<string, string>();
	const started = new Map<string, { url?: unknown; save_as?: unknown }>();
	pi.on('tool_execution_start', (event) => {
		if (event.toolName === 'fetch') started.set(event.toolCallId, (event.args ?? {}) as { url?: unknown; save_as?: unknown });
	});
	pi.on('tool_execution_end', (event) => {
		const args = started.get(event.toolCallId);
		started.delete(event.toolCallId);
		if (!args || event.isError || typeof args.url !== 'string' || typeof args.save_as !== 'string') return;
		saved.delete(args.url);
		saved.set(args.url, args.save_as);
	});
	return () => {
		if (saved.size === 0) return undefined;
		const pages = [...saved].slice(-FETCHED_SHOWN).map(([url, path]) => `${url} -> ${path}`);
		return `Already fetched in this task (read the saved file; do not fetch again): ${pages.join('; ')}.`;
	};
}

// Skill bodies share the budget (LFM3 e1 on Osaurus, 7513e421: a planning
// investigator loaded 11 skills in one turn, 99,184 characters; Pi cannot fold
// inside one turn's results, so the next request was 33,177 tokens with a
// 1-token completion budget). A skill is never cut: one that does not fit
// whole is not loaded, and the model can load it in a later turn.
export const skillNotLoaded = (name: string) =>
	`Not loaded: this turn's skills and reads already use the ${TURN_READ_CHARS_12B.toLocaleString('en-US')} characters one turn may load. Call invoke_skill for ${name} again in a later turn if you still need it.`;

// A skill loaded once stays in the conversation until a fold, and loading it
// again repeats its whole text (U4 candidate: repeated skill loads). Until the
// next fold a repeat call returns this line instead; a skill the read budget
// did not load counts as not loaded.
export const skillAlreadyLoaded = (name: string) => `${name} is already loaded above in this conversation; follow those instructions.`;

function registerSkillDedup(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	const loaded = new Set<string>();
	pi.on('session_compact', () => {
		loaded.clear();
	});
	const requested = new Map<string, unknown>();
	pi.on('tool_execution_start', (event) => {
		if (event.toolName === 'invoke_skill') {
			const args = event.args as { skill?: unknown; reference?: unknown } | undefined;
			requested.set(event.toolCallId, typeof args?.skill === 'string' ? `${args.skill}${typeof args.reference === 'string' ? '/' + args.reference : ''}` : undefined);
		}
	});
	pi.on('tool_execution_end', (event) => {
		const skill = requested.get(event.toolCallId);
		requested.delete(event.toolCallId);
		if (event.toolName !== 'invoke_skill' || event.isError || typeof skill !== 'string') return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		if (text !== '' && !text.startsWith('Not loaded:') && text !== skillAlreadyLoaded(skill)) loaded.add(skill);
	});
	return (tool) =>
		tool.name !== 'invoke_skill'
			? tool
			: {
					...tool,
					async execute(id, params, ...rest) {
						const args = params as { skill?: unknown; reference?: unknown };
						const skill = typeof args.skill === 'string' ? `${args.skill}${typeof args.reference === 'string' ? '/' + args.reference : ''}` : undefined;
						if (typeof skill === 'string' && loaded.has(skill)) return { content: [{ type: 'text', text: skillAlreadyLoaded(skill) }], details: undefined };
						return tool.execute(id, params, ...rest);
					},
				};
}

// U3 census (84674fd9): both models read the same file again and again (r5's
// orchestrator 61 to 167 times per case, base's investigator 56 to 129). A
// read of the same path and range whose text is unchanged since an earlier
// read in this conversation returns this line instead; a changed file, another
// range, a read the budget cut short, and any read after a fold return the text.
export const readAlreadyShown = (path: string) => `${path} is unchanged since you read it above in this conversation; use that text.`;

function registerReadDedup(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	// Read key (path and range) -> hash of the text it returned in full.
	const shown = new Map<string, string>();
	const pending = new Map<string, { key: string; hash: string }>();
	const readKey = (params: unknown) => {
		const { path, offset, limit, json_pointer } = params as { path?: unknown; offset?: unknown; limit?: unknown; json_pointer?: unknown };
		return JSON.stringify([path, offset ?? null, limit ?? null, json_pointer ?? null]);
	};
	const textOf = (result: { content?: Array<{ type: string; text?: string }> }) => (result.content ?? []).map((part) => (part.type === 'text' ? (part.text ?? '') : '')).join('');
	pi.on('session_compact', () => {
		shown.clear();
	});
	pi.on('tool_execution_end', (event) => {
		const read = pending.get(event.toolCallId);
		pending.delete(event.toolCallId);
		if (!read || event.isError) return;
		const text = textOf(event.result ?? {});
		if (!text.includes(READ_TRUNCATED) && !text.endsWith(' is unchanged since you read it above in this conversation; use that text.')) shown.set(read.key, read.hash);
	});
	return (tool) =>
		tool.name !== 'read_file'
			? tool
			: {
					...tool,
					async execute(id, params, ...rest) {
						const result = await tool.execute(id, params, ...rest);
						const key = readKey(params);
						const hash = createHash('sha256').update(textOf(result as { content?: Array<{ type: string; text?: string }> })).digest('hex');
						if (shown.get(key) === hash) return { content: [{ type: 'text', text: readAlreadyShown(String((params as { path?: unknown }).path)) }], details: undefined };
						pending.set(id, { key, hash });
						return result;
					},
				};
}

// The web tools' results share the same budget. r5 swiss on a4f47704: one
// 49,241-character http_get result right after a fold took an investigator's
// prompt from 19.4k to 38.8k tokens of the 40,960 window (about 2.3 characters
// per LFM3 token). A web result that does not fit is saved whole to a case
// file, and the model gets its head and the path.
const WEB_RESULTS = new Set(['http_get', 'fetch', 'browse']);
const WEB_HEAD_CHARS = 2_000;

function webResultSaved(head: string, length: number, path: string | undefined): string {
	return path
		? `${head}\n[This result is ${length} characters, more than this turn's reads leave room for. It is saved whole at ${path}: find what you need with grep_files and read only those lines with read_file offset/limit.]`
		: `${head}\n[This result is ${length} characters and was cut here; the rest could not be saved. Call it again with save_as to keep it whole.]`;
}

// A web tool's result against what is left of the turn's budget: undefined
// when the tool is not a web tool, otherwise the characters it uses and, when
// it does not fit, the text the model gets instead, after `save` stored it whole.
export function budgetedWebResult(tool: string, callId: string, text: string, left: number, save: (path: string, text: string) => string | undefined): { used: number; replacement?: string } | undefined {
	if (!WEB_RESULTS.has(tool)) return undefined;
	if (text.length <= left) return { used: text.length };
	const saved = save(`research/tool-results/${tool}-${callId.replace(/[^A-Za-z0-9_-]/g, '_')}.txt`, text);
	const head = text.slice(0, Math.min(WEB_HEAD_CHARS, left));
	return { used: head.length, replacement: webResultSaved(head, text.length, saved) };
}

function registerReadBudget(pi: ExtensionAPI, caseDir: string): void {
	let left = TURN_READ_CHARS_12B;
	pi.on('turn_start', () => {
		left = TURN_READ_CHARS_12B;
	});
	pi.on('tool_result', (event) => {
		if ((event.toolName !== 'read_file' && event.toolName !== 'invoke_skill' && !WEB_RESULTS.has(event.toolName)) || event.isError) return;
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('');
		const budgeted = budgetedWebResult(event.toolName, event.toolCallId, text, left, (path, whole) => {
			try {
				return writeConfined(caseDir, caseDir, path, whole);
			} catch (error) {
				log(`a ${event.toolName} result could not be saved: ${(error as Error).message}`);
				return undefined;
			}
		});
		if (budgeted) {
			left -= budgeted.used;
			return budgeted.replacement ? { content: [{ type: 'text' as const, text: budgeted.replacement }] } : undefined;
		}
		if (event.toolName === 'invoke_skill') {
			if (text.length <= left) {
				left -= text.length;
				return;
			}
			const name = String((event.input as { skill?: unknown }).skill ?? 'that skill');
			return { content: [{ type: 'text' as const, text: skillNotLoaded(name) }] };
		}
		const kept = Math.min(text.length, left);
		left -= kept;
		if (kept === text.length) return;
		return { content: [{ type: 'text' as const, text: `${text.slice(0, kept)}\n${READ_TRUNCATED}` }] };
	});
}

// Pi sizes each request's completion budget as the window minus its prompt
// estimate minus 4,096, and sends as little as 1 token (pi-ai
// clampMaxTokensToContext): the model can then only end its turn. Below this
// many tokens the 12b turn is stopped with an honest line instead; a fold
// cannot help, since Pi never cuts inside the turn that filled the window.
const MIN_COMPLETION_TOKENS = 512;
export const NO_ROOM_STOP = 'Spotlight stopped this turn because the conversation no longer leaves the model room to answer; nothing in it was used. The results before it stand.';

function registerCompletionFloor(pi: ExtensionAPI): void {
	let starved = false;
	pi.on('turn_start', () => {
		starved = false;
	});
	pi.on('before_provider_request', (event, ctx) => {
		const payload = event.payload as { max_completion_tokens?: unknown; max_tokens?: unknown } | undefined;
		const budget = payload?.max_completion_tokens ?? payload?.max_tokens;
		if (typeof budget !== 'number' || budget >= MIN_COMPLETION_TOKENS) return undefined;
		starved = true;
		log(`a turn with a ${budget}-token completion budget was stopped`);
		ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!starved || (event.message as { role?: string }).role !== 'assistant') return undefined;
		return shownStop(event, NO_ROOM_STOP);
	});
}

// The 12b can end a turn inside its own reasoning, with no text and no tool
// call (12B r3 A2 cc seq 4), and Pi then ends the run: the member sees nothing,
// and a worker's empty answer reaches the orchestrator as "(no output)". Such a
// turn gets this one follow-up, at most once per prompt. Every Spotlight process
// loads this extension, so workers get it too. The count resets on a user message
// other than the nudge, not on agent_start: a nudge sent after the run ended
// starts a new run, and resetting there could nudge forever.
export const EMPTY_TURN_NUDGE = 'Your last turn was empty. Call the next tool, or give your answer.';
// A turn that ran to the output cap with no tool call gets the same one follow-up.
export const RUNAWAY_TURN_NUDGE = 'Your last turn ran on without a tool call and was cut off. Call the next tool, or give your answer in a few sentences.';
const NUDGES = [EMPTY_TURN_NUDGE, RUNAWAY_TURN_NUDGE];

// Base 12B with no reasoning budget (step-eval, 2026-10-28) ran two turns to the
// 10,240-token cap: the orchestrator repeating "<channel|><|channel>thought"
// for 71,680 characters, a fact-checker cycling three sentences for 31,538.
// The guard stops a turn once the last TAIL_CHARS of its text or reasoning are
// one unit of at most MAX_PERIOD characters repeated exactly. Tool-call
// arguments are not watched: a long JSON write (r4arm cc:114, 34 KB) repeats its
// keys but never a whole 2,048-character stretch.
const TAIL_CHARS = 2048;
const MAX_PERIOD = 512;
const CHECK_EVERY = 256;
// base213 (Anomaly, 5ce09322): an investigator turn ran 10,240 tokens with no
// text, reasoning or tool call left after the server stripped its special
// tokens. Each streamed chunk is about one token, so this many chunks in a row
// with nothing visible stop the turn too. Prompt processing sends no chunks.
const INVISIBLE_CHUNKS = 1024;
// A server may send nothing at all for stripped tokens while the stream stays
// open, so a turn with nothing visible for this long after its first token
// chunk is stopped as well. The clock starts at the first chunk after the role
// chunk, so prompt processing (20-40 s for a 16k prompt on a Mac) never counts.
const INVISIBLE_MS = 60_000;
// A turn cut at the cap after this few tokens is a full context slot, not a
// ramble: a nudge would only overflow again.
const MIN_CUT_TOKENS = 10;

// Whether a raw OpenAI-style stream chunk carries anything the turn keeps.
function visibleChunk(data: unknown): boolean {
	const delta = (data as { choices?: Array<{ delta?: Record<string, unknown> }> } | undefined)?.choices?.[0]?.delta;
	if (!delta) return true;
	const text = (value: unknown) => typeof value === 'string' && value.length > 0;
	return text(delta.content) || text(delta.reasoning_content) || text(delta.reasoning) || (Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0);
}

export function repeatsItself(text: string): boolean {
	if (text.length < TAIL_CHARS + MAX_PERIOD) return false;
	const tail = text.slice(-(TAIL_CHARS + MAX_PERIOD));
	const window = tail.slice(-TAIL_CHARS);
	for (let period = 1; period <= MAX_PERIOD; period += 1) {
		if (tail.slice(-TAIL_CHARS - period, -period) === window) return true;
	}
	return false;
}

export const REPEAT_STOP = 'Spotlight stopped this turn because it ran on without calling a tool or writing anything usable; nothing in it was used. The results before it stand.';
// lfm3-r5 on uk-cpi (rc3, 52bf6183): the server streamed two complete tool
// calls, then held the stream open for 223 s. The stop dropped the calls and
// the run ended with no line for the member.
export const STALL_STOP = 'Spotlight stopped this turn because the model stopped responding. Ask Spotlight to continue.';

function registerEmptyTurnNudge(pi: ExtensionAPI): void {
	let nudged = false;
	let streamed = '';
	let unchecked = 0;
	let stopped = false;
	let stalled = false;
	let invisible = 0;
	let lastVisible = 0;
	let clock: ReturnType<typeof setInterval> | undefined;
	const stopClock = () => {
		if (clock) clearInterval(clock);
		clock = undefined;
	};
	const stopInvisible = (abort: () => void) => {
		stopClock();
		stopped = true;
		stalled = true;
		log('a turn streaming nothing visible was stopped');
		abort();
	};
	// Pi starts the assistant message after its first chunk, so each turn's
	// counters reset when the turn starts.
	pi.on('turn_start', () => {
		streamed = '';
		unchecked = 0;
		invisible = 0;
		stopped = false;
		stalled = false;
		stopClock();
	});
	pi.on('provider_stream_event', (event, ctx) => {
		if (stopped) return;
		const delta = (event.data as { choices?: Array<{ delta?: Record<string, unknown> }> } | undefined)?.choices?.[0]?.delta;
		if (delta && 'role' in delta && !visibleChunk(event.data)) return;
		if (visibleChunk(event.data)) {
			invisible = 0;
			lastVisible = Date.now();
		} else {
			invisible += 1;
		}
		if (!clock) {
			lastVisible = Date.now();
			clock = setInterval(() => {
				if (!stopped && Date.now() - lastVisible >= INVISIBLE_MS) stopInvisible(() => ctx.abort());
			}, 1000);
			clock.unref();
		}
		if (invisible >= INVISIBLE_CHUNKS) stopInvisible(() => ctx.abort());
	});
	pi.on('message_update', (event, ctx) => {
		const update = event.assistantMessageEvent;
		if (stopped || (update.type !== 'text_delta' && update.type !== 'thinking_delta')) return;
		streamed = (streamed + update.delta).slice(-(TAIL_CHARS + MAX_PERIOD));
		unchecked += update.delta.length;
		if (unchecked < CHECK_EVERY) return;
		unchecked = 0;
		if (!repeatsItself(streamed)) return;
		stopped = true;
		log('a turn repeating itself was stopped');
		ctx.abort();
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string; thinking?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (message.role === 'user') {
			if (!parts.some((part) => part.type === 'text' && NUDGES.includes(part.text ?? ''))) nudged = false;
			return;
		}
		if (message.role !== 'assistant') return undefined;
		stopClock();
		// Pi's abort ends the whole run and drops queued follow-ups, so a stopped
		// turn ends the run on an honest line instead of a nudge: the member sees
		// it, and a worker's line is its answer to the orchestrator. A turn stopped
		// after it streamed tool calls lost them in the abort.
		const calls = parts.some((part) => part.type === 'toolCall');
		if (stopped) return shownStop(event, stalled && calls ? STALL_STOP : REPEAT_STOP);
		if (calls) return undefined;
		const output = (event.message as { usage?: { output?: number } }).usage?.output;
		if (message.stopReason === 'length' && output && output < MIN_CUT_TOKENS) return undefined;
		const cut = message.stopReason === 'length';
		if (!cut && (message.stopReason !== 'stop' || parts.some((part) => part.type === 'text' && part.text?.trim()))) return undefined;
		if (nudged) return undefined;
		nudged = true;
		pi.sendUserMessage(cut ? RUNAWAY_TURN_NUDGE : EMPTY_TURN_NUDGE, { deliverAs: 'followUp' });
		return undefined;
	});
}

// The phases where the orchestrator does the next step itself and no gate waits
// for the member. Brief, Research and Ingest also hold conversations with the
// member, so a text turn there can be right.
const ACTING_PHASES = ['methodology', 'investigation', 'investigation_gate_finalization'];
export const textTurnNudge = (phase: string, owner: string) =>
	`Your turn ended with text, but the case is at ${phase} (skill: ${owner}) and no gate is waiting for the member, so the case moves only when you act. Take that phase's next step with your tools now; call spotlight_resolve if you are unsure what it is.`;

// r6 northwind on dd14e2e5: the orchestrator ended the run with a plain-text
// summary of the methodology after its gate steps were refused. A text turn
// with no tool call, in a phase only the orchestrator can move, gets one
// nudge; a second text turn in a row ends the run as before, so a model that
// is done is not held.
// `held`: code has stopped re-dispatching a stalled worker and the member was
// to be told; that text turn is right (Codex review of simp-redispatch, P2).
function registerTextTurnNudge(pi: ExtensionAPI, resolve: () => Promise<Record<string, unknown>>, held: () => boolean = () => false): void {
	let nudged = false;
	pi.on('message_end', async (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (message.role !== 'assistant') return;
		if (parts.some((part) => part.type === 'toolCall')) {
			nudged = false;
			return;
		}
		if (message.stopReason !== 'stop' || nudged || held() || !parts.some((part) => part.type === 'text' && part.text?.trim())) return;
		// The run that ends a held review: the decision's own fresh run follows (registerEndTurnOnOpen).
		if (parts.some((part) => part.text === DECISION_RECORDED || memberHasTheMove(part.text))) return;
		let status: Record<string, unknown>;
		try {
			status = await resolve();
		} catch (error) {
			log(`text-turn check skipped: ${(error as Error).message}`);
			return;
		}
		const phase = String(status.next_phase ?? '');
		if (!ACTING_PHASES.includes(phase)) return;
		nudged = true;
		pi.sendUserMessage(textTurnNudge(phase, String(status.owner ?? '')), { deliverAs: 'followUp' });
	});
}

// A small model can repeat one call over and over (12B retune: the same
// list four times, the same read forty). A call that already ran twice in a
// row and got the same result both times is refused instead of run. Keying
// on the result keeps status calls such as spotlight_resolve free to repeat
// while the state they read changes, and each new prompt starts afresh, so a
// member's gate decision between two resolves never trips it. With `stop`
// (12b workers), the third refusal in a row ends the worker's run with that
// line instead (12B r3 A2 nordhaven: six refusals of one search in a row).
// A call whose arguments fail Pi's schema check counts the same way, though
// Pi answers it before the tool_call hook: its error is its result, it is
// refused before the check (prepareArguments runs first), and the third
// refusal ends the run when the turn ends (12B r5 fsf s0: one invalid
// source_expressions call nine times). Returns the wrapper for each tool.
function registerLoopGuard(pi: ExtensionAPI, stop?: string): (tool: ToolDefinition) => ToolDefinition {
	let lastCall = '';
	let lastResult = '';
	let repeats = 0;
	let refusals = 0;
	let lastInvalid = false;
	let stopping = false;
	const key = (toolName: string, input: unknown) => JSON.stringify([toolName, input]);
	const startedWith = new Map<string, string>();
	pi.on('agent_start', () => {
		lastCall = '';
		repeats = 0;
		refusals = 0;
		lastInvalid = false;
		stopping = false;
	});
	pi.on('tool_execution_start', (event) => {
		startedWith.set(event.toolCallId, key(event.toolName, event.args));
	});
	pi.on('tool_execution_end', (event) => {
		const call = startedWith.get(event.toolCallId);
		startedWith.delete(event.toolCallId);
		const result = JSON.stringify(event.result?.content);
		const text = event.result?.content?.[0]?.text ?? '';
		if (call === undefined || !event.isError || !text.startsWith('Validation failed for tool ')) return;
		if (call !== lastCall) {
			lastCall = call;
			lastResult = '';
			repeats = 0;
		}
		repeats = result === lastResult ? repeats + 1 : 1;
		lastResult = result;
		lastInvalid = true;
		refusals = 0;
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	pi.on('tool_call', (event) => {
		const call = key(event.toolName, event.input);
		if (call !== lastCall) {
			lastCall = call;
			lastResult = '';
			repeats = 0;
		}
		if (repeats < 2) {
			refusals = 0;
			return undefined;
		}
		refusals += 1;
		return stop && refusals >= 3 ? { block: true, reason: stop, terminate: true } : { block: true, reason: REPEAT_REASON };
	});
	pi.on('tool_result', (event) => {
		const result = JSON.stringify(event.content);
		if (key(event.toolName, event.input) !== lastCall || result.includes(REPEAT_REASON)) return;
		repeats = result === lastResult ? repeats + 1 : 1;
		lastResult = result;
		lastInvalid = false;
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (lastInvalid && repeats >= 2 && key(tool.name, args) === lastCall) {
				refusals += 1;
				const reason = stop && refusals >= 3 ? stop : REPEAT_REASON;
				stopping = reason === stop;
				throw new Error(reason);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

// The loop guard sees only one call repeated. r6 on dd14e2e5: workers were
// refused for varied calls with no end (cc investigator-12: 15 writes refused
// before activation; fsf fact-checker-45 and nordhaven investigator-7: about 9
// source_expressions refusals each). Once a worker's last REFUSAL_STREAK
// results were all harness refusals, its next call ends the run with `stop`
// when the turn ends. prepareArguments runs before Pi's schema check and the
// tool_call hook, so every next call meets it. A web tool's error counts only
// when the harness refused the call (egress's "refused:"); a failed upstream
// (a search backend or a site down) is not the worker's doing, so it neither
// counts nor ends the streak (codex-review-comb 1).
const WEB_TOOLS = new Set(['fetch', 'search', 'http_get', 'browse']);

function registerRefusalStreak(pi: ExtensionAPI, stop: string): (tool: ToolDefinition) => ToolDefinition {
	let streak = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		streak = 0;
		stopping = false;
	});
	pi.on('tool_execution_end', (event) => {
		if (!event.isError) streak = 0;
		else if (!WEB_TOOLS.has(event.toolName) || /\brefused:/.test((event.result?.content ?? []).map((part: { text?: string }) => part.text ?? '').join('\n'))) streak += 1;
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (streak >= REFUSAL_STREAK) {
				stopping = true;
				throw new Error(stop);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

// Mandatory progress watchdog (plan 2026-11-01, "Constellation, structural
// delegation and watchdog"). Every worker, on every tier, is stopped once it
// has spent this many model turns without an accepted record:
// - PROGRESS_FIRST_RECORD_TURNS before its first one. Measured on recorded
//   workers (fine-tuning data/lfm3/u6/constellation-20261102/reports/watchdog.md):
//   productive workers took at most 14 turns to their first record.
// - PROGRESS_NEXT_RECORD_TURNS after its last one. ASSUMPTION: the productive
//   maximum gap was 3 turns, and no recorded worker stalled after a record, so
//   the stuck side of this bound is uncalibrated. It was 6 until
//   codex-review-comb 2: a worker gathering sources for its next finding can
//   honestly spend more than 6 turns, and stopping it then loses real work,
//   while 12 still ends a stalled worker well inside its call budget.
// An accepted record is a call that changed a case file's content: a record_*
// or source_expressions call the tool accepted, or a write_file/edit_file of
// one of this worker's own deliverable files (the launcher's
// SPOTLIGHT_DELIVERABLE_FILES, from roles.ts deliverableFor). Targets are
// compared case-relative, however the path was spelled, and rewriting the same
// content is not a record (review W1-W3, constellation-20261102). The stop ends
// the run before its next model request; its answer is the stop line and a
// machine-readable receipt. It is evidence for the orchestrator, not a prompt
// to keep going. Once another guard has told the worker to write (the batch
// fact-checker's, the fact-check due notice, the call budget), the watchdog
// defers to that guard for the rest of the run: that guard's own hard stop
// bounds the worker, and the watchdog only logs a receipt naming it.
export const PROGRESS_FIRST_RECORD_TURNS = 16;
export const PROGRESS_NEXT_RECORD_TURNS = 12;
export const PROGRESS_RECEIPT_TAG = '[progress-watchdog receipt]';
// The file each code-owned record tool writes, by its arguments.
function recordTarget(tool: string, args: Record<string, unknown>): string | undefined {
	if (tool === 'source_expressions') return args.action === 'fingerprint_findings' ? 'data/findings.json' : 'data/source-expressions.json';
	if (tool === 'record_indicator_framework') return 'data/indicator-framework.json';
	if (tool === 'record_finding' || tool === 'record_assessment' || tool === 'judge_finding') return canonicalWriteCall(tool, args)?.path;
	return undefined;
}

export type ProgressReceipt = {
	watchdog: 'progress';
	role: string;
	rule: 'no-first-record' | 'no-new-record';
	limit: number;
	turns: number;
	turns_since_last_record: number;
	last_record: { turn: number; tool: string; target: string } | null;
	deferred_to: string | null;
};
// Each guard that tells a worker to write, by the notice the worker is shown.
const WRITE_NOTICES: Array<[guard: string, notice: () => string]> = [
	['fc-batch', () => FC_BATCH_NOTICE],
	['fact-check-due', () => FACT_CHECK_DUE_NOTICE],
	['call-budget', () => CALL_BUDGET_SPENT],
];

export const progressStop = (receipt: ProgressReceipt) => `The ${receipt.role} ${PROGRESS_STOP}\n${PROGRESS_RECEIPT_TAG} ${JSON.stringify(receipt)}`;

function registerProgressWatchdog(pi: ExtensionAPI, role: string, caseDir: string): void {
	const deliverables = new Set((process.env.SPOTLIGHT_DELIVERABLE_FILES?.split(',') ?? deliverableFor(role, '', '')?.files ?? []).filter(Boolean));
	const caseRelativePath = (path: string) => relative(caseDir, resolve(caseDir, path.replace(/^\{CASE_DIR\}(?:[\\/]|$)/, ''))).split(sep).join('/');
	const contentHash = (target: string) => {
		const file = resolve(caseDir, target);
		return existsSync(file) ? createHash('sha256').update(readFileSync(file)).digest('hex') : '';
	};
	const before = new Map<string, { target: string; hash: string }>();
	// Not reset on agent_start: a fold's continuation prompt is the same task.
	let turns = 0;
	let last: ProgressReceipt['last_record'] = null;
	let stopping: ProgressReceipt | undefined;
	let deferredTo: string | null = null;
	let deferredLogged = false;
	pi.on('tool_execution_start', (event) => {
		const input = (event.args ?? {}) as Record<string, unknown>;
		const file = event.toolName === 'write_file' || event.toolName === 'edit_file' ? canonicalWriteCall(event.toolName, input)?.path : recordTarget(event.toolName, input);
		if (typeof file !== 'string') return;
		const target = caseRelativePath(file);
		if ((event.toolName === 'write_file' || event.toolName === 'edit_file') && !deliverables.has(target)) return;
		before.set(event.toolCallId, { target, hash: contentHash(target) });
	});
	pi.on('tool_execution_end', (event) => {
		const pending = before.get(event.toolCallId);
		before.delete(event.toolCallId);
		if (!pending || event.isError || contentHash(pending.target) === pending.hash) return;
		last = { turn: turns + 1, tool: event.toolName, target: pending.target };
	});
	pi.on('turn_end', (event) => {
		turns += 1;
		// A turn without tool calls ends the run on the worker's own answer.
		const content = (event.message as { content?: Array<{ type: string }> }).content;
		if (stopping || !Array.isArray(content) || !content.some((part) => part.type === 'toolCall')) return;
		const since = turns - (last?.turn ?? 0);
		const limit = last ? PROGRESS_NEXT_RECORD_TURNS : PROGRESS_FIRST_RECORD_TURNS;
		if (since < limit) return;
		const receipt: ProgressReceipt = { watchdog: 'progress', role, rule: last ? 'no-new-record' : 'no-first-record', limit, turns, turns_since_last_record: since, last_record: last, deferred_to: deferredTo };
		if (deferredTo) {
			if (!deferredLogged) log(`${PROGRESS_RECEIPT_TAG} ${JSON.stringify(receipt)}`);
			deferredLogged = true;
			return;
		}
		stopping = receipt;
		log(`${PROGRESS_RECEIPT_TAG} ${JSON.stringify(stopping)}`);
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stopping) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string; text?: string }> };
		if (message.role === 'toolResult' && !deferredTo) {
			const text = (message.content ?? []).map((part) => part.text ?? '').join('\n');
			deferredTo = WRITE_NOTICES.find(([, notice]) => text.includes(notice()))?.[0] ?? null;
		}
		if (!stopping || message.role !== 'assistant') return undefined;
		return { message: { ...event.message, content: [{ type: 'text', text: progressStop(stopping) }] } as typeof event.message };
	});
}

// One research direction's worker (directions.ts) has its share of the
// investigator's calls. Past it, only the calls that write its deliverable
// run, and CALL_BUDGET_GRACE calls later its run ends.
export const CALL_BUDGET_SPENT = "This task's calls are spent. Write only your assigned deliverable with what you have, state what remains incomplete, then finish.";
const CALL_BUDGET_GRACE = 10;
const BUDGET_WRITES = new Set(['write_file', 'edit_file', 'source_expressions', 'record_finding', 'record_assessment', 'judge_finding']);
function registerCallBudget(pi: ExtensionAPI, budget: number): void {
	let calls = 0;
	pi.on('tool_call', (event) => {
		calls += 1;
		if (calls <= budget) return undefined;
		if (calls > budget + CALL_BUDGET_GRACE) return { block: true, reason: `The worker ${CALL_BUDGET_STOP}`, terminate: true };
		return BUDGET_WRITES.has(event.toolName) ? undefined : { block: true, reason: CALL_BUDGET_SPENT };
	});
}

// LFM3 e2 kde stage 2 on Osaurus (03f008b0): the orchestrator rewrote an
// invalid report draft 23 times and open_review refused it 31 times. Writes
// came between the refusals, so no consecutive-refusal guard fired, and only
// the test's 30-minute cap ended it. On every tier the orchestrator's turn is
// stopped at the fifth refusal since the page last opened, before the next
// model request, and the member is told what failed. The count starts again
// after the stop, so a member who asks to try again gets five more attempts.
export const REVIEW_REFUSAL_CAP = 5;
export const PAGE_COULD_NOT_START = 'The Spotlight review page could not start.';
export const REVIEW_START_STOP = 'Spotlight stopped this turn because the review page could not start five times. Nothing was changed in your case. This is a problem with Spotlight on this computer, not with your case, so asking again will not help until it is fixed. The reason is in review-start.log in Spotlight\'s data folder.';

// The stop line is the member's, so it names the problem in plain words and
// shows at most the validator's first three FAIL lines.
export function reviewRefusalStop(refusal: string): string {
	const report = refusal.slice(PAGE_NOT_OPENED.length).startsWith('data/report-draft.json ');
	const stop = report
		? 'Spotlight stopped this turn because the report draft was refused five times. The draft did not pass the report check, so nothing was rendered or recorded. Ask Spotlight to try again, or say what to change.'
		: 'Spotlight stopped this turn because the review page was refused five times. The case files it needed did not pass their check, so nothing was recorded and your case is saved. Send a message here to try again. If the same problems come back, the page cannot open until they are fixed.';
	const errors = failureLines(refusal);
	return errors.length > 0 ? `${stop}\nThe check found these problems.\n${errors.join('\n')}` : stop;
}

// LFM3 r3 kde stage 2 on Osaurus (03f008b0): the review page opened, and the
// orchestrator went on calling open_review in the same turn 16 times; the
// member and the test driver act only between turns. Every phase skill ends
// the turn at the page, so once it opens the harness ends the turn before the
// next model request and the member sees this line.
// SPOTLIGHT_REVIEW_HOLD_MS=0 turns the hold off.
const reviewHoldMs = (env: NodeJS.ProcessEnv) => { const ms = Number(env.SPOTLIGHT_REVIEW_HOLD_MS ?? REVIEW_HOLD_MS); return Number.isFinite(ms) && ms >= 0 ? ms : REVIEW_HOLD_MS; };

export const REVIEW_OPEN_STOP = 'Spotlight opened the review page in your browser and is waiting for your decision there. Say here when you are done.';

function registerEndTurnOnOpen(pi: ExtensionAPI): void {
	let opened = false;
	let decided = false;
	let stopLine = REVIEW_OPEN_STOP;
	pi.on('agent_start', () => {
		opened = false;
	});
	pi.on('tool_execution_end', (event) => {
		if (event.toolName === 'open_review' && !event.isError && (event.result?.details as { opened?: unknown } | undefined)?.opened === true) {
			opened = true;
			stopLine = (event.result?.details as { stop?: string }).stop ?? REVIEW_OPEN_STOP;
		}
		if (event.toolName === 'open_review' && !event.isError && (event.result?.details as { decided?: unknown } | undefined)?.decided === true) decided = true;
	});
	// A decision on the page is the member's turn: a follow-up queued here starts a
	// fresh run, so every per-turn reset applies, before Osaurus's turn settles.
	pi.on('agent_end', () => {
		if (!decided) return;
		decided = false;
		pi.sendUserMessage(MEMBER_DECIDED, { deliverAs: 'followUp' });
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (opened) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!opened || (event.message as { role?: string }).role !== 'assistant') return undefined;
		opened = false;
		return shownStop(event, stopLine);
	});
}

// LFM3 r3 kde stage 3 on Osaurus (03f008b0): the analyst never ended its turn,
// so the builder the harness runs when it finishes never ran. Its one output is
// data/knowledge-proposal.json, refused when it fails interface 1, and then a
// short final answer (agents/analyst.md). Once a proposal is written, a text
// answer ends the run as usual; a message with more tool calls is replaced by
// this answer, with no calls, so the run ends and the builder runs.
export const PROPOSAL_DONE = 'The analyst wrote data/knowledge-proposal.json and was stopped there; its final answer and any watch candidates were not written.';

// arm2 ds-spectator on ce4f010e: after the write that completed its batch
// ("Finish now") the fact-checker rewrote the same claims and read for 32 more
// calls, and batch threads grew to about 55k tokens on reads and fetches. A
// batch fact-checker (SPOTLIGHT_FC_BATCH) ends once its batch has claims; past
// FC_BATCH_DUE calls, only writing and local inspection remain; at FC_BATCH_STOP the run ends.
export const FC_BATCH_DUE = 15;
export const FC_BATCH_STOP = 30;
export const FC_BATCH_DONE = 'Wrote the claims for this batch.';
export const FC_BATCH_NOTICE = 'Write this batch\'s claims now with what you have: a claim you could not confirm gets the verdict unverified, with the reason in grounding_assessment.assessment. Use the available local file tools to inspect saved evidence and judge_finding (or record_assessment, where offered) to finish; new source acquisition is closed for this batch. Write ONLY this batch’s claims to data/fact-check.json; the harness merges by finding_id and preserves omitted claims. Do not reconstruct the file or write probe files. Then finish.';
export const FC_BATCH_SPENT = 'made the batch\'s calls without writing all its claims; the batch is incomplete.';
// What a call to a tool the batch has closed gets, in place of Pi's "not
// found" (fz2 on comb: "invoke_skill ... belongs to the fact-checker", said to
// the fact-checker). The role text still names those tools; this says they are
// closed for this batch and what is open (review e2e 'FC tool narrowing').
// Set while registerBatchEnd has narrowed this fact-checker's tools; only then
// is a missing tool closed by the batch (review-fix7 B1: in sensitive mode
// search and fetch are missing from the first call for another reason).
const batchTools = { narrowed: false };
export const fcBatchClosed = (tool: string) =>
	`${tool} is closed for the rest of this batch because the batch's call budget is spent; source_expressions, invoke_skill and new sources are closed until the batch ends. Next: write this batch's claims with judge_finding (or record_assessment where offered, or write_file to data/fact-check.json). read_file, grep_files, list_files and hash_files stay open for saved evidence. Then finish.`;
function registerBatchEnd(pi: ExtensionAPI, tools: string[], offered: (tool: string) => boolean): void {
	const writingTools = tools.filter((name) => ['write_file', 'record_assessment', 'judge_finding', 'read_file', 'grep_files', 'list_files', 'hash_files'].includes(name));
	let done = false;
	let calls = 0;
	pi.on('agent_start', () => {
		done = false;
		calls = 0;
		batchTools.narrowed = false;
		pi.setActiveTools(tools.filter(offered));
	});
	// Reserve each call before execution: a model can emit many in one response.
	pi.on('tool_call', (event) => {
		if (done) return { block: true, reason: FC_BATCH_DONE, terminate: true };
		calls += 1;
		if (calls > FC_BATCH_STOP) return { block: true, reason: `The fact-checker ${FC_BATCH_SPENT}`, terminate: true };
		if (calls > FC_BATCH_DUE && !writingTools.includes(event.toolName)) return { block: true, reason: fcBatchClosed(event.toolName) };
		return undefined;
	});
	// A fetch may have brought the second source that opens record_assessment.
	pi.on('tool_execution_end', () => {
		if (calls >= FC_BATCH_DUE) batchTools.narrowed = true;
		pi.setActiveTools((batchTools.narrowed ? writingTools : tools).filter(offered));
	});
	pi.on('tool_result', async (event) => {
		const text = event.content.map((part) => part.type === 'text' ? part.text : '').join('\n');
		if ((event.toolName === 'write_file' || event.toolName === 'edit_file' || event.toolName === 'record_assessment' || event.toolName === 'judge_finding') && !event.isError && text.includes('Every finding in your batch has a claim')) {
			const caseDir = required('SPOTLIGHT_CASE_DIR');
			const batch = new Set(required('SPOTLIGHT_FC_BATCH').split(','));
			const docs = readCheckDocs(caseDir);
			const findings = (docs.findings as { findings?: Array<{ id: string }> })?.findings ?? [];
			const future = findings.filter((finding) => !batch.has(finding.id)).map((finding) => finding.id);
			const checked = await validateFactCheckBatch({ caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON || 'python3', configPath: required('SPOTLIGHT_CONFIG'), env: process.env }, future);
			done = checked.passed;
			if (!done) return { content: [...event.content, { type: 'text' as const, text: `\nBatch validation failed; repair these errors within the remaining call allowance before finishing:\n${checked.report}` }] };
		}
		if (done || calls < FC_BATCH_DUE) return undefined;
		return { content: [...event.content, { type: 'text' as const, text: `\n${FC_BATCH_NOTICE}` }] };
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		if (done) {
			log('the fact-checker went on after its batch had claims; its turn was ended');
			return { message: { ...event.message, content: [{ type: 'text', text: FC_BATCH_DONE }] } as typeof event.message };
		}
		if (calls < FC_BATCH_STOP) return undefined;
		log(`the fact-checker made ${FC_BATCH_STOP} calls on one batch; its turn was ended`);
		return { message: { ...event.message, content: [{ type: 'text', text: `The fact-checker ${FC_BATCH_SPENT}` }] } as typeof event.message };
	});
}

// B21 (K4 Tor P5: the analyst put its knowledge proposal in its final reply
// as JSON, three times, and never wrote it). When a worker's final reply holds
// one JSON object and its deliverable is one file whose write runs the full
// validator, code writes that object, as it is, through the worker's own
// write_file: the same refusals, and the same envelope a direct write gets.
// Nothing is added to the model's fields. Accepted: the answer says so.
// Refused: the refusal goes back to the worker as its next prompt, once per
// distinct object; the worker's own write route is unchanged.
// Other deliverables wait for a write-time check as complete as the proposal's.
export const REPLY_DELIVERABLES = ['data/knowledge-proposal.json'];
export const replyTaken = (file: string) => `[Harness: the JSON in this reply was written to ${file} through write_file, which accepted it.]`;
export const replyRefused = (file: string, reason: string) => `Your reply held JSON for ${file}, not a write. The harness offered it to write_file, which refused it: ${reason} Fix what it names and write ${file} with write_file; then finish.`;
// The one JSON object a reply holds: the whole reply, or its one fenced block.
export function replyObject(text: string): Record<string, unknown> | undefined {
	const blocks = [...text.matchAll(/```(?:json)?[ \t]*\n([\s\S]*?)\n```/g)].map((match) => match[1]);
	const candidates = blocks.length ? blocks : [text.trim()];
	if (candidates.length !== 1) return undefined;
	try {
		const value = JSON.parse(candidates[0]) as unknown;
		return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
	} catch {
		return undefined;
	}
}
function registerReplyDeliverable(pi: ExtensionAPI, write: ToolDefinition, file: string): void {
	const offered = new Set<string>();
	// Once the deliverable is accepted a later reply never replaces it (Codex
	// review of simp-stack, P2). Not reset on agent_start: a fold's continuation
	// is the same task.
	let accepted = false;
	pi.on('tool_execution_end', (event) => {
		if ((event.toolName !== 'write_file' && event.toolName !== 'edit_file') || event.isError) return;
		const said: string = event.result?.content?.[0]?.text ?? '';
		if (/^(?:wrote|edited) /.test(said) && said.replace(/\\/g, '/').split(/\s/)[1]?.endsWith(file)) accepted = true;
	});
	pi.on('message_end', async (event) => {
		const message = event.message as { role?: string; stopReason?: string; content?: string | Array<{ type: string; text?: string }> };
		const parts = typeof message.content === 'string' ? [{ type: 'text', text: message.content }] : (message.content ?? []);
		if (accepted || message.role !== 'assistant' || message.stopReason !== 'stop' || parts.some((part) => part.type === 'toolCall')) return undefined;
		const object = replyObject(parts.filter((part) => part.type === 'text').map((part) => part.text ?? '').join('\n'));
		if (!object) return undefined;
		const content = `${JSON.stringify(object, null, 2)}\n`;
		if (offered.has(content)) return undefined;
		offered.add(content);
		try {
			await write.execute(`reply-deliverable-${offered.size}`, { path: file, content }, undefined, undefined, undefined as never);
		} catch (error) {
			log(`the ${file} JSON in a worker's reply was refused`);
			pi.sendUserMessage(replyRefused(file, (error as Error).message), { deliverAs: 'followUp' });
			return undefined;
		}
		accepted = true;
		log(`the ${file} JSON in a worker's reply was written`);
		return { message: { ...event.message, content: [...parts, { type: 'text', text: `\n${replyTaken(file)}` }] } as typeof event.message };
	});
}

// B28 (Tom, 2026-11-04; lfm3-short-check.md: LFM3 rewrote a written plan):
// on the local tiers with the code router on, once a worker's write-file
// deliverable is written and passes its check (validators.ts
// deliverablePasses), the worker's turn ends: a later message with tool calls
// is replaced by this answer, and any call still pending is blocked. A write
// that fails the check changes nothing; the post-worker check and its
// correction run as before. Not reset on agent_start: a fold's continuation is
// the same task.
export const deliverableDone = (file: string) => `The worker wrote ${file}, which passes its check, and was stopped there.`;
function registerDeliverableEnd(pi: ExtensionAPI, file: string, caseDir: string): void {
	let done = false;
	pi.on('tool_call', () => (done ? { block: true, reason: deliverableDone(file), terminate: true } : undefined));
	pi.on('tool_result', async (event) => {
		if (done || (event.toolName !== 'write_file' && event.toolName !== 'edit_file') || event.isError) return undefined;
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('\n');
		if (typeof event.input.path !== 'string' || caseRelative(event.input.path, caseDir) !== file || !/^(?:wrote|edited) /.test(text)) return undefined;
		done = await deliverablePasses(file, { caseDir, writeRoot: caseDir, python: process.env.SPOTLIGHT_PYTHON || 'python3', configPath: required('SPOTLIGHT_CONFIG'), env: process.env });
		if (done) log(`${file} is written and passes its check; the worker's turn ends`);
		return undefined;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (!done || message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		log(`the worker went on after ${file} passed its check; its turn was ended`);
		return { message: { ...event.message, content: [{ type: 'text', text: deliverableDone(file) }] } as typeof event.message };
	});
}

function registerProposalEnd(pi: ExtensionAPI): void {
	let written = false;
	pi.on('agent_start', () => {
		written = false;
	});
	pi.on('tool_execution_end', (event) => {
		if ((event.toolName !== 'write_file' && event.toolName !== 'edit_file') || event.isError) return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		if (/^(wrote|edited) (?:.*[\\/])?data[\\/]knowledge-proposal\.json$/.test(text.trim())) written = true;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (!written || message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		log('the analyst went on after writing a valid proposal; its turn was ended');
		return { message: { ...event.message, content: [{ type: 'text', text: PROPOSAL_DONE }] } as typeof event.message };
	});
}

// U3 census, r5 on swiss (84674fd9): the orchestrator made one osint_tools
// call 286 times in one turn and got the loop guard's notice 273 times. The
// guard stops only workers (ending the orchestrator would end the case), so
// nothing bounded it. At the fifth notice in one run the orchestrator's turn
// ends, not the case, before the next request, and the member sees this line.
export const LOOP_NOTICE_CAP = 5;
export const ORCHESTRATOR_LOOP_STOP = 'Spotlight stopped this turn because it kept repeating the same step without getting anything new. Ask Spotlight to continue, or say what to change.';

// U3 census stage 1 (0af4585b): told to spawn the fact-checker, the r5
// orchestrator read and listed case files for 60 minutes, 197 to 280 calls,
// each returning nothing new (not found, no matching files, unchanged), with
// pairs varied enough that the loop guard never fired. Such results are
// counted per run: at the fifth the next one carries the step the case is
// waiting on, and at the fifteenth the turn ends with the loop stop line.
export const UNPRODUCTIVE_NEXT = 5;
export const UNPRODUCTIVE_STOP = 15;
const unproductive = (text: string) => text.startsWith('not found: ') || text === 'no matching files' || text.endsWith(' is unchanged since you read it above in this conversation; use that text.');

function registerUnproductiveBound(pi: ExtensionAPI, next: () => Promise<string | undefined>): void {
	let count = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		count = 0;
		stopping = false;
	});
	pi.on('tool_result', async (event) => {
		const text = event.content.map((part) => (part.type === 'text' ? part.text : '')).join('');
		if (!unproductive(text)) return undefined;
		count += 1;
		if (count >= UNPRODUCTIVE_STOP && !stopping) {
			stopping = true;
			log(`the orchestrator got ${UNPRODUCTIVE_STOP} results with nothing new; its turn was stopped`);
		}
		if (count !== UNPRODUCTIVE_NEXT) return undefined;
		const step = await next();
		return step ? { content: [{ type: 'text' as const, text: `${text}\n${step}` }] } : undefined;
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stopping) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!stopping || (event.message as { role?: string }).role !== 'assistant') return undefined;
		stopping = false;
		count = 0;
		return shownStop(event, ORCHESTRATOR_LOOP_STOP);
	});
}

function registerOrchestratorLoopBound(pi: ExtensionAPI): void {
	let notices = 0;
	let stopping = false;
	pi.on('agent_start', () => {
		notices = 0;
		stopping = false;
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string; text?: string }> };
		if (message.role === 'toolResult') {
			if (message.content?.some((part) => part.type === 'text' && part.text?.includes(REPEAT_REASON)) && ++notices >= LOOP_NOTICE_CAP && !stopping) {
				stopping = true;
				log(`the orchestrator got ${LOOP_NOTICE_CAP} loop notices; its turn was stopped`);
			}
			return undefined;
		}
		if (!stopping || message.role !== 'assistant') return undefined;
		stopping = false;
		notices = 0;
		return shownStop(event, ORCHESTRATOR_LOOP_STOP);
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stopping) ctx.abort();
		return undefined;
	});
}

// U3 census, r5 on uk-cpi (84674fd9): the case reached `blocked` (the cycle
// cap spent) and the orchestrator went on to about 300 calls. At `blocked`
// phase-investigation says STOP: present the gaps and wait for a revised
// methodology. Once a resolve or transition returns it, a text answer ends
// the turn as usual; a message with more tool calls is replaced by this line,
// with no calls, so the turn ends. The member's next message starts afresh.
export const blockedStop = (open: number) =>
	`Spotlight stopped the investigation because it has tried every step it is allowed and ${open > 1 ? 'some questions are' : 'one question is'} still open. To continue, change the methodology and approve it again.`;

function registerBlockedStop(pi: ExtensionAPI): void {
	let blocked = false;
	let open = 1;
	pi.on('agent_start', () => {
		blocked = false;
	});
	pi.on('tool_execution_end', (event) => {
		if ((event.toolName !== 'spotlight_resolve' && event.toolName !== 'spotlight_transition') || event.isError) return;
		try {
			const status = JSON.parse(event.result?.content?.[0]?.text ?? '') as { status?: unknown; blocked?: { gaps_report?: unknown } };
			if (status.status !== 'blocked') return;
			blocked = true;
			const gaps = status.blocked?.gaps_report;
			open = Array.isArray(gaps) && gaps.length > 1 ? gaps.length : 1;
		} catch {
			// Not a resolved state.
		}
	});
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; content?: Array<{ type: string }> };
		if (!blocked || message.role !== 'assistant' || !message.content?.some((part) => part.type === 'toolCall')) return undefined;
		log('the case is blocked and the orchestrator went on; its turn was ended');
		return shownStop(event, blockedStop(open));
	});
}

// DeepSeek V4.1 Flash on wikimedia stage 0 (704dcc78): the fact-checker
// chased a real address conflict for 501 calls through three research rounds
// and never wrote data/fact-check.json; its calls kept producing something,
// so no other bound tripped. From FACT_CHECK_DUE calls without a write of the
// file, the first result past the count carries this notice, and again every
// FACT_CHECK_DUE_REPEAT calls (a parallel batch can step over the exact count,
// and a fold drops the notice); at FACT_CHECK_STOP the run stops
// as its answer, and the orchestrator re-spawns for the missing file (#245).
export const FACT_CHECK_DUE = 150;
export const FACT_CHECK_STOP = 250;
export const FACT_CHECK_DUE_REPEAT = 50;
export const FACT_CHECK_DUE_NOTICE = 'Write data/fact-check.json now with what you have. A claim whose sources conflict gets the verdict disputed or unverified, with the conflict named in its grounding_assessment.assessment.';

function registerFactCheckDue(pi: ExtensionAPI): (tool: ToolDefinition) => ToolDefinition {
	let calls = 0;
	let notice = FACT_CHECK_DUE;
	let stopping = false;
	const started = new Map<string, unknown>();
	pi.on('agent_start', () => {
		calls = 0;
		notice = FACT_CHECK_DUE;
		stopping = false;
	});
	pi.on('tool_execution_start', (event) => {
		started.set(event.toolCallId, canonicalWriteCall(event.toolName, event.args)?.path);
	});
	pi.on('tool_execution_end', (event) => {
		const path = started.get(event.toolCallId);
		started.delete(event.toolCallId);
		calls += 1;
		if (!event.isError && (event.toolName === 'write_file' || event.toolName === 'edit_file' || event.toolName === 'record_assessment' || event.toolName === 'judge_finding') && typeof path === 'string' && path.replace(/^\{CASE_DIR\}[\\/]/, '') === 'data/fact-check.json') {
			calls = 0;
			notice = FACT_CHECK_DUE;
		}
	});
	pi.on('tool_result', (event) => {
		if (calls + 1 < notice) return undefined;
		notice = calls + 1 + FACT_CHECK_DUE_REPEAT;
		return { content: [...event.content, { type: 'text' as const, text: `\n${FACT_CHECK_DUE_NOTICE}` }] };
	});
	pi.on('turn_end', (_event, ctx) => {
		if (stopping) ctx.abort();
	});
	return (tool) => ({
		...tool,
		prepareArguments: (args: unknown) => {
			if (calls >= FACT_CHECK_STOP) {
				stopping = true;
				throw new Error(`The fact-checker ${FACT_CHECK_DUE_STOP}`);
			}
			return tool.prepareArguments ? tool.prepareArguments(args) : args;
		},
	});
}

function registerReviewRefusalCap(pi: ExtensionAPI): void {
	let refusals = 0;
	let stop: string | undefined;
	pi.on('tool_execution_end', (event) => {
		if (event.toolName !== 'open_review') return;
		const text: string = event.result?.content?.[0]?.text ?? '';
		// A page that could not start comes back as a plain result, not an error
		// (tools/review.ts), so it is counted here too (qwen real launch: 140+ calls).
		const couldNotStart = text.startsWith(PAGE_COULD_NOT_START);
		if (!event.isError && !couldNotStart) refusals = 0;
		else if ((couldNotStart || text.startsWith(PAGE_NOT_OPENED)) && ++refusals >= REVIEW_REFUSAL_CAP) {
			refusals = 0;
			stop = couldNotStart ? REVIEW_START_STOP : reviewRefusalStop(text);
			log(`open_review was refused ${REVIEW_REFUSAL_CAP} times; the turn was stopped`);
		}
	});
	pi.on('before_provider_request', (_event, ctx) => {
		if (stop) ctx.abort();
		return undefined;
	});
	pi.on('message_end', (event) => {
		if (!stop || (event.message as { role?: string }).role !== 'assistant') return undefined;
		const text = stop;
		stop = undefined;
		return shownStop(event, text);
	});
}

// Phase B (Gemma12, 2026-11-02): record_finding was refused five times for
// grounding.support_type "direct_support" with only "must be equal to one of
// the allowed values". Pi validates arguments before a tool runs; each failing
// path that is an enum in the tool's own schema gets its allowed values.
export function allowedAt(schema: unknown, path: string): unknown[] | undefined {
	let nodes: unknown[] = [schema];
	for (const part of path === 'root' ? [] : path.split('.')) {
		nodes = nodes.flatMap(function expand(node: unknown): unknown[] {
			const at = node as { properties?: Record<string, unknown>; items?: unknown; anyOf?: unknown[]; allOf?: unknown[]; oneOf?: unknown[] } | undefined;
			if (!at || typeof at !== 'object') return [];
			const branches = [...(at.anyOf ?? []), ...(at.allOf ?? []), ...(at.oneOf ?? [])].flatMap(expand);
			const own = /^\d+$/.test(part) ? (at.items === undefined ? [] : [at.items]) : at.properties?.[part] === undefined ? [] : [at.properties[part]];
			return [...own, ...branches];
		});
	}
	const values = nodes.flatMap(function enums(node: unknown): unknown[] {
		const at = node as { enum?: unknown[]; const?: unknown; anyOf?: unknown[]; oneOf?: unknown[] } | undefined;
		if (!at || typeof at !== 'object') return [];
		return [...(at.enum ?? []), ...('const' in at ? [at.const] : []), ...[...(at.anyOf ?? []), ...(at.oneOf ?? [])].flatMap(enums)];
	});
	return values.length ? [...new Set(values)] : undefined;
}

function registerSchemaHints(pi: ExtensionAPI, tools: ToolDefinition[]): void {
	const schemas = new Map(tools.map((tool) => [tool.name, tool.parameters]));
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; isError?: boolean; content?: Array<{ type: string; text?: string }> };
		const text = message.content?.[0]?.text ?? '';
		const name = /^Validation failed for tool "([^"]+)"/.exec(text)?.[1];
		if (message.role !== 'toolResult' || !message.isError || !name || !schemas.has(name)) return undefined;
		const hinted = text.replace(/^(  - ([^:\n]+): must be equal to (?:one of the allowed values|constant))$/gm, (line, _all, path: string) => {
			const allowed = allowedAt(schemas.get(name), path);
			return allowed ? `${line} (allowed: ${allowed.map((value) => JSON.stringify(value)).join(', ')})` : line;
		});
		return hinted === text ? undefined : { message: { ...event.message, content: [{ type: 'text', text: hinted }] } as typeof event.message };
	});
}

// Pi answers a call to a tool the role does not have with "Tool X not found".
// When another Spotlight role owns X, say which, so the model takes the next
// step instead of retrying (gold run 1: the orchestrator called
// source_expressions twice).
function registerToolOwners(pi: ExtensionAPI, role: string): void {
	const composition = loadComposition();
	pi.on('message_end', (event) => {
		const message = event.message as { role?: string; isError?: boolean; content?: Array<{ type: string; text?: string }> };
		if (message.role !== 'toolResult' || !message.isError) return undefined;
		const name = /^Tool (\S+) not found$/.exec(message.content?.[0]?.text ?? '')?.[1];
		const owners = Object.entries(composition)
			.filter(([, spec]) => spec.tools.includes(name ?? ''))
			.map(([owner]) => owner);
		if (!name || owners.length === 0) return undefined;
		if (role === 'fact-checker' && name === 'record_assessment' && assessmentHeld.held) return { message: { ...event.message, content: [{ type: 'text', text: ASSESSMENT_HELD }] } as typeof event.message };
		// The batch fact-checker's own tool is missing only because the batch closed it.
		if (role === 'fact-checker' && batchTools.narrowed && owners.includes(role)) return { message: { ...event.message, content: [{ type: 'text', text: fcBatchClosed(name) }] } as typeof event.message };
		const next = role === ORCHESTRATOR ? 'Delegate that step with spawn_agent.' : 'Leave that step to them and say so in your answer.';
		return { message: { ...event.message, content: [{ type: 'text', text: `${name} is not one of your tools; it belongs to the ${owners.join(' and the ')}. ${next}` }] } as typeof event.message };
	});
}
