# Structure

This doc describes the repo layout: what's where, why, and how the pieces connect. If you want to add a skill, source, schema, or runtime adapter, start here.

## Top-level layout

```
spotlight/
├── AGENTS.md                 # Runtime contract — verb registry, agent manifests, skill registry
├── README.md                 # Humans-first entry doc (quick-start per runtime)
├── setup.html                # Browser-based installer — picks runtime, collects keys, generates install script
├── .spotlight-config.json    # Per-session config (search library, vault path, cases root, runtime)
├── .gitignore
├── schemas/                  # JSON schemas — 5 case files, all schema_version 1.0
├── skills/                   # 11 skills (pi-native SKILL.md format)
├── agents/                   # 2 agent prompt bundles (investigator + fact-checker)
├── integrations/             # External tool integrations (browser-use, Junkipedia, OSINT Navigator)
├── docs/                     # You are here. Operator manual.
├── monitoring/               # Feed framework (ACLED, GDELT, RSS, GDACS)
└── cases/                    # Per-investigation output (gitignored)
```

## AGENTS.md — the runtime contract

`AGENTS.md` is loaded by every runtime at session start. It declares:

1. **13-verb registry** — the abstract tool vocabulary every skill instruction uses
2. **Agent manifests** — `investigator` and `fact-checker` with `allowed_verbs`, `iteration_limit`, `preferred_model`
3. **Skill registry** — 10 skills with IDs, paths, and which agents can invoke them
4. **Cases directory structure** — `cases/{project}/{data,research}/` convention
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
| `query-vault` | `query-vault(vault_path, query)` | Search vault (backing: `qmd query`) |
| `vault-write` | `vault-write(vault_path, note_path, content)` | Write vault note + update registry |

These are **abstract** — the runtime adapter binds each to a concrete tool. See [integrations.md](integrations.md) for per-runtime mappings.

## schemas/ — 5 case files

Every case file validates against a schema. All declare `schema_version: "1.0"`.

| Schema | Case file | Role |
|---|---|---|
| `findings.schema.json` | `cases/{project}/data/findings.json` | Investigator output — claims, evidence, sources, confidence, perspective, monitoring_recommendations |
| `fact-check.schema.json` | `cases/{project}/data/fact-check.json` | Fact-checker output — per-claim verdicts, evidence_for/against, gaps_for_next_cycle |
| `methodology.schema.json` | `cases/{project}/data/methodology.json` | Investigator PLANNING output — investigation_plan, tools_required, opsec_considerations |
| `investigation-log.schema.json` | `cases/{project}/data/investigation-log.json` | Append-only cycle audit trail |
| `summary.schema.json` | `cases/{project}/data/summary.json` | Gate 1 summary |

Validate a case file:

```bash
python3 -m jsonschema -i cases/{project}/data/findings.json schemas/findings.schema.json
```

## skills/ — 11 skills

Each skill is a directory with `SKILL.md` (+ optional `references/*.md` for large supporting content).

### Orchestrator skill

- **`spotlight`** — the investigation orchestrator. Phase 0 preflight → Brief → Methodology → Execution cycles (max 5) → Gate 1 → Ingestion. Invokes investigator and fact-checker as agents.

### Pipeline-support skills (invocable by orchestrator)

- **`review`** — post-Gate-1 HTML review artifact. Renders a self-contained `cases/{project}/review.html` the journalist opens in any browser, submits structured feedback, downloads as JSON. Mode B re-spawns the investigator to process the feedback and regenerates the HTML. No server required.
- **`integrations`** — routing layer for external tool integrations (browser-use, Junkipedia, OSINT Navigator). Reads live preflight status, maps investigation tasks to integrations. See `integrations/` at repo root for manifests + per-integration usage docs.
- **`ingest`** — archival from case files to vault. 7-step process with `.ingest-lock` concurrency and directory fallback.
- **`monitoring`** — feed framework integration. Hooks preflight and recommendation lifecycle.

### Agent-support skills (invocable by investigator / fact-checker)

- **`web-archiving`** — Wayback → Archive.today → local scrape hierarchy. Chain of custody blocks.
- **`content-access`** — 8-step paywall hierarchy. `access_method` enum.
- **`osint`** — tool routing table + 150-tool catalog + OSINT Navigator integration.
- **`investigate`** — step-by-step techniques (geolocation, person, platform, verification, transport).
- **`follow-the-money`** — financial methodology (UBO, offshore, budget, assets).
- **`social-media-intelligence`** — account authenticity, coordination detection, narrative tracking.

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

