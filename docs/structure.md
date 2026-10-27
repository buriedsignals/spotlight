# Structure

This doc describes the repo layout: what's where, why, and how the pieces connect. If you want to add a skill, monitoring backend, schema, or runtime adapter, start here.

## Top-level layout

```
spotlight/
├── AGENTS.md                 # Runtime contract — verb registry, agent manifests, skill registry
├── README.md                 # Humans-first entry doc (quick-start per runtime)
├── install-spotlight.sh      # Contributor/dev installer (not the journalist path)
├── install/                  # Local configurator used by the contributor installer
├── .spotlight-config.json    # Per-session config (search library, vault path, cases root, runtime)
├── .gitignore
├── schemas/                  # JSON schemas — case artifacts, activation, migration, and reports
├── skills/                   # 17 skills (pi-native SKILL.md format)
├── agents/                   # 2 agent prompt bundles (investigator + fact-checker)
├── integrations/             # External tool integrations (dev-browser, Junkipedia, Apify, Arbiter, Noosphere C2PA, OSINT Navigator, Unpaywall)
├── docs/                     # You are here. Operator manual.
├── monitoring/               # Case-level monitor registry helper + leads queue
└── cases/                    # Per-investigation output (gitignored)
```

## AGENTS.md — the runtime contract

`AGENTS.md` is loaded by every runtime at session start. It declares:

1. **13-verb registry** — the abstract tool vocabulary every skill instruction uses
2. **Agent manifests** — `investigator` and `fact-checker` with `allowed_verbs`, `iteration_limit`, `preferred_model`
3. **Skill registry** — 17 skills with IDs, paths, and which agents can invoke them
4. **Cases directory structure** — `{CASE_DIR}/{data,research}/` convention
5. **Schema reference** — pointers to `schemas/*.json`
6. **Sensitive mode** — toggle that strips `fetch`/`search` from allowed_verbs

### The 13 verbs

| Verb | Signature | Semantics |
|---|---|---|
| `fetch` | `fetch(url, output_path)` | Scrape URL, save file. Backing: `firecrawl scrape` |
| `search` | `search(query, output_path, limit)` | Web search, save results. Backing: `firecrawl search` |
| `read-file` | `read-file(path)` | Read file contents |
| `write-file` | `write-file(path, content)` | Write file (full overwrite) |
| `edit-file` | `edit-file(path, old, new)` | Targeted string replacement |
| `list-files` | `list-files(pattern)` | Glob / pattern match |
| `grep-files` | `grep-files(pattern, path)` | Regex search file contents |
| `execute-shell` | `execute-shell(command)` | Run shell command, return stdout+stderr |
| `spawn-agent` | `spawn-agent(agent_id, prompt, config)` | Launch sub-agent |
| `wait-agent` | `wait-agent(handle)` | Block until agent completes |
| `invoke-skill` | `invoke-skill(skill_id)` | Load skill instructions into context |
| `query-vault` | `query-vault(workspace_path, query)` | Search the member wiki through Engine's `bsig wiki search` |
| `vault-write` | — | Retired with the 1.0 graph; solo Ingest writes the wiki through `scripts/wiki-destination.py` |

These are **abstract** — the runtime adapter binds each to a concrete tool. See [integrations.md](integrations.md) for per-runtime mappings.

## schemas/ — case contracts

Every structured case file validates against a schema. The default findings
contract is `1.0`; activated cases use findings contract `1.1` plus a separate
activation receipt. Other artifacts retain their schema-specific versions.

