---
name: ingest
description: Archive investigation findings into a Markdown vault (Obsidian, Tolaria, or directory) as structured knowledge — entity notes, methodology notes, tool notes, with registries and wikilinks. Works standalone or as part of the Spotlight pipeline.
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
- `vault_type` — `"obsidian"`, `"tolaria"`, or `"directory"`
- `project` — project slug

At entry, write `{CASE_DIR}/data/ingestion.json` with
`{"schema_version":"1.0","status":"requested"}`. This receipt is case-local even
when every durable output is written to an external vault.

Read these case files:

```
{CASE_DIR}/data/findings.json
{CASE_DIR}/data/fact-check.json
{CASE_DIR}/data/investigation-log.json
{CASE_DIR}/data/summary.json
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
- If the config already says `vault_type = "tolaria"` — keep `tolaria`; use Markdown files, YAML frontmatter, and wikilinks.
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

Remove the lock when ingestion completes — whether successful or failed. Always clean up (these commands follow the `shell-safety` skill's probe → resolve → operate pattern via `scripts/spotlight_safe.py`):

```
execute-shell("python3 scripts/spotlight_safe.py destructive-probe --base {vault} --path .ingest-lock")
execute-shell("python3 scripts/spotlight_safe.py resolve-path --base {vault} --path .ingest-lock")
execute-shell("rm <resolved-lock-path-from-probe>")
```

If the process errors partway through, remove the lock before reporting the error.

---

## Ingestion Process

Eight steps. Execute in order. Do not skip steps.

### Step 1 — Read Current Vault State

Read all registry files:

```
read-file("{vault}/_registry.json")
read-file("{vault}/investigations/_registry.json")
read-file("{vault}/entities/_registry.json")
read-file("{vault}/entities/_aliases.json")
read-file("{vault}/entities/_merge-proposals.json")
read-file("{vault}/methodology/_registry.json")
read-file("{vault}/tools/_registry.json")
read-file("{vault}/claims/_registry.json")
```

If the vault is empty (registries do not exist), initialize each with the empty schema from `references/registry-spec.md` and `schema_version: "1.0"`. A vault created before the claims layer existed simply lacks `claims/_registry.json`, `entities/_aliases.json`, and `entities/_merge-proposals.json` — initialize those with their empty schemas and continue; nothing else about the vault needs migration. Create the directories:

```
{vault}/investigations/
{vault}/entities/
{vault}/methodology/
{vault}/tools/
{vault}/claims/
```

### Step 2 — Create Investigation Note

`write-file("{vault}/investigations/{project-id}.md", ...)`.

**Frontmatter** — per `references/entity-model.md` Investigation Note schema:

```yaml
---
id: {project-id}
type: investigation
title: {from summary.json title, or derive from findings}
description: {one sentence, ~150 chars, summarizing the investigation — retrieval hint}
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
3. **Connections** — linked entities: `[entity-id](../entities/entity-id.md)` for each entity involved.
4. **Gaps** — unresolved questions, noted limitations.
5. **Methodology Applied** — techniques and tools used, linked: `[technique-id](../methodology/technique-id.md)`, `[tool-id](../tools/tool-id.md)`.

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
- Add a row to the "Role in Investigations" table: `| [{project-id}](../investigations/{project-id}.md) | {role description} | {date} |`
- Add `{project-id}` to frontmatter `investigations` array (if not already present)
- `write-file` the updated note

**If entity is new — alias collision check first:**

Normalize the new entity's name and aliases (lowercase, trim, collapse whitespace) and look each up in `entities/_aliases.json`. If any normalized form maps to a *different* existing entity ID, this may be the same real-world entity under another name. Do **not** merge. Append a proposal to `entities/_merge-proposals.json` (per `references/registry-spec.md` — skip if the same pair already has a proposal in any status) and proceed with both entities separate. Then:

`write-file("{vault}/entities/{entity-id}.md", ...)` per `references/entity-model.md`:

```yaml
---
id: {entity-id}
type: {inferred type}
description: {one sentence on who/what this entity is — retrieval hint}
subtype: {if determinable, else omit}
aliases: [{alternate names found in findings}]
country: {if determinable}
region: {if determinable}
investigations: [{project-id}]
first_seen: {today YYYY-MM-DD}
---
```

Body: Description, Role in Investigations table (one row for this project), Key Relationships (relative markdown links to other entities from same investigation).

### Step 4 — Create or Update Methodology Notes

Extract techniques from `investigation-log.json`:

- `cycles[].methodology.techniques_used`

