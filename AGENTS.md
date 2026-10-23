---
name: spotlight
description: Runtime contract for the Spotlight OSINT investigation system
version: "1.0"
runtime_version: "1"
sensitive: false
---

# Spotlight — Runtime Contract

## Evidence and intellectual independence

This policy is non-negotiable. Accuracy and the best achievable result take priority over agreement, reassurance, speed, or satisfying Tom's perceived intent.

- Treat Tom's claims, figures, assumptions, framing, and preferred solution as unverified inputs. Assess them independently and challenge them directly when evidence or sound reasoning points elsewhere.
- Model memory, familiarity, and plausibility are not evidence. They may guide what to inspect, but material or changeable claims require the strongest available current source: local code, configuration, tests, documentation, and artifacts for workspace behavior; official documentation for tools, libraries, and services; and primary records or data for external facts.
- Make the evidence chain inspectable. Cite or link the exact source supporting each material factual claim and confirm that it supports the claim. Separate verified fact from inference, assumption, estimate, and recommendation, and state uncertainty or confidence.
- Ground reasoning, methodology, code, and plans in explicit requirements and evidence. Explain material tradeoffs and verify behavior with proportionate tests, checks, reproduced calculations, and artifact or diff inspection. Never claim completion from intention or code inspection alone.
- Seek and report disconfirming evidence, contradictions, limitations, and the strongest reasonable counterargument. Do not cherry-pick evidence or hide bad news.
- Never invent or imply a source, quotation, citation, file content, tool or test result, API behavior, or verification state. If evidence is missing or inaccessible, say what remains unknown, lower confidence, and make any bounded assumption explicit.
- Do not flatter, praise the premise, mirror Tom's confidence, or tell him what he appears to want to hear. Be candid and respectful; optimize for the outcome he would choose with better information, even when that means rejecting his proposed approach.
- When creating an independent repository or workspace, copy this section into its root `AGENTS.md`; do not rely on parent-directory inheritance.


## Landing Site Deployment

The production landing site is GitHub Pages at
`https://spotlight.buriedsignals.com/`. GitHub Pages is configured in legacy
branch mode with `main` and the repository root (`/`) as its source. There is
no Render service and no in-repository deployment workflow for the landing
site: merging or pushing the intended site changes to `main` triggers the
Pages build.

Before merging, validate the affected static HTML and the repository checks
appropriate to the change. After merging, confirm the Pages source and status
with `gh api repos/buriedsignals/spotlight/pages`, inspect the newest
`github-pages` deployment for the merged SHA, and verify the changed copy at
the production URL. GitHub Pages may serve cached content for up to ten
minutes, so do not treat an immediately stale response as a failed deploy.

## Session Preflight

Before coding in this project on a Buried Signals dev machine, load the shared `coding-rules` skill from OpenKnowledge (and `test-audit` when touching tests). It is the canonical source for workflow routing, coding standards, Jujutsu/version-control rules, GitHub operations, and parallel-agent isolation. Local instructions below add project-specific constraints.

## Tool Verb Registry

Fixed vocabulary of abstract operations. Every runtime adapter MUST implement all verbs except the retired `vault-write`. If a verb is unsupported, the adapter raises an explicit error at load time.

One exception: a runtime MAY omit `execute-shell` when every shell step in `agents/` and `skills/` either has a typed equivalent in that runtime or is run by the runtime itself at a fixed point (the validators, when a worker finishes), and every other step reports itself unavailable instead of being improvised. Such a runtime keeps `{CASE_DIR}` confinement and public-only egress in its typed tools. The Pi harness (`harness/pi`) is such a runtime; its step-by-step mapping is in `harness/pi/README.md`. Claude Code, Codex, Goose and subscription mode keep `execute-shell` and run the shell steps as written.

