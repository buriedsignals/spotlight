---
name: integrations
description: Use when an investigation step may need an external integration such as browser acquisition, Maigret account discovery, Junkipedia narrative tracking, Arbiter social-media case studies, OSINT Navigator tool discovery, Noosphere C2PA signing, or Unpaywall access lookup.
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
| `dev-browser` | browser-automation | dynamic-page-acquisition, form-navigation, screenshot-capture, download-capture, visual-verification, authenticated-browser-session | Use for specific investigative tasks that require browser automation after ordinary search/scrape is insufficient: portals, JS-rendered pages, forms, screenshots, downloads, visual verification, and acquisition evidence bundles. |
| `browse` | browser-automation | skill-catalog-navigation, selector-based-driver, ref-based-driver, accessibility-tree-snapshot, portal-navigation | Second-tier browser tool. Use when a curated browse.sh skill exists for the target portal and dev-browser would require writing navigation logic from scratch. |
| `browser-harness` | browser-automation | cdp-browser-control, dynamic-page-acquisition, screenshot-capture, download-capture, visual-verification | Legacy browser fallback. Do not pick as the default while dev-browser is green. |
| `browser-use` | browser-automation | form-navigation, search-export, login-driving, multi-step-browsing | Legacy/adjacent browser automation. Do not pick as the default while dev-browser is green. |
| `arbiter` | social-osint | curated-case-study-browse, entity-stance-analysis, hierarchical-theme-analysis, consolidated-case-study-report, case-study-creation | Analysing a collected social corpus: per-entity stance, theme clustering, actor/community structure, archived posts. Uses the member's own Arbiter key through Data Navigator. |
| `junkipedia` | social-osint | narrative-tracking, misinformation-search, social-media-monitoring, cross-platform-query | Tracking how a claim spread; finding social posts deleted from origin; cross-platform narrative investigation. |
| `maigret` | social-osint | username-search, account-discovery, profile-url-collection | Username-led account discovery. Produces candidate profile leads only; never use as attribution proof. |
| `noosphere-c2pa` | provenance-signing | case-provenance-manifest, c2pa-content-credentials, optional-signing-receipt | **PENDING integration — opt-in signer.** Preflight reports `unconfigured` until `NOOSPHERE_C2PA_URL` is set. After Gate 1, the base provenance path always writes `status: unsigned` and proceeds; signing runs only against a configured signer endpoint. Never a mandatory or blocking step. |
| `osint-navigator` | tool-discovery | tool-search-by-keyword, complex-query-synthesis, country-specific-tool-lookup | Entitlement-gated (subscription tier): first tool-discovery pass in Phase 2 when green + sensitive mode false. Local/open tier and all fallbacks use `scripts/osint-tools.py find` (local SQL index, 12,500 tools). |
| `unpaywall` | academic-open-access | doi-open-access-lookup, academic-fulltext-discovery, legal-pdf-location | Academic papers with DOIs when the content-access hierarchy needs a legal open-access copy. |

## Routing decision tree

```
What's the task?
│
├── "Navigate a form / click through a UI / extract from a JS-rendered page"
│     → dev-browser — NATIVE harness capability (interactive / authenticated / JS-rendered /
│       chain-of-custody capture); available to ALL tiers, not an integration, not preflight-gated
│     → for STATIC content extraction, use the Crawl4AI scraping seam (also native) — a different
│       job, not a dev-browser fallback (the two serve different purposes)
│     → legacy alternates only if dev-browser is genuinely unavailable: browse (curated browse.sh),
│       then browser-harness / browser-use; last resort fetch() static scrape (weak on JS-heavy pages)
│
├── "Find deleted social posts / track narrative spread / cross-platform search"
│     → junkipedia  (if green — check preflight)
│     → fallback: search() + social-media-intelligence skill (limited without Junkipedia's archive)
│
├── "Analyse a whole social corpus — entity stance, narrative themes, who is amplifying,
│    engagement over time" / "cite a social post that may be deleted at origin"
│     → arbiter  (if green — check preflight) through
│       `navigator query global/arbiter/case-studies`; browse the topic menu for a case study
│       that already covers the event, or create one from a reviewed plan when nothing matches
│     → requires eligible Data Navigator access and the member's own Arbiter key; `/arbiter`
│       provides the attributed signup link and local key-setup command
│     → post pulls and agent questions are credit-metered to that member's Arbiter account
│     → fallback: junkipedia, then search() + social-media-intelligence skill
│
├── "Find accounts from one or more usernames / handles / aliases"
│     → maigret if preflight is green and the operator accepts account-discovery noise
│     → output is unverified account-discovery leads only
│     → fallback: search() + social-media-intelligence skill
│
├── "Phase 2 methodology tool selection" / "Need a tool I don't know for category X" / "Compare tools" / "Niche country-specific tool"
│     → osint-navigator  (first pass ONLY if entitled + green + sensitive mode false — subscription tier)
│     → otherwise (DEFAULT for local / open tier): execute-shell("python3 scripts/osint-tools.py find \"<keywords>\" [--category X]")  — local SQL index, 12,500 tools, offline, no Navigator needed
│
├── "Static page scrape / web search"
│     → fetch / search (verbs, no integration needed)
│
├── "Keep watching this after the cycle ends"
│     → invoke-skill("monitoring") to present a recommendation and, with
│       explicit approval, hand it to Mycroft. Spotlight never calls Scoutpost.
│
├── "Find a legal open-access copy of an academic paper with a DOI"
│     → unpaywall  (if green — check preflight)
│     → fallback: invoke-skill("content-access") and continue with CORE / Semantic Scholar
│
├── "Chain-of-custody evidence capture (court records, gov portals)"
│     → dev-browser + web-archiving, recorded in evidence-bundle.json
│
└── "Paywalled / gated content"
      → invoke-skill("content-access")  (hierarchy before marking inaccessible)
```

## How to check preflight status mid-execution

```
execute-shell("python3 integrations/preflight.py --model-tier {config.model_tier} --json")
```

Parse the JSON output. `summary.green` tells you how many integrations are usable. `integrations[].status` tells you per-integration. Only route to `green` integrations; log a note and fall back for `red`/`yellow`/`unconfigured`.

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

- Save raw responses to `{CASE_DIR}/research/<integration-id>-<slug>-<timestamp>.json`
- Cite the integration explicitly in the source entry: `"access_method": "<appropriate enum>", "access_notes": "Retrieved via <integration-name> API"`
- Record the exact query / parameters in the source entry so the retrieval is reproducible
- Archive the underlying origin URLs per `invoke-skill("web-archiving")` — an integration's copy is supplementary, not primary
- Treat Maigret and model-derived artifacts as leads only. They must not write `verified`, `confirmed`, or `publishable` statuses.

## Sensitive mode

When `sensitive: true`, most integrations go dark:

- Any integration that requires a remote API call becomes unavailable (the `fetch`/`search` verbs are stripped, and `execute-shell("curl …")` against remote hosts should be guarded at the skill layer)
- Pre-cached integration responses in `{CASE_DIR}/research/` remain readable via `read-file`
- Local-only browser runs against local/pre-archived content may still work through dev-browser — check the integration's `integration.md` § Sensitive mode

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
