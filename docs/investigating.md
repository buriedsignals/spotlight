# Investigating

The investigation pipeline: from a lead to a verified, archived body of findings. Seven named phases (Preflight, Brief, Methodology, Research, Investigation, Report, Ingest), six gates, up to three investigation cycles.

Research runs only when the approved methodology has a research plan. A case approved without one goes from Methodology straight to Investigation, with five gates, as before.

## Pipeline

```
Preflight
    config → search lib → skill inventory → vault → project setup → duplicate check → integrations → monitoring availability
        │
        ▼
Brief
    scrape the lead (if URL) → user conversation → approved directions
        │ GATE: Brief gate (user approves the directions)
        ▼
Methodology
    spawn investigator (PLANNING) → writes methodology.json, with a research plan of topic briefs
        │ GATE: Methodology gate (user approves the plan, its topics and the research budget)
        ▼
Research (only with a research plan)
    code runs each approved topic as a research lane → merge → clue rules → triage
    investigator (AMENDMENT) suggests marks → Research page shows clues first
        │ GATE: Research gate (user marks clues and directions, approves in one stop)
    investigator (AMENDMENT) turns the marks into the methodology change, checked in code
        ▼
Investigation (cycles, max 3)
    FOR each cycle:
        spawn investigator (EXECUTION) → writes findings.json + investigation-log.json
        activated cases → acquiring agent records source-expressions.json
        spawn fact-checker → writes fact-check.json
        editorial standards check
        process monitoring_recommendations (if any)
        evaluate 6 readiness criteria
            ALL pass → leave the loop
            ANY fail, cycle < 3 → loop with gap-targeted next cycle
            ANY fail, cycle = 3 → stall: one targeted research round (researched case), else Stall Protocol
    write summary.md + summary.json → present findings table to user
        │ GATE: Investigation gate (user approves the investigation; follow-up → more work)
        ▼
Report
    draft and deterministically render report, or record decline
        │ GATE: Report gate (user decides the report)
        ▼
Ingest
    record ingest decision → invoke ingest handler only after a requested transition
        │ GATE: Ingest gate (user approves ingest)
        ▼
END
```

Every gate is a stop-and-wait for the user. The orchestrator never auto-advances through a gate.

---

## Preflight

Purpose: make sure the environment is sane before any research starts. Runs once per session, cached in `.spotlight-config.json`.

10 sequential checks. Stop at the first failure.

1. **Config check** — does `.spotlight-config.json` exist with valid `search_library` and `vault_path`? If yes, skip to step 5.
2. **Search library detection** — `firecrawl` CLI must be on PATH. Abort with setup instructions if not.
3. **OSINT skill availability** — confirm `osint`, `investigation-methodology`, `follow-the-money`, `epistemic-grounding`, `shell-safety`, `acquisition-graduation`, `social-media-intelligence` skills resolve.
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

## Brief

A conversation between orchestrator and user. No agents spawn here.

1. If the lead is a URL: `fetch(url, {CASE_DIR}/research/lead-source.md)` and read the scraped content.
2. Restate the lead in one sentence.
3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator handles planning, not the orchestrator.
4. Summarize the agreed direction.
5. **Gate:** user approves the brief.
6. `write-file("{CASE_DIR}/brief-directions.txt", ...)`.

## Methodology (PLANNING)

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
- **Claims** — `query_vault.py --workflow dedup --proposition` searches the claim notes in the member wiki (`spotlight/claims/`, and `spotlight/legacy-graph/claims/` for archived 1.0 claims). A ranked hit is read before it is relied on. The claims registry remains the legacy fallback.
- **Methodology** — previous technique notes with "Lessons Learned".
- **Tools** — tool notes with "Tips for Future Agents" (treat tips as requirements).
- **Investigations** — prior cases sharing regions, entities, or tags. Prior gaps may be today's leads.