| Verb | Signature | Semantics | Universal backing |
|------|-----------|-----------|-------------------|
| `fetch` | `fetch(url, output_path)` | Scrape URL content, save to file | `integrations.scraping` (Crawl4AI; Firecrawl fallback if keyed) |
| `search` | `search(query, output_path, limit)` | Web search, save results to file | `integrations.search` (SearXNG; Firecrawl union optional) |
| `read-file` | `read-file(path)` | Read file contents | filesystem |
| `write-file` | `write-file(path, content)` | Write file (full overwrite) | filesystem |
| `edit-file` | `edit-file(path, old, new)` | Targeted string replacement | filesystem |
| `list-files` | `list-files(pattern)` | Glob/search for files matching pattern | glob |
| `grep-files` | `grep-files(pattern, path)` | Search file contents by regex | ripgrep |
| `execute-shell` | `execute-shell(command)` | Run shell command, return stdout + stderr | shell subprocess |
| `spawn-agent` | `spawn-agent(agent_id, prompt, config)` | Launch sub-agent with prompt and config | runtime-specific |
| `wait-agent` | `wait-agent(handle)` | Block until agent completes, return output | runtime-specific |
| `invoke-skill` | `invoke-skill(skill_id)` | Load skill instructions into current context | runtime-specific |
| `query-vault` | `query-vault(workspace_path, query)` | Search the member wiki for prior work: Spotlight's claim, event and story-arc notes and the member's own notes | Spotlight `query_vault.py` over Engine's `bsig wiki search` (cloud audience, protected sources redacted); without Engine or a wiki, an empty result with an `unavailable` reason |
| `vault-write` | — | Retired with the 1.0 graph; adapters need not implement it | Solo Ingest writes the wiki through `scripts/wiki-destination.py` |

## Agent Manifests

### investigator

```yaml
name: investigator
description: Plans and executes OSINT investigations using structured methodology
iteration_limit: 80
allowed_verbs:
  - fetch
  - search
  - read-file
  - write-file
  - edit-file
  - list-files
  - grep-files
  - invoke-skill
  - query-vault
  - execute-shell
disallowed_verbs:
  - spawn-agent
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  local: hf.co/tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M
  fallback_note: Two-tier local fleet, both Tom's journalism fine-tunes. Default — Qwen 3.5 9B Journalist (tomvaillant/qwen3.5-9b-abliterated-journalist-GGUF:Q4_K_M) for 16 GB Macs, ~6 GB on disk. Heavy tier — Qwen 3.6 27B Journalist (tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M) for 32 GB+ Macs, ~15 GB on disk, ~22 GB at runtime, runs in thinking mode (abliterated /no_think path is broken). The setup form's fit-check enforces the 32 GB minimum before the 27B option commits. Bench-validated against the eval suite in tools/fine-tuning/eval/.
vault_context:
  enabled: true
  query_on_load: true
```

The investigator operates in two modes:

- **PLANNING** — Analyzes the brief, queries the vault for prior work, designs methodology, writes `{CASE_DIR}/data/methodology.json`
- **EXECUTION** — Follows approved methodology, executes research using `fetch` and `search`, writes `{CASE_DIR}/data/findings.json` and appends to `{CASE_DIR}/data/investigation-log.json`

Full prompt bundle: `agents/investigator.md`.

### fact-checker

```yaml
name: fact-checker
description: Independent verification of investigation findings using SIFT methodology
iteration_limit: 50
allowed_verbs:
  - fetch
  - search
  - read-file
  - write-file
  - list-files
  - grep-files
  - invoke-skill
  - query-vault
  - execute-shell
disallowed_verbs:
  - spawn-agent
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  local: gemma-4-26B-A4B-it
  fallback_note: Fact-checking accuracy degrades significantly on lighter models. Local ship — unsloth/gemma-4-26B-A4B-it-GGUF (Q4_K_M for 24GB+ Macs, Q6_K_XL for 48GB+). Native vision for scanned docs + satellite imagery.
vault_context:
  enabled: true
  query_on_load: true
```

The fact-checker operates independently from the investigator. Spawned with its own context, it reads only the investigator's JSON output — not their reasoning — and writes `{CASE_DIR}/data/fact-check.json` with per-claim verdicts and evidence trails.

**Verdict taxonomy** (fact-check contract `1.1`, Batch 1.1's five verdicts): `verified` | `unverified` | `disputed` | `false` | `not_checkable`, with confidence `high` | `medium` | `low` and optional Batch 1.1 flags. Legacy `1.0` files keep `partially_verified` and `mischaracterized` (and findings' `disputed` confidence) and stay readable; an activated case uses only the Batch 1.1 vocabulary.

