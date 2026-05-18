# Evidence Grounding Reference

Evidence grounding rules for the investigation pipeline. Referenced by the orchestrator and investigation agents.

---

## Evidence Grounding Rules

1. **Store all research per-case.** Every scraped file, search result, and downloaded document goes into `cases/{project}/research/`. No research lives outside the case folder.

2. **Scrape before cite.** A finding without a scraped file is a claim, not a finding. Before referencing information, use `fetch(url, cases/{project}/research/<name>.md)` to store the source content locally.

3. **Quote verbatim from primary sources.** Evidence fields in findings must contain direct quotes from the scraped material, not paraphrases or summaries.

4. **Ground the exact claim.** A source must support the exact material claim elements: actor, action, object, time, place, amount, relationship, and status. If the source only mentions the topic, the item is a source-adjacent lead, not a finding.

5. **Link every finding to file and archive.** Each source entry in a finding must include `local_file`, `archive_url`, and `access_method`.

6. **Fill the grounding object.** Every finding must include `grounding.support_type`, `grounding_strength`, `source_role`, `quote_match`, supported claim elements, missing assumptions, contradictions, confidence cap, misgrounding risk, and rationale.

7. **If cannot scrape, explain why.** Document the reason (paywall, geo-block, requires login, site down) and mark the finding's confidence accordingly. A finding that relies on an unscraped source cannot be "high" confidence.

8. **Use epistemic-grounding for confidence caps.** Invoke `epistemic-grounding` when extracting findings or fact-checking. Weak claim-to-evidence fit caps confidence even when a source exists.

## Grounding Ladder

| Level | Meaning | Editorial Status |
|---|---|---|
| Unsourced signal | Interesting but no source anchor | Lead only |
| Source-adjacent lead | Source mentions the topic but not the claim | Lead only |
| Partially grounded claim | Some elements supported; assumptions remain | At most medium confidence |
| Directly grounded claim | All material elements directly supported | Finding candidate |
| Independently verified finding | Direct grounding plus independent corroboration and no unresolved contradiction | Gate-ready finding |

---

## Search and Scrape Operations

All search and scrape operations use tool verbs from the registry defined in `AGENTS.md`. The runtime adapter maps these to the available library (firecrawl, exa, tavily, or equivalent).

| Operation | Tool Verb | Example |
|-----------|-----------|---------|
| Web search | `search` | `search("query terms", cases/{project}/research/search-results.json, 10)` |
| Scrape URL | `fetch` | `fetch(url, cases/{project}/research/source-name.md)` |
| Read scraped content | `read-file` | `read-file(cases/{project}/research/source-name.md)` |
| List research files | `list-files` | `list-files(cases/{project}/research/*.md)` |
| Search across research | `grep-files` | `grep-files(pattern, cases/{project}/research/)` |

The adapter is responsible for detecting and configuring the underlying search library. If no search library is available, the adapter MUST raise an error at load time with setup instructions.
