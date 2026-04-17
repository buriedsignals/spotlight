---
name: ingest
description: Archive investigation findings into an Obsidian vault (or directory) as structured knowledge — entity notes, methodology notes, tool notes, with registries and wikilinks. Works standalone or as part of the Spotlight pipeline.
version: "1.0"
invocable_by: [orchestrator, user]
requires: []
---

# Ingest — Knowledge Archival

You are archiving confirmed investigation findings into a structured knowledge base.

This skill instructs. You — the host runtime — execute. You read investigation files, write vault notes, update registries, and maintain the knowledge graph. The user sees the result; you do the work.

Two input modes:

- **Pipeline mode** — invoked by Spotlight after Gate 1 approval. Project path and vault config are already known.
- **Standalone mode** — invoked directly. You gather inputs interactively.

---

## Input Mode Detection

### Mode A — From Spotlight Pipeline

The orchestrator passes project path and vault config (from `.spotlight-config.json`). All inputs are known:

- `vault_path` — target vault or directory
- `vault_type` — `"obsidian"` or `"directory"`
- `project` — project slug

Read these case files:

```
cases/{project}/data/findings.json
cases/{project}/data/fact-check.json
cases/{project}/data/investigation-log.json
cases/{project}/data/summary.json
```

Skip to the Ingestion Process.

### Mode B — Standalone

The user requests ingestion directly.

**Step 1 — Findings source:**

> "Point me to your findings file (JSON with claims, sources, and evidence)."

`read-file(<path>)`. Validate it contains a `findings` array where entries have `sources`. If the structure is wrong:

> "This file doesn't match the expected format. I need a JSON file with a `findings` array where each finding has `claim`, `sources`, and `evidence` fields."

**STOP.**

**Step 2 — Vault target:**

> "Which vault or directory should I archive to?"

Check if the path contains `.obsidian/`:

```
list-files("{path}/.obsidian")
```

- Found: `vault_type = "obsidian"` — wikilinks enabled.
- Not found: `vault_type = "directory"` — relative markdown links.

**Step 3 — Supplementary files:**

Check whether these exist alongside the findings file. Use whatever is available; do not require all of them:

- `fact-check.json` — verdict annotations
- `investigation-log.json` — methodology, tools, search queries
- `summary.json` — overview and conclusions

Proceed to the Ingestion Process with whatever files were found.

---

## Concurrency Lock

Before starting the ingestion process, check for a lock file:

```
list-files("{vault}/.ingest-lock")
```

**If present:**

> "Another ingestion is in progress. Wait for it to complete before running again."

**STOP.** Do not proceed.

**If absent:**

Create the lock:

```
write-file("{vault}/.ingest-lock", "{project-id} {ISO timestamp}")
```

Remove the lock when ingestion completes — whether successful or failed. Always clean up:

```
execute-shell("rm {vault}/.ingest-lock")
```

If the process errors partway through, remove the lock before reporting the error.

---

## Ingestion Process

Seven steps. Execute in order. Do not skip steps.

### Step 1 — Read Current Vault State

Read all registry files:

```
read-file("{vault}/_registry.json")
read-file("{vault}/investigations/_registry.json")
read-file("{vault}/entities/_registry.json")
read-file("{vault}/methodology/_registry.json")
read-file("{vault}/tools/_registry.json")
```

If the vault is empty (registries do not exist), initialize each with the empty schema from `references/registry-spec.md` and `schema_version: "1.0"`. Create the directories:

```
{vault}/investigations/
{vault}/entities/
{vault}/methodology/
{vault}/tools/
```

### Step 2 — Create Investigation Note

`write-file("{vault}/investigations/{project-id}.md", ...)`.

**Frontmatter** — per `references/entity-model.md` Investigation Note schema:

```yaml
---
id: {project-id}
title: {from summary.json title, or derive from findings}
status: confirmed
date: {today YYYY-MM-DD}
regions: [{from findings}]
entities: [{entity IDs extracted in Step 3}]
methodology: [{technique IDs extracted in Step 4}]
tools: [{tool IDs extracted in Step 5}]
tags: [{derived from findings topics}]
verified_count: {count of high-confidence verified findings}
total_findings: {total findings count}
---
```

