# Entity Model

Note types, frontmatter contracts, and cross-link conventions for vault ingestion.

---

## Note Types

### 1. Investigation Note

**Path:** `{vault}/investigations/{project-id}.md`

```yaml
---
id: project-id
type: investigation
title: Human-readable title
description: One sentence (~150 chars) summarizing the investigation — retrieval hint
status: confirmed
date: YYYY-MM-DD
regions: [list]
entities: [entity-id-1, entity-id-2]
methodology: [technique-id-1]
tools: [tool-id-1]
tags: [tag1, tag2]
verified_count: N
total_findings: N
---
```

**Body structure:**

1. **Summary** — Brief overview of the investigation and its conclusions.
2. **Key Findings** — One section per finding:
   - Claim
   - Confidence (high / medium / low)
   - Verdict — the fact-check verdict, verbatim: verified / partially_verified / unverified / disputed / false / mischaracterized
   - Evidence
   - Sources
   - Perspective
3. **Connections** — Linked entities (relative markdown links) involved in this investigation.
4. **Gaps** — Open questions and unresolved leads.
5. **Methodology Applied** — Techniques and tools used, with relative markdown links.

---

### 2. Entity Note

**Path:** `{vault}/entities/{entity-id}.md`

```yaml
---
id: entity-id
type: person|organization|company|place
description: One sentence (~150 chars) on who/what this entity is — retrieval hint
subtype: optional-subtype
aliases: [list]
country: XX
region: region-name
investigations: [project-id-1]
first_seen: YYYY-MM-DD
---
```

**Body structure:**

1. **Description** — Who or what this entity is.
2. **Role in Investigations** — Table:

| Investigation | Role | Date |
|---------------|------|------|
| [project-id](../investigations/project-id.md) | description of role | YYYY-MM-DD |

3. **Key Relationships** — Relative markdown links to other entities with relationship context.

---

### 3. Methodology Note

**Path:** `{vault}/methodology/{technique-id}.md`

```yaml
---
id: technique-id
type: technique
description: One sentence (~150 chars) on what this technique does — retrieval hint
category: osint-category
tools: [tool-id-1]
investigations: [project-id-1]
---
```

**Body structure:**

1. **Description** — What this technique does and when to use it.
2. **Steps** — Ordered procedure.
3. **Tools** — Linked tools (relative markdown links) used by this technique.
4. **Usage History** — Table:

| Investigation | Context | Date |
|---------------|---------|------|
| [project-id](../investigations/project-id.md) | how it was applied | YYYY-MM-DD |

5. **Lessons Learned** — What worked, what failed, what to do differently.

---

### 4. Tool Note

**Path:** `{vault}/tools/{tool-id}.md`

```yaml
---
id: tool-id
type: tool
description: One sentence (~150 chars) on what this tool does — retrieval hint
category: osint-category
url: https://...
access: free|freemium|paid|signup-required
methodology: [technique-id-1]
investigations: [project-id-1]
usage_count: N
---
```

**Body structure:**

1. **Capabilities** — What this tool does.
2. **Access Notes** — How to get access, cost, rate limits.
3. **Usage History** — Table (max 10 entries, most recent first):

| Investigation | Context | Date |
|---------------|---------|------|
| [project-id](../investigations/project-id.md) | how it was used | YYYY-MM-DD |

4. **Tips for Future Agents** — Curated advice for effective use.

---

### 5. Claim Note

**Path:** `{vault}/claims/{claim-id}.md` where `claim-id` is `{project-id}-f{n}` (lowercased finding ID of the originating case — e.g. `acme-files-f1`). The claim's identity stays bound to the case that first recorded it; later cases append to it rather than minting a new ID.

```yaml
---
id: acme-files-f1
type: claim
project: acme-files
finding_id: F1
entities: [acme-corp, john-doe]
verdict: verified
confidence: high
confidence_cap: high
layer: durable
recorded: YYYY-MM-DD
verified: YYYY-MM-DD
verified_by: acme-files
needs_verification: false
---
```

**Field semantics:**

- `verdict` — the fact-check verdict, exactly one of the existing taxonomy values that pass the eligibility gate: `verified` or `partially_verified`. Other verdicts never produce claim notes (see eligibility below).
- `confidence` / `confidence_cap` — carried from the finding and its grounding object, unchanged.
- `layer` — derived, never set by hand: `verified` → `durable`; `partially_verified` → `lead`.
- `recorded` — the ingest date.
- `verified` / `verified_by` — fact-check date and the project whose fact-check produced the verdict.
- `needs_verification` — `true` for every `lead`-layer claim; `false` for `durable`.

**Eligibility gate (hard rule).** A finding becomes a claim note only when ALL hold:

1. Fact-check verdict is `verified` or `partially_verified`.
2. Grounding `confidence_cap` is above `low`.
3. At least one source reference is present.
4. The finding is not RLM-derived (RLM artifacts are leads inside a case, never vault knowledge). Detection: sources referencing `data/rlm-analysis.json` or an RLM artifact ID, or an `rlm_assisted: true` marker on the finding.

Findings that fail the gate stay in the investigation note (flagged, as today) and case files. The claims layer is the cross-case queryable surface; it admits verified intelligence only.

**Body structure:**

1. **Claim** — the exact claim text, verbatim from the finding.
2. **Evidence Summary** — brief description of the supporting evidence.
3. **Sources** — list with URLs/refs and access dates, carried from the finding.
4. **Supersession History** — append-only table; a later investigation that re-verifies, strengthens, or supersedes this claim appends a row and never rewrites prior rows:

| Date | Investigation | Event | Verdict |
|------|---------------|-------|---------|
| YYYY-MM-DD | [project-id](../investigations/project-id.md) | re-verified / superseded / strengthened | verified |

5. **Connections** — relative markdown links to `[entity-id](../entities/entity-id.md)`s and the originating `[project-id](../investigations/project-id.md)`.

6. **Source Expressions** — optional deterministic managed block for activated
   `1.1` cases. Snapshots are keyed by `(project, expression_id,
   expression_fingerprint)` and carry the exact passage, anchor/original hashes,
   finding relation, and append-only lifecycle and ingest events. Only
   `scripts/ingest-source-expressions.py` writes this block.

Snapshots inherit the claim eligibility gate. Inactive snapshots remain as
history; ineligible findings and legacy expression-less claims have no block.

**Sensitive-vault parity:** when a sensitive vault is enabled, it carries the same `claims/` structure; the existing rule that the two vaults never cross-link applies to claim notes unchanged.
Expression snapshots store case-relative evidence references as data, not links
to another vault, so they do not create cross-vault edges.

---

## Cross-Link Conventions

All cross-references use **relative markdown links** (Obsidian resolves these into its
graph and backlinks exactly like wikilinks, and they stay portable to non-Obsidian
consumers). Paths are relative to the linking note's directory:

| Reference type | Format |
|----------------|--------|
| Entity | `[entity-id](../entities/entity-id.md)` |
| Investigation | `[project-id](../investigations/project-id.md)` |
| Methodology | `[technique-id](../methodology/technique-id.md)` |
| Tool | `[tool-id](../tools/tool-id.md)` |
| Claim | `[claim-id](../claims/claim-id.md)` |

**ID rules:**
- All IDs are **kebab-case** (lowercase, hyphens, no spaces).
- Examples: `swiss-leaks`, `john-doe`, `reverse-image-search`, `bellingcat-osm`.
- Claim IDs are `{project-id}-f{n}`: `acme-files-f1`.
