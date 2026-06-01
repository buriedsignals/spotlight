---
name: integrations
description: Routing table for external tool integrations — Browser Harness, browser-use, Junkipedia, Noosphere C2PA, OSINT Navigator, Scoutpost, and more. Agents invoke this skill to discover which integrations are live and which one fits a given investigation task.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator]
requires: []
---

# Integrations — External Tool Routing

Spotlight ships with a framework for external OSINT tool integrations (`integrations/` at the repo root). Each integration is a directory with `manifest.json` + `integration.md`. This skill is the agent-facing routing layer: given an investigation need, which integration (if any) should the agent reach for?

**The preflight result is authoritative.** Before routing, the orchestrator should have run `integrations/preflight.py` at Phase 0. If an integration is `red` (missing env var) or `yellow` (smoke test failed), do not route to it — use a fallback.

## When to invoke this skill

- Before you make a decision that could benefit from an external tool: "should I use an integration for this step, or stay with the core verbs?"
- When the orchestrator wants to show the user which integrations are live
- Mid-investigation: "I need to verify a claim from a deleted tweet — is Junkipedia available?"

The skill is cheap to load — it's a routing table, not a deep methodology guide.

## Current integrations

| Integration | Category | Capabilities | When to pick |
|---|---|---|---|
| `browser-harness` | browser-automation | cdp-browser-control, dynamic-page-acquisition, screenshot-capture, download-capture, visual-verification | Preferred v2 browser fallback after Firecrawl misses material evidence. Use for portals, JS-rendered pages, screenshots, downloads, iframes/shadow DOM, and acquisition evidence bundles. |
| `browse` | browser-automation | skill-catalog-navigation, selector-based-driver, ref-based-driver, accessibility-tree-snapshot, portal-navigation | Second-tier browser tool. Use when a curated browse.sh skill exists for the target portal (e.g. OpenCorporates filings, Wayback Machine snapshot search) — saves writing navigation logic from scratch. Default `--local`, no API key. Browser Harness remains primary for general portal work; see `integrations/browse/integration.md` for the routing rules. |
| `browser-use` | browser-automation | form-navigation, search-export, login-driving, multi-step-browsing | Optional legacy/adjacent browser automation. Prefer Browser Harness for Spotlight acquisition evidence. |
| `junkipedia` | social-osint | narrative-tracking, misinformation-search, social-media-monitoring, cross-platform-query | Tracking how a claim spread; finding social posts deleted from origin; cross-platform narrative investigation. |
| `noosphere-c2pa` | provenance-signing | case-provenance-manifest, c2pa-content-credentials, optional-signing-receipt | After Gate 1, package and optionally sign the investigation trail. No API key; Noosphere controls signing credentials. |
| `osint-navigator` | tool-discovery | tool-search-by-keyword, complex-query-synthesis, country-specific-tool-lookup | First tool-discovery pass during Phase 2 methodology when preflight is green and sensitive mode is false. Otherwise fallback to the curated 150-tool catalog. |
| `scoutpost` | monitoring | project-scoped-monitoring, scout-creation, information-unit-retrieval, scheduled-monitoring | Approved monitoring that should keep running after the current investigation cycle. |
| `unpaywall` | academic-open-access | doi-open-access-lookup, academic-fulltext-discovery, legal-pdf-location | Academic papers with DOIs when the content-access hierarchy needs a legal open-access copy. |

## Routing decision tree

