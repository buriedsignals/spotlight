---
name: fact-checker
description: Independent verification of investigation findings using SIFT methodology
version: "1.0"
agent绑定: fact-checker
---

# Fact-Checker Skill

Independent verification agent for investigating claims using the SIFT methodology.

## Overview

The fact-checker operates independently from the investigator. It reads `cases/{project}/data/findings.json`, conducts its own verification research, and writes `cases/{project}/data/fact-check.json` with per-claim verdicts and evidence trails.

## SIFT Methodology

The fact-checker uses the SIFT method for fact-checking:

1. **Stop** - Before diving into a source, stop and ask: What do I already know about this topic?
2. **Investigate the source** - Click downstream to see who is behind the article. Look for author credibility, publication reputation, and potential biases.
3. **Find better coverage** - Look for original sources or better-quality reporting on the same topic.
4. **Trace claims** - Verify quotes, statistics, and data by going back to the original source.

## Operation Mode

The fact-checker operates in **VERIFICATION** mode:

1. **LOAD** - Read `cases/{project}/data/findings.json` to get the claims to verify
2. **QUERY** - Check `query-vault(vault_path, claim_keywords)` for prior fact-checks on same entities
3. **VERIFY** - For each claim:
   - Use `search(claim_text)` to find supporting/disputing sources
   - Use `fetch(source_url)` to get primary source content
   - Classify evidence as `evidence_for` or `evidence_against`
4. **JUDGE** - Assign verdict: `verified`, `unverified`, `disputed`, or `false`
5. **WRITE** - Output to `cases/{project}/data/fact-check.json`

## Verdict Taxonomy

| Verdict | Definition | Confidence |
|---------|------------|------------|
| `verified` | Multiple credible sources confirm the claim | high |
| `unverified` | Insufficient evidence to confirm or deny | medium/low |
| `disputed` | Credible sources contradict each other | medium |
| `false` | Credible sources definitively disprove the claim | high |

## Evidence Classification

Each piece of evidence must be classified by:

- **source_type**: `primary` (original document) or `secondary` (reporting/analysis)
- **access_method**: `full_text`, `open_access`, `archive_copy`, `abstract_only`, `inaccessible`
- **archive_url**: If using an archived copy, provide the archive URL

## Output Schema

The fact-checker writes to `cases/{project}/data/fact-check.json` following `schemas/fact-check.schema.json`:

```json
{
  "schema_version": "1.0",
  "project": "{project-id}",
  "source_document": "findings.json",
  "checked_at": "{ISO-8601-timestamp}",
  "cycle": {cycle-number},
  "summary": {
    "total_claims": N,
    "verified": N,
    "unverified": N,
    "disputed": N,
    "false": N
  },
  "claims": [
    {
      "id": 1,
      "finding_id": "{finding-reference}",
      "claim_text": "...",
      "verdict": "verified|unverified|disputed|false",
      "confidence": "high|medium|low",
      "evidence_for": [...],
      "evidence_against": [...],
      "sources": [...],
      "notes": "..."
    }
  ],
  "gaps_for_next_cycle": [...]
}
```

## Allowed Verbs

- `search` - Find sources on the web
- `fetch` - Retrieve and store source content
- `read-file` - Read case files
- `write-file` - Write fact-check output
- `list-files` - Navigate case directory
- `grep-files` - Search within case files
- `invoke-skill` - Load supporting skills
- `query-vault` - Check for prior work
- `execute-shell` - Run辅助 scripts

## Invoked Skills

The fact-checker may invoke:
- `spotlight` - Pipeline coordination
- `web-archiving` - Wayback Machine, archive.today archival
- `content-access` - Paywall bypass, access method classification

## Confidence Guidelines

| Evidence Quality | Resulting Confidence |
|-----------------|---------------------|
| 2+ primary sources, full text, high credibility | high |
| 1 primary source or 2+ secondary sources | medium |
| Abstract only, inaccessible, or single low-credibility source | low |

## Limitations

- The fact-checker does NOT resolve disputes - it documents them
- If a claim cannot be verified due to inaccessible sources, mark as `unverified` with `inaccessible` access method
- All claims require at least one `evidence_for` or `evidence_against` entry
