---
name: phase-preflight
description: Spotlight Phase 0 — Preflight: config detection, sovereign search/fetch backing checks, OSINT skill inventory, OpenKnowledge workspace validation, project setup, integration tiering, resume feedback check
invocable_by: [orchestrator]
phase: preflight
---

# Phase 0 — Preflight

Run these checks in order. Stop at the first failure.

## 1. Config check

Use `read-file` on `.spotlight-config.json` in the working directory. If it exists and contains valid `search_library`, `vault_path`, and `case_workspace_root` (or legacy `cases_root`) fields, update `last_used` to the current timestamp and skip to step 5 (project setup).

`case_workspace_root` is the active investigation workspace. `vault_path` is the durable knowledge vault. Do not write active case files into the vault. At case start, query the vault for prior context; at case end, ask before ingesting verified material into the vault.

## 2. Search/scrape backing detection

Spotlight is **sovereign by default**: `fetch` → Crawl4AI (`integrations.scraping`, no API key), `search` → SearXNG (`integrations.search`, self-hosted). Firecrawl is an **optional** escape hatch (scrape fallback on a hard bot-block, or a `--union` search engine) enabled only when `FIRECRAWL_API_KEY` is present. Check the sovereign backings with:

```
execute-shell("command -v crwl || command -v uvx")   # Crawl4AI (or uvx cold-start)
execute-shell("curl -s -o /dev/null -w '%{http_code}' \"${SEARXNG_URL:-http://localhost:8899}/search?q=ping&format=json\"")   # SearXNG
```

If Crawl4AI is missing:

> "No Crawl4AI detected. Spotlight's `fetch` verb uses Crawl4AI. Run `install-spotlight.sh` (provisions `crawl4ai` + `crawl4ai-setup`), or set `FIRECRAWL_API_KEY` to use the Firecrawl fallback."

If SearXNG is unreachable, `search` falls back to Firecrawl when `FIRECRAWL_API_KEY` is set; otherwise report the gap. Proceed once at least one search + one fetch backing is available — a pure-sovereign install (no Firecrawl key) is fully supported.

## 3. OSINT skill availability

Confirm the following skills resolve via `invoke-skill`:

- `osint` — tool routing and technique catalog
- `investigate` — step-by-step techniques
- `follow-the-money` — financial investigation methodology
- `epistemic-grounding` — claim-to-evidence grounding and confidence caps
- `shell-safety` — safe command construction and destructive-operation probes
- `acquisition-graduation` — reusable dev-browser acquisition paths
- `social-media-intelligence` — account authenticity, coordination detection

These ship in `skills/` in this repo. If your runtime cannot resolve them, fix the skill-loading configuration before proceeding.

## 3.5. Agent skill inventory

No user action required. This step establishes what capabilities your agents have access to before you spawn them.

Agents have access to the following skills by their own `invoke-skill` calls:

| Skill | Agent(s) | Purpose |
|---|---|---|
| `acquisition-graduation` | investigator, fact-checker | Graduate repeated dev-browser acquisition paths into durable source/domain guidance |
| `web-archiving` | investigator, fact-checker | Archive all evidence before citing |
| `content-access` | investigator, fact-checker | Work through paywall hierarchy before marking sources inaccessible |
| `epistemic-grounding` | investigator, fact-checker | Test whether exact evidence actually supports exact claims; cap confidence when grounding is weak |
| `shell-safety` | investigator, fact-checker | Validate untrusted values before execute-shell; require probes for destructive operations |
| `provenance-signing` | orchestrator, user | Build a case provenance manifest and optionally hand it to Noosphere C2PA signing |
| `osint`, `investigate`, `follow-the-money` | investigator | Tool routing + technique catalog |
| `social-media-intelligence` | investigator, fact-checker | Account authenticity, coordination detection, narrative tracking |

When building spawn prompts, remind agents these are available and expected.

## 3.6. Parent/child phase contract

Spotlight keeps a one-level physical skill layout, but phase execution has
mandatory child-skill loading. Use `skills-manifest.json` as the maintenance
contract and apply this runtime table:

