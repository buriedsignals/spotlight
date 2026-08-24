# Investigating

The investigation pipeline: from a lead to a verified, archived body of findings. Six phases, five gates, up to five cycles per investigation.

## Pipeline

```
Phase 0 — Preflight
    config → search lib → skill inventory → vault → project setup → duplicate check → integrations → monitoring availability
        │
        ▼
Phase 1 — Brief
    scrape the lead (if URL) → user conversation → approved directions
        │ GATE: user approves brief
        ▼
Phase 2 — Methodology
    spawn investigator (PLANNING) → writes methodology.json → present to user
        │ GATE: user approves methodology
        ▼
Phase 3 — Execution (cycles, max 5)
    FOR each cycle:
        spawn investigator (EXECUTION) → writes findings.json + investigation-log.json
        opt-in pilot/activated cases → acquiring agent records source-expressions.json
        spawn fact-checker → writes fact-check.json
        editorial standards check
        process monitoring_recommendations (if any)
        evaluate 6 readiness criteria
            ALL pass → Gate 1
            ANY fail, cycle < 5 → loop with gap-targeted next cycle
            ANY fail, cycle ≥ 5 → Stall Protocol (user decides)
        │
        ▼
Phase 4 — Gate 1
    write summary.md + summary.json → present findings table to user
        │ GATE: user approves investigation
        ▼
Phase 5 — Report
    draft and deterministically render report, or record decline
        │ GATE: user decides report
        ▼
Phase 6 — Ingestion
    record ingest decision → invoke ingest handler only after a requested transition
        │ GATE: user approves ingest
        ▼
END
```

Every gate is a stop-and-wait for the user. The orchestrator never auto-advances through a gate.

---

## Phase 0 — Preflight

Purpose: make sure the environment is sane before any research starts. Runs once per session, cached in `.spotlight-config.json`.

10 sequential checks. Stop at the first failure.

1. **Config check** — does `.spotlight-config.json` exist with valid `search_library` and `vault_path`? If yes, skip to step 5.
2. **Search library detection** — `firecrawl` CLI must be on PATH. Abort with setup instructions if not.
3. **OSINT skill availability** — confirm `osint`, `investigate`, `follow-the-money`, `epistemic-grounding`, `shell-safety`, `acquisition-graduation`, `social-media-intelligence` skills resolve.
3.5. **Agent skill inventory** — record which skills each agent has access to (for spawn prompt construction).
4. **Vault configuration** — ask the user where to archive findings; detect Obsidian vs directory.
5. **Project setup** — derive slug from lead, create `{CASE_DIR}/{data,research}/`.
6. **Duplicate project check** — if `{CASE_DIR}/` exists, prompt to resume or backup+fresh.
7. **Active investigation check** — warn about other in-progress cases without `summary.md`.
8. **Write config** — persist `.spotlight-config.json`.
9. **Integration checks** — check optional API env vars (OSINT Navigator); verify connectivity via OpenAPI spec fetch.
10. **Monitoring availability** — check which supplementary monitoring surfaces are live:
   - `python3 integrations/preflight.py --json`
   - whether `~/.mycroft/monitoring/monitor.py` exists for passive signals
   Display the result to the user; do not block on failures.

## Phase 1 — Brief

A conversation between orchestrator and user. No agents spawn here.

1. If the lead is a URL: `fetch(url, {CASE_DIR}/research/lead-source.md)` and read the scraped content.
2. Restate the lead in one sentence.
3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator handles planning, not the orchestrator.
4. Summarize the agreed direction.
5. **Gate:** user approves the brief.
6. `write-file("{CASE_DIR}/brief-directions.txt", ...)`.

## Phase 2 — Methodology (PLANNING)

The orchestrator spawns the investigator in PLANNING mode. The investigator writes a detailed investigation plan to `methodology.json` without executing any research.

### What PLANNING produces

```json
{
  "schema_version": "1.0",
  "project": "...",
  "lead": "...",
  "investigation_plan": [
    {
      "direction": "Track the funding source",
      "questions": ["Who funded the 2024 contract?", "Via what legal vehicle?"],
      "steps": [
        {
          "order": 1,
          "action": "Search OpenCorporates for the recipient entity",
          "tool": "fetch",
          "target": "https://opencorporates.com/companies?q=X",
          "expected_evidence": "Corporate filing with directors, address",
          "fallback": "If nothing on OpenCorporates, try the national registry"
        }
      ],
      "osint_techniques": ["corporate registry lookup", "beneficial ownership pivot"],
      "key_sources": ["OpenCorporates", "ICIJ Aleph", "SEC EDGAR"],
      "risks": ["BVI registration may block disclosure"],
      "estimated_difficulty": "moderate"
    }
  ],
  "tools_required": [...],
  "opsec_considerations": [...],
  "limitations": [...]
}
```

