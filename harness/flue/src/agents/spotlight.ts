import { defineAgent, defineAgentProfile } from '@flue/runtime';
import { local } from '@flue/runtime/node';
import { roleBody, FLUE_VERB_ADAPTER, HARNESS_ROOT } from '../lib/roles.ts';

// The local/cloud tier model. llama-server serves the Gemma GGUF as `local/…`;
// swap to the 31B or Fireworks GLM-5.2 via env (U10/U13).
const MODEL = process.env.SPOTLIGHT_FLUE_MODEL ?? 'local/gemma-4-12b-it';

// Gate compaction (LOCAL tier only — API/frontier models keep Flue's defaults).
// A small local model generates fast at low depth and degrades past its tier's
// threshold, but the investigation's real state lives in the phase ARTIFACTS on disk
// (methodology.json, findings…), not the transcript — so the verbose inter-gate turns
// are disposable. Flue's native threshold compaction implements this: the context
// folds into a structured checkpoint summary at ~COMPACT_AT tokens (roughly once per
// phase/gate), summarized by the CHEAP resident e4b (the same llama.cpp the
// `fetch --rlm` seam uses) instead of blocking the slow session model for minutes.
// Tier profiles: bigger models hold discipline deeper, so they keep a longer
// conversation before folding ("tune this for the 12b — the 26b gets it free", with a
// longer leash). SPOTLIGHT_COMPACT_AT / SPOTLIGHT_COMPACT_KEEP override the tier.
// reserveTokens is derived (trigger = contextWindow − reserveTokens), so the trigger
// stays at COMPACT_AT whatever ctx-size the launcher serves.
const TIER = process.env.SPOTLIGHT_MODEL_TIER ?? '12b';
const TIER_COMPACT: Record<string, { at: number; keep: number }> = {
	'12b': { at: 16384, keep: 4000 },
	'26b': { at: 24576, keep: 6000 },
	'31b': { at: 28672, keep: 8000 },
};
const tierCompact = TIER_COMPACT[TIER] ?? TIER_COMPACT['12b'];
const LOCAL_CTX = Number(process.env.SPOTLIGHT_LOCAL_CTX ?? 32768);
const COMPACT_AT = Number(process.env.SPOTLIGHT_COMPACT_AT ?? tierCompact.at);
const COMPACTION = MODEL.startsWith('local/')
	? {
			reserveTokens: Math.max(8192, LOCAL_CTX - COMPACT_AT),
			keepRecentTokens: Number(process.env.SPOTLIGHT_COMPACT_KEEP ?? tierCompact.keep),
			// Summarizer = SESSION MODEL (the runtime default), deliberately. Setting the
			// cheap e4b here (`model: rlm/…`) kills the submission the moment threshold
			// compaction fires: the summarizer call dies inside the runtime's internal
			// hop with a swallowed "Connection error" — it never reaches any server —
			// while the SAME rlm provider works fine as a session model (@flue/runtime
			// 1.0.0-beta.9, reproducer: data/gold-gold-inv-ef-0.db + one turn, fails <10s;
			// grounded 2026-07-10). Session-model summaries are slower at gate boundaries
			// but correct. Re-add the rlm override only after the upstream fix.
		}
	: undefined;

// Investigator + fact-checker run as delegated subagents in their OWN child sessions
// (fact-check independence, U8). Skill subsets are the U2 composition (harness/composition.json);
// the skills themselves are discovered from <cwd>/.agents/skills.
// Skills are workspace-discovered from <cwd>/.agents/skills (the Engine/OpenKnowledge
// projection store), using flat skill names rather than a product namespace:
// all are available by name, bodies loaded on-invoke (D1). This natively delivers the
// per-agent "lightening" (D2) — no agent holds skill bodies up front. `harness/composition.json`
// (U2) remains the documented intent; each role's instructions steer which it actually uses.
// Subagents must NEVER delegate. The shared adapter maps spawn-agent→task, and Flue exposes
// the `task` tool to them, so an undisciplined 12B subagent will re-delegate → task:task:task
// recursion → DelegationDepthExceededError → the whole run fails. Only the orchestrator delegates.
const WORKER_GUARD = `

## CRITICAL — you are a WORKER subagent, not an orchestrator
Do the task YOURSELF with your own tools (bash, search, fetch, browse, read, write, the RLM). You have NO subagents. **NEVER call the \`task\` tool, never "spawn-agent", never "delegate"** — you are the one who was delegated to. Delegating from here recurses (task→task→task) and hard-fails the run. The "spawn-agent → task" mapping in the adapter above applies ONLY to the orchestrator, not to you. If the task is large, do it step by step yourself.

**You have no user and no gates.** Never wait for user input or approval — you were delegated to. Do your task end-to-end and return your result to the orchestrator; the orchestrator owns the human-approval gates, not you.`;

