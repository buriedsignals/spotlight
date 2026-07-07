import { defineAgent, defineAgentProfile } from '@flue/runtime';
import { local } from '@flue/runtime/node';
import { roleBody, FLUE_VERB_ADAPTER } from '../lib/roles.ts';

// The local/cloud tier model. llama-server serves the Gemma GGUF as `local/…`;
// swap to the 31B or Fireworks GLM-5.2 via env (U10/U13).
const MODEL = process.env.SPOTLIGHT_FLUE_MODEL ?? 'local/gemma-4-12b-it';

// Investigator + fact-checker run as delegated subagents in their OWN child sessions
// (fact-check independence, U8). Skill subsets are the U2 composition (harness/composition.json);
// the skills themselves are discovered from <cwd>/.agents/skills/spotlight.
// Skills are workspace-discovered from <cwd>/.agents/skills (the engine-placed store):
// all are available by name, bodies loaded on-invoke (D1). This natively delivers the
// per-agent "lightening" (D2) — no agent holds skill bodies up front. `harness/composition.json`
// (U2) remains the documented intent; each role's instructions steer which it actually uses.
// Subagents must NEVER delegate. The shared adapter maps spawn-agent→task, and Flue exposes
// the `task` tool to them, so an undisciplined 12B subagent will re-delegate → task:task:task
// recursion → DelegationDepthExceededError → the whole run fails. Only the orchestrator delegates.
const WORKER_GUARD = `

## CRITICAL — you are a WORKER subagent, not an orchestrator
Do the task YOURSELF with your own tools (bash, search, fetch, browse, read, write, the RLM). You have NO subagents. **NEVER call the \`task\` tool, never "spawn-agent", never "delegate"** — you are the one who was delegated to. Delegating from here recurses (task→task→task) and hard-fails the run. The "spawn-agent → task" mapping in the adapter above applies ONLY to the orchestrator, not to you. If the task is large, do it step by step yourself.`;

const investigator = defineAgentProfile({
	name: 'investigator',
	description: 'Plans and executes OSINT research cycles. Delegate all research here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('investigator')}${WORKER_GUARD}`,
});

const factChecker = defineAgentProfile({
	name: 'fact-checker',
	description: 'Independent SIFT verification of findings, in its own context. Delegate verification here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('fact-checker')}${WORKER_GUARD}`,
});

// The orchestrator is the discovered agent (`flue run spotlight`). It NEVER investigates
// directly — it delegates to the subagents and manages the phase pipeline (the `spotlight` skill).
export default defineAgent(() => ({
	model: MODEL,
	// Real host filesystem + shell at the case dir (subagents inherit this sandbox, U8):
	// the agents run bash / the scraping seam / qmd and persist evidence to real files.
	// cwd is also where Flue discovers .agents/skills (the engine-placed store).
	sandbox: local(),
	cwd: process.env.SPOTLIGHT_CWD ?? process.cwd(),
	instructions: `${FLUE_VERB_ADAPTER}

You are the Spotlight orchestrator. You NEVER investigate directly. You delegate all research to the \`investigator\` subagent and all verification to the \`fact-checker\` subagent (via the \`task\` tool), then evaluate results, manage gates, and synthesise for the user. Follow the \`spotlight\` skill for the phase pipeline (brief → methodology → research cycles → fact-check → report).`,
	subagents: [investigator, factChecker],
}));
