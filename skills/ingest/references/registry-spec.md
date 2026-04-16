# Registry Spec

Registry JSON schemas, decomposition logic, and hard rules for vault ingestion.

---

## Registry Schemas

All registries use `schema_version: "1.0"`.

### 1. Master Registry

**Path:** `{vault}/_registry.json`

```json
{
  "schema_version": "1.0",
  "stats": {
    "investigations": 0,
    "entities": 0,
    "methodology": 0,
    "tools": 0
  },
  "last_updated": "ISO 8601"
}
```

### 2. Investigations Registry

**Path:** `{vault}/investigations/_registry.json`

```json
{
  "schema_version": "1.0",
  "investigations": [
    {
      "id": "project-id",
      "title": "Human-readable title",
      "date": "YYYY-MM-DD",
      "status": "confirmed",
      "regions": [],
      "entities": [],
      "verified_count": 0,
      "total_findings": 0
    }
  ]
}
```

### 3. Entities Registry

**Path:** `{vault}/entities/_registry.json`

```json
{
  "schema_version": "1.0",
  "entities": [
    {
      "id": "entity-id",
      "type": "person|organization|company|place",
      "aliases": [],
      "country": "XX",
      "investigations": [],
      "first_seen": "YYYY-MM-DD"
    }
  ]
}
```

### 4. Methodology Registry

**Path:** `{vault}/methodology/_registry.json`

```json
{
  "schema_version": "1.0",
  "methodology": [
    {
      "id": "technique-id",
      "category": "osint-category",
      "tools": [],
      "investigations": []
    }
  ]
}
```

### 5. Tools Registry

**Path:** `{vault}/tools/_registry.json`

```json
{
  "schema_version": "1.0",
  "tools": [
    {
      "id": "tool-id",
      "category": "osint-category",
      "url": "https://...",
      "usage_count": 0,
      "investigations": []
    }
  ]
}
```

---

## Decomposition Logic

How Spotlight case files map to vault notes and registries.

| Source file | Extract | Creates/Updates |
|-------------|---------|-----------------|
| findings.json `connections[].from/to` | Named entities | Entity notes |
| findings.json `findings[].claim` | Named entities (NER) | Entity notes |
| findings.json `findings[]` | Each finding | Investigation note sections |
| investigation-log.json `cycles[].methodology.techniques_used` | Technique names | Methodology notes |
| investigation-log.json `cycles[].methodology.tools_used` | Tool names | Tool notes |
| investigation-log.json `cycles[].methodology.failed_approaches` | Lessons | Methodology "Lessons Learned" |
| investigation-log.json `cycles[].methodology.search_queries` | Useful queries | Tool "Tips" |
| fact-check.json `summary` | Verdict stats | Investigation note frontmatter |
| fact-check.json `claims[]` | Per-claim verdicts | Investigation note annotations |
| summary.json | Title, scope, conclusions | Investigation note frontmatter + summary |

---

## Hard Rules

1. **Atomic updates.** Registry updates and note creation happen together — never one without the other.
2. **No duplicates.** Check the relevant registry before creating any note. Match on `id`.
3. **Tips are curated.** Read existing tips before adding new ones. Don't duplicate advice.
4. **Frontmatter is the contract.** Agents rely on frontmatter fields programmatically. Never omit or rename fields.
5. **Only confirmed knowledge enters the vault.** Unverified findings stay in Spotlight case files.

---

## _INDEX.md Template

Generated at `{vault}/_INDEX.md` and updated on every ingestion run.

```markdown
# Knowledge Base Index

**Last updated:** {ISO 8601}

## Stats

| Type | Count |
|------|-------|
| Investigations | {N} |
| Entities | {N} |
| Methodology | {N} |
| Tools | {N} |

## Recent Investigations

| Investigation | Date | Findings | Status |
|--------------|------|----------|--------|
| [[project-id]] | YYYY-MM-DD | N verified / M total | confirmed |

## Browse

- [Investigations](investigations/)
- [Entities](entities/)
- [Methodology](methodology/)
- [Tools](tools/)
```
