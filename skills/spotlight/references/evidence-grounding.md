# Evidence Grounding Reference

Evidence grounding rules for the investigation pipeline. Referenced by the orchestrator and investigation agents.

---

## Evidence Grounding Rules

1. **Store all research per-case.** Every scraped file, search result, and downloaded document goes into `cases/{project}/research/`. No research lives outside the case folder.

2. **Scrape before cite.** A finding without a scraped file is a claim, not a finding. Before referencing information, use `fetch(url, cases/{project}/research/<name>.md)` to store the source content locally.

3. **Quote verbatim from primary sources.** Evidence fields in findings must contain direct quotes from the scraped material, not paraphrases or summaries.

4. **Link every finding to file.** Each source entry in a finding must include a `local_file` path pointing to the scraped content in `cases/{project}/research/`.

5. **If cannot scrape, explain why.** Document the reason (paywall, geo-block, requires login, site down) and mark the finding's confidence accordingly. A finding that relies on an unscraped source cannot be "high" confidence.

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
