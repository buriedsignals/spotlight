# Navigator — CLI-first research routing

**What:** A live Navigator capability maintained by Indicator Media. It routes both OSINT tool discovery and executable structured public-data sources. It complements Spotlight's offline tool catalog and ordinary web acquisition.

**When to use:**

- You need a tool for a niche category not in the curated catalog (e.g. Argentine corporate registry, wildlife trade monitoring, specific cryptocurrency forensics)
- You need a reproducible structured source for filings, procurement, sanctions, PEP, lobbying, legislation, or public registries
- You need to compare multiple tools for a given task (e.g. "which free satellite imagery source is strongest for East Africa?")
- You want a synthesized, natural-language answer to an investigation-method question ("How do I verify the authenticity of a leaked document?")
- You're looking for recently published tools that may not be in the curated list yet

**When NOT to use:**

- The curated 150-tool catalog in `skills/osint/references/tools-by-category.md` covers the request → use that first (offline, no rate limit)
- You know the exact tool you need — just use it; don't round-trip through Navigator

## Setup and commands

Engine-managed installations connect an account through `bsig auth login`; do
not ask a journalist to create or paste an API key. Load the portable
`navigator` skill, then use the CLI:

```bash
navigator tools find "company registry Argentina" --json
navigator tools show <tool-id>
navigator data find "public procurement awards" --json
navigator data show <source-id>
navigator query <source-id> --help
```

Inspect a selected tool or source playbook before execution. Save the CLI's
machine-readable output under `{CASE_DIR}/research/`; record the command mode,
catalog ID/version or retrieval time, non-secret parameters, source URLs,
warnings, and output digest in methodology/evidence records.

The direct API is a compatibility fallback only when the CLI is unavailable and
Engine has injected the credential into the managed child process. Never put a
PAT in `.env`, argv, saved scripts, or methodology data.

## Full API reference

The portable unified skill and CLI are the agent-facing contract:

**`navigator skill print`**

This integration makes Navigator discoverable to Spotlight. It does not own a
second copy of Navigator instructions.

## Cycle integration

The OSINT skill includes `references/cycle-integration.md` — documented integration points for Navigator within the investigation cycle (Phase 2 methodology design, Phase 3 execution). Read that ref before using Navigator mid-investigation.

## Output handling

Navigator returns JSON. Tool search returns a list of tool objects; query endpoint returns a synthesized answer plus citations. Save responses verbatim to `{CASE_DIR}/research/` with a `navigator-<type>-<slug>-<timestamp>.json` naming convention.

For tool recommendations derived from Navigator output, cite the Navigator response in the methodology's `key_sources[]` or `tools_required[]`:

```
"tools_required": ["OpenCorporates", "CompaniesHouse UK", "OCCRP Aleph (via Navigator recommendation — {CASE_DIR}/research/navigator-query-ubo-uk.json)"]
```

## Sensitive mode

Navigator requires remote API access, so it's blocked in sensitive mode. Fallback: the curated 150-tool catalog in `skills/osint/references/tools-by-category.md` is offline-capable and covers the most common investigation scenarios. The OSINT skill explicitly documents this offline fallback.
