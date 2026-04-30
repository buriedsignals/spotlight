# OSINT Navigator — Tool Discovery API

**What:** A live tool-discovery API maintained by Indicator Media. Weekly-updated database of 10,000+ OSINT tools with AI-powered synthesized answers to investigation-method questions. Complements the curated 150-tool catalog in the `osint` skill's `references/tools-by-category.md`.

**When to use:**

- You need a tool for a niche category not in the curated catalog (e.g. Argentine corporate registry, wildlife trade monitoring, specific cryptocurrency forensics)
- You need to compare multiple tools for a given task (e.g. "which free satellite imagery source is strongest for East Africa?")
- You want a synthesized, natural-language answer to an investigation-method question ("How do I verify the authenticity of a leaked document?")
- You're looking for recently published tools that may not be in the curated list yet

**When NOT to use:**

- The curated 150-tool catalog in `skills/osint/references/tools-by-category.md` covers the request → use that first (offline, no rate limit)
- You know the exact tool you need — just use it; don't round-trip through Navigator

## Setup

Request an API key from Indicator Media: https://navigator.indicator.media/

Set in `.env`:

```
OSINT_NAV_API_KEY=on_xxxxx
```

## Verb calls

### Tool search by keyword / category (unlimited on free tier)

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/tools/search \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"<keywords>\", \"category\": \"<category_slug>\"}" \
  -o cases/{project}/research/navigator-search-<slug>.json')
```

### Complex question (10/day free, 50/day pro)

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  -X POST https://navigator.indicator.media/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"How do I <investigation task>?\"}" \
  -o cases/{project}/research/navigator-query-<slug>.json')
```

### Health check (used by preflight)

```
execute-shell('curl -s -H "Authorization: Bearer $OSINT_NAV_API_KEY" \
  https://navigator.indicator.media/api/openapi.json')
```

## Full API reference

Complete endpoint catalog, response schemas, and per-endpoint examples live at:

**`skills/osint/references/navigator-integration.md`**

This integration's purpose is to make Navigator **discoverable** as a Spotlight integration (preflight, setup.html, skills/integrations/SKILL.md routing). The agent-facing how-to-use lives alongside the OSINT skill proper since Navigator is tightly coupled to OSINT methodology.

## Cycle integration

The OSINT skill includes `references/cycle-integration.md` — documented integration points for Navigator within the investigation cycle (Phase 2 methodology design, Phase 3 execution). Read that ref before using Navigator mid-investigation.

## Output handling

Navigator returns JSON. Tool search returns a list of tool objects; query endpoint returns a synthesized answer plus citations. Save responses verbatim to `cases/{project}/research/` with a `navigator-<type>-<slug>-<timestamp>.json` naming convention.

For tool recommendations derived from Navigator output, cite the Navigator response in the methodology's `key_sources[]` or `tools_required[]`:

```
"tools_required": ["OpenCorporates", "CompaniesHouse UK", "OCCRP Aleph (via Navigator recommendation — cases/{project}/research/navigator-query-ubo-uk.json)"]
```

## Sensitive mode

Navigator requires remote API access, so it's blocked in sensitive mode. Fallback: the curated 150-tool catalog in `skills/osint/references/tools-by-category.md` is offline-capable and covers the most common investigation scenarios. The OSINT skill explicitly documents this offline fallback.
