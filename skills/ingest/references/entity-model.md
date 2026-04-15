# Entity Model

Note types, frontmatter contracts, and wikilink conventions for vault ingestion.

---

## Note Types

### 1. Investigation Note

**Path:** `{vault}/investigations/{project-id}.md`

```yaml
---
id: project-id
title: Human-readable title
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
   - Verdict (confirmed / unconfirmed / debunked)
   - Evidence
   - Sources
   - Perspective
3. **Connections** — Wikilinked entities involved in this investigation.
4. **Gaps** — Open questions and unresolved leads.
5. **Methodology Applied** — Techniques and tools used, with wikilinks.

---

### 2. Entity Note

**Path:** `{vault}/entities/{entity-id}.md`

```yaml
---
id: entity-id
type: person|organization|company|place
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
| [[project-id]] | description of role | YYYY-MM-DD |

3. **Key Relationships** — Wikilinks to other entities with relationship context.

---

### 3. Methodology Note

**Path:** `{vault}/methodology/{technique-id}.md`

```yaml
---
id: technique-id
type: technique
category: osint-category
tools: [tool-id-1]
investigations: [project-id-1]
---
```

**Body structure:**

1. **Description** — What this technique does and when to use it.
2. **Steps** — Ordered procedure.
3. **Tools** — Wikilinked tools used by this technique.
4. **Usage History** — Table:

| Investigation | Context | Date |
|---------------|---------|------|
| [[project-id]] | how it was applied | YYYY-MM-DD |

5. **Lessons Learned** — What worked, what failed, what to do differently.

---

### 4. Tool Note

**Path:** `{vault}/tools/{tool-id}.md`

```yaml
---
id: tool-id
type: tool
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
| [[project-id]] | how it was used | YYYY-MM-DD |

4. **Tips for Future Agents** — Curated advice for effective use.

---

## Wikilink Conventions

| Reference type | Format |
|----------------|--------|
| Entity | `[[entity-id]]` |
| Investigation | `[[project-id]]` |
| Methodology | `[[technique-id]]` |
| Tool | `[[tool-id]]` |

**ID rules:**
- All IDs are **kebab-case** (lowercase, hyphens, no spaces).
- Examples: `swiss-leaks`, `john-doe`, `reverse-image-search`, `bellingcat-osm`.

**Directory fallback** — When wikilinks don't resolve (e.g., flat export or non-Obsidian vault), use relative links:

```markdown
[entity-id](../entities/entity-id.md)
```