```
What's the task?
│
├── "Navigate a form / click through a UI / extract from a JS-rendered page"
│     → check `browse skills find <domain>` first — if a curated skill exists, prefer `browse` (saves writing nav logic from scratch; default `--local`)
│     → otherwise browser-harness  (preferred general fallback if green — check preflight)
│     → fallback: browser-use for non-evidence-grade automation if Browser Harness is unavailable
│     → fallback: fetch() static scrape; may not work for JS-heavy pages
│
├── "Find deleted social posts / track narrative spread / cross-platform search"
│     → junkipedia  (if green — check preflight)
│     → fallback: search() + social-media-intelligence skill (limited without Junkipedia's archive)
│
├── "Phase 2 methodology tool selection" / "Need a tool I don't know for category X" / "Compare tools" / "Niche country-specific tool"
│     → osint-navigator  (mandatory first pass if green and sensitive mode is false)
│     → fallback: skills/osint/references/tools-by-category.md (offline, 150 tools)
│
├── "Static page scrape / web search"
│     → fetch / search (verbs, no integration needed)
│
├── "Create a durable monitor / keep watching this after the cycle ends"
│     → scoutpost  (if green — check preflight)
│     → fallback: invoke-skill("monitoring") for runtime-native routine guidance
│
├── "Find a legal open-access copy of an academic paper with a DOI"
│     → unpaywall  (if green — check preflight)
│     → fallback: invoke-skill("content-access") and continue with CORE / Semantic Scholar
│
├── "Chain-of-custody evidence capture (court records, gov portals)"
│     → browser-harness + web-archiving, recorded in evidence-bundle.json
│
└── "Paywalled / gated content"
      → invoke-skill("content-access")  (hierarchy before marking inaccessible)
```

## How to check preflight status mid-execution

```
execute-shell("python3 integrations/preflight.py --json")
```

Parse the JSON output. `summary.green` tells you how many integrations are usable. `integrations[].status` tells you per-integration. Only route to `green` integrations; log a note and fall back for `red`/`yellow`.

## Using an integration

Each integration's exact usage — verb calls, request shape, output format — is documented in `integrations/<id>/integration.md`. Read that file when you decide to use an integration, then emit the documented verb calls.

Example flow for a narrative-tracking task:

1. Read this skill (you are here)
2. Pick `junkipedia` from the routing table
3. Check preflight: `junkipedia` is green?
4. `read-file("integrations/junkipedia/integration.md")`
5. Follow the documented `execute-shell("curl …")` calls
6. Parse output, fold into findings

## Output handling

Any data retrieved through an integration follows the usual evidence-grounding rules:

- Save raw responses to `cases/{project}/research/<integration-id>-<slug>-<timestamp>.json`
- Cite the integration explicitly in the source entry: `"access_method": "<appropriate enum>", "access_notes": "Retrieved via <integration-name> API"`
- Record the exact query / parameters in the source entry so the retrieval is reproducible
- Archive the underlying origin URLs per `invoke-skill("web-archiving")` — an integration's copy is supplementary, not primary

## Sensitive mode

When `sensitive: true`, most integrations go dark:

- Any integration that requires a remote API call becomes unavailable (the `fetch`/`search` verbs are stripped, and `execute-shell("curl …")` against remote hosts should be guarded at the skill layer)
- Pre-cached integration responses in `cases/{project}/research/` remain readable via `read-file`
- Local-only integrations (Browser Harness against local/pre-archived content or browser-use against a local file) may still work — check the integration's `integration.md` § Sensitive mode

The orchestrator flags sensitive-mode investigations at Gate 1 to note which integrations were unavailable during the work.

## Adding / discovering new integrations

Integrations are drop-in directories under `integrations/`. When a new one appears (manifest.json + integration.md), add it to the routing table above. Preflight discovers it automatically — no code changes to `preflight.py`.

For integrations whose architecture is ready but API access has not yet been granted, see `docs/integrations-roadmap.md`. Activation moves an entry out of that roadmap and into the routing table above.

## Reference

| File | Purpose |
|---|---|
| `integrations/README.md` | Framework overview, manifest contract, add-a-new-one procedure |
| `integrations/preflight.py` | Env-var + smoke-test checker |
| `integrations/<id>/manifest.json` | Per-integration contract |
| `integrations/<id>/integration.md` | Per-integration agent-facing usage |
