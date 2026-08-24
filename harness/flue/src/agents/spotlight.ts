import { defineAgent, defineAgentProfile } from '@flue/runtime';
import { local } from '@flue/runtime/node';
import { roleBody, FLUE_VERB_ADAPTER, HARNESS_ROOT } from '../lib/roles.ts';
import { createSpotlightTools } from '../lib/spotlight-tools.ts';

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
	tools: [],
	// The investigator's research cycles grow context fastest of all three agents.
	compaction: COMPACTION,
});

const factChecker = defineAgentProfile({
	name: 'fact-checker',
	description: 'Independent SIFT verification of findings, in its own context. Delegate verification here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('fact-checker')}${WORKER_GUARD}`,
	tools: [],
	compaction: COMPACTION,
});

// The orchestrator is the discovered agent (`flue run spotlight`). It NEVER investigates
// directly — it delegates to the subagents and manages the phase pipeline (the `spotlight` skill).
export default defineAgent(() => {
	const tools = createSpotlightTools({
		activeCaseDir: process.env.SPOTLIGHT_ACTIVE_CASE ?? '',
		casesRoot: process.env.SPOTLIGHT_CASES_ROOT ?? '',
	});
	return {
		model: MODEL,
		tools,
		// Real host filesystem + shell at the case dir (subagents inherit this sandbox, U8):
		// the agents run bash / the scraping seam / OpenKnowledge and persist evidence to real files.
		// cwd is also where Flue discovers .agents/skills (the engine-placed store).
		// local() ALLOWLISTS env — the harness process env does NOT reach the sandboxed bash
		// unless passed here. Without SPOTLIGHT_RLM_OPENAI_BASE_URL the `fetch --rlm` seam
		// silently falls back to raw pages (grounded 2026-07-09: --rlm ran, e4b never saw a
		// request) — the #1 local-tier context killer. undefined values are dropped.
		sandbox: local({
			env: {
				BSIG_BIN: process.env.BSIG_BIN,
				SPOTLIGHT_RLM_OPENAI_BASE_URL: process.env.SPOTLIGHT_RLM_OPENAI_BASE_URL,
				SPOTLIGHT_RLM_OPENAI_MODEL: process.env.SPOTLIGHT_RLM_OPENAI_MODEL,
				FIRECRAWL_API_KEY: process.env.FIRECRAWL_API_KEY, // bot-block escalation (opt-in)
				SPOTLIGHT_ANONYMIZE_FETCH: process.env.SPOTLIGHT_ANONYMIZE_FETCH, // Tor opt-in (U7)
			},
		}),
		cwd: process.env.SPOTLIGHT_CWD ?? process.cwd(),
		instructions: `${FLUE_VERB_ADAPTER}

You are the Spotlight orchestrator. You NEVER investigate directly. Delegate research to the \`investigator\` subagent and verification to the \`fact-checker\` subagent through the \`task\` tool.

Start and resume by invoking \`phase-preflight\` first and completing its Flue-native checks against the launcher-bound case. Then call \`spotlight_resolve({})\`. Follow the \`spotlight\` skill, dynamically invoke exactly the returned phase owner, apply only its structured \`spotlight_transition({ operation, payload })\`, then resolve again. The durable sequence is Brief → Methodology → Execution → Gate 1 → Report → Ingest; do not infer phase completion from files or invoke orchestration scripts directly.

**Human gates are real — no exceptions, including this local harness.** When the returned phase owner presents a gate, present its synthesis and decisions, then **END YOUR TURN**. Do not proceed, delegate, transition, or self-approve until the user replies with an explicit decision. Stopping and waiting is correct behavior.`,
		compaction: COMPACTION,
		subagents: [investigator, factChecker],
	};
});
