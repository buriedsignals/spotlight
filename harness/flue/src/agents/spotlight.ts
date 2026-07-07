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
const investigator = defineAgentProfile({
	name: 'investigator',
	description: 'Plans and executes OSINT research cycles. Delegate all research here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('investigator')}`,
});

const factChecker = defineAgentProfile({
	name: 'fact-checker',
	description: 'Independent SIFT verification of findings, in its own context. Delegate verification here.',
	instructions: `${FLUE_VERB_ADAPTER}\n\n${roleBody('fact-checker')}`,
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
