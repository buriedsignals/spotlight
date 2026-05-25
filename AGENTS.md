---
name: spotlight
description: Runtime contract for the Spotlight OSINT investigation system
version: "1.0"
runtime_version: "1"
sensitive: false
---

# Spotlight — Runtime Contract

This file defines the portable contract for the Spotlight investigation system. Any runtime that can (a) read this file + the skills in `skills/`, (b) dispatch the 13 verbs to native tools, and (c) spawn sub-agents can run Spotlight investigations.

Target runtimes: **pi** (https://pi.dev — native AGENTS.md + SKILL.md support), **Hermes** (production Mycroft agent), **Goose** (served as an extension pack), **Codex CLI**, **Gemini CLI**, and any harness driving a local OpenAI-compatible endpoint (llama-server, Ollama, Exoscale, vLLM). Claude Code users stay on the existing marketplace plugin at `~/buried_signals/tools/skills/spotlight/` — this repo exists for non-Claude runtimes.

Per-runtime wiring is documented in `docs/integrations.md`.

## Tool Verb Registry

Fixed vocabulary of abstract operations. Every runtime adapter MUST implement all verbs. If a verb is unsupported, the adapter raises an explicit error at load time.

| Verb | Signature | Semantics | Universal backing |
|------|-----------|-----------|-------------------|
| `fetch` | `fetch(url, output_path)` | Scrape URL content, save to file | `firecrawl scrape` |
| `search` | `search(query, output_path, limit)` | Web search, save results to file | `firecrawl search` |
| `read-file` | `read-file(path)` | Read file contents | filesystem |
| `write-file` | `write-file(path, content)` | Write file (full overwrite) | filesystem |
| `edit-file` | `edit-file(path, old, new)` | Targeted string replacement | filesystem |
| `list-files` | `list-files(pattern)` | Glob/search for files matching pattern | glob |
| `grep-files` | `grep-files(pattern, path)` | Search file contents by regex | ripgrep |
| `execute-shell` | `execute-shell(command)` | Run shell command, return stdout + stderr | shell subprocess |
| `spawn-agent` | `spawn-agent(agent_id, prompt, config)` | Launch sub-agent with prompt and config | runtime-specific |
| `wait-agent` | `wait-agent(handle)` | Block until agent completes, return output | runtime-specific |
| `invoke-skill` | `invoke-skill(skill_id)` | Load skill instructions into current context | runtime-specific |
| `query-vault` | `query-vault(vault_path, query)` | Search knowledge vault for context | `BUN_INSTALL="" qmd query` |
| `vault-write` | `vault-write(vault_path, note_path, content)` | Write note to vault and update registry | `obsidian` CLI |

## Agent Manifests

### investigator

```yaml
name: investigator
description: Plans and executes OSINT investigations using structured methodology
iteration_limit: 80
allowed_verbs:
  - fetch
  - search
  - read-file
  - write-file
  - edit-file
  - list-files
  - grep-files
  - invoke-skill
  - query-vault
  - execute-shell
disallowed_verbs:
  - spawn-agent
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  local: hf.co/tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M
  fallback_note: Two-tier local fleet, both Tom's journalism fine-tunes. Default — Qwen 3.5 9B Journalist (tomvaillant/qwen3.5-9b-abliterated-journalist-GGUF:Q4_K_M) for 16 GB Macs, ~6 GB on disk. Heavy tier — Qwen 3.6 27B Journalist (tomvaillant/qwen3.6-27b-abliterated-journalist-GGUF:Q4_K_M) for 32 GB+ Macs, ~15 GB on disk, ~22 GB at runtime, runs in thinking mode (abliterated /no_think path is broken). The setup form's fit-check enforces the 32 GB minimum before the 27B option commits. Bench-validated against the eval suite in tools/fine-tuning/eval/.
vault_context:
  enabled: true
  query_on_load: true
```

The investigator operates in two modes:

- **PLANNING** — Analyzes the brief, queries the vault for prior work, designs methodology, writes `cases/{project}/data/methodology.json`
- **EXECUTION** — Follows approved methodology, executes research using `fetch` and `search`, writes `cases/{project}/data/findings.json` and appends to `cases/{project}/data/investigation-log.json`

Full prompt bundle: `agents/investigator.md`.

### fact-checker

```yaml
name: fact-checker
description: Independent verification of investigation findings using SIFT methodology
iteration_limit: 50
allowed_verbs:
  - fetch
  - search
  - read-file
  - write-file
  - list-files
  - grep-files
  - invoke-skill
  - query-vault
  - execute-shell
disallowed_verbs:
  - spawn-agent
preferred_model:
  claude: opus
  gemini: gemini-2.5-pro
  gpt: gpt-4o
  local: gemma-4-26B-A4B-it
  fallback_note: Fact-checking accuracy degrades significantly on lighter models. Local ship — unsloth/gemma-4-26B-A4B-it-GGUF (Q4_K_M for 24GB+ Macs, Q6_K_XL for 48GB+). Native vision for scanned docs + satellite imagery.
vault_context:
  enabled: true
  query_on_load: true
```

The fact-checker operates independently from the investigator. Spawned with its own context, it reads only the investigator's JSON output — not their reasoning — and writes `cases/{project}/data/fact-check.json` with per-claim verdicts and evidence trails.

**Verdict taxonomy:** `verified` | `unverified` | `disputed` | `false`

Full prompt bundle: `agents/fact-checker.md`.

## Skill Registry

Skills are markdown playbooks loaded via `invoke-skill(skill_id)`. Each skill lives in `skills/{skill_id}/SKILL.md` with optional reference files in `skills/{skill_id}/references/`.

| Skill ID | Path | Description | Invocable By |
|----------|------|-------------|--------------|
| `spotlight` | `skills/spotlight/SKILL.md` | Investigation orchestrator — pipeline phases, gates, cycle evaluation | orchestrator (top-level) |
| `review` | `skills/review/SKILL.md` | Post-Gate-1 HTML review artifact + structured feedback loop; re-spawns investigator on feedback submission | orchestrator, user |
| `integrations` | `skills/integrations/SKILL.md` | Routing layer for external tool integrations — Browser Harness, browser-use, Junkipedia, Noosphere C2PA, OSINT Navigator, Unpaywall. Reads live preflight status, maps investigation tasks to integrations | investigator, fact-checker, orchestrator |
| `ingest` | `skills/ingest/SKILL.md` | Knowledge archival — vault ingestion from case files | orchestrator, user |
| `report-drafting` | `skills/report-drafting/SKILL.md` | Post-Gate-1 public-facing report.html drafting — phase-by-phase methodology, replication blocks, evidence ledger. Distinct from `review` (editorial loop) and `ingest` (vault archival). | orchestrator, user |
| `monitoring` | `skills/monitoring/SKILL.md` | Monitoring orchestration — Mycroft passive signals, coJournalist projects/scouts, runtime-native fallbacks | orchestrator |
| `provenance-signing` | `skills/provenance-signing/SKILL.md` | Build a case-level provenance manifest and optionally hand it to Noosphere C2PA signing before final report delivery | orchestrator, user |
| `acquisition-graduation` | `skills/acquisition-graduation/SKILL.md` | Convert repeated Browser Harness acquisitions into durable source/domain guidance without secrets or brittle session details | investigator, fact-checker, orchestrator, user |
| `web-archiving` | `skills/web-archiving/SKILL.md` | Wayback Machine, Archive.today, local archival with chain of custody | investigator, fact-checker |
| `content-access` | `skills/content-access/SKILL.md` | Paywall access hierarchy, access_method classification | investigator, fact-checker |
| `epistemic-grounding` | `skills/epistemic-grounding/SKILL.md` | Claim-to-evidence grounding, confidence caps, and failure routing for weak or adjacent evidence | investigator, fact-checker, orchestrator, user |
| `shell-safety` | `skills/shell-safety/SKILL.md` | Safe command construction, value validation, and destructive-operation probe rules for execute-shell use | investigator, fact-checker, orchestrator, user |
| `osint` | `skills/osint/SKILL.md` | OSINT tool routing table + 150-tool catalog + OSINT Navigator integration | investigator, fact-checker, user |
| `investigate` | `skills/investigate/SKILL.md` | Step-by-step techniques: geolocation, verification, person, platform, transport, archiving | investigator, user |
| `follow-the-money` | `skills/follow-the-money/SKILL.md` | Financial methodology: UBO, offshore, budget/revenue, asset tracing | investigator, user |
| `social-media-intelligence` | `skills/social-media-intelligence/SKILL.md` | Account authenticity, coordination detection, narrative tracking | investigator, fact-checker, user |

## Sensitive Mode

When `sensitive: true` is set in this manifest (or toggled at runtime), the adapter MUST strip `fetch` and `search` from all agent `allowed_verbs` lists. All research becomes local-only — agents can only use `read-file`, `grep-files`, `list-files`, and `query-vault` for information gathering.

The adapter's enforcement point varies by runtime (pi extension / Hermes `local-gemma` routing / Goose tool allowlist / Codex tool allowlist / per-session provider binding). See `docs/integrations.md#sensitive-mode-across-runtimes`.

A sensitive investigation cannot satisfy the "document trail" readiness criterion from external sources. The orchestrator marks the investigation as **sensitive-mode constrained** at Gate 1.

To activate: set `sensitive: true` in this file or issue a runtime command.
To deactivate: set `sensitive: false` or issue a runtime command.

## Cases Directory Structure

Each investigation creates an isolated directory under `cases/`:

```
cases/{project}/
├── brief-directions.txt         # Phase 1 — approved brief
├── summary.md                   # Phase 4 — Gate 1 summary (human-readable)
├── data/
│   ├── methodology.json         # Schema: schemas/methodology.schema.json
│   ├── findings.json            # Schema: schemas/findings.schema.json
│   ├── fact-check.json          # Schema: schemas/fact-check.schema.json
│   ├── evidence-bundle.json     # Schema: schemas/evidence-bundle.schema.json
│   ├── investigation-log.json   # Schema: schemas/investigation-log.schema.json
│   ├── summary.json             # Schema: schemas/summary.schema.json
│   ├── provenance-manifest.json  # Schema: schemas/provenance-manifest.schema.json
│   └── monitoring.json          # (optional) External monitor registry for the case
└── research/
    ├── *.md                     # Scraped web content
    ├── *.json                   # Search results
    ├── archived/                # Wayback / Archive.today preservation
    └── media/                   # Images, PDFs, other media
└── evidence/
    ├── *.png                    # Screenshots and visual verification captures
    └── *.pdf                    # Downloaded or preserved source documents
```

All schemas are in `schemas/` at the repo root with `schema_version: "1.0"`.

## Schema Reference

| Schema | Path | Purpose |
|--------|------|---------|
| Findings | `schemas/findings.schema.json` | Investigation findings with sources, confidence, claim-to-evidence grounding, connections, monitoring_recommendations |
| Fact-Check | `schemas/fact-check.schema.json` | Per-claim verdicts with evidence_for/evidence_against trails and grounding assessment |
| Evidence Bundle | `schemas/evidence-bundle.schema.json` | Acquisition artifacts with method, missing-source gate, hashes, and claim links |
| Provenance Manifest | `schemas/provenance-manifest.schema.json` | Case artifact hashes, claim/verdict links, evidence refs, and optional Noosphere C2PA signing metadata |
| Methodology | `schemas/methodology.schema.json` | Investigation plan with directions, steps, tools_required, opsec_considerations |
| Investigation Log | `schemas/investigation-log.schema.json` | Append-only cycle audit trail |
| Summary | `schemas/summary.schema.json` | Gate 1 summary for review |
