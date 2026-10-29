// Compaction summaries. Pi sends a compaction's summary request outside the
// agent loop, so `before_provider_request` never sees it; every summary
// request here goes through the fixed request-body policy itself.
//
// Local tier: a small local model degrades as its context grows, but a case's
// state lives in its files on disk and in the resolver, not in the
// transcript. Past the tier's fold point (set by the launcher in Pi's
// compaction settings) Pi compacts, and this handler replaces Pi's generic
// summary with a Spotlight checkpoint written by the session model on the
// same route, or by the RLM when the harness has one (the e4b that distills
// every fetch, on an endpoint the operator runs). Frontier tier: Pi's own threshold and summary, only with the
// policy applied. On every tier a worker's fold ends with its task.
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
import { compact, convertToLlm, serializeConversation } from '@earendil-works/pi-coding-agent';
import { applyFixedRequestBody } from './provider-policy.ts';
import { rlmComplete, rlmEndpoint, rlmModel } from './tools/fetch.ts';

type FixedBody = Parameters<typeof applyFixedRequestBody>[1];

const CHECKPOINT_PROMPT = `Write a checkpoint of the Spotlight conversation above. It replaces those turns: the agent continues from this checkpoint plus the few most recent messages. The case files and the Spotlight resolver hold the case's state; the checkpoint records where the conversation stands so the agent knows what to re-read and do next.

Use exactly these sections, in this order, and nothing else:

## Goal
What the member asked for and the case's question, in one or two lines.

## Progress
What was done: phases completed, workers delegated and what they returned, files written (exact paths relative to the case folder).

## Decisions
Decisions taken and by whom (the member, a review page, a harness check), with exact values where they matter.

## Next
The next concrete step.

## Open gate
The gate waiting for the member's decision on the review page, or "None".

Keep exact file paths, tool names, finding ids and error messages. Do not invent anything that is not in the conversation.`;

const STATE_NOTE = '\n\n(Checkpoint of earlier turns. The case files and `spotlight_resolve` are the source of truth: re-read them before acting on this.)';

