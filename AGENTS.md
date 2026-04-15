---
name: spotlight
description: Runtime contract for the Spotlight OSINT investigation system
version: "1.0"
runtime_version: "1"
sensitive: false
---

# Spotlight — Runtime Contract

This file defines the portable contract for the Spotlight investigation system. Any runtime (Claude Code, Hermes, Gemini, GPT) that implements the tool verb registry and agent manifest bindings can run Spotlight investigations.

## Tool Verb Registry

Fixed vocabulary of abstract operations. Every runtime adapter MUST implement all verbs. If a verb is unsupported, the adapter raises an explicit error at load time.

| Verb | Signature | Semantics |
|------|-----------|-----------|
| `fetch` | `fetch(url, output_path)` | Scrape URL content, save to file |
| `search` | `search(query, output_path, limit)` | Web search, save results to file |
| `read-file` | `read-file(path)` | Read file contents |
| `write-file` | `write-file(path, content)` | Write file (full overwrite) |
| `edit-file` | `edit-file(path, old, new)` | Targeted string replacement |
| `list-files` | `list-files(pattern)` | Glob/search for files matching pattern |
| `grep-files` | `grep-files(pattern, path)` | Search file contents by regex |
| `execute-shell` | `execute-shell(command)` | Run shell command, return stdout + stderr |
| `spawn-agent` | `spawn-agent(agent_id, prompt, config)` | Launch sub-agent with prompt and config |
| `wait-agent` | `wait-agent(handle)` | Block until agent completes, return output |
| `invoke-skill` | `invoke-skill(skill_id)` | Load skill instructions into current context |
| `query-vault` | `query-vault(vault_path, query)` | Search knowledge vault for context |
| `vault-write` | `vault-write(vault_path, note_path, content)` | Write note to vault and update registry |

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
  - list-files
  - grep-files
  - invoke-skill
  - query-vault
  - execute-shell
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  fallback_note: Investigation quality degrades significantly on lighter models
vault_context:
  enabled: true
  query_on_load: true
```

The investigator operates in two modes:

- **PLANNING** — Analyzes the brief, queries the vault for prior work, designs methodology, writes `cases/{project}/data/methodology.json`
- **EXECUTION** — Follows approved methodology, executes research using `fetch` and `search`, writes `cases/{project}/data/findings.json` and appends to `cases/{project}/data/investigation-log.json`

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
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  fallback_note: Fact-checking accuracy degrades significantly on lighter models
vault_context:
  enabled: true
  query_on_load: true
```

The fact-checker operates independently from the investigator. It reads `cases/{project}/data/findings.json`, conducts its own verification research, and writes `cases/{project}/data/fact-check.json` with per-claim verdicts and evidence trails.

**Verdict taxonomy:** verified | unverified | disputed | false

## Skill Registry

Skills are markdown playbooks loaded via `invoke-skill(skill_id)`. Each skill lives in `skills/{skill_id}/SKILL.md` with optional reference files in `skills/{skill_id}/references/`.

| Skill ID | Path | Description | Invocable By |
|----------|------|-------------|--------------|
| `spotlight` | `skills/spotlight/SKILL.md` | Investigation orchestrator — pipeline phases, gates, cycle evaluation | orchestrator (top-level) |
| `ingest` | `skills/ingest/SKILL.md` | Knowledge archival — vault ingestion from case files | orchestrator |
| `monitoring` | `skills/monitoring/SKILL.md` | Feed framework integration — coJournalist scouts, GDELT, RSS, GDACS | orchestrator |
| `web-archiving` | `skills/web-archiving/SKILL.md` | Wayback Machine, Archive.today, local archival | investigator, fact-checker |
| `content-access` | `skills/content-access/SKILL.md` | Paywall bypass hierarchy, access method classification | investigator, fact-checker |
| `osint` | `skills/osint/SKILL.md` | OSINT tool routing and technique reference | investigator |

## Sensitive Mode

When `sensitive: true` is set in this manifest (or toggled at runtime), the adapter MUST strip `fetch` and `search` from all agent `allowed_verbs` lists. All research becomes local-only — agents can only use `read-file`, `grep-files`, `list-files`, and `query-vault` for information gathering.

To activate: set `sensitive: true` in this file or issue a runtime command.
To deactivate: set `sensitive: false` or issue a runtime command.

## Cases Directory Structure

Each investigation creates an isolated directory under `cases/`:

```
cases/{project}/
├── data/
│   ├── findings.json           # Schema: schemas/findings.schema.json
│   ├── fact-check.json         # Schema: schemas/fact-check.schema.json
│   ├── methodology.json        # Schema: schemas/methodology.schema.json
│   ├── investigation-log.json  # Schema: schemas/investigation-log.schema.json
│   └── summary.json            # Schema: schemas/summary.schema.json
└── research/
    ├── *.md                    # Scraped web content
    ├── *.json                  # Search results
    └── media/                  # Images, PDFs, other media
```

All schemas are in `schemas/` at the repo root with `schema_version: "1.0"`.

## Schema Reference

| Schema | Path | Purpose |
|--------|------|---------|
| Findings | `schemas/findings.schema.json` | Investigation findings with sources, confidence, connections |
| Fact-Check | `schemas/fact-check.schema.json` | Per-claim verdicts with evidence trails |
| Methodology | `schemas/methodology.schema.json` | Investigation plan with directions, steps, tools |
| Investigation Log | `schemas/investigation-log.schema.json` | Append-only cycle audit trail |
| Summary | `schemas/summary.schema.json` | Gate 1 summary for review |
