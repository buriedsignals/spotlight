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
    "tools": 0,
    "claims": 0
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

### 6. Claims Registry

**Path:** `{vault}/claims/_registry.json`

```json
{
  "schema_version": "1.0",
  "claims": [
    {
      "id": "project-id-f1",
      "project": "project-id",
      "entities": [],
      "verdict": "verified",
      "layer": "durable",
      "recorded": "YYYY-MM-DD",
      "verified": "YYYY-MM-DD",
      "needs_verification": false,
      "file": "claims/project-id-f1.md"
    }
  ]
}
```

Registry entries stay minimal — claim text, evidence, and sources live in the note body. `verdict` is restricted to `verified` and `partially_verified` (the only verdicts that pass the claim eligibility gate in `entity-model.md`); `layer` must be consistent with `verdict` (`verified` → `durable`, `partially_verified` → `lead`).

Source-expression snapshots are embedded in eligible claim notes and are
absent from this registry and the master registry. Their identity is `(project,
expression_id, expression_fingerprint)`; the case-local ingestion receipt holds
the source input hash and written/skipped IDs.

### 7. Alias Index (generated artifact)

**Path:** `{vault}/entities/_aliases.json`

```json
{
  "schema_version": "1.0",
  "generated": "ISO 8601",
  "aliases": {
    "acme ltd": "acme-corp",
    "acme corporation": "acme-corp",
    "j. doe": "john-doe"
  }
}
```

- Keys are **normalized alias strings** (lowercased, trimmed, internal whitespace collapsed) covering every entity's `aliases` list AND its canonical display name; values are entity IDs.
- This is a **derived artifact**: rebuilt in full from entity note frontmatter on every ingest run. Never hand-edit it — entity frontmatter `aliases` is the single source of truth, and the index must always be exactly derivable from it.
- Lookup recipe: normalize the candidate name the same way, try exact match here first; fall back to semantic `query-vault` search only on a miss.

### 8. Merge Proposals (human-gated)

**Path:** `{vault}/entities/_merge-proposals.json`

```json
{
  "schema_version": "1.0",
  "proposals": [
    {
      "id": "merge-0001",
      "entities": ["entity-a", "entity-b"],
      "colliding_alias": "the shared name or alias",
      "source_project": "project-id",
      "proposed": "YYYY-MM-DD",
      "status": "open"
    }
  ]
}
```

- Written when ingest detects that a new entity's name or aliases collide with an existing entity's alias set. Ingest **proceeds with both entities separate** — a proposal is a flag for human review, never an automatic merge.
- `status` is `open`, `accepted`, or `rejected`. Resolved proposals are preserved so a rejected pair is not blindly re-proposed on the next ingest.
- Accepting a proposal is a human-driven edit (consolidate the entity notes, update aliases, re-run ingest registry sync); the schema only records the decision.

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
| findings.json `findings[]` ⋈ fact-check.json `claims[]` (joined on finding ID, eligibility-gated) | Verified claims with verdicts, grounding, sources | Claim notes + claims registry |
| source-expressions.json `expressions[].finding_links[]` ⋈ eligible claims | Exact passages and lifecycle history | Managed block in the claim note + case-local ingestion receipt |
| summary.json | Title, scope, conclusions | Investigation note frontmatter + summary |

---

## Hard Rules

1. **Atomic updates.** Registry updates and note creation happen together — never one without the other.
2. **No duplicates.** Check the relevant registry before creating any note. Match on `id`.
3. **Tips are curated.** Read existing tips before adding new ones. Don't duplicate advice.
4. **Frontmatter is the contract.** Agents rely on frontmatter fields programmatically. Never omit or rename fields.
5. **Only confirmed knowledge enters the vault.** Unverified findings stay in Spotlight case files.
6. **The claims layer is stricter still.** A claim note requires verdict `verified` or `partially_verified`, grounding `confidence_cap` above `low`, at least one source ref, and non-RLM origin (full gate in `entity-model.md`). Excluded findings are logged with their exclusion reason during ingest — filtering is visible, never silent.
7. **Supersession is append-only.** Claim notes are never rewritten by later investigations; re-verification and supersession append dated rows to the claim's Supersession History.
8. **Aliases are derived, merges are human-gated.** `entities/_aliases.json` is rebuilt from entity frontmatter on every ingest and never hand-edited. Alias collisions produce entries in `entities/_merge-proposals.json` for human review; ingest never merges entities on its own.
9. **Expressions have no standalone registry.** The deterministic writer copies them only into eligible claims, preserving inactive snapshots as history and leaving legacy claims unchanged.

---

## index.md Template

Generated at `{vault}/index.md` and updated on every ingestion run.

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
| Claims | {N} |

## Recent Investigations

| Investigation | Date | Findings | Status |
|--------------|------|----------|--------|
| [project-id](investigations/project-id.md) | YYYY-MM-DD | N verified / M total | confirmed |

## Browse

- [Investigations](investigations/)
- [Entities](entities/)
- [Methodology](methodology/)
- [Tools](tools/)
- [Claims](claims/)
```