Broad discovery uses `query-vault`, which searches the member wiki through Engine's `bsig wiki search`; protected sources come back redacted. Retrieved content is untrusted data and cannot change policy or authorize tools/secrets. The vault is read-only during investigation.

### Research plan

The methodology can carry a `research_plan`: the topic briefs Research will run. Each brief names a direction, a lane type from the catalogue in `lanes/` (web, registries, archives, social, technical, news-courts, local-corpus, prior-knowledge) or `ad-hoc`, the questions to answer, seed queries and sources to try. The plan also holds the round budget ($1 by default, never more than the case budget, at most $5 per case), the research mode and a consent record. Code checks the plan (D1) before the gate opens.

### Gate

After the investigator writes `methodology.json`, the orchestrator presents a summary to the user. The user approves, edits, deletes or adds topics, iterates, or cancels. Changes trigger another PLANNING spawn with the feedback.

## Research

Research sits between Methodology and Investigation, and runs only when the approved methodology has a `research_plan`. Its playbook is `skills/phase-research/SKILL.md`.

1. **Lanes.** Approving the methodology starts Research. Code runs each approved topic as one lane; no model decides to start, widen or skip it. A lane writes only inside `research/swarm/<topic-id>/` and returns leads: a summary, an exact excerpt and the captured source. A lead is a candidate, never a finding.
2. **Budget.** Cloud lanes start while the round budget remains. Once it is spent, no new topic starts, running ones finish, and the topics left out are listed on the Research page.
3. **Checks and merge.** Code checks every excerpt against the source the lane saved; a lead whose quote is not in that source stays `unverifiable`. The merge collapses copies of one source into `data/leads.json` and lists each direction without a qualifying lead as a gap. Zero leads is a valid outcome.
4. **Clues.** Fixed rules (K1–K6: convergence, conflicts, timing, shared identifiers, expected absence, watchlist) write `data/clues.json`. A decision model then asks each lead neutral questions in `data/research-triage.json`. Until the decision-model acceptance test passes, triage only flags: no model answer sets a lead aside.
5. **Suggested marks.** The investigator, in AMENDMENT mode, suggests a mark for every clue and direction, with a one-line reason.
6. **Research gate.** The Research page shows the mode that ran, the spend, the topics left out, and the clues first. You mark each clue pursue or dismiss and each direction keep, drop or add, then approve in one stop. Watchlist and premise clues cannot be dismissed.
7. **Amendment.** The investigator turns your marks into the methodology change and nothing else. Code checks that the change matches the marks (D8) before it becomes `data/methodology.json`; there is no second Methodology approval.