Full prompt bundle: `agents/fact-checker.md`.

### researcher

```yaml
name: researcher
description: Works one approved research topic brief and returns leads with captured sources
iteration_limit: 40
allowed_verbs: [fetch, search, read-file, write-file, list-files, query-vault]
disallowed_verbs: [spawn-agent, execute-shell]
preferred_model:
  swarm: deepseek/deepseek-v4-flash
  swarm_fallback: z-ai/glm-5.3
  local: the harness's local model (local corpus and prior knowledge lanes only)
```

The researcher never sees the case: it gets one approved topic brief from the methodology's `research_plan` and writes only its lane folder `{CASE_DIR}/research/swarm/<topic-id>/`. Code starts it, never a model: the Pi harness runs each approved topic as a lane (`harness/pi/src/lanes.ts`, lane types in `lanes/*.yaml`); in subscription mode `phase-research` spawns it through the host's subagents. Web lanes run only on the opt-in research swarm; the local corpus and prior-knowledge lanes only on the local model.

Full prompt bundle: `agents/researcher.md`.

## Skill Registry

Skills are markdown playbooks loaded via `invoke-skill(skill_id)`. Each skill lives in `skills/{skill_id}/SKILL.md` with optional reference files in `skills/{skill_id}/references/`.

`install-spotlight.sh` surfaces exactly the skills listed in `skills.manifest` (the engine-resolved set, generated by `bsig skills resolve`; the engine catalog is the source of truth) into the chosen harness's `skills/spotlight/` dir. Regenerate the manifest when the catalog/skill set changes.

### Checkout layout

Every `scripts/`, `integrations/`, `schemas/`, and `skills/` path in a skill or
agent prompt is relative to the checkout root. Skills and scripts must be
co-located in one checkout and reachable from a single shell whose working
directory is that root. A runtime may link or copy `skills/` elsewhere for
discovery, but `execute-shell` runs against the checkout. The agent establishes
that shell before Phase 0 and stops if it cannot (`skills/phase-preflight`,
step 0). A runtime without `execute-shell` (see the Tool Verb Registry) skips
that shell check and confirms the checkout through its typed tools. Do not
rewrite script paths per runtime.

Skills run Python through `scripts/spotlight-python`, never a bare `python3`
(a system Python may lack Crawl4AI and the other dependencies). It runs
`$SPOTLIGHT_PYTHON` when set (an absolute path; an unusable one is an error,
not a fallback), else the checkout's `.venv/` interpreter, else `python3` on
`PATH`. The review server resolves the same interpreter.

`.spotlight-config.json` is `$SPOTLIGHT_CONFIG` when set (an absolute path),
else `.spotlight-config.json` at the checkout root. Every script that reads
the config resolves it through `config_path()` in
`integrations/_credentials.py`; skills pass `--config` only to override it.