- **`investigator.md`** — two modes: `PLANNING` (writes `methodology.json`) and `EXECUTION` (writes `findings.json` + appends `investigation-log.json`). Iteration limit 80. Loads skills osint, investigate, follow-the-money, web-archiving, content-access, social-media-intelligence.
- **`fact-checker.md`** — SIFT methodology, verdict taxonomy, independent from investigator. Iteration limit 50. Loads skills osint, web-archiving, content-access. Cannot `spawn-agent` (no recursive spawning).

Frontmatter declares `allowed_verbs`, `preferred_model` (per-runtime mapping), `vault_context` (whether to query the vault before research).

## monitoring/ — feed framework

Pluggable feed sources for persistent investigation monitoring. Each source is a drop-in directory.

```
monitoring/
├── feeds/
│   ├── monitor.py            # CLI: list / query / check-all
│   ├── preflight.py          # Env-var + smoke-test checker
│   ├── _shared.py            # Shared utilities (topic loading, scoring, dedup)
│   └── sources/
│       ├── gdelt/            # GDELT Document API
│       ├── rss_investigative/ # Bellingcat, ICIJ, The Intercept, Crisis Group
│       ├── rss_regional/     # 17 regional feeds
│       ├── gdacs/            # Disaster alerts
│       └── acled/            # Armed conflict events (requires key)
└── leads/                    # Scraping queue (feed → case handoff)
```

Per-source layout:

```
sources/<id>/
├── manifest.json      # Metadata + env_vars contract
└── fetch.py           # Exposes fetch(query, topics, since) -> list[signal]
```

Manifest contract:

```json
{
  "id": "<source_id>",
  "name": "...",
  "description": "...",
  "category": "news|conflict|disasters|regulatory|...",
  "regions": [],
  "requires_key": true|false,
  "env_vars": ["ENV_VAR_1"],
  "rate_limit_note": "...",
  "default_since": "24h"
}
```

Signal shape returned by `fetch()`:

```json
{
  "title": "...",
  "url": "...",
  "source_name": "...",
  "source_domain": "...",
  "date": "ISO 8601",
  "summary": "...",
  "category": "...",
  "relevance_score": 0,
  "matched_keywords": [],
  "language": "eng"
}
```

See [monitoring.md](monitoring.md) for the full lifecycle.

## cases/ — investigation output

Every investigation creates an isolated directory. Gitignored.

```
cases/{project}/
├── brief-directions.txt      # User-approved brief (Phase 1)
├── summary.md                # Gate 1 summary (markdown, human-readable)
├── data/
│   ├── methodology.json
│   ├── findings.json
│   ├── fact-check.json
│   ├── investigation-log.json
│   ├── summary.json
│   └── monitoring.json       # optional
└── research/
    ├── *.md                  # Scraped web content
    ├── *.json                # Search results
    └── archived/             # Wayback / Archive.today preservation
```

## .spotlight-config.json

Per-machine config created during Phase 0. Fields:

```json
{
  "search_library": "firecrawl",
  "vault_path": "/Users/you/Documents/intelligence/",
  "vault_type": "obsidian|directory",
  "cases_root": "cases/",
  "integrations": {
    "osint_navigator": true|false
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
| New feed source | `monitoring/feeds/sources/<new-id>/` | `manifest.json` + `fetch.py`. Update `skills/monitoring/references/source-catalog.md` |
| New schema | `schemas/<new>.schema.json` | Draft-07 JSON Schema with `schema_version: "1.0"`. Update `AGENTS.md` schema reference |
| New runtime adapter | `docs/integrations.md` | New section with verb mapping, sub-agent strategy, sensitive-mode enforcement |
| New agent | `agents/<new-id>.md` | YAML frontmatter with `allowed_verbs`, `iteration_limit`, `preferred_model`. Add to `AGENTS.md` agent manifest. Consider: does this agent need a corresponding agent-support skill? |

Changes to the 13-verb registry are breaking and require bumping `runtime_version` in `AGENTS.md`. All other extensions are additive.