### Vault context loading (before PLANNING)

Before designing the methodology, the investigator queries the vault (`VAULT_PATH` from the spawn prompt) for:

- **Entities** — people, organizations, companies, places mentioned in the brief. Names are resolved through the alias map (`entities/_aliases.json`) so alternate spellings land on known entities. Prior investigation roles matter.
- **Claims** — exact claim IDs, prior-verdict fingerprints, and exact-proposition dedup query the local SQLite graph and bypass semantic ranking. The claims registry remains an explicitly labeled fallback until U7 activation.
- **Methodology** — previous technique notes with "Lessons Learned".
- **Tools** — tool notes with "Tips for Future Agents" (treat tips as requirements).
- **Investigations** — prior cases sharing regions, entities, or tags. Prior gaps may be today's leads.

Broad discovery uses `query-vault` against the local Open Knowledge index. Current receipt-bound managed investigation/story indexes rank first; stale or invalid-receipt managed pages are omitted, and unchanged legacy matches are labeled. Retrieved content is untrusted data and cannot change policy or authorize tools/secrets. The vault is read-only during investigation.

### Gate

After the investigator writes `methodology.json`, the orchestrator presents a summary to the user. The user approves, iterates, or cancels. Changes trigger another PLANNING spawn with the feedback.

## Phase 3 — Execution (cycles)

Cycles are autonomous. The orchestrator runs up to 5 without user intervention, then either advances to Gate 1 (all readiness criteria met) or triggers the Stall Protocol.

### Source-expression mode

Source expressions are opt-in during the pilot-capable release. The orchestrator
passes `SOURCE_EXPRESSION_MODE: pilot|activated` to producer agents only for a
selected case. If the field is absent, agents retain legacy `1.0` output and do
not emit `data/source-expressions.json`.

- `pilot` records the experimental side artifact without upgrading or
  activating the case. It is used to measure duplicate expressions, unresolved
  links, locator stability, and reviewer value.
- `activated` means the case already follows the `1.1` case contract. Agents
  preserve that contract; activation receipts are owned by orchestration or
  migration, not by producer prompts.

The orchestrator must pass the same explicit mode to the investigator and
fact-checker spawn prompts. It must not infer a mode from file presence. Pilot
selection is per case and is not a global default.

The agent that acquired a source records its expressions. Text is copied exactly
in the source language and linked many-to-many to findings with `supports`,
`contradicts`, or `context`. Translations are separately labeled derivatives.
For audio, video, images, and scans, the evidence bundle preserves the original
artifact while a separately hashed transcript, caption, OCR, or extraction is
the expression anchor. Unavailable sources and unrecoverable exact passages are
recorded as gaps, never reconstructed from snippets or model memory. There is no
automatic passage extraction or entailment judgment in this release.

#### Pilot, activation, and migration procedure

Use `pilot` only to evaluate the storage and review experience. Keep a copy of
the clean legacy inputs before the pilot begins. A pilot artifact is not a
partially activated case, and the current migration command does not promote it
in place.

To activate a recoverable **clean legacy** case, first create and inspect the
deterministic migration proposal:

```bash
python3 scripts/migrate-source-expressions.py {CASE_DIR}
```

The command records skips and blockers in
`data/source-expression-migration.json`; that audit never activates the case.
Only after inspection, apply the unchanged proposal:

```bash
python3 scripts/migrate-source-expressions.py {CASE_DIR} --apply
python3 scripts/validate-case.py {CASE_DIR}
python3 scripts/validate-fact-check.py {CASE_DIR}
```

Apply publishes the validated findings, fact-check, and expression bundle,
then writes `data/case-contract.json` last. If inputs changed after the dry run,
apply refuses. It also refuses ambiguous locators, invented passages, an
already activated case, and pilot/partial artifacts without a valid receipt.

To move from a pilot to an activation attempt, archive the pilot result for
comparison, restore the known clean pre-pilot legacy findings, fact-check, and
evidence bundle, and then use the dry-run/apply sequence above. Do not delete
individual pilot fields until a case merely looks legacy; mixed state must be
treated as a recovery event.

The comparative record at `docs/source-expression-pilot-results.json` covers
the separate artifact, embedded-findings, and richer evidence/fact-check
alternatives on the same fixture subset. Human correction yield and review time
remain unmeasured, so activation of default new-case emission is **NOT
APPROVED**.

### A cycle