**If technique exists** in `{vault}/methodology/_registry.json`:

- `read-file` the existing note
- Add a row to "Usage History" table: `| [{project-id}](../investigations/{project-id}.md) | {context} | {date} |`
- Add lessons from `cycles[].methodology.failed_approaches` to "Lessons Learned" section
- Add `{project-id}` to frontmatter `investigations` array
- `write-file` the updated note

**If technique is new:**

`write-file("{vault}/methodology/{technique-id}.md", ...)` per `references/entity-model.md`:

```yaml
---
id: {technique-id}
type: technique
description: {one sentence, ~150 chars, on what this technique does — retrieval hint}
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
description: {one sentence, ~150 chars, on what this tool does — retrieval hint}
category: {infer from tool name}
url: {if known}
access: {if known, else omit}
methodology: [{technique IDs that use this tool}]
investigations: [{project-id}]
usage_count: 1
---
```

Body: Capabilities, Access Notes, Usage History table (one row), Tips for Future Agents (from search queries if useful).

### Step 6 — Create or Update Claim Notes

For each finding in `findings.json`, join its matching fact-check entry from `fact-check.json` (on finding ID) and apply the **eligibility gate** from `references/entity-model.md`:

1. Verdict is `verified` or `partially_verified`.
2. Grounding `confidence_cap` is above `low`.
3. At least one source reference is present.
4. The finding is not RLM-derived. Detection rule: a finding is RLM-derived when any of its sources reference `data/rlm-analysis.json` (or an RLM artifact ID), or it carries an `rlm_assisted: true` marker. The investigator contract already forbids copying RLM artifacts into `findings.json`; this check is the defensive backstop, not the primary control.

**Ineligible findings are never written as claims.** Record each exclusion for the ingest summary — finding ID and reason (e.g., `F3: verdict disputed`, `F7: grounding capped low`, `F9: no sources`). Filtering must be visible in the final report, never silent. Excluded findings still appear in the investigation note (Step 2), flagged as before.

**For each eligible finding**, claim ID is `{project-id}-f{n}`:

**If the claim is new** — `write-file("{vault}/claims/{claim-id}.md", ...)` per the Claim Note schema in `references/entity-model.md`:

- `verdict`, `confidence`, `confidence_cap` carried from the finding and fact-check unchanged
- `layer: durable` when verdict is `verified`; `layer: lead` + `needs_verification: true` when `partially_verified`
- `recorded` = today; `verified` = fact-check date; `verified_by` = this project
- `entities` = entity IDs from Step 3 that this finding references
- Body: Claim (verbatim), Evidence Summary, Sources (with access dates), Supersession History (empty table), Connections (relative markdown links to entities and `[{project-id}](../investigations/{project-id}.md)`)

**If the claim already exists** (re-ingest of the same project, or a later project re-verifying the same claim):

- Same project re-ingest: update the note idempotently — identical inputs must produce identical output, no duplicate registry entries.
- Different project re-verifying or superseding: **never rewrite the existing note's claim, evidence, or history.** Append one dated row to the Supersession History table (`re-verified` / `strengthened` / `superseded`), update frontmatter `verified`/`verified_by` to the latest verification, and promote `layer` to `durable` if the new verdict is `verified`. History is append-only.

### Step 7 — Update ALL Registries

This is mandatory. Update every registry affected by the ingestion.

- **`{vault}/investigations/_registry.json`** — add or update the investigation entry.
- **`{vault}/entities/_registry.json`** — add new entities, update `investigations` arrays for existing ones.
- **`{vault}/methodology/_registry.json`** — add new techniques, update `investigations` arrays for existing ones.
- **`{vault}/tools/_registry.json`** — add new tools, update `investigations` and `usage_count` for existing ones.
- **`{vault}/claims/_registry.json`** — add new claims, update `verified`/`layer`/`needs_verification` for re-verified ones. Entries stay minimal per `references/registry-spec.md`.
- **`{vault}/entities/_aliases.json`** — rebuild in full from the frontmatter of every entity note (canonical names + all aliases, normalized). This is a derived artifact; never merge by hand.
- **`{vault}/entities/_merge-proposals.json`** — write any proposals collected in Step 3. Preserve resolved (`accepted`/`rejected`) proposals.
- **`{vault}/_registry.json`** (master) — update `stats` counts (including `claims`) and `last_updated` to current ISO 8601 timestamp.

See `references/registry-spec.md` for exact schemas.

### Step 8 — Update index.md

`write-file("{vault}/index.md", ...)` using the template from `references/registry-spec.md`.