| Schema | Case file | Role |
|---|---|---|
| `findings.schema.json` | `{CASE_DIR}/data/findings.json` | Investigator output — claims, evidence, sources, confidence, perspective, optional explicit technical indicators, monitoring recommendations |
| `fact-check.schema.json` | `{CASE_DIR}/data/fact-check.json` | Fact-checker output — per-claim verdicts, evidence_for/against, gaps_for_next_cycle |
| `source-expressions.schema.json` | `{CASE_DIR}/data/source-expressions.json` | Opt-in exact passages, locators, hashes, lifecycle, and finding relations |
| `source-positions.schema.json` | `{CASE_DIR}/data/source-positions.json` | Who asserts or disputes each finding, with cited passages and verbatim attribution |
| `case-contract.schema.json` | `{CASE_DIR}/data/case-contract.json` | Sole authoritative activation receipt for findings contract `1.1` |
| `source-expression-migration.schema.json` | `{CASE_DIR}/data/source-expression-migration.json` | Migration dry-run/apply audit; never an activation signal |
| `methodology.schema.json` | `{CASE_DIR}/data/methodology.json` | Investigator PLANNING output — investigation_plan, tools_required, opsec_considerations |
| `evidence-bundle.schema.json` | `{CASE_DIR}/data/evidence-bundle.json` | Acquisition artifacts, missing-source gates, hashes, and claim links |
| `investigation-log.schema.json` | `{CASE_DIR}/data/investigation-log.json` | Append-only cycle audit trail |
| `summary.schema.json` | `{CASE_DIR}/data/summary.json` | Investigation gate summary |
| `rlm-analysis.schema.json` | `{CASE_DIR}/data/rlm-analysis.json` | Optional RLM case-corpus analysis — leads only, every artifact `needs_verification` |
| `provenance-manifest.schema.json` | `{CASE_DIR}/data/provenance-manifest.json` | Case artifact hashes, claim/verdict links, evidence refs, optional C2PA signing metadata |

### Source-expression ownership

`data/source-expressions.json` owns immutable passage cores and their
many-to-many `supports`, `contradicts`, or `context` finding links.
`evidence-bundle.json` owns acquisition artifacts and text derivatives.
`fact-check.json` records which immutable expression/finding relation informed
a verdict. Reports and provenance refer to the same expression identities.
This avoids copying source wording into several independently mutable records.

The release modes are deliberately explicit:

| Mode | Authoritative state | Meaning |
|---|---|---|
| Activated | Findings contract `1.1` plus a valid `case-contract.json` | Default for every new case. Strict expression validation applies and cannot be downgraded; from the Investigation-gate approval on, the artifacts must match the binding that approval wrote. |
| Legacy | Findings contract `1.0`, no valid case contract | Existing cases. Expression protection is not claimed; forward-only. |
| Pilot | Legacy state plus an explicitly requested side artifact | Evaluation only. Artifact presence never activates a case. |

See `source-expression-pilot-results.json` for the decision, the stop
thresholds and the measurements still due after the default changed.

Validate a case file:

```bash
python3 -m jsonschema -i {CASE_DIR}/data/findings.json schemas/findings.schema.json
```

## skills/ — 17 skills

Each skill is a directory with `SKILL.md` (+ optional `references/*.md` for large supporting content).

### Orchestrator skill

- **`spotlight`** — the investigation dispatcher. After preflight it resolves durable state, dynamically invokes one phase owner, and repeats through Brief → Methodology → Research (only with a research plan) → Investigation cycles (max 3) → Investigation gate → Report → Ingest.

### Pipeline-support skills (invocable by orchestrator)

- **`editorial-review`** — post-Investigation-gate HTML review artifact. Renders a self-contained `{CASE_DIR}/review.html` that exports structured feedback for validation by the Investigation gate owner. It never spawns agents or derives phase state. No server required.
- **`integrations`** — routing layer for external tool integrations (dev-browser, Junkipedia, Apify, Arbiter, Noosphere C2PA, OSINT Navigator, Unpaywall). Reads live preflight status, maps investigation tasks to integrations. See `integrations/` at repo root for manifests + per-integration usage docs.
- **`ingest`** — retired 1.0 ingest. A `1.0` case is no longer ingested; it is activated or continued as a follow-up 1.1 case. Activated cases are written to the wiki by `scripts/wiki-destination.py` (see `phase-ingest`).
- **`monitoring`** — case-level monitoring recommendations and explicit Mycroft handoff.
- **`acquisition-graduation`** — turns repeated dev-browser acquisition successes into durable source/domain guidance without secrets or brittle session details.
- **`report-drafting`** — post-Investigation-gate report drafting with methodology and evidence ledgers. An optional diagram selects existing `findings.json.connections`; the renderer produces `flow`, `hierarchy`, `network`, or `loop` diagrams without accepting model-authored Mermaid or layout code.