| Parent phase | Required child skills | Conditional child skills | Validation |
|---|---|---|---|
| Phase 0 Preflight | `integrations` | `shell-safety` if preflight executes dynamic shell values | `.spotlight-config.json` stores full integration status, not only booleans. |
| Phase 2 Methodology | `integrations`, `osint`, `investigate`, `epistemic-grounding` | `follow-the-money`, `social-media-intelligence`, `technical-investigation`, `content-access` | `methodology.json` includes `skills_invoked[]` and required Navigator fields when green. |
| Phase 3 Execution | `epistemic-grounding`, `shell-safety`, `web-archiving` | `content-access`, `acquisition-graduation`, `social-media-intelligence`, `technical-investigation` | findings contain evidence refs, archives, confidence caps. |
| Phase 3 Fact-check | `epistemic-grounding`, `content-access`, `shell-safety` | `osint`, `social-media-intelligence`, `technical-investigation`, `web-archiving` | fact-check output independently checks investigator claims. |
| Phase 5 Report | `report-drafting`, `epistemic-grounding` | `provenance-signing`, `technical-investigation` | report claims map to evidence ledger. |
| Ingest | `ingest` | none | only verified or explicitly caveated material enters vault. |

## 3.7. Knowledge workspace preflight

Spotlight uses OpenKnowledge as its canonical local knowledge workspace.
Confirm that the selected workspace contains `.ok/config.yml` and
that `ok --cwd "{workspace_path}" config validate` succeeds. If either check
fails, stop with the exact repair command from OpenKnowledge; do not
silently switch to a different directory or legacy vault backend.

`obsidian_legacy` remains an explicit migration-only backend. Run an Obsidian
CLI check only when that exact backend is recorded in `.spotlight-config.json`.

## 4. Vault configuration

Ask the user:

> "Which OpenKnowledge workspace should archive verified findings?"

Use the configured workspace by default. Keep Markdown/YAML frontmatter
and relative links portable. Tolaria and `obsidian_legacy` may be selected only
through explicit migration configuration; never infer them from directory
contents.

## 5. Project setup

Derive a project slug from the user's lead (lowercase, hyphens, no spaces). Resolve `CASE_ROOT` from `case_workspace_root`; if absent, use legacy `cases_root`; if absent, use `cases/`. Resolve `CASE_DIR = CASE_ROOT/{project}`. Create:

```
{CASE_DIR}/
{CASE_DIR}/data/
{CASE_DIR}/research/
{CASE_DIR}/evidence/
```

## 6. Duplicate project check

If `CASE_DIR` already exists, prompt:

> "An investigation named `{project}` already exists. Resume the existing investigation, or start fresh?"

If resume, run
`python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}` and return
its `next_phase` and resume detail to the dispatcher without inspecting case
artifacts or `data/orchestration.json`. If fresh, back up the existing directory
to `{CASE_ROOT}/{project}-{timestamp}/` and create a new one.

## 7. Active investigation check

Use `list-files("{CASE_ROOT}/*")` to enumerate case directories, then run
`python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}` for each valid case.
Cases whose returned `next_phase` is not `complete` are active:

> "Note: {N} investigation(s) in progress: {names}. Continuing with `{project}`."

## 8. Write config

Write `.spotlight-config.json` via `write-file`:

```json
{
  "search_library": "<detected library>",
  "vault_path": "<user-provided path or ./vault/>",
  "vault_type": "openknowledge | tolaria | obsidian_legacy | directory",
  "case_workspace_root": "cases/",
  "cases_root": "cases/",
  "integrations": {
    "osint_navigator": {
      "status": "unknown",
      "checked_at": "<ISO timestamp>",
      "source": "not checked yet",
      "required_in_phase_2": false,
      "reason": "preflight not run yet"
    },
    "rlm": {
      "enabled": false,
      "mode": "off",
      "model": null,
      "prefilter": false,
      "hybrid": false,
      "evidence_boundary": "lead-only; never verified or publishable"
    }
  },
  "sensitive": false,
  "created_at": "<ISO timestamp>",
  "last_used": "<ISO timestamp>",
  "active_project": "<project slug>"
}
```