| Skill ID | Path | Description | Invocable By |
|----------|------|-------------|--------------|
| `spotlight` | `skills/spotlight/SKILL.md` | Investigation orchestrator — pipeline phases, gates, cycle evaluation | orchestrator (top-level) |
| `editorial-review` | `skills/editorial-review/SKILL.md` | Post-Investigation-gate HTML review artifact; exports structured feedback to the Investigation gate owner | orchestrator, user |
| `integrations` | `skills/integrations/SKILL.md` | Routing layer for external tool integrations — dev-browser, Junkipedia, Arbiter, Noosphere C2PA, OSINT Navigator, Unpaywall. Reads live preflight status, maps investigation tasks to integrations | investigator, fact-checker, orchestrator |
| `ingest` | `skills/ingest/SKILL.md` | Retired 1.0 ingest: explains that an old case continues as a follow-up 1.1 case | orchestrator |
| `report-drafting` | `skills/report-drafting/SKILL.md` | Post-Investigation-gate hybrid report: model-authored localized framing and priority in `data/report-draft.json`; deterministic finding-reference, verdict, confidence, escaping, and artifact rendering. | orchestrator, user |
| `monitoring` | `skills/monitoring/SKILL.md` | Monitoring orchestration — Mycroft passive signals, coJournalist projects/scouts, runtime-native fallbacks | orchestrator |
| `provenance-signing` | `skills/provenance-signing/SKILL.md` | Build a case-level provenance manifest and optionally hand it to Noosphere C2PA signing before final report delivery | orchestrator, user |
| `acquisition-graduation` | `skills/acquisition-graduation/SKILL.md` | Convert repeated dev-browser acquisitions into durable source/domain guidance without secrets or brittle session details | investigator, fact-checker, orchestrator, user |
| `web-archiving` | `skills/web-archiving/SKILL.md` | Wayback Machine, Archive.today, local archival with chain of custody | investigator, fact-checker |
| `content-access` | `skills/content-access/SKILL.md` | Paywall access hierarchy, access_method classification | investigator, fact-checker |
| `epistemic-grounding` | `skills/epistemic-grounding/SKILL.md` | Claim-to-evidence grounding, confidence caps, and failure routing for weak or adjacent evidence | investigator, fact-checker, orchestrator, user |
| `shell-safety` | `skills/shell-safety/SKILL.md` | Safe command construction, value validation, and destructive-operation probe rules for execute-shell use | investigator, fact-checker, orchestrator, user |
| `osint` | `skills/osint/SKILL.md` | OSINT tool routing table + 150-tool catalog + OSINT Navigator integration | investigator, fact-checker, user |
| `navigator` | `skills/navigator/SKILL.md` | Navigator routing: OSINT tool discovery for Pro and Lab members | investigator, fact-checker, orchestrator, user |
| `arbiter` | `skills/arbiter/SKILL.md` | Live `/arbiter` entry point: guides each member through attributed signup and local BYO-key setup, then runs the native Arbiter HTTPS case-study and deterministic report workflow | investigator, fact-checker, orchestrator, user |
| `investigation-methodology` | `skills/investigation-methodology/SKILL.md` | Step-by-step techniques: geolocation, verification, person, platform, transport, archiving | investigator, user |
| `follow-the-money` | `skills/follow-the-money/SKILL.md` | Financial methodology: UBO, offshore, budgets, assets, public blockchain tracing | investigator, user |
| `technical-investigation` | `skills/technical-investigation/SKILL.md` | Passive technical investigation: indicators, infrastructure history, local document/email metadata, public GitHub history, verified indicator export | investigator, fact-checker, orchestrator, user |
| `social-media-intelligence` | `skills/social-media-intelligence/SKILL.md` | Account authenticity, coordination detection, narrative tracking | investigator, fact-checker, user |
| `phase-preflight` | `skills/phase-preflight/SKILL.md` | Preflight child skill — config, sovereign backings, workspace validation, project setup, integration tiering | orchestrator |
| `phase-methodology` | `skills/phase-methodology/SKILL.md` | Brief and Methodology child skill — brief conversation with approval gate, investigator PLANNING spawn, methodology validation and approval | orchestrator |
| `phase-research` | `skills/phase-research/SKILL.md` | Research child skill — approved topic briefs run as research lanes started by code, merged and triaged leads and clues, suggested marks, Research gate user approval, the AMENDMENT that turns the marks into the methodology change, and a stalled investigation's research round | orchestrator |
| `phase-investigation` | `skills/phase-investigation/SKILL.md` | Investigation child skill — autonomous investigation cycles (max 3; a researched case then gets one targeted research round), fact-check spawns, validators, readiness criteria, Stall Protocol | orchestrator |
| `phase-investigation-gate` | `skills/phase-investigation-gate/SKILL.md` | Investigation closing child skill — summary + machine contract generation, Investigation gate user approval (ends turn), provenance packaging, review artifact | orchestrator |
| `phase-report` | `skills/phase-report/SKILL.md` | Report child skill — public-facing report drafting via report-drafting + deterministic finalize-report.py rendering | orchestrator |
| `phase-ingest` | `skills/phase-ingest/SKILL.md` | Ingest child skill — ingestion transition marker, user confirmation, vault ingestion or decline | orchestrator |

