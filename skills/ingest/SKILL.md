---
name: ingest
description: Knowledge archival skill — decomposes Spotlight case files into vault notes, updates registries, and generates the knowledge base index
version: "1.0"
invocable_by: [orchestrator, user]
requires: []
---

# Ingest — Knowledge Archival

The ingest skill transforms completed Spotlight investigations into permanent knowledge in the vault. It decomposes case files into structured notes, maintains registries, and keeps the knowledge base index current.

---

## When to Invoke

| Trigger | Source | Action |
|---------|--------|--------|
| Gate 1 reached | Orchestrator calls ingest after investigation advances | Archive full case |
| Investigation closed | User requests archival of partial investigation | Archive available findings |
| Monitoring requested | `monitoring_recommendations[]` in findings | Create monitoring stubs |

---

## Input Files

Ingest reads these files from `cases/{project}/data/`:

| File | Required | Purpose |
|------|----------|---------|
| `findings.json` | Yes | Claims, entities, connections, gaps |
| `fact-check.json` | Yes | Per-claim verdicts, evidence trails |
| `summary.json` | No | Title, scope, conclusions (Gate 1 only) |
| `investigation-log.json` | Yes | Methodology, tools, cycle history |
| `methodology.json` | No | Planned vs. applied methodology |

---

## Vault Structure

```
{vault}/
├── _registry.json              # Master registry
├── _INDEX.md                   # Knowledge base index
├── investigations/
│   ├── _registry.json
│   └── {project-id}.md        # One note per investigation
├── entities/
│   ├── _registry.json
│   └── {entity-id}.md         # One note per entity
├── methodology/
│   ├── _registry.json
│   └── {technique-id}.md      # One note per technique
└── tools/
    ├── _registry.json
    └── {tool-id}.md            # One note per tool
```

---

## Decomposition Process

### 1. Pre-flight Checks

- Verify all required input files exist and are valid JSON
- Check `{vault}/investigations/_registry.json` for existing `id` — if found, this is an update, not a new case
- Read existing notes only when updating; for new cases, skip to creation

### 2. Entity Extraction

Extract entities from multiple sources:

| Source | Entity Type | ID Strategy |
|--------|-------------|-------------|
| `findings.json` `connections[].from/to` | Named entities | Use connection label as id or NER |
| `findings.json` `findings[].claim` | Named entities (NER) | Detect person/org/company/location |
| `findings.json` `findings[].perspective` | Affected person | Create if not already tracked |

**For each entity:**
1. Check `{vault}/entities/_registry.json` for existing `id`
2. If new: create `{vault}/entities/{id}.md` per entity-model.md template
3. If exists: append new investigation to `investigations[]` array in frontmatter
4. Update `{vault}/entities/_registry.json`

### 3. Methodology Extraction

Extract techniques from `investigation-log.json`:

```json
{
  "cycles": [{
    "methodology": {
      "techniques_used": ["technique-name-1"],
      "failed_approaches": [{"approach": "...", "lesson": "..."}]
    }
  }]
}
```

**For each technique:**
1. Check `{vault}/methodology/_registry.json` for existing `id` (kebab-case of technique name)
2. If new: create `{vault}/methodology/{id}.md` per entity-model.md template
3. If exists: append investigation to `investigations[]`, merge lessons
4. Update `{vault}/methodology/_registry.json`

### 4. Tool Extraction

Extract tools from `investigation-log.json`:

```json
{
  "cycles": [{
    "methodology": {
      "tools_used": ["tool-name-1"],
      "search_queries": [{"query": "...", "useful": true}]
    }
  }]
}
```

**For each tool:**
1. Check `{vault}/tools/_registry.json` for existing `id`
2. If new: create `{vault}/tools/{id}.md` per entity-model.md template
3. If exists: increment `usage_count`, append to usage history
4. Add search query tips if `useful: true`
5. Update `{vault}/tools/_registry.json`

### 5. Investigation Note Creation

Create `{vault}/investigations/{project-id}.md`:

**Frontmatter:**
```yaml
---
id: {project-id}
title: {from summary.json or derive from targets}
status: confirmed|unconfirmed|debunked
date: {YYYY-MM-DD from summary.json or today}
regions: [{list from findings}]
entities: [{entity-id-1, entity-id-2}]
methodology: [{technique-id-1, technique-id-2}]
tools: [{tool-id-1, tool-id-2}]
tags: [{tags from findings}]
verified_count: {N from fact-check verdicts}
total_findings: {M from findings.json}
---
```

**Body sections (per entity-model.md):**

1. **Summary** — From `summary.json` if available, else derive from high-confidence findings
2. **Key Findings** — One subsection per finding:
   - Claim (verbatim from `findings.json`)
   - Confidence (high/medium/low)
   - Verdict (from `fact-check.json` matching `claims[]`)
   - Evidence (from `fact-check.json` `evidence_for` or local file paths)
   - Sources (with wikilinks to entity notes where applicable)
   - Perspective (from `findings.json` `perspective`)
3. **Connections** — Table of entity wikilinks with relationship context
4. **Gaps** — Open questions from `findings.json` `gaps[]`
5. **Methodology Applied** — Techniques and tools with wikilinks

### 6. Registry Updates

After creating/updating all notes:

1. **Update `{vault}/investigations/_registry.json`** — Add or update investigation entry
2. **Update `{vault}/entities/_registry.json`** — Add new entities, update existing
3. **Update `{vault}/methodology/_registry.json`** — Add new techniques
4. **Update `{vault}/tools/_registry.json`** — Add new tools, increment usage counts
5. **Update `{vault}/_registry.json`** — Recalculate stats

### 7. Index Regeneration

Update `{vault}/_INDEX.md` with current stats and recent investigations.

---

## Hard Rules

1. **Atomic writes.** Create all notes first, then update all registries. If interrupted, re-run ingest to complete.
2. **No duplicate IDs.** Always check registries before creating notes. Match on `id` field.
3. **Verified only.** Only confirmed/unconfirmed/debunked findings enter the vault. Do not archive disputed claims without resolution.
4. **Frontmatter is the contract.** Do not omit or rename frontmatter fields — downstream agents parse them programmatically.
5. **Append-only history.** Never delete entries from usage history or investigation lists. Only add new ones.

---

## Error Handling

| Error | Response |
|-------|----------|
| Missing required input file | Abort. Report which file is missing. |
| Invalid JSON in input | Abort. Report file and parse error. |
| Entity ID collision | Skip entity creation. Append investigation to existing entity's list. |
| Vault directory missing | Create vault structure before ingesting. |

---

## Invoking Monitoring

If `findings.json` contains `monitoring_recommendations[]`:

1. Extract each recommendation
2. Create stub entries in `{vault}/monitoring/` (if monitoring skill exists)
3. Flag in investigation note that monitoring is pending activation

---

## Reference

- Entity note template: `skills/ingest/references/entity-model.md`
- Registry schemas: `skills/ingest/references/registry-spec.md`
- Orchestrator reference: `skills/spotlight/SKILL.md`
