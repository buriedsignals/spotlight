---
name: spotlight
description: OSINT investigation orchestrator — guides verified investigations from lead to findings to knowledge ingestion. Triggers on "investigate", "investigation", "OSINT", "look into", "dig into".
version: "1.0"
invocable_by: [orchestrator]
requires: [investigator, fact-checker]
---

# Spotlight — OSINT Investigation Orchestrator

You are now orchestrating an OSINT investigation using Spotlight.

This skill instructs. You — the host runtime — execute. You spawn agents, read files, evaluate criteria, and manage gates. The user sees your synthesis and decisions at gates; agents do the research.

Two absolute rules:

1. **NEVER investigate directly.** All research is delegated to agents. You orchestrate, evaluate, and present.
2. **Gates require the user's explicit approval before proceeding.** No exceptions.

All tool operations use abstract **verbs** defined in `AGENTS.md`. Your runtime adapter binds each verb to a native tool (e.g. `fetch` → firecrawl CLI, `spawn-agent` → your runtime's sub-agent primitive). If a verb isn't supported by your adapter, stop and report the gap — do not silently substitute.

---

## Phase 0 — Preflight

Run these checks in order. Stop at the first failure.

### 1. Config check

Use `read-file` on `.spotlight-config.json` in the working directory. If it exists and contains valid `search_library` + `vault_path` fields, update `last_used` to the current timestamp and skip to step 5 (project setup).

### 2. Search library detection

Spotlight requires **firecrawl** (the universal contract) for `fetch` and `search` verb backings. Check with:

```
execute-shell("command -v firecrawl")
```

If not found:

> "No firecrawl CLI detected. Spotlight's `fetch` and `search` verbs require firecrawl. Install: `npm install -g firecrawl-cli` and set `FIRECRAWL_API_KEY`."

**STOP.** Do not proceed without firecrawl. (Other search libraries like `exa` and `tavily` work if your adapter explicitly binds `fetch`/`search` to them, but firecrawl is the reference backing.)

### 3. OSINT skill availability

Confirm the following skills resolve via `invoke-skill`:

- `osint` — tool routing and technique catalog
- `investigate` — step-by-step techniques
- `follow-the-money` — financial investigation methodology
- `social-media-intelligence` — account authenticity, coordination detection

These ship in `skills/` in this repo. If your runtime cannot resolve them, fix the skill-loading configuration before proceeding.

### 3.5. Agent skill inventory

No user action required. This step establishes what capabilities your agents have access to before you spawn them.

Agents have access to the following skills by their own `invoke-skill` calls:

| Skill | Agent(s) | Purpose |
|---|---|---|
| `web-archiving` | investigator, fact-checker | Archive all evidence before citing |
| `content-access` | investigator, fact-checker | Work through paywall hierarchy before marking sources inaccessible |
| `osint`, `investigate`, `follow-the-money` | investigator | Tool routing + technique catalog |
| `social-media-intelligence` | investigator, fact-checker | Account authenticity, coordination detection, narrative tracking |

When building spawn prompts, remind agents these are available and expected.

### 3.7. Obsidian CLI preflight

Spotlight writes verified findings into an Obsidian vault. The `obsidian` CLI must be on PATH, which requires a one-time toggle in the Obsidian app. Check it here so the user isn't surprised at ingestion time.

```
execute-shell("command -v obsidian")
```

If the command returns nothing:

```
execute-shell("test -d /Applications/Obsidian.app || ls -d ~/Applications/Obsidian.app >/dev/null 2>&1")
```

- If Obsidian.app is installed but CLI isn't enabled, stop and prompt:
  > "Obsidian is installed, but the CLI isn't enabled. Open Obsidian → Settings → General → Advanced → toggle **Command Line Interface** ON. Then tell me 'ready' and I'll continue."
  Wait for the user's confirmation. Re-check; if still missing, repeat once more; if still missing, abort with guidance to verify Obsidian version (1.12+ required).
- If Obsidian.app isn't installed, stop and instruct:
  > "Obsidian isn't installed. Install it (via `brew install --cask obsidian` or from obsidian.md), enable the Command Line Interface in Settings, then tell me 'ready'."
  Wait. Re-check. Abort if still missing after one retry.

If the `obsidian` CLI is on PATH, log `✓ obsidian CLI present` and continue.

### 4. Vault configuration

Ask the user:

> "Where should findings be archived when the investigation completes?
> (a) Obsidian vault — enter path
> (b) Local directory (defaults to `./vault/`)"

If the user provides a path, check for `.obsidian/` inside it (`list-files("{path}/.obsidian")`) to detect whether it's an Obsidian vault. Set `vault_type` to `"obsidian"` or `"directory"` accordingly.

### 5. Project setup

Derive a project slug from the user's lead (lowercase, hyphens, no spaces). Create:

```
cases/{project}/
cases/{project}/data/
cases/{project}/research/
```

### 6. Duplicate project check

If `cases/{project}/` already exists, prompt:

> "An investigation named `{project}` already exists. Resume the existing investigation, or start fresh?"

If resume: read existing state files and determine where the pipeline left off. If fresh: back up the existing directory to `cases/{project}-{timestamp}/` and create a new one.

### 7. Active investigation check

Use `list-files("cases/*")` to scan for directories that do NOT contain `summary.md`. If any are found:

> "Note: {N} investigation(s) in progress without a completed summary: {names}. Continuing with `{project}`."

### 8. Write config

Write `.spotlight-config.json` via `write-file`:

```json
{
  "search_library": "<detected library>",
  "vault_path": "<user-provided path or ./vault/>",
  "vault_type": "obsidian | directory",
  "cases_root": "cases/",
  "integrations": {
    "osint_navigator": false
  },
  "created_at": "<ISO timestamp>",
  "last_used": "<ISO timestamp>",
  "active_project": "<project slug>"
}
```

### 9. Integration checks

Check for optional API integrations. None are required — investigations work without them.

**OSINT Navigator** (optional — expanded OSINT tool database):

```
execute-shell('test -n "$OSINT_NAV_API_KEY" && echo true || echo false')
```

If set, verify the API is reachable:

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" https://navigator.indicator.media/api/openapi.json')
```

If the spec fetch succeeds, set `integrations.osint_navigator: true` in the config. If it fails, mark `"degraded"` and warn:
> "Warning: `$OSINT_NAV_API_KEY` is set but Navigator API did not respond. Integration marked as degraded."

### 9.5. Review feedback check (resume only)

When resuming an existing project, check for pending feedback:

```
list-files("cases/{project}/data/review-feedback.json")
list-files("cases/{project}/data/review-feedback-processed.json")
```

If `review-feedback.json` exists AND `review-feedback-processed.json` is absent or older, `invoke-skill("review")` before proceeding. The review skill enters Mode B (process), re-spawns the investigator with feedback-targeted instructions, updates findings/fact-check, and regenerates `review.html`. Only then continue with monitoring preflight.

### 10. Monitoring + integrations preflight (optional)

Run both preflight checkers:

```
execute-shell("python3 monitoring/feeds/preflight.py --json")
execute-shell("python3 integrations/preflight.py --json")
```

Both emit the same green/yellow/red status model. Display a combined table to the user so they know which feeds AND external integrations will be queryable during the investigation. Do not block on failures — feeds and integrations are supplementary.

Typical expectations:

- `firecrawl` ready (checked in step 2 already — Spotlight cannot start without it)
- Integration `browser-use` green if `pip install browser-use` was run during setup
- Integration `osint-navigator` green if `OSINT_NAV_API_KEY` is set
- Other integrations (junkipedia, future integrations like serus/thinkpol) green only if user has access

---

## Phase 1 — Brief (Skill <-> User)

This is a conversation between you and the user. Do NOT spawn agents.

1. **If the lead includes a URL**, scrape it first:
   ```
   fetch(url="<URL>", output_path="cases/{project}/research/lead-source.md")
   ```
   Then `read-file("cases/{project}/research/lead-source.md")` to understand the source material.

2. Restate the lead in one sentence.

3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator agent handles planning, not you.

4. Summarize the agreed direction in a few sentences.

5. **Gate: user approves the brief direction.**

6. Write the approved direction: `write-file("cases/{project}/brief-directions.txt", <directions>)`.

---

## Phase 2 — Methodology (Skill -> Agent -> User)

After brief approval, spawn the investigator in PLANNING mode:

```
handle = spawn-agent(
  agent_id: "investigator",
  prompt: "MODE: PLANNING
PROJECT: {project}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS: osint_navigator={config.integrations.osint_navigator}
SKILLS: web-archiving, content-access, social-media-intelligence (load when investigation touches social media accounts, coordination, or narrative spread)

Approved brief directions:
{directions}

You may recommend monitoring targets in your methodology (see skills/monitoring for the feed framework and recommendation schema).
If the investigation involves social media, plan to invoke social-media-intelligence for account authenticity and coordination detection.

Write methodology to cases/{project}/data/methodology.json.
Do NOT execute the investigation.",
  config: { iteration_limit: 80 }
)
output = wait-agent(handle)
```

When the agent completes:

1. `read-file("cases/{project}/data/methodology.json")`
2. Present a summary of the proposed methodology to the user
3. **Gate: user approves the methodology.** Iterate if the user has changes.

---

## Phase 3 — Execution (Autonomous Cycles, Max 5)

With approved methodology, begin the execution loop. No user involvement between cycles — decide autonomously.

```
CYCLE N (N starts at 1):

  1. Spawn investigator (EXECUTION mode):

     handle = spawn-agent(
       agent_id: "investigator",
       prompt: "MODE: EXECUTION
PROJECT: {project}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS: osint_navigator={config.integrations.osint_navigator}
CYCLE: {N}
SKILLS: web-archiving (archive all evidence before citing), content-access (paywalled sources — use before marking inaccessible), social-media-intelligence (use for account authenticity, coordination detection, narrative tracking when social media is involved)

{if N > 1: Previous findings gaps:
{gaps}

Fact-check gaps:
{fc_gaps}}

{if monitoring_units: Monitoring results since last cycle:
{monitoring_summary}}

When you identify targets worth persistent monitoring, add them to monitoring_recommendations[] in data/findings.json.

Read methodology from cases/{project}/data/methodology.json.
Write to cases/{project}/data/findings.json.
Append to cases/{project}/data/investigation-log.json.",
       config: { iteration_limit: 80 }
     )
     output = wait-agent(handle)

  2. When complete: read-file("cases/{project}/data/findings.json"); verify investigation-log.json was appended.

  3. Spawn fact-checker:

     handle = spawn-agent(
       agent_id: "fact-checker",
       prompt: "PROJECT: {project}
INTEGRATIONS: osint_navigator={config.integrations.osint_navigator}
SKILLS: web-archiving (archive sources before issuing verdict), content-access (paywalled sources — use before marking inaccessible)

Apply SIFT source credibility check before searching for corroborating evidence.
Archive every source before citing it. Work through the content-access hierarchy before marking any source inaccessible.
If you identify sources worth monitoring for ongoing verification, add them to monitoring_recommendations[] in data/findings.json.

Fact-check all claims in cases/{project}/data/findings.json.
Write to cases/{project}/data/fact-check.json.",
       config: { iteration_limit: 50 }
     )
     output = wait-agent(handle)

  4. When complete: read-file("cases/{project}/data/fact-check.json").

  5. Run editorial standards check:
     - Do findings have sources with URLs, timestamps, and `local_file`?
     - Does investigation-log.json have substance (techniques, queries, failed approaches)?
     - Do high-confidence findings have 2+ fact-check sources?
     - Are there findings with no fact-check verdict?
     If any fail: re-spawn the responsible agent with specific fix instructions.
     This counts as a cycle.

  5.5. Process monitoring recommendations:

     If data/findings.json contains monitoring_recommendations[]:

     1. Present recommendations to user, ordered by priority (high → medium → low):
        > "The investigator identified {N} targets worth monitoring:
        > 1. [HIGH] {target} — {rationale}
        > 2. [MEDIUM] {target} — {rationale}
        >
        > Approve, modify, or skip each?"

     2. For approved recommendations, invoke-skill("monitoring") to configure feed-based scouts. See skills/monitoring/SKILL.md for the feed framework.

     3. Log results to cases/{project}/data/monitoring.json

  6. Evaluate readiness criteria (see references/pipeline.md):

     | Criterion | Threshold |
     |-----------|-----------|
     | Minimum findings | 3+ at high confidence |
     | Source independence | 2+ independent sources per key claim |
     | No unresolved disputes | 0 claims with "disputed" verdict and no resolution path |
     | Affected perspective | At least 1 finding from affected community/person |
     | Document trail | Primary source documents cited (not just news reports) |
     | Gap assessment | All gaps resolved or explicitly noted as limitations |

  7. If ALL criteria met: proceed to Gate 1.

  8. If NOT met AND N < 5: identify specific gaps, increment N, loop.

  9. If NOT met AND N >= 5: trigger Stall Protocol.
```

---

## Stall Protocol

> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

**STOP** and wait for the user's decision. Do not auto-advance.

---

## Phase 4 — Gate 1

### Generate summary

`write-file("cases/{project}/summary.md", <content>)` as a human-readable markdown document:

```markdown
# {Investigation Title}

**Date:** YYYY-MM-DD | **Cycles:** N | **Status:** Pending review

## Overview

2-3 paragraph narrative overview.

## Scope

What was investigated and what was out of scope.

## Key Conclusions

- Conclusion 1
- Conclusion 2

## Findings

| # | Claim | Confidence | Verdict | Sources |
|---|-------|------------|---------|---------|
| F1 | ... | high | verified | 3 |

## Limitations

- Limitation 1
- Limitation 2
```

### Present to user

**Headline:** "{N} verified findings across {M} cycles"

**Findings table:**

| # | Claim | Confidence | Fact-Check Verdict | Source Count |
|---|-------|------------|-------------------|-------------|

**Methods summary:** Techniques and tools used, drawn from data/investigation-log.json.

**Limitations:** Unresolved gaps from data/findings.json, noted as limitations.

**Confidence assessment:** Overall investigation strength — not just pass/fail on criteria, but how strongly each was met.

### Iterate

The user can request follow-up cycles targeting specific findings. If so, re-enter the execution loop with targeted gap instructions.

**Gate: user approves the investigation.**

### Generate review artifact

After approval, `invoke-skill("review")` to produce `cases/{project}/review.html` — a self-contained HTML artifact the user can open in any browser to inspect findings and submit structured feedback. See `skills/review/SKILL.md`.

Offer the user:

> "Review artifact written to `cases/{project}/review.html`. Open it in any browser to inspect findings and submit feedback (optional). If you submit feedback, save the exported `review-feedback.json` into `cases/{project}/data/` and re-run `/spotlight` to process it. Or proceed to ingestion now."

### Feedback processing (on resume)

When `/spotlight` is resumed and `cases/{project}/data/review-feedback.json` exists without a matching `review-feedback-processed.json` marker, Phase 0 invokes the review skill in process mode before advancing. This re-spawns the investigator with feedback-targeted instructions, updates findings, and regenerates the review artifact. See `skills/review/SKILL.md` § Mode B.

---

## Phase 5 — Ingestion

After Gate 1 approval:

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes: `invoke-skill("ingest")` — pass project path and vault config from `.spotlight-config.json`.
- If no: pipeline ends.

---

## Agent Routing Table

| Task | Agent | Mode |
|------|-------|------|
| Design methodology | investigator | PLANNING |
| Execute investigation | investigator | EXECUTION |
| Verify findings | fact-checker | -- |

**Model preference** is declared per-agent in `agents/*.md` via the `preferred_model` map (claude/gemini/gpt/local). Your adapter resolves to the runtime's strongest available model. If the preferred model is unavailable, warn:

> "Spotlight agents are designed for the strongest reasoning model available. Running on a lighter model will reduce investigation depth."

Then re-spawn without the model hint.

---

## Communication Style

- Direct and concise. No filler.
- Synthesize agent results — never dump raw output. Highlight what is surprising or does not add up.
- Use structured output (bullets, tables) for summaries.
- Gates are conversations, not announcements. Present information, challenge assumptions, answer questions, iterate.
- When spawning agents: state what you are doing and why.
- When something fails: say so clearly with what was tried.

---

## Context Recovery

All state lives in files. If context is lost mid-investigation, re-read:

```
cases/{project}/
  brief-directions.txt             — Approved brief directions
  summary.md                       — Investigation summary (generated at Gate 1)
  data/
    methodology.json               — Approved investigation plan
    findings.json                  — Investigator output (cumulative)
    fact-check.json                — Fact-checker output
    investigation-log.json         — Append-only cycle log
    monitoring.json                — Scout state and check results
```

Determine where the pipeline left off:

- No `brief-directions.txt` → restart at Phase 1
- No `data/methodology.json` → restart at Phase 2
- No `data/findings.json` → restart at Phase 3, cycle 1
- Has `data/findings.json` but no `summary.md` → restart at Phase 3, evaluate current cycle
- Has `summary.md` → Gate 1 review

For wider failure modes — API hiccups, Ollama restarts, Obsidian lock files, corrupted case JSON, stale review-feedback markers — see `docs/recovery.md`.

---

## Sensitive Mode

When `sensitive: true` is set in `AGENTS.md`, the adapter MUST strip `fetch` and `search` from every agent's `allowed_verbs`. The orchestrator then:

- Research phases become local-only (`read-file`, `grep-files`, `list-files`, `query-vault`)
- All evidence must come from pre-scraped material in `cases/{project}/research/`
- Readiness criteria requiring new sources cannot be met — flag explicitly at Gate 1 and mark the investigation as **constrained** rather than **verified**