## Sensitive Mode

When `sensitive: true` is set in this manifest (or toggled at runtime), the adapter MUST strip `fetch` and `search` from all agent `allowed_verbs` lists. All research becomes local-only — agents can only use `read-file`, `grep-files`, `list-files`, and `query-vault` for information gathering.

The adapter's enforcement point varies by runtime (pi extension / Hermes `local-gemma` routing / Goose tool allowlist / Codex tool allowlist / per-session provider binding). See `docs/integrations.md#sensitive-mode-across-runtimes`.

A sensitive investigation cannot satisfy the "document trail" readiness criterion from external sources. The orchestrator marks the investigation as **sensitive-mode constrained** at the Investigation gate.

To activate: set `sensitive: true` in this file or issue a runtime command.
To deactivate: set `sensitive: false` or issue a runtime command.

### Anonymized fetch (Tor) — a distinct posture

`sensitive` mode and **anonymized fetch** are two *different* postures; do not conflate them:

- **`sensitive: true`** = **no external egress.** `fetch`/`search` are stripped; research is local-only.
- **anonymized fetch (Tor)** = **egress still happens, but the operator's IP is hidden.** Use it when you *must* scrape a target-of-investigation without revealing that the investigation is looking (KTD8 / U7). The `fetch` seam routes the Crawl4AI browser through the local Tor SOCKS proxy (`socks5://127.0.0.1:9050`).

Triggering (per-run is the default posture):