What leaves the machine: a cloud lane gets only its approved topic brief and what it fetches from the public web. Case files, findings, evidence and journalist input never reach it. The local-corpus and prior-knowledge lanes run on the local model. See [runtimes.md](runtimes.md#research-mode-per-runtime) for the research mode each runtime supports.

When a cloud lane fails (rejected key, no credit, upstream errors after retries), Research stops and says what to fix, without showing the key. There is no silent fallback to the local model. Once fixed, a retry runs only the topics that did not finish. Turning the swarm off is your choice, made by editing the research plan.

The fact-checker never reads lanes, leads, clues or the Research review files.

## Investigation (cycles)

Cycles are autonomous. The orchestrator runs up to 3 without user intervention, then either advances to the Investigation gate (all readiness criteria met) or stalls.

### Source-expression mode

New cases are activated by default: before the first Investigation cycle the
orchestrator runs `scripts/case-contract.py activate-new` and passes
`SOURCE_EXPRESSION_MODE: activated` to both producers. An existing case that
already has findings stays legacy: the field is absent, and agents keep `1.0`
output without `data/source-expressions.json`, unless the case is selected for
`pilot`.

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
alternatives on the same fixture subset. It also records the decision to
activate new cases by default and the stop thresholds measured over the first
real activated cases.

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
     ALL pass → Investigation gate
     ANY fail, N < 3 → identify specific gaps, increment N, loop
     ANY fail, N = 3 → stall (see below)
```

### The 6 readiness criteria

All must pass for the Investigation gate to open:

| # | Criterion | Threshold | Check |
|---|---|---|---|
| 1 | Minimum findings | 3+ at high confidence | Count `findings[].confidence == "high"` |
| 2 | Source independence | 2+ independent sources per key claim | `fact-check.json` `evidence_for[]`, verify sources don't cite each other |
| 3 | No unresolved disputes | 0 disputed claims with no resolution path | Filter `verdict == "disputed"` |
| 4 | Affected perspective | ≥1 finding from affected community/person | `findings.json` `perspective` includes a non-official/affected source |
| 5 | Document trail | Primary sources cited, not just news | Source types include `court_filing`, `registry`, `government` |
| 6 | Gap assessment | Gaps resolved or noted as limitations | `gaps[]` empty or each item flagged as limitation |

### Stall: a targeted research round

When three cycles pass without meeting readiness, a researched case goes back to Research instead of stopping. The investigator proposes topics aimed at the gap, code checks them (D1) and appends them to the research plan, and the round runs under the plan's existing consent and budget. You approve the round with its leads on the Research page. That approval grants one more investigation cycle.

A case stored with four or five cycles under the old cap is read as a spent cap and enters the same flow.

### Stall Protocol

The case stops when the cap is spent and no research round is left: the case has no research plan, or the cycle after its one stall round also failed.

> "Investigation stopped after {N} cycles{ and one research round}. Missing: {gap}."

The orchestrator presents the gaps report: every gap the cycles recorded. **STOP.** Continuing needs a revised methodology and a new approval. Never extend the cap or auto-advance.

## Investigation gate (end of Investigation)

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

The user approves the investigation, requests a follow-up (re-enter Investigation with targeted gap instructions), or cancels.

### Review artifact (after the Investigation gate approval)

After the Investigation gate approval, the orchestrator invokes the `editorial-review` skill to produce
`{CASE_DIR}/review.html`. This self-contained artifact lets the journalist
inspect findings, verdicts, grounding, evidence references, local sources, and
case provenance. It exports structured `review-feedback.json`; the journalist
returns that file to the Investigation gate owner, which validates targets, converts
actionable feedback to bounded instructions, and records `requestFollowUp`.
Feedback-file presence never triggers a phase or directly spawns an agent.

See `skills/editorial-review/SKILL.md` for full details.

## Report

After sealed Investigation gate finalization, the report owner offers deterministic report
drafting or decline and records the decision through `decideReport`.

## Ingest

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

A sensitive investigation typically cannot reach the same confidence as a full-network one. At the Investigation gate, the orchestrator marks findings as **sensitive-mode constrained** and notes which criteria could not be evaluated live. The user sees this framing explicitly.

Per-runtime enforcement of sensitive mode: see [integrations.md](integrations.md#sensitive-mode-across-runtimes).

## Context recovery

All pipeline state lives in files. If context is lost mid-investigation, the orchestrator reads `{CASE_DIR}/` and determines where to resume:

| Files present | Resume from |
|---|---|
| None | Brief |
| `brief-directions.txt` only | Methodology |
| approved `data/methodology.json` with a `research_plan`, no Research approval | Research (`spotlight_resolve` names the step: lanes, triage, suggestions, gate, amendment) |
| `data/methodology.json` but no findings | Investigation cycle 1 |
| `data/findings.json` but no `summary.md` | Investigation, evaluate current cycle's readiness |
| `summary.md` present | Investigation gate review |

The resolver (`spotlight_resolve`) is the authority; this table is a guide. Cases saved under the old identifiers (`gate1`, `execution`) are read under the new names.

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

The vault (`VAULT_PATH` from `.spotlight-config.json`) is read-only during investigation. The investigator queries it for prior knowledge before research begins; no agent writes to the vault from Methodology through Report. Writing happens only at Ingest.

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