// A checkpoint is kept only in its own sections. base213 (5ce09322): the e4b
// RLM wrote its reasoning ("Here's a thinking process to construct the
// checkpoint: 1. Analyze the Request...") with the sections only as bullets
// inside it, and the investigator that read it next produced 10,240 tokens of
// nothing, twice. Text before the first "## Goal" line is cut; without Goal,
// Progress and Next lines the checkpoint is refused.
export function checkpointSections(text: string): { kept: string; cut: boolean } | undefined {
	const start = text.search(/^## Goal\b/m);
	if (start < 0) return undefined;
	const kept = text.slice(start).trim();
	if (!/^## Progress\b/m.test(kept) || !/^## Next\b/m.test(kept)) return undefined;
	return { kept, cut: text.slice(0, start).trim() !== '' };
}

function log(message: string): void {
	process.stderr.write(`spotlight-extension: ${message}\n`);
}

// The request-body policy as an onPayload callback. A payload the policy
// cannot merge into is replaced by the fixed fields alone (no transcript) and
// `blocked()` turns true, so the caller discards the result.
function policy(fixedBody: FixedBody) {
	let blocked = false;
	return {
		onPayload: (payload: unknown) => {
			const outcome = applyFixedRequestBody(payload, fixedBody);
			blocked ||= outcome.blocked;
			return outcome.payload;
		},
		blocked: () => blocked,
	};
}

// tools-da on b0d862e2 (DeepSeek V4.1 Flash pilot, 0 of 6 accepted): a fold
// replaces a worker's first message, its task, and the checkpoint keeps none of
// the task's fields (MODE, SOURCE_EXPRESSION_MODE, the direction, the
// deliverable line). The investigator then re-read the methodology 25 times and
// the case contract 22 times to rebuild them. A worker has one task per
// process, so every fold, on every tier, ends with that task word for word.
export const TASK_AFTER_FOLD = 'Your task, word for word as it was given (the checkpoint above replaces the message that carried it):';

export function registerCompaction(pi: ExtensionAPI, fixedBody: FixedBody, local: boolean, env: NodeJS.ProcessEnv = process.env, worker = false): void {
	let task: string | undefined;
	if (worker) {
		pi.on('before_agent_start', (event) => {
			if (task === undefined && event.messageType === 'user') task = event.prompt;
			return undefined;
		});
	}
	const withTask = (summary: string) => (task ? `${summary}\n\n${TASK_AFTER_FOLD}\n\n${task}` : summary);
	if (local) registerCheckpoint(pi, fixedBody, env, withTask);
	else if (fixedBody || worker) registerPolicyOnDefaultSummary(pi, fixedBody, withTask);
}

function registerPolicyOnDefaultSummary(pi: ExtensionAPI, fixedBody: FixedBody, withTask: (summary: string) => string): void {
	pi.on('session_before_compact', async (event, ctx: ExtensionContext) => {
		if (!ctx.model) return { cancel: true };
		const guard = policy(fixedBody);
		try {
			const result = await compact(
				event.preparation,
				ctx.model,
				undefined,
				undefined,
				event.customInstructions,
				event.signal,
				undefined,
				(model, context, options) => ctx.modelRegistry.streamSimple(model, context, { ...options, onPayload: guard.onPayload }),
			);
			if (guard.blocked()) throw new Error('the provider privacy policy could not apply to the summary request');
			return { compaction: { ...result, summary: withTask(result.summary) } };
		} catch (error) {
			log(`summary failed, compaction skipped: ${(error as Error).message}`);
			return { cancel: true };
		}
	});
}

function registerCheckpoint(pi: ExtensionAPI, fixedBody: FixedBody, env: NodeJS.ProcessEnv, withTask: (summary: string) => string): void {
	pi.on('session_before_compact', async (event, ctx) => {
		const { preparation, signal } = event;
		const messages = [...preparation.messagesToSummarize, ...preparation.turnPrefixMessages];
		// r5 norway on cc8710fd: with the task inside the previous checkpoint, the
		// session model answered the checkpoint request with read_file calls and the
		// fold was skipped (15 times against 4 on main). The task is attached
		// again after the new checkpoint, so the request never carries it.
		const earlier = preparation.previousSummary?.split(`\n\n${TASK_AFTER_FOLD}\n\n`)[0];
		const previous = earlier ? `<previous-checkpoint>\n${earlier}\n</previous-checkpoint>\n\n` : '';
		const text = `<conversation>\n${serializeConversation(convertToLlm(messages))}\n</conversation>\n\n${previous}${CHECKPOINT_PROMPT}`;
		try {
			// Sensitive mode keeps case text away from the RLM, as it does for run_rlm.
			const rlm = env.SPOTLIGHT_SENSITIVE === 'true' ? undefined : rlmEndpoint(env);
			// The RLM writes it when there is one; a checkpoint out of shape falls
			// back once to the session model, with the same prompt and check.
			// Each outcome is logged, so the arms show how often each path runs.
			let written: { kept: string; cut: boolean } | undefined;
			let usage: Awaited<ReturnType<typeof sessionCheckpoint>>['usage'] | undefined;
			if (rlm) {
				written = checkpointSections((await rlmComplete(rlm, { model: rlmModel(env), messages: [{ role: 'user', content: text }], temperature: 0 }, signal)) ?? '');
				if (written) log(`checkpoint: ${written.cut ? 'rlm-cut-preamble' : 'rlm-ok'}`);
			}
			if (!written) {
				const session = await sessionCheckpoint(ctx, fixedBody, text, signal);
				written = checkpointSections(session.summary);
				usage = session.usage;
				const source = rlm ? 'rlm-failed, session' : 'session';
				log(`checkpoint: ${written ? `${source}-${written.cut ? 'cut-preamble' : 'ok'}` : rlm ? 'both-failed' : 'session-failed'}`);
			}
			if (!written) throw new Error('the checkpoint has no Goal, Progress and Next sections');
			const summary = written.kept;
			return {
				compaction: {
					summary: withTask(`${summary}${STATE_NOTE}`),
					firstKeptEntryId: preparation.firstKeptEntryId,
					tokensBefore: preparation.tokensBefore,
					usage,
					details: { kind: 'spotlight-checkpoint' },
				},
			};
		} catch (error) {
			// The conversation stays as it is; Pi tries again at its next check.
			log(`checkpoint failed, compaction skipped: ${(error as Error).message}`);
			return { cancel: true };
		}
	});
}

// The checkpoint from the session model, under the fixed request body. With
// no session model it fails rather than fall back to Pi's own summary
// request, which is sent without the before_provider_request policy.
async function sessionCheckpoint(ctx: ExtensionContext, fixedBody: FixedBody, text: string, signal?: AbortSignal) {
	if (!ctx.model) throw new Error('no session model for the checkpoint');
	const guard = policy(fixedBody);
	const response = await ctx.modelRegistry.complete(
		ctx.model,
		{ messages: [{ role: 'user', content: [{ type: 'text', text }], timestamp: Date.now() }] },
		{
			signal,
			cacheRetention: 'none',
			onPayload: guard.onPayload,
		},
	);
	if (guard.blocked()) throw new Error('the provider privacy policy could not apply to the checkpoint request');
	if (response.stopReason === 'error' || response.stopReason === 'aborted' || response.stopReason === 'length') {
		throw new Error(`checkpoint request ended with ${response.stopReason}`);
	}
	const summary = response.content
		.filter((block): block is { type: 'text'; text: string } => block.type === 'text')
		.map((block) => block.text)
		.join('\n')
		.trim();
	return { summary, usage: response.usage };
}