### Agent-support skills (invocable by investigator / fact-checker)

- **`web-archiving`** — Wayback → Archive.today → local scrape hierarchy. Chain of custody blocks.
- **`content-access`** — 8-step paywall hierarchy. `access_method` enum.
- **`epistemic-grounding`** — claim-to-evidence grounding, confidence caps, and failure routing for weak or adjacent evidence.
- **`shell-safety`** — safe command construction, validation helpers, and destructive-operation probe rules.
- **`osint`** — tool routing table + 150-tool catalog + OSINT Navigator integration.
- **`investigation-methodology`** — step-by-step techniques (geolocation, person, platform, verification, transport).
- **`follow-the-money`** — financial methodology (UBO, offshore, budgets, assets, public blockchain tracing).
- **`social-media-intelligence`** — account authenticity, coordination detection, narrative tracking.
- **`technical-investigation`** — passive technical indicators and infrastructure history, local document/email metadata, public GitHub history, and verified-indicator export.

See [technical-investigation.md](technical-investigation.md) for tier loading,
case fields, verified export, source provenance, and upstream maintenance.

### Per-skill anatomy

```
skills/<id>/
├── SKILL.md           # YAML frontmatter + instructions (how the skill works)
└── references/        # Optional — deep reference content the SKILL.md points to
    └── *.md
```

SKILL.md frontmatter:

```yaml
---
name: <skill-id>
description: <one-line>
version: "1.0"
invocable_by: [orchestrator | investigator | fact-checker | user]
requires: [<other-skill-id>]    # optional
env_vars: [ENV_VAR_1]           # optional
---
```

The body is instructions for the runtime's model: what to do when invoked, which other skills to invoke, which verbs to call, what output to produce.

## agents/ — 2 prompt bundles

Unlike skills (which are invoked), agents are **spawned**. Their markdown files are prompt bundles consumed by `spawn-agent`.

- **`investigator.md`** — two modes: `PLANNING` (writes `methodology.json`) and `EXECUTION` (writes `findings.json` + appends `investigation-log.json`). Iteration limit 80. Loads skills acquisition-graduation, osint, investigation-methodology, follow-the-money, web-archiving, content-access, epistemic-grounding, shell-safety, social-media-intelligence, and technical-investigation.
- **`fact-checker.md`** — SIFT methodology, verdict taxonomy, independent from investigator. Iteration limit 50. Loads skills osint, web-archiving, content-access, epistemic-grounding, shell-safety, and technical-investigation. Cannot `spawn-agent` (no recursive spawning).

Frontmatter declares `allowed_verbs`, `preferred_model` (per-runtime mapping), `vault_context` (whether to query the vault before research).

## monitoring/ — case registry helpers

Spotlight no longer ships a passive feed engine or a durable-monitor backend.
It keeps only investigation-scoped recommendations and may hand them to
Mycroft after explicit user approval.

```
monitoring/
├── registry.py               # CLI helper for {CASE_DIR}/data/monitoring.json
├── alerts/                   # Optional local alert artifacts / future hooks
└── leads/                    # Scraping queue (monitor → case handoff)
```

`registry.py` owns the local `monitoring.json` shape:

- initialize a v2 external-monitor registry for a case
- normalize or migrate legacy feed-oriented `monitoring.json`
- record linked Mycroft topic slugs
- record the user's recommendation decision and any Mycroft handoff

See [monitoring.md](monitoring.md) for the lifecycle and registry fields.

## cases/ — investigation output

Every investigation creates an isolated working directory. Gitignored. This is
not the knowledge vault. Spotlight queries the vault when a case starts and
ingests verified material into the vault only after an explicit end-of-case
decision.