**Body:**

1. **Summary** — from `summary.json` `overview`. If no summary.json, synthesize from findings.
2. **Key Findings** — one section per finding from `findings.json`:
   - **Claim** — the finding's claim
   - **Confidence** — high / medium / low
   - **Verdict** — from `fact-check.json` matching claim. If verdict is `disputed` or `false`, flag prominently: `> **DISPUTED** — {reason}` or `> **FALSE** — {reason}`
   - **Evidence** — supporting evidence (verbatim quote)
   - **Sources** — with URLs and access timestamps
   - **Perspective** — whose perspective this represents
3. **Connections** — wikilinked entities: `[[entity-id]]` for each entity involved.
4. **Gaps** — unresolved questions, noted limitations.
5. **Methodology Applied** — techniques and tools used, wikilinked: `[[technique-id]]`, `[[tool-id]]`.

### Step 3 — Create or Update Entity Notes

Extract entities from:

- `findings.json` — `connections[].from` and `connections[].to`
- `findings.json` — named entities in `findings[].claim` (apply basic NER: proper nouns, organization names, geographic names)

Infer entity type:

| Pattern | Type |
|---------|------|
| Person names (first + last) | `person` |
| Known organization patterns (UN, EU, ministry, commission, etc.) | `organization` |
| Company indicators (Inc, Ltd, GmbH, AG, SA, etc.) | `company` |
| Geographic names (countries, cities, regions) | `place` |

Generate kebab-case ID from entity name.

**If entity exists** in `{vault}/entities/_registry.json` (match on `id`):

- `read-file("{vault}/entities/{entity-id}.md")`
- Add a row to the "Role in Investigations" table: `| [[{project-id}]] | {role description} | {date} |`
- Add `{project-id}` to frontmatter `investigations` array (if not already present)
- `write-file` the updated note

**If entity is new:**

`write-file("{vault}/entities/{entity-id}.md", ...)` per `references/entity-model.md`:

```yaml
---
id: {entity-id}
type: {inferred type}
subtype: {if determinable, else omit}
aliases: [{alternate names found in findings}]
country: {if determinable}
region: {if determinable}
investigations: [{project-id}]
first_seen: {today YYYY-MM-DD}
---
```

Body: Description, Role in Investigations table (one row for this project), Key Relationships (wikilinks to other entities from same investigation).

### Step 4 — Create or Update Methodology Notes

Extract techniques from `investigation-log.json`:

- `cycles[].methodology.techniques_used`

**If technique exists** in `{vault}/methodology/_registry.json`:

- `read-file` the existing note
- Add a row to "Usage History" table: `| [[{project-id}]] | {context} | {date} |`
- Add lessons from `cycles[].methodology.failed_approaches` to "Lessons Learned" section
- Add `{project-id}` to frontmatter `investigations` array
- `write-file` the updated note

**If technique is new:**

`write-file("{vault}/methodology/{technique-id}.md", ...)` per `references/entity-model.md`:

```yaml
---
id: {technique-id}
type: technique
category: {infer from technique name}
tools: [{tool IDs used with this technique}]
investigations: [{project-id}]
---
```

Body: Description, Steps (if inferable from log), Tools (wikilinked), Usage History table, Lessons Learned.

### Step 5 — Create or Update Tool Notes

Extract tools from `investigation-log.json`:

- `cycles[].methodology.tools_used`

**If tool exists** in `{vault}/tools/_registry.json`:

- `read-file` the existing note
- Add a row to "Usage History" table (max 10 entries, most recent first — remove oldest if at limit)
- Increment `usage_count` in frontmatter
- Read existing "Tips for Future Agents" — add genuinely novel tips from `cycles[].methodology.search_queries` only if they are not duplicates of existing advice
- Add `{project-id}` to frontmatter `investigations` array
- `write-file` the updated note

**If tool is new:**

`write-file("{vault}/tools/{tool-id}.md", ...)` per `references/entity-model.md`:

```yaml
---
id: {tool-id}
type: tool
category: {infer from tool name}
url: {if known}
access: {if known, else omit}
methodology: [{technique IDs that use this tool}]
investigations: [{project-id}]
usage_count: 1
---
```