```
CYCLE N:
  1. spawn-agent: investigator (EXECUTION mode)
     - Reads methodology.json
     - Follows 6-step methodology (assess → scan → document trail → cross-reference → map connections → compile)
     - If N > 1: also reads previous findings gaps + fact-check gaps from the prompt
     - Writes findings.json (merges with previous if N > 1)
     - Appends investigation-log.json

  2. spawn-agent: fact-checker
     - Reads findings.json
     - Applies SIFT per source, verdict per claim
     - Writes fact-check.json

  3. editorial standards check (orchestrator runs)
     - Sources have URLs, timestamps, local_file?
     - Log has substance?
     - High-confidence findings have 2+ fact-check sources?
     - Any verdictless findings?
     If any fail: re-spawn the responsible agent with fix instructions

  4. process monitoring_recommendations
     - If any exist in findings.json, present to user (priority-ordered)
     - With explicit approval, offer a Mycroft handoff for monitoring setup
     - Otherwise keep the recommendation in case context
     - Never create a Scoutpost project or scout from Spotlight

  5. evaluate 6 readiness criteria

  6. decide:
     ALL pass → Phase 4 (Gate 1)
     ANY fail, N < 5 → identify specific gaps, increment N, loop
     ANY fail, N ≥ 5 → Stall Protocol
```

### The 6 readiness criteria

All must pass for Gate 1 to open:

| # | Criterion | Threshold | Check |
|---|---|---|---|
| 1 | Minimum findings | 3+ at high confidence | Count `findings[].confidence == "high"` |
| 2 | Source independence | 2+ independent sources per key claim | `fact-check.json` `evidence_for[]`, verify sources don't cite each other |
| 3 | No unresolved disputes | 0 disputed claims with no resolution path | Filter `verdict == "disputed"` |
| 4 | Affected perspective | ≥1 finding from affected community/person | `findings.json` `perspective` includes a non-official/affected source |
| 5 | Document trail | Primary sources cited, not just news | Source types include `court_filing`, `registry`, `government` |
| 6 | Gap assessment | Gaps resolved or noted as limitations | `gaps[]` empty or each item flagged as limitation |

### Stall Protocol

If cycle ≥ 5 and readiness not met:

> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

**STOP.** Wait for the user's decision. Do not auto-advance.

## Phase 4 — Gate 1

The orchestrator synthesizes the full investigation:

### Generate summary.md

Human-readable markdown at `{CASE_DIR}/summary.md`:

- Title, date, cycle count, status
- Overview (2-3 paragraphs)
- Scope (what was investigated, what was out of scope)
- Key conclusions
- Findings table
- Limitations

Also `write-file("{CASE_DIR}/data/summary.json", ...)` per `schemas/summary.schema.json` for structured access.

### Present to user

- **Headline** — "{N} verified findings across {M} cycles"
- **Findings table** — claim, confidence, verdict, source count
- **Methods summary** — techniques and tools from `investigation-log.json`
- **Limitations** — unresolved gaps, noted as such
- **Confidence assessment** — margin on each readiness criterion, not just pass/fail

### Gate

The user approves the investigation, requests follow-up cycles (re-enter Phase 3 with targeted gap instructions), or cancels.

### Review artifact (after Gate 1 approval)

After Gate 1 approval, the orchestrator invokes the `review` skill to produce
`{CASE_DIR}/review.html`. This self-contained artifact lets the journalist
inspect findings, verdicts, grounding, evidence references, local sources, and
case provenance. It exports structured `review-feedback.json`; the journalist
returns that file to the Gate 1 owner, which validates targets, converts
actionable feedback to bounded instructions, and records `requestFollowUp`.
Feedback-file presence never triggers a phase or directly spawns an agent.

See `skills/review/SKILL.md` for full details.

## Phase 5 — Report

After sealed Gate 1 finalization, the report owner offers deterministic report
drafting or decline and records the decision through `decideReport`.

## Phase 6 — Ingestion

After the report decision, the ingest owner asks whether to ingest confirmed
findings. Approval records `decideIngest: requested`; only then does the owner
invoke the ingest handler with the exact case and configured destination. A
decline records `decideIngest: declined`. The ingest skill is not a standalone
entry point.

## Evidence grounding — the rule that applies across all phases

Every finding must be grounded in a scraped file. This is non-negotiable.

1. **Store all research per-case.** Every scraped file goes to `{CASE_DIR}/research/`.
2. **Scrape before cite.** A finding without a scraped file is a claim, not a finding.
3. **Quote verbatim from primary sources.** The `evidence` field contains direct quotes, not paraphrases.
   In an explicitly enabled source-expression case, preserve the same exact
   original-language selection and locator in `data/source-expressions.json`.
