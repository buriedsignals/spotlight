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

All tool operations use abstract **verbs** defined in `AGENTS.md`. Your runtime adapter binds each verb to a native tool (e.g. `fetch` → Crawl4AI via `integrations.scraping`, `search` → SearXNG via `integrations.search`, `spawn-agent` → your runtime's sub-agent primitive). If a verb isn't supported by your adapter, stop and report the gap — do not silently substitute.

---

## Phase 0 — Preflight

Run these checks in order. Stop at the first failure.

### 1. Config check

Use `read-file` on `.spotlight-config.json` in the working directory. If it exists and contains valid `search_library`, `vault_path`, and `case_workspace_root` (or legacy `cases_root`) fields, update `last_used` to the current timestamp and skip to step 5 (project setup).

`case_workspace_root` is the active investigation workspace. `vault_path` is the durable knowledge vault. Do not write active case files into the vault. At case start, query the vault for prior context; at case end, ask before ingesting verified material into the vault.

### 2. Search/scrape backing detection

Spotlight is **sovereign by default**: `fetch` → Crawl4AI (`integrations.scraping`, no API key), `search` → SearXNG (`integrations.search`, self-hosted). Firecrawl is an **optional** escape hatch (scrape fallback on a hard bot-block, or a `--union` search engine) enabled only when `FIRECRAWL_API_KEY` is present. Check the sovereign backings with:

```
execute-shell("command -v crwl || command -v uvx")   # Crawl4AI (or uvx cold-start)
execute-shell("curl -s -o /dev/null -w '%{http_code}' \"${SEARXNG_URL:-http://localhost:8899}/search?q=ping&format=json\"")   # SearXNG
```

If Crawl4AI is missing:

> "No Crawl4AI detected. Spotlight's `fetch` verb uses Crawl4AI. Run `install-spotlight.sh` (provisions `crawl4ai` + `crawl4ai-setup`), or set `FIRECRAWL_API_KEY` to use the Firecrawl fallback."

If SearXNG is unreachable, `search` falls back to Firecrawl when `FIRECRAWL_API_KEY` is set; otherwise report the gap. Proceed once at least one search + one fetch backing is available — a pure-sovereign install (no Firecrawl key) is fully supported.

### 3. OSINT skill availability

Confirm the following skills resolve via `invoke-skill`:

- `osint` — tool routing and technique catalog
- `investigate` — step-by-step techniques
- `follow-the-money` — financial investigation methodology
- `epistemic-grounding` — claim-to-evidence grounding and confidence caps
- `shell-safety` — safe command construction and destructive-operation probes
- `acquisition-graduation` — reusable dev-browser acquisition paths
- `social-media-intelligence` — account authenticity, coordination detection

These ship in `skills/` in this repo. If your runtime cannot resolve them, fix the skill-loading configuration before proceeding.

### 3.5. Agent skill inventory

No user action required. This step establishes what capabilities your agents have access to before you spawn them.

Agents have access to the following skills by their own `invoke-skill` calls:

| Skill | Agent(s) | Purpose |
|---|---|---|
| `acquisition-graduation` | investigator, fact-checker | Graduate repeated dev-browser acquisition paths into durable source/domain guidance |
| `web-archiving` | investigator, fact-checker | Archive all evidence before citing |
| `content-access` | investigator, fact-checker | Work through paywall hierarchy before marking sources inaccessible |
| `epistemic-grounding` | investigator, fact-checker | Test whether exact evidence actually supports exact claims; cap confidence when grounding is weak |
| `shell-safety` | investigator, fact-checker | Validate untrusted values before execute-shell; require probes for destructive operations |
| `provenance-signing` | orchestrator, user | Build a case provenance manifest and optionally hand it to Noosphere C2PA signing |
| `osint`, `investigate`, `follow-the-money` | investigator | Tool routing + technique catalog |
| `social-media-intelligence` | investigator, fact-checker | Account authenticity, coordination detection, narrative tracking |

When building spawn prompts, remind agents these are available and expected.

### 3.6. Parent/child phase contract

Spotlight keeps a one-level physical skill layout, but phase execution has
mandatory child-skill loading. Use `skills-manifest.json` as the maintenance
contract and apply this runtime table:

| Parent phase | Required child skills | Conditional child skills | Validation |
|---|---|---|---|
| Phase 0 Preflight | `integrations` | `shell-safety` if preflight executes dynamic shell values | `.spotlight-config.json` stores full integration status, not only booleans. |
| Phase 2 Methodology | `integrations`, `osint`, `investigate`, `epistemic-grounding` | `follow-the-money`, `social-media-intelligence`, `technical-investigation`, `content-access` | `methodology.json` includes `skills_invoked[]` and required Navigator fields when green. |
| Phase 3 Execution | `epistemic-grounding`, `shell-safety`, `web-archiving` | `content-access`, `acquisition-graduation`, `social-media-intelligence`, `technical-investigation` | findings contain evidence refs, archives, confidence caps. |
| Phase 3 Fact-check | `epistemic-grounding`, `content-access`, `shell-safety` | `osint`, `social-media-intelligence`, `technical-investigation`, `web-archiving` | fact-check output independently checks investigator claims. |
| Phase 5 Report | `report-drafting`, `epistemic-grounding` | `provenance-signing`, `technical-investigation` | report claims map to evidence ledger. |
| Ingest | `ingest` | none | only verified or explicitly caveated material enters vault. |

### 3.7. Knowledge workspace preflight

Engine-managed Spotlight uses OpenKnowledge as its canonical knowledge store.
Confirm that the selected workspace contains `.openknowledge/config.json` and
that `ok --cwd "{workspace_path}" config validate` succeeds. If either check
fails, stop with the exact repair command from Engine/OpenKnowledge; do not
silently switch to a different directory or legacy vault backend.

`obsidian_legacy` remains an explicit migration-only backend. Run an Obsidian
CLI check only when that exact backend is recorded in `.spotlight-config.json`.

### 4. Vault configuration

Ask the user:

> "Which OpenKnowledge workspace should archive verified findings?"

Use the Engine-selected workspace by default. Keep Markdown/YAML frontmatter
and relative links portable. Tolaria and `obsidian_legacy` may be selected only
through explicit migration configuration; never infer them from directory
contents.

### 5. Project setup

Derive a project slug from the user's lead (lowercase, hyphens, no spaces). Resolve `CASE_ROOT` from `case_workspace_root`; if absent, use legacy `cases_root`; if absent, use `cases/`. Resolve `CASE_DIR = CASE_ROOT/{project}`. Create:

```
{CASE_DIR}/
{CASE_DIR}/data/
{CASE_DIR}/research/
{CASE_DIR}/evidence/
```

### 6. Duplicate project check

If `CASE_DIR` already exists, prompt:

> "An investigation named `{project}` already exists. Resume the existing investigation, or start fresh?"

If resume: read existing state files and determine where the pipeline left off. If fresh: back up the existing directory to `{CASE_ROOT}/{project}-{timestamp}/` and create a new one.

### 7. Active investigation check

Use `list-files("{CASE_ROOT}/*")` to scan for directories that do NOT contain `summary.md`. If any are found:

> "Note: {N} investigation(s) in progress without a completed summary: {names}. Continuing with `{project}`."

### 8. Write config

Write `.spotlight-config.json` via `write-file`:

```json
{
  "search_library": "<detected library>",
  "vault_path": "<user-provided path or ./vault/>",
  "vault_type": "openknowledge | tolaria | obsidian_legacy | directory",
  "case_workspace_root": "cases/",
  "cases_root": "cases/",
  "integrations": {
    "osint_navigator": {
      "status": "unknown",
      "checked_at": "<ISO timestamp>",
      "source": "not checked yet",
      "required_in_phase_2": false,
      "reason": "preflight not run yet"
    },
    "rlm": {
      "enabled": false,
      "mode": "off",
      "model": null,
      "prefilter": false,
      "hybrid": false,
      "evidence_boundary": "lead-only; never verified or publishable"
    }
  },
  "sensitive": false,
  "created_at": "<ISO timestamp>",
  "last_used": "<ISO timestamp>",
  "active_project": "<project slug>"
}
```

### 9. Integration checks

Check for optional API integrations. None are required for Spotlight to start,
but a green OSINT Navigator result becomes mandatory during Phase 2 tool
selection when sensitive mode is false.

Run the manifest-based preflight and parse the `osint-navigator` entry:

```
execute-shell("python3 integrations/preflight.py --model-tier {config.model_tier} --json")
```

Update `.spotlight-config.json` with the full status, not a boolean:

```json
{
  "integrations": {
    "osint_navigator": {
      "status": "green|yellow|red",
      "checked_at": "<ISO timestamp>",
      "source": "integrations/preflight.py --json",
      "required_in_phase_2": true,
      "reason": ""
    }
  }
}
```

Set `required_in_phase_2: true` only when ALL hold:

- `model_tier` is NOT `12b` (the constrained tier dismisses every integration — see below), and
- `sensitive` is false, and
- the `osint-navigator` preflight entry has `status: "green"` (needs a subscription entitlement — `OSINT_NAV_API_KEY`).

Set `required_in_phase_2: false` and preserve a concrete `reason` when
`model_tier` is `12b`, Navigator is red/yellow, sensitive mode is active, the
lead is local-only, or the user explicitly forbids external APIs. On the open
tier, Phase-2 tool discovery uses `scripts/osint-tools.py find` (local SQL index).

**Integration tiering (`model_tier`):**
- `12b` — ALL integrations dismissed; native capabilities only (dev-browser, Crawl4AI seam, `osint-tools` SQL, native verbs). `integrations/preflight.py --model-tier 12b` forces every integration `status: "dismissed"`.
- `26b` / `31b` / `frontier` / `api` — integrations on by default EXCEPT `scoutpost` and `unpaywall` (opt-in at config), Navigator (entitlement-gated), and Noosphere (opt-in key, pending).

Also preserve any existing `integrations.rlm` setup block from
`.spotlight-config.json`. If it is absent, treat RLM as not installed:

```json
{
  "integrations": {
    "rlm": {
      "enabled": false,
      "mode": "off",
      "model": null,
      "prefilter": false,
      "hybrid": false,
      "evidence_boundary": "lead-only; never verified or publishable"
    }
  }
}
```

If `integrations.rlm.enabled` is true, find the `rlm` entry in
`integrations/preflight.py --json` and record its status under the same
`integrations.rlm` object as `preflight_status`, `checked_at`, `source`, and
`reason`. Do not ask the user about RLM during Phase 0.

### 9.5. Review feedback check (resume only)

When resuming an existing project, check for pending feedback:

```
list-files("{CASE_DIR}/data/review-feedback.json")
list-files("{CASE_DIR}/data/review-feedback-processed.json")
```

If `review-feedback.json` exists AND `review-feedback-processed.json` is absent or older, `invoke-skill("review")` before proceeding. The review skill enters Mode B (process), re-spawns the investigator with feedback-targeted instructions, updates findings/fact-check, and regenerates `review.html`. Only then continue with monitoring preflight.

### 10. Monitoring + integrations availability (optional)

Run integration preflight and check whether Mycroft passive monitoring is installed:

```
execute-shell("python3 integrations/preflight.py --model-tier {config.model_tier} --json")
execute-shell('test -f ~/.mycroft/monitoring/monitor.py && echo true || echo false')
```

Display a combined summary to the user so they know which external integrations are green and whether passive Mycroft signals are available. Do not block on failures — supplementary monitoring is optional.

Typical expectations:

- Sovereign search + fetch backing ready (checked in step 2: Crawl4AI + SearXNG; `firecrawl` optional, only if `FIRECRAWL_API_KEY` is set)
- Integration `dev-browser` green if the `dev-browser` CLI is available
- Integration `osint-navigator` green if `OSINT_NAV_API_KEY` is set
- Other integrations (junkipedia, future integrations like serus/thinkpol) green only if user has access

---

## Phase 1 — Brief (Skill <-> User)

This is a conversation between you and the user. Do NOT spawn agents.

1. **If the lead includes a URL**, scrape it first:
   ```
   fetch(url="<URL>", output_path="{CASE_DIR}/research/lead-source.md")
   ```
   Then `read-file("{CASE_DIR}/research/lead-source.md")` to understand the source material.

2. Restate the lead in one sentence.

3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator agent handles planning, not you.

4. Summarize the agreed direction in a few sentences.

5. **Gate: user approves the brief direction.**

6. Write the approved direction: `write-file("{CASE_DIR}/brief-directions.txt", <directions>)`.

---

## Phase 2 — Methodology (Skill -> Agent -> User)

After brief approval, spawn the investigator in PLANNING mode:

```
handle = spawn-agent(
  agent_id: "investigator",
  prompt: "MODE: PLANNING
PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
SKILLS: integrations, osint, investigate, epistemic-grounding, acquisition-graduation, web-archiving, content-access, shell-safety, social-media-intelligence (social investigations), technical-investigation (technical leads)

NAVIGATOR ROUTING (CLI-first — make TWO independent decisions per direction):

If osint_navigator_required=true (subscription / entitled deployments), before writing methodology.json:
1. invoke-skill("integrations")
2. invoke-skill("osint")
3. invoke-skill("navigator")
4. run `navigator tools find` and inspect chosen tools with `navigator tools show`
5. independently run `navigator data find` and inspect a relevant source with `navigator data show`
6. run `navigator query` only for an approved structured source and save machine-readable output to {CASE_DIR}/research/
7. record CLI/API mode, catalog ID/version or retrieval time, non-secret parameters, warnings, source URLs, digest, and a per-direction data-source decision or skip reason
In sensitive/offline mode make no Navigator request; record both modes as policy-skipped and use allowed local fallbacks.

If osint_navigator_required=false (local / open-weights deployments — no Navigator entitlement), discover tools from the LOCAL index — no external call, no mandatory reads:
- execute-shell("python3 scripts/osint-tools.py find \"<lead-derived keywords>\" [--category <cat>] [--limit 8]")
  Derive keywords from the lead = entity type + geography + task. Scope with --category when the direction is clear (`python3 scripts/osint-tools.py categories` lists them). Examples:
    Swiss foundation leadership  ->  find "switzerland swiss zug registry" --category public_records   ;   find "people directory" --category people
    crypto wallet tracing        ->  find "ethereum wallet blockchain" --category cryptocurrency
- list the tools you chose in methodology.json tools_required[]; set navigator:{required:false, used:false, fallback_reason:"local osint-tools index (no Navigator entitlement)"} — or omit the navigator block entirely.

Approved brief directions:
{directions}

You may recommend monitoring targets in your methodology (see skills/monitoring for the recommendation schema and external-monitor lifecycle).
If the investigation involves social media, plan to invoke social-media-intelligence for account authenticity and coordination detection.

Write methodology to {CASE_DIR}/data/methodology.json.
Include skills_invoked[] and the navigator block required by schemas/methodology.schema.json.
Do NOT execute the investigation.",
  config: { iteration_limit: 80 }
)
output = wait-agent(handle)
```

When the agent completes:

1. `read-file("{CASE_DIR}/data/methodology.json")`
2. Run the methodology gate (tier-aware — the validator enforces the Navigator contract ONLY when osint_navigator_required=true; on the open tier it just checks the navigator block is absent or consistent):

   ```
   execute-shell("python3 scripts/validate-methodology-navigator.py {CASE_DIR} --config .spotlight-config.json")
   ```

   If validation fails, do not present the methodology for approval. Re-spawn or
   re-prompt the investigator with the fix the validator prints:

   > (Navigator entitled) "methodology.json does not show a CLI-first Navigator decision. Record tool and data-source decisions, provenance, and any policy/entitlement skip."
   > (local / open tier) "Fix the navigator block per the validator: set navigator:{required:false, used:false, fallback_reason:...} or omit it, and ensure tools_required[] lists the tools osint-tools returned."

3. Present a summary of the proposed methodology to the user
4. **Tier split (read first):** on the **local / Pi / non-frontier harness**, the RLM is **default-on and auto-run per research cycle without a user-approval gate** — see the runtime adapter; it benchmarks better on small models, which need the context reduction. The proposal/approval flow in this step applies to **interactive cloud/frontier setups only** (where RLM is opt-in). If you are running autonomously (no user to ask), do not propose — just run RLM per the adapter and continue.

   If `.spotlight-config.json` has `integrations.rlm.enabled=true` (frontier opt-in), propose
   RLM as a methodology-phase option before the approval gate. Use this exact
   decision boundary:

   - If RLM mode is `lite`, propose deterministic RLM.
   - If RLM mode is `local_gemma4_e4b` and `integrations.rlm.preflight_status`
     is `green`, propose hybrid Gemma4 E4B RLM.
   - If RLM mode is `local_gemma4_e4b` but preflight is `yellow` or `red`, say
     the configured RLM is unavailable and skip without blocking methodology.
   - If RLM is not enabled/configured, skip this check silently.

   Proposal wording:

   > "RLM is installed for this Spotlight setup. Benchmarks on the synthetic
   > context-rot suite improved average recall from 0.75 to 1.0, removed decoy
   > hits from 4 to 0, and cut average downstream lines from 10.25 to 3.0 with
   > hybrid Gemma4 E4B. It adds a short local analysis pass and produces
   > `data/rlm-analysis.json` as leads only, never verified facts. Use RLM for
   > this methodology?"

   If the user approves, write/update `methodology.json` with:

   ```json
   {
     "rlm": {
       "available": true,
       "proposed": true,
       "approved": true,
       "mode": "lite|local_gemma4_e4b",
       "model": "gemma4:e4b|null",
       "prefilter": true,
       "hybrid": true,
       "decision_at": "<ISO timestamp>",
       "decision_by": "user",
       "run_id": "<timestamp>-rlm",
       "request_path": "{CASE_DIR}/data/rlm-request.json",
       "analysis_path": "{CASE_DIR}/data/rlm-analysis.json",
       "audit_path": "docs/rlm-benchmark-audit.md",
       "evidence_boundary": "lead-only; never verified or publishable"
     }
   }
   ```

   Then write `{CASE_DIR}/data/rlm-request.json` with the chosen mode,
   model, prefilter/hybrid flags, and a `corpus_paths` list containing
   case-contained text/JSON/Markdown files already created for the lead,
   brief, methodology, and saved source material. Run:

   ```
   execute-shell("python3 integrations/rlm/run_rlm.py {CASE_DIR}/data/rlm-request.json")
   ```

   If the user declines, write `rlm.available=true`, `proposed=true`,
   `approved=false`, `mode="off"`, and a short `declined_reason`.

   If RLM was configured but unavailable, write `rlm.available=false`,
   `proposed=false`, `approved=false`, `mode="off"`, and a concrete
   `skip_reason`. Do not block methodology approval.

5. **Gate: user approves the methodology.** Iterate if the user has changes.
   If the methodology changes after an approved RLM run, update
   `methodology.json`, regenerate `rlm-request.json` with the changed
   methodology/corpus paths, and rerun RLM before Phase 3.
6. After approval and before Phase 3 research begins, remind the user:

   > **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

---

## Phase 3 — Execution (Autonomous Cycles, Max 5)

With approved methodology, begin the execution loop. No user involvement between cycles — decide autonomously.

### Source-expression release mode

Resolve this once before the first cycle and preserve it on every investigator
and fact-checker spawn:

1. If `data/case-contract.json` validates, findings use contract `1.1`, and
   `python3 scripts/validate-case.py {CASE_DIR}` passes, set
   `SOURCE_EXPRESSION_MODE: activated`.
2. Otherwise, set `SOURCE_EXPRESSION_MODE: pilot` only when the operator has
   explicitly selected this case for the source-expression pilot. Record or
   preserve a clean pre-pilot legacy bundle for later comparison/recovery.
3. In every other case, omit the field. This is the default and preserves
   findings contract `1.0`; do not create `data/source-expressions.json`.

Never infer activation from `source-expressions.json`, findings version, or a
migration audit alone. `data/case-contract.json` is the sole activation
authority. Pilot output is a side artifact and cannot be promoted in place by
the current migration command. Activation of a clean legacy case is a separate
operator-reviewed dry-run/apply flow:

```text
python3 scripts/migrate-source-expressions.py {CASE_DIR}
python3 scripts/migrate-source-expressions.py {CASE_DIR} --apply
python3 scripts/validate-case.py {CASE_DIR}
python3 scripts/validate-fact-check.py {CASE_DIR}
```

The checked-in comparison is
`docs/source-expression-pilot-results.json`. Its activation status is **NOT
APPROVED** because timed human review, correction yield, longitudinal locator
stability, and same-fixture migration effort remain unmeasured. Do not enable
`1.1` as the new-case default.

Expression validation proves exact-text, locator, hash, reference, lifecycle,
and status integrity. It does not prove truth, entailment, completeness, or
editorial fairness.

```
CYCLE N (N starts at 1):

  1. Spawn investigator (EXECUTION mode):

     handle = spawn-agent(
       agent_id: "investigator",
       prompt: "MODE: EXECUTION
PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
{if source_expression_mode: SOURCE_EXPRESSION_MODE: {source_expression_mode}}
VAULT_PATH: {vault_path or 'none'}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
  rlm_approved={methodology.rlm.approved}
  rlm_analysis_path={methodology.rlm.analysis_path or 'none'}
CYCLE: {N}
SKILLS: acquisition-graduation (graduate repeated dev-browser paths only after repeatability is proven), web-archiving (archive all evidence before citing), content-access (paywalled sources — use before marking inaccessible), epistemic-grounding (fill grounding object and cap confidence when support is weak), shell-safety (validate untrusted values before execute-shell), social-media-intelligence (social investigations), technical-investigation (technical leads)

ACQUISITION: Firecrawl first via search/fetch. After every Firecrawl result, run the missing-source gate. Use dev-browser when static acquisition is insufficient for dynamic pages, portals, downloads, screenshots, visual verification, forms, or legally appropriate authenticated/local-browser contexts.

{if N > 1: Previous findings gaps:
{gaps}

Fact-check gaps:
{fc_gaps}}

{if monitoring_units: Monitoring results since last cycle:
{monitoring_summary}}

When you identify targets worth persistent monitoring, add them to monitoring_recommendations[] in data/findings.json.

Read methodology from {CASE_DIR}/data/methodology.json.
If methodology.rlm.approved=true and data/rlm-analysis.json exists, read it as
lead-routing context only. Treat every RLM artifact as `needs_verification`;
do not cite RLM output as evidence.
Write to {CASE_DIR}/data/findings.json.
Write/update {CASE_DIR}/data/evidence-bundle.json with acquisition attempts, missing-source gate answers, artifact paths, hashes, and claim links.
Append to {CASE_DIR}/data/investigation-log.json.",
       config: { iteration_limit: 80 }
     )
     output = wait-agent(handle)

  2. When complete: read-file("{CASE_DIR}/data/findings.json"); verify investigation-log.json was appended.

  2.5. Validate the investigator output before fact-checking:

     ```
     execute-shell("python3 scripts/validate-case.py {CASE_DIR}")
     ```

     If validation reports errors (non-zero exit), the investigator left data
     bugs — empty `claim` fields, missing required keys, wrong-shape output,
     or dangling references. Do NOT proceed to the fact-checker. Re-spawn the
     investigator with the validator errors quoted verbatim in the prompt and
     a directive: "fix these data bugs without changing your findings or
     verdicts; only correct the shape." Loop until the validator passes.

  3. Spawn fact-checker:

     handle = spawn-agent(
       agent_id: "fact-checker",
       prompt: "PROJECT: {project}
PROFILE: {profile}
TIER: {config.model_tier}
CASE_ROOT: {CASE_ROOT}
CASE_DIR: {CASE_DIR}
{if source_expression_mode: SOURCE_EXPRESSION_MODE: {source_expression_mode}}
INTEGRATIONS:
  osint_navigator_status={config.integrations.osint_navigator.status}
  osint_navigator_required={config.integrations.osint_navigator.required_in_phase_2}
SKILLS: web-archiving (archive sources before issuing verdict), content-access (paywalled sources — use before marking inaccessible), epistemic-grounding (judge whether evidence actually grounds each claim), shell-safety (validate untrusted values before execute-shell), technical-investigation (technical claims)

Apply SIFT source credibility check before searching for corroborating evidence.
Independently assess claim-to-evidence grounding before assigning verdicts or confidence.
Archive every source before citing it. Work through the content-access hierarchy before marking any source inaccessible.
If you identify sources worth monitoring for ongoing verification, add them to monitoring_recommendations[] in data/findings.json.

Fact-check all claims in {CASE_DIR}/data/findings.json.
Read {CASE_DIR}/data/evidence-bundle.json when present and use it to assess acquisition quality, missing-source gates, screenshots/downloads, hashes, and human-verification flags.
Write to {CASE_DIR}/data/fact-check.json.",
       config: { iteration_limit: 50 }
     )
     output = wait-agent(handle)

  4. When complete: read-file("{CASE_DIR}/data/fact-check.json").

  4.5. Validate the fact-checker output before the editorial check:

     ```
     execute-shell("python3 scripts/validate-case.py {CASE_DIR}")
     execute-shell("python3 scripts/validate-fact-check.py {CASE_DIR}")
     ```

     If the structural validator fails, use the same shape-only correction rules
     as 2.5. If the evidence validator fails, re-spawn the fact-checker once with
     its reasons quoted verbatim: repair the named case-local path, line range,
     JSON Pointer, quote, or hash; otherwise downgrade the verdict and explain the
     gap. Never ask it to change prose merely to satisfy a language heuristic.
     Present Gate 1 only after the evidence validator passes, or explicitly disclose
     the remaining claim as unverified.

  5. Run editorial standards check:
     - Do findings have sources with URLs, timestamps, and `local_file`?
     - Does every finding include a `grounding` object with support type, source role, missing assumptions, and confidence cap?
     - Does evidence-bundle.json exist with acquisition method, artifact paths, missing-source gate answers, and claim links?
     - Does investigation-log.json have substance (techniques, queries, failed approaches)?
     - Do high-confidence findings have 2+ fact-check sources?
     - Do fact-check claims include `grounding_assessment`?
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

     2. For approved recommendations, invoke-skill("monitoring") to:
        - register passive topics in Mycroft when useful,
        - create durable monitors in Scoutpost by project_id when available,
        - or fall back to runtime-native routines.

     3. Log all created monitor links to {CASE_DIR}/data/monitoring.json

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

`write-file("{CASE_DIR}/summary.md", <content>)` as a human-readable markdown document:

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

Also write `{CASE_DIR}/data/summary.json` as the machine contract for
review, report drafting, and ingest:

```json
{
  "schema_version": "1.0",
  "project": "{project}",
  "title": "{Investigation Title}",
  "generated_at": "ISO 8601 timestamp",
  "status": "pending_review",
  "cycles": 3,
  "verified_findings": 3,
  "summary": "2-3 paragraph narrative overview.",
  "key_conclusions": ["Conclusion 1", "Conclusion 2"],
  "limitations": ["Limitation 1", "Limitation 2"],
  "methodology_summary": "Techniques and tools used, drawn from data/investigation-log.json.",
  "findings": [
    {
      "id": "F1",
      "claim": "specific finding claim",
      "confidence": "high",
      "fact_check_verdict": "verified",
      "source_count": 3
    }
  ]
}
```

`summary.md` is the human artifact; `data/summary.json` is the machine
contract. Generate both.

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

Before asking the user to approve the investigation as ready for report drafting and ingestion, remind them:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

**Gate: user approves the investigation.**

### Package provenance before HTML review

After approval and before invoking the review skill, invoke `provenance-signing`:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

This creates `{CASE_DIR}/data/provenance-manifest.json` with hashes for the case artifacts, claim-to-verdict links, evidence bundle refs, and `requires_api_key: false`.

If `NOOSPHERE_C2PA_URL` is configured, optionally request signing:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\"")
```

Signing failures do not block review. Preserve the unsigned manifest and report the failure clearly.

### Generate review artifact

After approval, `invoke-skill("review")` to produce `{CASE_DIR}/review.html` — a self-contained HTML artifact the user can open in any browser to inspect findings and submit structured feedback. See `skills/review/SKILL.md`.

Offer the user:

> "Review artifact written to `{CASE_DIR}/review.html`. Open it in any browser to inspect findings and submit feedback (optional). If you submit feedback, save the exported `review-feedback.json` into `{CASE_DIR}/data/` and re-run `/spotlight` to process it. Or proceed to drafting the public report now."

### Feedback processing (on resume)

When `/spotlight` is resumed and `{CASE_DIR}/data/review-feedback.json` exists without a matching `review-feedback-processed.json` marker, Phase 0 invokes the review skill in process mode before advancing. This re-spawns the investigator with feedback-targeted instructions, updates findings, and regenerates the review artifact. See `skills/review/SKILL.md` § Mode B.

---

## Phase 5 — Report drafting (public-facing)

Before asking whether to draft the public-facing report, remind the user:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

After Gate 1 approval, offer the user the public-facing report:

> "Draft the public-facing journalist-grade report now?
> (a) Yes — invoke report-drafting to produce report.html + findings-report.md + evidence-map.json.
> (b) No — run `python3 scripts/decline-report.py {CASE_DIR}`, then skip to ingestion. (`review.html` already covers editorial review.)"

If (a): invoke `report-drafting`. The orchestrator authors `data/report-draft.json` to choose localized title, deck, finding order, editorial summaries, emphasis, caveats, and next steps. Then run `execute-shell("python3 scripts/finalize-report.py {CASE_DIR}")`; deterministic code validates finding references, attaches canonical verdict/confidence, and safely renders `report.html`, `findings-report.md`, and `evidence-map.json`. Semantic accuracy remains part of the independent fact-check and final human editorial gate. Do not hand-edit generated HTML or Markdown. Present completion only when the finalizer passes.

`technical_indicators` present: invoke `technical-investigation`; offer verified JSON, CSV, or STIX.

### Hybrid mode (data-detective handover)

When `{CASE_DIR}/data-detective-handover/` exists (i.e. this Spotlight run was triggered by a data-detective formal handover, not a standalone lead), `report-drafting` runs in hybrid mode: the methodology section spans both orchestrators in phase order — upstream data-detective phases (P0/P1 ingest, P3 detect, P6 handover) followed by Spotlight phases (P1 brief, P2 method, P3 cycles + fact-check, P4 Gate 1, P5 report-drafting). Each finding's `.path` block walks the actual trail from upstream detector to downstream fact-checker. Skip the offer above — drafting is the whole point of the handover. Invoke `report-drafting` automatically.

---

## Phase 6 — Ingestion

After report drafting (or after Gate 1 if drafting was skipped):

Before entering this phase, write `data/ingestion.json` with
`{"schema_version":"1.0","status":"pending"}` and run
`python3 scripts/finalize-report.py {CASE_DIR} --if-ready`. This case-local transition
marker makes a skipped Phase 5 visible even if ingestion writes only to an external
vault. Stop if the finalizer fails.

Remind the user before asking for ingestion confirmation:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes: update `data/ingestion.json` to status `requested`, then `invoke-skill("ingest")` — pass project path and vault config from `.spotlight-config.json`.
- If no: update `data/ingestion.json` to status `declined`; pipeline ends.

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
{CASE_DIR}/
  brief-directions.txt             — Approved brief directions
  summary.md                       — Investigation summary (generated at Gate 1)
  data/
    methodology.json               — Approved investigation plan
    findings.json                  — Investigator output (cumulative)
    fact-check.json                — Fact-checker output
    source-expressions.json        — Pilot side artifact or activated passage chain
    case-contract.json             — Sole authoritative activation receipt
    source-expression-migration.json — Migration audit only; never activation
    investigation-log.json         — Append-only cycle log
    provenance-manifest.json       — Case artifact hashes + optional C2PA signing status
    monitoring.json                — Scout state and check results
```

First classify the case contract:

- A valid `case-contract.json`, findings contract `1.1`, and matching artifact
  hashes means **activated**. Run `scripts/validate-case.py`, resume only with
  `SOURCE_EXPRESSION_MODE: activated`, and never downgrade it.
- Findings contract `1.0` plus an explicitly recorded pilot side artifact means
  **pilot**. Resume only with `SOURCE_EXPRESSION_MODE: pilot`. File presence
  alone is not enough to infer that operator choice.
- Findings `1.1`, source-expression refs, or migration outputs without a valid
  contract is an interrupted/partial migration. Stop. Restore the known clean
  legacy bundle, then rerun migration dry-run/apply; do not delete fields until
  the case merely looks legacy.
- A valid receipt with missing or hash-mismatched activated artifacts is stale
  or damaged. Stop and restore the matching bundle or use the supported
  supersession/revalidation flow. Never fall back to legacy interpretation.
- Otherwise the case is **legacy**, and source-expression mode stays omitted.

Then determine where the pipeline left off:

- No `brief-directions.txt` → restart at Phase 1
- No `data/methodology.json` → restart at Phase 2
- No `data/findings.json` → restart at Phase 3, cycle 1
- Has `data/findings.json` but no `summary.md` → restart at Phase 3, evaluate current cycle
- Has `summary.md` → Gate 1 review

An older runtime that cannot validate contract `1.1` must refuse an activated
case. Rollback may disable future activation only; existing activated cases
remain strict.

For wider failure modes — API hiccups, Ollama restarts, Obsidian lock files, corrupted case JSON, stale review-feedback markers — see `docs/recovery.md`.

---

## Sensitive Mode

When `sensitive: true` is set in `AGENTS.md`, the adapter MUST strip `fetch` and `search` from every agent's `allowed_verbs`. The orchestrator then:

- Research phases become local-only (`read-file`, `grep-files`, `list-files`, `query-vault`)
- All evidence must come from pre-scraped material in `{CASE_DIR}/research/`
- Readiness criteria requiring new sources cannot be met — flag explicitly at Gate 1 and mark the investigation as **constrained** rather than **verified**