Body: Capabilities, Access Notes, Usage History table (one row), Tips for Future Agents (from search queries if useful).

### Step 6 — Update ALL Registries

This is mandatory. Update every registry affected by the ingestion.

- **`{vault}/investigations/_registry.json`** — add or update the investigation entry.
- **`{vault}/entities/_registry.json`** — add new entities, update `investigations` arrays for existing ones.
- **`{vault}/methodology/_registry.json`** — add new techniques, update `investigations` arrays for existing ones.
- **`{vault}/tools/_registry.json`** — add new tools, update `investigations` and `usage_count` for existing ones.
- **`{vault}/_registry.json`** (master) — update `stats` counts and `last_updated` to current ISO 8601 timestamp.

See `references/registry-spec.md` for exact schemas.

### Step 7 — Update _INDEX.md

`write-file("{vault}/_INDEX.md", ...)` using the template from `references/registry-spec.md`.

- Stats from master registry
- Recent Investigations table from investigations registry (sorted by date, newest first)
- Browse links

For Obsidian vaults: use wikilinks in the investigations table (`[[project-id]]`).
For directory fallback: use relative links (`[project-id](investigations/project-id.md)`).

After Step 7 completes, remove the `.ingest-lock`.

---

## Directory Fallback

When `vault_type` is `"directory"` (no `.obsidian/` detected):

- Same directory structure, same frontmatter, same body sections.
- Replace all wikilinks `[[entity-id]]` with relative markdown links `[entity-id](../entities/entity-id.md)`.
- Replace all wikilinks `[[project-id]]` with `[project-id](../investigations/project-id.md)`.
- Replace all wikilinks `[[technique-id]]` with `[technique-id](../methodology/technique-id.md)`.
- Replace all wikilinks `[[tool-id]]` with `[tool-id](../tools/tool-id.md)`.
- `_INDEX.md` browse section uses relative links too.

Frontmatter and registry JSON are identical regardless of vault type.

---

## Hard Rules

1. **Registry updates are atomic with note creation.** Never create a note without updating its registry. Never update a registry without the note existing.
2. **No duplicates.** Check registries before creating. Match on `id`. If it exists, update it.
3. **Tips are curated.** Read existing tips before adding new ones. Only add genuinely novel insights — not rephrased duplicates.
4. **Frontmatter is the contract.** Every note must have complete frontmatter per `references/entity-model.md`. Agents rely on it programmatically. Never omit or rename fields.
5. **Wikilinks create the graph.** Use `[[entity-id]]` format in Obsidian vaults for all cross-references.
6. **IDs are kebab-case.** Lowercase, hyphens, no spaces. Examples: `swiss-leaks`, `john-doe`, `reverse-image-search`.
7. **Only confirmed knowledge enters.** No speculative findings, no in-progress research. Low-confidence claims must be explicitly flagged with `> **LOW CONFIDENCE** — {reason}` if included at all.

---

## Vault-Write Verb

For runtimes that provide a native `vault-write(vault_path, note_path, content)` verb, prefer it over raw `write-file`. The `vault-write` verb should handle:

- Obsidian-specific formatting (wikilinks, frontmatter validation)
- Registry update atomicity
- `.ingest-lock` coordination

If the adapter doesn't implement `vault-write`, fall back to the per-step `write-file` + `read-file` pattern described above.

---

## File Locations

```
Reads from:
  cases/{project}/data/findings.json
  cases/{project}/data/fact-check.json
  cases/{project}/data/investigation-log.json
  cases/{project}/data/summary.json
  {vault}/_registry.json
  {vault}/investigations/_registry.json
  {vault}/entities/_registry.json
  {vault}/methodology/_registry.json
  {vault}/tools/_registry.json

Writes to:
  {vault}/investigations/{project-id}.md
  {vault}/entities/{entity-id}.md          (per entity)
  {vault}/methodology/{technique-id}.md    (per technique)
  {vault}/tools/{tool-id}.md               (per tool)
  {vault}/investigations/_registry.json
  {vault}/entities/_registry.json
  {vault}/methodology/_registry.json
  {vault}/tools/_registry.json
  {vault}/_registry.json                   (master)
  {vault}/_INDEX.md
```