- Stats from master registry
- Recent Investigations table from investigations registry (sorted by date, newest first)
- Browse links

Use relative markdown links in the investigations table (`[project-id](investigations/project-id.md)`).

After Step 8 completes, remove the `.ingest-lock`. Include the claim exclusion log from Step 6 in the ingest summary reported to the user: claims written, claims updated, and each excluded finding with its reason.

In pipeline mode, update `{CASE_DIR}/data/ingestion.json` to
`{"schema_version":"1.0","status":"completed"}` after the lock is removed. If
ingestion fails, write status `failed` before reporting the failure.

---

## Cross-Links

All cross-references use **relative markdown links**, regardless of vault type. Obsidian
resolves these into its graph and backlinks exactly like `[[wikilinks]]` (verified), and
they stay portable to non-Obsidian consumers:

- Entities: `[entity-id](../entities/entity-id.md)`
- Investigations: `[project-id](../investigations/project-id.md)`
- Methodology: `[technique-id](../methodology/technique-id.md)`
- Tools: `[tool-id](../tools/tool-id.md)`
- Claims: `[claim-id](../claims/claim-id.md)`
- `index.md` browse section uses relative links from the vault root (`[project-id](investigations/project-id.md)`).

Frontmatter and registry JSON are identical regardless of vault type.

---

## Hard Rules

1. **Registry updates are atomic with note creation.** Never create a note without updating its registry. Never update a registry without the note existing.
2. **No duplicates.** Check registries before creating. Match on `id`. If it exists, update it.
3. **Tips are curated.** Read existing tips before adding new ones. Only add genuinely novel insights — not rephrased duplicates.
4. **Frontmatter is the contract.** Every note must have complete frontmatter per `references/entity-model.md`. Agents rely on it programmatically. Never omit or rename fields.
5. **Relative markdown links create the graph.** Use `[entity-id](../entities/entity-id.md)` form for all cross-references — Obsidian resolves these into its graph and backlinks, and they stay portable to other consumers.
6. **IDs are kebab-case.** Lowercase, hyphens, no spaces. Examples: `swiss-leaks`, `john-doe`, `reverse-image-search`.
7. **Only confirmed knowledge enters.** No speculative findings, no in-progress research. Low-confidence claims must be explicitly flagged with `> **LOW CONFIDENCE** — {reason}` if included at all.
8. **The claims layer admits verified intelligence only.** Verdict `verified` or `partially_verified`, grounding cap above `low`, sources present, non-RLM origin. Every exclusion is logged with its reason in the ingest summary.
9. **Claim history is append-only.** Re-verification and supersession append dated rows; existing claim content is never rewritten by a later investigation.
10. **Aliases are derived, merges are human-gated.** Rebuild `entities/_aliases.json` from entity frontmatter every run; alias collisions become merge proposals, never automatic merges.

---

## Vault-Write Verb

For runtimes that provide a native `vault-write(vault_path, note_path, content)` verb, prefer it over raw `write-file`. The `vault-write` verb should handle:

- Vault-specific formatting (relative markdown links, frontmatter validation)
- Registry update atomicity
- `.ingest-lock` coordination

If the adapter doesn't implement `vault-write`, fall back to the per-step `write-file` + `read-file` pattern described above.

---

## File Locations

```
Reads from:
  {CASE_DIR}/data/findings.json
  {CASE_DIR}/data/fact-check.json
  {CASE_DIR}/data/investigation-log.json
  {CASE_DIR}/data/summary.json
  {vault}/_registry.json
  {vault}/investigations/_registry.json
  {vault}/entities/_registry.json
  {vault}/entities/_aliases.json
  {vault}/entities/_merge-proposals.json
  {vault}/methodology/_registry.json
  {vault}/tools/_registry.json
  {vault}/claims/_registry.json

Writes to:
  {vault}/investigations/{project-id}.md
  {vault}/entities/{entity-id}.md          (per entity)
  {vault}/methodology/{technique-id}.md    (per technique)
  {vault}/tools/{tool-id}.md               (per tool)
  {vault}/claims/{claim-id}.md             (per eligible finding)
  {vault}/investigations/_registry.json
  {vault}/entities/_registry.json
  {vault}/entities/_aliases.json           (rebuilt, derived)
  {vault}/entities/_merge-proposals.json   (human-gated)
  {vault}/methodology/_registry.json
  {vault}/tools/_registry.json
  {vault}/claims/_registry.json
  {vault}/_registry.json                   (master)
  {vault}/index.md
```