```
{CASE_DIR}/
├── brief-directions.txt      # User-approved brief (Phase 1)
├── summary.md                # Investigation gate summary (markdown, human-readable)
├── data/
│   ├── methodology.json
│   ├── findings.json
│   ├── fact-check.json
│   ├── source-expressions.json  # pilot or activated cases
│   ├── source-positions.json    # actor positions per finding (same writer)
│   ├── case-contract.json       # activated cases only
│   ├── source-expression-migration.json # migration audit only
│   ├── investigation-log.json
│   ├── summary.json
│   ├── anomaly-signals.json  # optional: Anomaly detector runs and signals (leads, never findings)
│   └── monitoring.json       # optional external-monitor registry
└── research/
    ├── *.md                  # Scraped web content
    ├── *.json                # Search results
    └── archived/             # Wayback / Archive.today preservation
```

<!-- research-phase case files: start. Written for the Research phase; the
     knowledge-model docs may fold these entries into the tree above. -->
### Research-phase case files

Only a case whose approved methodology has a `research_plan` has these.

```
{CASE_DIR}/
├── data/
│   ├── leads.json                # merged leads, gaps and topics left out (research-leads schema)
│   ├── clues.json                # clue rules K1-K6 and method signals (research-clues schema)
│   ├── research-triage.json      # triage flags and answers per lead
│   ├── research-suggestions.json # the investigator's suggested marks
│   ├── research-review.json      # your marks, saved by the Research page
│   ├── methodology-amendment.json # the change your marks produce, checked by D8
│   └── research-round.json       # a stalled investigation's round topics
└── research/
    ├── rounds.json               # per round: mode, budget, spend, ran, left out, stopped
    └── swarm/<topic-id>/         # one folder per approved topic, owner-only
        ├── lane.json             # lane status and budget (research-lane schema)
        ├── leads.jsonl           # one lead per line (research-lead schema)
        └── sources/              # the pages the lane captured
```

The Research page is served by the local review server; it is not a file in
the case. The fact-checker cannot read `research/swarm/`, `data/leads.json`,
`data/clues.json` or any `data/research-*.json`.
<!-- research-phase case files: end -->

## .spotlight-config.json

Per-machine config created during Phase 0. Fields:

```json
{
  "search_library": "firecrawl",
  "vault_path": "/Users/you/Documents/intelligence/",
  "knowledge_destination": {
    "type": "openknowledge|markdown|obsidian_legacy|none",
    "workspace_path": "/Users/you/Documents/OpenKnowledge",
    "namespace": "spotlight",
    "mode": "solo|newsroom"
  },
  "case_workspace_root": "/Users/you/Documents/Spotlight/cases",
  "cases_root": "/Users/you/Documents/Spotlight/cases",
  "integrations": {
    "osint_navigator": {
      "status": "unknown",
      "checked_at": "<ISO timestamp>",
      "source": "setup",
      "required_in_phase_2": false,
      "reason": "preflight not run yet"
    }
  },
  "created_at": "ISO 8601",
  "last_used": "ISO 8601",
  "active_project": "<slug>"
}
```

This file is gitignored — each user's config is local.

## How to extend

| Extension | Where | What to write |
|---|---|---|
| New skill | `skills/<new-id>/SKILL.md` | YAML frontmatter + body. Add row to `AGENTS.md` skill registry |
| New monitoring backend | `integrations/<new-id>/` or Mycroft | Integration manifest + usage doc, or Mycroft passive-feed update. Update `skills/monitoring/` references accordingly |
| New schema | `schemas/<new>.schema.json` | Draft-07 JSON Schema with `schema_version: "1.0"`. Update `AGENTS.md` schema reference |
| New runtime adapter | `docs/integrations.md` | New section with verb mapping, sub-agent strategy, sensitive-mode enforcement |
| New agent | `agents/<new-id>.md` | YAML frontmatter with `allowed_verbs`, `iteration_limit`, `preferred_model`. Add to `AGENTS.md` agent manifest. Consider: does this agent need a corresponding agent-support skill? |

Changes to the 13-verb registry are breaking and require bumping `runtime_version` in `AGENTS.md`. All other extensions are additive.

## See Also

- [epistemic-grounding.md](epistemic-grounding.md) — claim-to-evidence grounding and evidence bundles.
- [vulnerabilities.md](vulnerabilities.md) — shell-safety risk model and mitigations.