- **Per-run:** set `SPOTLIGHT_ANONYMIZE_FETCH=true` (or `anonymize_fetch: true` in this manifest's frontmatter) — every `fetch` in the run egresses via Tor.
- **Per-fetch override:** pass `--tor` / `--no-tor` to a single `integrations.scraping` call.

A Tor-proxied fetch **never silently falls back to a direct (de-anonymizing) fetch** — on a Tor failure the seam raises, and the operator chooses to re-run `--no-tor` (revealing their IP) or abort. Tor exits are widely blocklisted, so an anonymized fetch of a hard target may simply fail; that is by design, not a bug.

## Cases Directory Structure

Each investigation creates an isolated directory under the configured
`case_workspace_root` from `.spotlight-config.json`. The orchestrator resolves
that path once as `CASE_ROOT`, then passes the concrete case directory to agents
as `CASE_DIR`. Do not infer the case path from the knowledge vault path.

`CASE_ROOT` must be durable, user-owned storage that survives the session.
Phase 0 confirms this with a probe write and read-back before any case file
exists, stops and asks the user when it cannot, and records the resolved
absolute path and the result in `.spotlight-config.json` under `storage`.

```
{CASE_DIR}/
├── brief-directions.txt         # Phase 1 — approved brief
├── summary.md                   # Phase 3 — Investigation gate summary (human-readable)
├── data/
│   ├── methodology.json         # Schema: schemas/methodology.schema.json
│   ├── findings.json            # Schema: schemas/findings.schema.json
│   ├── fact-check.json          # Schema: schemas/fact-check.schema.json
│   ├── source-expressions.json  # Exact passages; written only by scripts/write-source-expressions.py
│   ├── source-positions.json    # Who asserts or disputes each finding; same writer
│   ├── case-contract.json       # Sole authoritative 1.1 activation receipt
│   ├── source-expression-migration.json # Dry-run audit only; never activates a case
│   ├── report-draft.json        # Schema: schemas/report-draft.schema.json (when report requested)
│   ├── report-declined.json     # Explicit Phase 4 decline marker (when report skipped)
│   ├── ingestion.json           # Case-local Phase 5 transition/receipt
│   ├── <gate>-review.json       # Review page record per gate: suggested and final marks, dispositions, selection
│   ├── review-nonces.json       # Hashes of one-time decision nonces; a gate decision counts only when backed here
│   ├── evidence-bundle.json     # Schema: schemas/evidence-bundle.schema.json
│   ├── investigation-log.json   # Schema: schemas/investigation-log.schema.json
│   ├── summary.json             # Schema: schemas/summary.schema.json
│   ├── provenance-manifest.json  # Schema: schemas/provenance-manifest.schema.json
│   ├── knowledge-proposal.json   # Analyst proposal at Ingest (claims, events, arcs); interface 1
│   ├── knowledge-ids.json        # Builder id map: proposal and case keys to record ids and versions
│   ├── knowledge-mentions.json   # Entity types and strong identifiers per mention, for the wiki's register lookup; never in a batch
│   ├── knowledge-batch.json      # Reviewed Batch 1.1 (core), built by scripts/build-knowledge-batch.py
│   ├── knowledge-batch-draft.json # Builder draft for review, with flags awaiting a disposition
│   ├── ingest-review.json        # Ingest page choices: record selection and flag dispositions
│   ├── decision-signals.json     # Optional decision-model review signals (opt-in; never evidence)
│   └── monitoring.json          # (optional) External monitor registry for the case
└── research/
    ├── *.md                     # Scraped web content
    ├── *.json                   # Search results
    ├── archived/                # Wayback / Archive.today preservation
    └── media/                   # Images, PDFs, other media
└── evidence/
    ├── *.png                    # Screenshots and visual verification captures
    └── *.pdf                    # Downloaded or preserved source documents
```

All schemas are in `schemas/` at the repo root. New cases start at findings
contract `1.1` (activated); existing cases keep `1.0`. Source-expression and
activation documents use their own schema versions as declared below.

## Schema Reference

| Schema | Path | Purpose |
|--------|------|---------|
| Findings | `schemas/findings.schema.json` | Investigation findings with sources, confidence, claim-to-evidence grounding, connections, monitoring_recommendations |
| Fact-Check | `schemas/fact-check.schema.json` | Per-claim verdicts with evidence_for/evidence_against trails and grounding assessment |
| Source Expressions | `schemas/source-expressions.schema.json` | Exact case-local passages, locators, hashes, lifecycle, and many-to-many finding relations |
| Source Positions | `schemas/source-positions.schema.json` | Per finding: actor, stance, cited passages and verbatim attribution; bound by the gate approval when present |
| Case Contract | `schemas/case-contract.schema.json` | Append-only receipt that solely determines whether a case is activated as findings contract `1.1` |
| Source-Expression Migration | `schemas/source-expression-migration.schema.json` | Dry-run/apply audit record; informative only and never an activation signal |
| Evidence Bundle | `schemas/evidence-bundle.schema.json` | Acquisition artifacts with method, missing-source gate, hashes, and claim links |
| Provenance Manifest | `schemas/provenance-manifest.schema.json` | Case artifact hashes, claim/verdict links, evidence refs, and optional Noosphere C2PA signing metadata |
| Decision Signals | `schemas/decision-signals.schema.json` | Opt-in Jev decision-model answers and derived review signals for grounding, report prose and ingest; can only lower confidence or add flags |
| Knowledge Batch 1.1 (core) | `schemas/knowledge-batch-1.1.schema.json` | Open-source core of Batch 1.1: evidence, passages, claims, claim–passage links, source positions, assessments, events, story arcs and memberships, with continuous versions and exact refs. Cross-record rules C1–C10 and C13 are checked by `scripts/validate-knowledge-batch.py` |
| Knowledge Proposal | `schemas/knowledge-proposal.schema.json` | Analyst interface 1: normalized claims per finding (with `attributed`), events, story arcs and memberships; `scripts/build-knowledge-batch.py` adds the rest |
| Methodology | `schemas/methodology.schema.json` | Investigation plan with directions, steps, tools_required, opsec_considerations, and the optional `research_plan` (topic briefs, round budget, keys, egress, consent) |
| Research Lane | `schemas/research-lane.schema.json` | `research/swarm/<topic-id>/lane.json`: one lane's route, status, budget and spend |
| Research Lead | `schemas/research-lead.schema.json` | One `leads.jsonl` line: a candidate with Batch 1.1 evidence fields, a verbatim excerpt and optional attribution; never a finding |
| Research Leads | `schemas/research-leads.schema.json` | `data/leads.json`: every approved lane's leads merged by canonical URL and content hash |
| Research Clues | `schemas/research-clues.schema.json` | `data/clues.json`: clues from the rules, triage questions and approved methods; bound by the Research gate receipt |
| Research Suggestions | `schemas/research-suggestions.schema.json` | `data/research-suggestions.json`: the investigator's suggested mark and one-line reason per clue and direction, written in AMENDMENT mode before the Research page opens; only the member's marks count |
| Investigation Log | `schemas/investigation-log.schema.json` | Append-only cycle audit trail |
| Summary | `schemas/summary.schema.json` | Investigation gate summary for review |

## Old cases

Cases from before Batch 1.1 are forward-only:

1. They stay readable as findings contract `1.0`, and nothing converts them.
2. To activate one, run `scripts/spotlight-python scripts/migrate-source-expressions.py {CASE_DIR}`
   as a dry run, and apply it with `--apply` only when the dry run says the
   case is eligible.
3. Otherwise start a follow-up case from the old brief, with the old
   `research/` and `evidence/` available as local sources.

A `1.0` case is no longer ingested: the 1.0 graph it went into is retired, and
Engine's update exports any old graph to `spotlight/legacy-graph/` in the wiki
before deleting it. To ingest an old case's findings, activate it (step 2) or
continue it as a follow-up 1.1 case (step 3).

Some things cannot be migrated mechanically and need a person: normalized
propositions, source positions, the old `partially_verified` and
`mischaracterized` verdicts, a finding's `disputed` confidence, flag
dispositions, events and story arcs.

## Solo wiki destination

An activated case in solo mode is written to the member's wiki by
`scripts/wiki-destination.py`, after the ingest page's request, under
`spotlight/` only:

- `spotlight/batches/<batch>.json`: each approved batch, whole and append-only;
- `spotlight/decisions.jsonl`: the local decision journal, one line per batch;
- `spotlight/claims/`, `spotlight/events/`, `spotlight/story-arcs/`: one note
  per lineage, regenerated from the kept batches, status derived from the
  journal;
- `spotlight/generated-notes.json`: the hash of every generated note, so a
  hand edit is detected and never overwritten.

Entity mentions link to `wiki/entities/` only on an exact register match from
`bsig wiki resolve`; the register links on a strong identifier, so the
lookup sends the analyst's identifiers for that mention
(`data/knowledge-mentions.json`, bound by the ingest approval), which never
enter a batch. Each batch's links are kept in `spotlight/entity-links.json`, so
rebuilding the notes for a later batch keeps them. Spotlight
never writes the register. Without Engine there are no links. The write holds
the wiki's `.wiki-lock` and the case lock throughout. Newsroom-mode cases
write nothing locally.

## Source-Expression Release Modes

- **Activated (default for new cases):** before the first Investigation cycle
  of a case with no findings yet, `scripts/case-contract.py activate-new`
  writes `data/case-contract.json` with a `born` event. Findings use contract
  `1.1`, the fact-check uses contract `1.1`, and producers write passages only
  through `scripts/write-source-expressions.py`. The Investigation-gate
  approval binds the artifact hashes it covers. Activated cases remain strict
  and must never be treated as legacy during recovery or rollback.
- **Legacy (existing cases):** a case that already has findings keeps contract
  `1.0`; producer spawn prompts omit `SOURCE_EXPRESSION_MODE`; no expression
  protection is claimed. Old cases are forward-only: they are activated only
  through the operator-reviewed `scripts/migrate-source-expressions.py`
  dry-run/apply flow, or continued as a new follow-up case.
- **Pilot (explicit opt-in on a legacy case):** pass
  `SOURCE_EXPRESSION_MODE: pilot` to both producers.
  `data/source-expressions.json` is an experimental side artifact; it does not
  upgrade the findings contract and never activates the case.

Deterministic checks establish exact-text, locator, hash, lifecycle, reference,
and status integrity. They do not establish truth, entailment, completeness, or
fair editorial framing. Independent fact-checking and human review remain
required.

The decision and its stop thresholds are recorded in
`docs/source-expression-pilot-results.json`: approved for new cases on 22
October 2026, with review time, correction yield and locator stability
measured over the first real activated cases. When a threshold is breached, a
release returns new cases to legacy until the producer is fixed; an
activated case never falls back.