const investigator = defineAgentProfile({
	name: 'investigator',
	description: 'Plans and executes OSINT research cycles. Delegate all research here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('investigator')}${WORKER_GUARD}`,
	// The investigator's research cycles grow context fastest of all three agents.
	compaction: COMPACTION,
});

const factChecker = defineAgentProfile({
	name: 'fact-checker',
	description: 'Independent SIFT verification of findings, in its own context. Delegate verification here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('fact-checker')}${WORKER_GUARD}`,
	compaction: COMPACTION,
});

// The orchestrator is the discovered agent (`flue run spotlight`). It NEVER investigates
// directly — it delegates to the subagents and manages the phase pipeline (the `spotlight` skill).
export default defineAgent(() => ({
	model: MODEL,
	// Real host filesystem + shell at the case dir (subagents inherit this sandbox, U8):
	// the agents run bash / the scraping seam / qmd and persist evidence to real files.
	// cwd is also where Flue discovers .agents/skills (the engine-placed store).
	// local() ALLOWLISTS env — the harness process env does NOT reach the sandboxed bash
	// unless passed here. Without SPOTLIGHT_RLM_OPENAI_BASE_URL the `fetch --rlm` seam
	// silently falls back to raw pages (grounded 2026-07-09: --rlm ran, e4b never saw a
	// request) — the #1 local-tier context killer. undefined values are dropped.
	sandbox: local({
		env: {
			SPOTLIGHT_RLM_OPENAI_BASE_URL: process.env.SPOTLIGHT_RLM_OPENAI_BASE_URL,
			SPOTLIGHT_RLM_OPENAI_MODEL: process.env.SPOTLIGHT_RLM_OPENAI_MODEL,
			FIRECRAWL_API_KEY: process.env.FIRECRAWL_API_KEY, // bot-block escalation (opt-in)
			SPOTLIGHT_ANONYMIZE_FETCH: process.env.SPOTLIGHT_ANONYMIZE_FETCH, // Tor opt-in (U7)
		},
	}),
	cwd: process.env.SPOTLIGHT_CWD ?? process.cwd(),
	instructions: `${FLUE_VERB_ADAPTER}

You are the Spotlight orchestrator. You NEVER investigate directly. You delegate all research to the \`investigator\` subagent and all verification to the \`fact-checker\` subagent (via the \`task\` tool), then evaluate results, manage gates, and synthesise for the user. Follow the \`spotlight\` skill for the phase pipeline (brief → methodology → research cycles → fact-check → report).

**Gates are real and require the user — no exceptions, on every path including this local harness.** At each gate the skill defines (brief direction, methodology, and the fact-check→report handoff), present your synthesis and decisions, then **END YOUR TURN**. Do not proceed, delegate, or self-approve. The user replies with approval or changes in the very next message; act only on that reply. Never skip a gate, never auto-approve, and never treat the absence of a reply as approval — stopping and waiting IS the correct behavior. (The RLM context-hygiene pass is the one step you run without a gate, per the adapter.)

**Fact-check evidence gate (deterministic and language-neutral).** After the fact-checker returns and BEFORE presenting the fact-check gate, run \`bash\`: \`python3 ${HARNESS_ROOT}/scripts/validate-fact-check.py <CASE_DIR>\`. If it FAILS, re-delegate ONCE to the fact-checker with the failure reasons verbatim ("these positive verdicts lack intact case-local evidence anchors — add the stored file/line/JSON pointer, mark them unverified with the reason, or re-acquire the source"); re-run the validator on its revision. Present the gate only after it passes, or with the remaining failures explicitly disclosed as unverified. The gate checks paths, structured pointers, exact quotes, and hashes without judging prose or assuming a language. A claim with no intact evidence anchor is never "verified".

**Report gate (hybrid editorial + deterministic build).** After Gate 1 approval, invoke \`report-drafting\` and author \`<CASE_DIR>/data/report-draft.json\`: you choose localized title, deck, finding order, headlines, concise summaries, why each finding matters, caveats, and next steps; every prose block cites finding IDs. Then run \`bash\`: \`python3 ${HARNESS_ROOT}/scripts/finalize-report.py <CASE_DIR>\`. It validates reference coverage, attaches canonical verdict/confidence, safely renders \`findings-report.md\`, \`report.html\`, and \`evidence-map.json\`, and validates the outputs. Semantic accuracy remains subject to the independent fact-check and human editorial gate. NEVER hand-edit generated HTML or Markdown. If it FAILS, fix only the named structured input and re-run. Never describe an artifact as "ready" unless the finalizer prints \`report finalizer: PASSED\`.`,
	compaction: COMPACTION,
	subagents: [investigator, factChecker],
}));
