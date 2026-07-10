---
name: spotlight
description: Runtime contract for the Spotlight OSINT investigation system
version: "1.0"
runtime_version: "1"
sensitive: false
---

# Spotlight — Runtime Contract

> **TODO (Tom, 2026-07-10):** run the **26b + e4b RLM full-investigation co-residency test**
> on the M4 Pro 48 GB (and sanity-check the 31b tier the same way) — the last unknown before
> calling the sovereign-local mid tier real. Switch = edit `SPOTLIGHT_GGUF_PATH` +
> `SPOTLIGHT_MODEL_TIER=26b` in `$SPOTLIGHT_DIR/.env`, `spotlight-local --stop`, then
> `spotlight <case> "<brief>"`. Watch: RAM headroom under KV growth + subagents, compaction
> fires at the 26b profile (~24.5k) on the e4b, `fetch --rlm` distills. Also: fresh-install
> smoke test of the new launcher path, and run the **v3 tune + 26b through the OSINT facet
> benchmark** (the going-sovereign page has a placeholder for the tuned facet number).
> Record results in `tools/fine-tuning/docs/local-serving-efficiency.md` (experiment log)
> and delete this TODO.
>
> **TODO — verification rigor (the v2 tune focus, observed 2026-07-09 chain test):** the
> 12b now *executes* the pipeline on bare approvals; what it fabricates is **verification
> evidence**. Grounded failures from run `GOLD-gold-inv-ef-0`: (1) fact-check verdict FC1
> cites "corporate registry (Zefix)" as confirming a claim when the in-run Zefix fetch
> returned a bot-wall/0 results; (2) FC2 cites "official bylaws" that were never fetched;
> (3) the investigator read the Zefix bot-wall as "no entity registered" instead of
> escalating to `browse` (the U20a gap); (4) empty-assistant mirroring after degenerate
> turns (see `tools/fine-tuning/docs/tuning-12b.md`).
>
> **Chain-test verdict (2026-07-10 00:18, neutral approvals only):** turn 1 = full cycle +
> gate stop (15.5 min); turn 2 = 52 min of REAL work (research cycle, fact-check
> delegation, findings.json + evidence-bundle.json written; compaction fired 3× incl.
> inside a subagent — the context system held) but the **closing gate presentation came
> back empty**; turn 3 mirrored the empty (1 token) → stall. So the v2 behavioral target
> is exactly: *end every turn with a non-empty gate presentation; never mirror a prior
> empty turn.* A one-line user nudge ("You returned an empty reply — respond with text…")
> demonstrably breaks the mirror → **guard #0 (no training): auto-nudge once from the
> launcher/driver when the final text is empty** (an orchestrator turn should never end
> empty, so the retry is always safe). Log: scratchpad chain-test.log; run db
> `harness/flue/data/gold-gold-inv-ef-0.db`.
>
> **How to train it FAST — reuse, don't re-run (no 100-loop grind):**
> 1. **Ship the deterministic guard first (no training, today):** validate
>    `fact-check.json` on write — any `status:"verified"` whose
>    `verification_evidence` doesn't reference an existing file under
>    `{CASE_DIR}/research|evidence/` containing the claim's entities gets bounced back
>    to the fact-checker ONCE with the rejection reason (extend
>    `scripts/validate-case.py`; the schemas already exist). This both blocks fabricated
>    verdicts in production and *generates corrected-turn training pairs for free*.
> 2. **Mine the runs we already have:** the durable dbs (`harness/flue/data/gold-*.db`)
>    hold this week's real trajectories — good turns AND the exact failure contexts.
>    Extract the failure turns; no new investigations needed.
> 3. **Teacher-correct at the turn level:** replay each failure context through the 31B
>    on OpenRouter (the proven harness-logic ladder) with a rigor-focused fact-checker
>    prompt to produce the corrected turn ("unverified — source unavailable (bot-wall);
>    escalating to browse" / verdicts that cite real on-disk files). Turn-level
>    relabeling = dozens of contrastive pairs in hours for a few dollars.
> 4. **Machine-checkable gold filter:** a trajectory enters the v2 dataset only if every
>    "verified" verdict passes the guard from (1) — data QA becomes automatic, killing
>    the eyeball-loop iteration cost that burned v1/v2.
> 5. **Role-scoped masked SFT:** train on fact-checker (and investigator-escalation)
>    turns with the existing Unsloth pipeline (`scripts/gemma4-12b/`, transformers
>    override + `train_on_responses_only` + Unsloth-native merge — all proven); mix with
>    the 12 gold + empty-turn-recovery examples. One RunPod cycle, ~$3–5.
> 6. **Eval without loops:** extend the neutral-approval chain-test driver with a rigor
>    grader (every verified claim → cited file exists + contains the claim tokens);
>    gold cases are the fixtures. Pass/fail per run, no manual reading.

## Session Preflight

Before coding in this project on a Buried Signals dev machine, read the shared `coding-rules` skill (`kit/coding-rules/SKILL.md` in the sibling shared-skills repo, if present). It is the canonical source for workflow routing, coding standards, Jujutsu/version-control rules, GitHub operations, and parallel-agent isolation. Local instructions below add project-specific constraints.

## Tool Verb Registry

Fixed vocabulary of abstract operations. Every runtime adapter MUST implement all verbs. If a verb is unsupported, the adapter raises an explicit error at load time.

| Verb | Signature | Semantics | Universal backing |
|------|-----------|-----------|-------------------|
| `fetch` | `fetch(url, output_path)` | Scrape URL content, save to file | `integrations.scraping` (Crawl4AI; Firecrawl fallback if keyed) |
| `search` | `search(query, output_path, limit)` | Web search, save results to file | `integrations.search` (SearXNG; Firecrawl union optional) |
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

- **PLANNING** — Analyzes the brief, queries the vault for prior work, designs methodology, writes `{CASE_DIR}/data/methodology.json`
- **EXECUTION** — Follows approved methodology, executes research using `fetch` and `search`, writes `{CASE_DIR}/data/findings.json` and appends to `{CASE_DIR}/data/investigation-log.json`

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

The fact-checker operates independently from the investigator. Spawned with its own context, it reads only the investigator's JSON output — not their reasoning — and writes `{CASE_DIR}/data/fact-check.json` with per-claim verdicts and evidence trails.

**Verdict taxonomy:** `verified` | `partially_verified` | `unverified` | `disputed` | `false` | `mischaracterized`

Full prompt bundle: `agents/fact-checker.md`.

## Skill Registry

Skills are markdown playbooks loaded via `invoke-skill(skill_id)`. Each skill lives in `skills/{skill_id}/SKILL.md` with optional reference files in `skills/{skill_id}/references/`.

`install-spotlight.sh` surfaces exactly the skills listed in `skills.manifest` (the engine-resolved set, generated by `bsig skills resolve`; the engine catalog is the source of truth) into the chosen harness's `skills/spotlight/` dir. Regenerate the manifest when the catalog/skill set changes.

| Skill ID | Path | Description | Invocable By |
|----------|------|-------------|--------------|
| `spotlight` | `skills/spotlight/SKILL.md` | Investigation orchestrator — pipeline phases, gates, cycle evaluation | orchestrator (top-level) |
| `review` | `skills/review/SKILL.md` | Post-Gate-1 HTML review artifact + structured feedback loop; re-spawns investigator on feedback submission | orchestrator, user |
| `integrations` | `skills/integrations/SKILL.md` | Routing layer for external tool integrations — dev-browser, Junkipedia, Noosphere C2PA, OSINT Navigator, Scoutpost, Unpaywall. Reads live preflight status, maps investigation tasks to integrations | investigator, fact-checker, orchestrator |
| `ingest` | `skills/ingest/SKILL.md` | Knowledge archival — vault ingestion from case files | orchestrator, user |
| `report-drafting` | `skills/report-drafting/SKILL.md` | Post-Gate-1 hybrid report: model-authored localized framing and priority in `data/report-draft.json`; deterministic finding-reference, verdict, confidence, escaping, and artifact rendering. | orchestrator, user |
| `monitoring` | `skills/monitoring/SKILL.md` | Monitoring orchestration — Mycroft passive signals, coJournalist projects/scouts, runtime-native fallbacks | orchestrator |
| `provenance-signing` | `skills/provenance-signing/SKILL.md` | Build a case-level provenance manifest and optionally hand it to Noosphere C2PA signing before final report delivery | orchestrator, user |
| `acquisition-graduation` | `skills/acquisition-graduation/SKILL.md` | Convert repeated dev-browser acquisitions into durable source/domain guidance without secrets or brittle session details | investigator, fact-checker, orchestrator, user |
| `web-archiving` | `skills/web-archiving/SKILL.md` | Wayback Machine, Archive.today, local archival with chain of custody | investigator, fact-checker |
| `content-access` | `skills/content-access/SKILL.md` | Paywall access hierarchy, access_method classification | investigator, fact-checker |
| `epistemic-grounding` | `skills/epistemic-grounding/SKILL.md` | Claim-to-evidence grounding, confidence caps, and failure routing for weak or adjacent evidence | investigator, fact-checker, orchestrator, user |
| `shell-safety` | `skills/shell-safety/SKILL.md` | Safe command construction, value validation, and destructive-operation probe rules for execute-shell use | investigator, fact-checker, orchestrator, user |
| `osint` | `skills/osint/SKILL.md` | OSINT tool routing table + 150-tool catalog + OSINT Navigator integration | investigator, fact-checker, user |
| `investigate` | `skills/investigate/SKILL.md` | Step-by-step techniques: geolocation, verification, person, platform, transport, archiving | investigator, user |
| `follow-the-money` | `skills/follow-the-money/SKILL.md` | Financial methodology: UBO, offshore, budgets, assets, public blockchain tracing | investigator, user |
| `technical-investigation` | `skills/technical-investigation/SKILL.md` | Passive technical investigation: indicators, infrastructure history, local document/email metadata, public GitHub history, verified indicator export | investigator, fact-checker, orchestrator, user |
| `social-media-intelligence` | `skills/social-media-intelligence/SKILL.md` | Account authenticity, coordination detection, narrative tracking | investigator, fact-checker, user |

## Sensitive Mode

When `sensitive: true` is set in this manifest (or toggled at runtime), the adapter MUST strip `fetch` and `search` from all agent `allowed_verbs` lists. All research becomes local-only — agents can only use `read-file`, `grep-files`, `list-files`, and `query-vault` for information gathering.

The adapter's enforcement point varies by runtime (pi extension / Hermes `local-gemma` routing / Goose tool allowlist / Codex tool allowlist / per-session provider binding). See `docs/integrations.md#sensitive-mode-across-runtimes`.

A sensitive investigation cannot satisfy the "document trail" readiness criterion from external sources. The orchestrator marks the investigation as **sensitive-mode constrained** at Gate 1.

To activate: set `sensitive: true` in this file or issue a runtime command.
To deactivate: set `sensitive: false` or issue a runtime command.

### Anonymized fetch (Tor) — a distinct posture

`sensitive` mode and **anonymized fetch** are two *different* postures; do not conflate them:

- **`sensitive: true`** = **no external egress.** `fetch`/`search` are stripped; research is local-only.
- **anonymized fetch (Tor)** = **egress still happens, but the operator's IP is hidden.** Use it when you *must* scrape a target-of-investigation without revealing that the investigation is looking (KTD8 / U7). The `fetch` seam routes the Crawl4AI browser through the local Tor SOCKS proxy (`socks5://127.0.0.1:9050`).

Triggering (per-run is the default posture):

- **Per-run:** set `SPOTLIGHT_ANONYMIZE_FETCH=true` (or `anonymize_fetch: true` in this manifest's frontmatter) — every `fetch` in the run egresses via Tor.
- **Per-fetch override:** pass `--tor` / `--no-tor` to a single `integrations.scraping` call.

A Tor-proxied fetch **never silently falls back to a direct (de-anonymizing) fetch** — on a Tor failure the seam raises, and the operator chooses to re-run `--no-tor` (revealing their IP) or abort. Tor exits are widely blocklisted, so an anonymized fetch of a hard target may simply fail; that is by design, not a bug.

## Cases Directory Structure

Each investigation creates an isolated directory under the configured
`case_workspace_root` from `.spotlight-config.json`. The orchestrator resolves
that path once as `CASE_ROOT`, then passes the concrete case directory to agents
as `CASE_DIR`. Do not infer the case path from the knowledge vault path.

```
{CASE_DIR}/
├── brief-directions.txt         # Phase 1 — approved brief
├── summary.md                   # Phase 4 — Gate 1 summary (human-readable)
├── data/
│   ├── methodology.json         # Schema: schemas/methodology.schema.json
│   ├── findings.json            # Schema: schemas/findings.schema.json
│   ├── fact-check.json          # Schema: schemas/fact-check.schema.json
│   ├── report-draft.json        # Schema: schemas/report-draft.schema.json (when report requested)
│   ├── report-declined.json     # Explicit Phase 5 decline marker (when report skipped)
│   ├── ingestion.json           # Case-local Phase 6 transition/receipt
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