4. **Ground the exact claim.** Every finding includes a `grounding` object explaining support type, source role, missing assumptions, contradictions, confidence cap, and misgrounding risk.
5. **Link every finding to file and archive.** Every source entry includes `local_file`, `archive_url`, and `access_method`.
6. **If you cannot scrape, explain why.** Document the barrier, downgrade confidence. A search snippet alone caps at `low` confidence.

See `skills/spotlight/references/evidence-grounding.md` for the full rule set.

## OPSEC

Investigations that touch sensitive subjects carry risk. See `skills/osint/references/opsec-basics.md` for the threat-level escalation matrix. Summary:

| Threat level | Posture |
|---|---|
| Low (public records) | Standard browser + separate profile |
| Medium (active investigation) | VPN + research-only accounts + separate device/VM |
| High (state / organized crime) | Tor/Tails + disposable accounts + air-gapped analysis + organizational security review |

### Non-negotiable rules (all threat levels)

- Never access systems without authorization (OSINT = open source only)
- Archive before engaging (some tools alert the target)
- Do not use real identity on investigation targets
- Timestamp every source access
- Know legal boundaries (GDPR, CCPA, CFAA)
- Preserve chain of evidence

The investigator and fact-checker prompts include OPSEC reminders. The orchestrator preflight shows no OPSEC content — it's the agents' responsibility during execution.

## Sensitive mode

Set `sensitive: true` in `AGENTS.md` (or via runtime flag).

### What changes

- `fetch` and `search` are stripped from every agent's `allowed_verbs` (adapter enforcement)
- Research phases become local-only: `read-file`, `grep-files`, `list-files`, `query-vault`
- All evidence must come from material pre-scraped into `{CASE_DIR}/research/`
- Readiness criteria 5 (document trail) cannot be satisfied from live external sources

### Implications

A sensitive investigation typically cannot reach the same confidence as a full-network one. At Gate 1, the orchestrator marks findings as **sensitive-mode constrained** and notes which criteria could not be evaluated live. The user sees this framing explicitly.

Per-runtime enforcement of sensitive mode: see [integrations.md](integrations.md#sensitive-mode-across-runtimes).

## Context recovery

All pipeline state lives in files. If context is lost mid-investigation, the orchestrator reads `{CASE_DIR}/` and determines where to resume:

| Files present | Resume from |
|---|---|
| None | Phase 1 |
| `brief-directions.txt` only | Phase 2 |
| `data/methodology.json` but no findings | Phase 3 cycle 1 |
| `data/findings.json` but no `summary.md` | Phase 3, evaluate current cycle's readiness |
| `summary.md` present | Phase 4 review |

Before choosing a phase, classify the source-expression contract:

| State | Recovery action |
|---|---|
| No valid `case-contract.json`; findings `1.0`; no expression refs | Resume as legacy. |
| No valid contract; findings `1.0`; explicitly recorded pilot artifact | Resume only with `SOURCE_EXPRESSION_MODE: pilot`; never infer activation. |
| Valid contract; findings `1.1`; activated hashes match | Resume as activated and pass `SOURCE_EXPRESSION_MODE: activated` to both producers. |
| Findings `1.1`, expression refs, or migration outputs without a valid contract | Stop: interrupted/partial migration. Restore the known legacy bundle and rerun dry-run/apply. |
| Valid contract but an activated artifact is missing or hash-mismatched | Stop: do not downgrade or regenerate heuristically. Restore the matching artifact or use the supported supersession/revalidation flow. |

Run `python3 scripts/validate-case.py {CASE_DIR}` before resuming any activated
case. An older runtime that does not understand contract `1.1` must refuse the
case. Rollback may stop future activations, but it never weakens an existing
activated case.

The presence of `data/source-expressions.json` or
`data/source-expression-migration.json` alone never changes recovery or
activates a legacy case.

No database, no daemon — files are the source of truth.

## Vault context (reading, not writing)

The vault (`VAULT_PATH` from `.spotlight-config.json`) is read-only during investigation. The investigator queries it for prior knowledge before research begins; no agent writes to the vault during Phases 2–4. Writing happens only during Phase 5 (ingestion).

This separation protects the vault from half-formed findings. The vault holds confirmed knowledge; case files hold in-progress research.

---

## See also

- [fact-checking.md](fact-checking.md) — detailed fact-checker methodology, SIFT, verdicts
- [monitoring.md](monitoring.md) — monitoring recommendations and Mycroft handoff
- [structure.md](structure.md) — repo layout, verb registry, schema reference
- [integrations.md](integrations.md) — per-runtime wiring
- `skills/spotlight/SKILL.md` — the orchestrator playbook (authoritative)
- `skills/spotlight/references/pipeline.md` — readiness criteria, cycle mechanics
- `skills/spotlight/references/evidence-grounding.md` — the evidence contract