## 9. Integration checks

Check for optional API integrations. None are required for Spotlight to start,
but a green OSINT Navigator result becomes mandatory during Phase 2 tool
selection when sensitive mode is false.

Run the manifest-based preflight and parse the `osint-navigator` entry:

```
execute-shell("python3 integrations/preflight.py --model-tier {config.model_tier} --json")
```

Update `.spotlight-config.json` with the full status, not a boolean:

```json
{
  "integrations": {
    "osint_navigator": {
      "status": "green|yellow|red|unconfigured|dismissed",
      "checked_at": "<ISO timestamp>",
      "source": "integrations/preflight.py --json",
      "required_in_phase_2": true,
      "reason": ""
    }
  }
}
```

Set `required_in_phase_2: true` only when ALL hold:

- `model_tier` is NOT `12b` (the constrained tier dismisses every integration — see below), and
- `sensitive` is false, and
- the `osint-navigator` preflight entry has `status: "green"` (needs a subscription entitlement — `OSINT_NAV_API_KEY`).

Set `required_in_phase_2: false` and preserve a concrete `reason` when
`model_tier` is `12b`, Navigator is red/yellow, sensitive mode is active, the
lead is local-only, or the user explicitly forbids external APIs. On the open
tier, Phase-2 tool discovery uses `scripts/osint-tools.py find` (local SQL index).

**Integration tiering (`model_tier`):**
- `12b` — ALL integrations dismissed; native capabilities only (dev-browser, Crawl4AI seam, `osint-tools` SQL, native verbs). `integrations/preflight.py --model-tier 12b` forces every integration `status: "dismissed"`.
- `26b` / `31b` / `frontier` / `api` — integrations on by default except Unpaywall (opt-in), Navigator (entitlement-gated), and Noosphere (opt-in, pending). Scoutpost is not a Spotlight integration.

Also preserve any existing `integrations.rlm` setup block from
`.spotlight-config.json`. If it is absent, treat RLM as not installed:

```json
{
  "integrations": {
    "rlm": {
      "enabled": false,
      "mode": "off",
      "model": null,
      "prefilter": false,
      "hybrid": false,
      "evidence_boundary": "lead-only; never verified or publishable"
    }
  }
}
```

If `integrations.rlm.enabled` is true, find the `rlm` entry in
`integrations/preflight.py --json` and record its status under the same
`integrations.rlm` object as `preflight_status`, `checked_at`, `source`, and
`reason`. Do not ask the user about RLM during Phase 0.

## 9.5. Follow-up recovery (resume only)

When resuming an existing project, consume the same orchestration status result
used above. If it returns `next_phase: execution` with
`follow_up.instructions`, pass those instructions to `skills/phase-execution`.
Do not scan feedback files or processed markers to derive resume state. The
Gate 1 owner validates structured feedback and records the durable transition
through `spotlight-orchestration.py request-follow-up` before execution resumes.

## 10. Monitoring + integrations availability (optional)

Run integration preflight and check whether Mycroft passive monitoring is installed:

```
execute-shell("python3 integrations/preflight.py --model-tier {config.model_tier} --json")
execute-shell('test -f ~/.mycroft/monitoring/monitor.py && echo true || echo false')
```

Display a combined summary to the user so they know which external integrations are green and whether passive Mycroft signals are available. Do not block on failures — supplementary monitoring is optional.

Typical expectations:

- Sovereign search + fetch backing ready (checked in step 2: Crawl4AI + SearXNG; `firecrawl` optional, only if `FIRECRAWL_API_KEY` is set)
- Integration `dev-browser` green if the `dev-browser` CLI is available
- Integration `osint-navigator` green if `OSINT_NAV_API_KEY` is set
- Integration `arbiter` green if `ARBITER_API_KEY` is set (opt-in — curated social-media case studies, credit-metered to the member's account; unconfigured until set, and Spotlight works fully without it via the junkipedia/search fallbacks)
- Other integrations (junkipedia, future integrations like serus/thinkpol) green only if user has access
