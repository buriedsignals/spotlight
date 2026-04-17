# Investigating

The investigation pipeline: from a lead to a verified, archived body of findings. Six phases, five gates, up to five cycles per investigation.

## Pipeline

```
Phase 0 — Preflight
    config → search lib → skill inventory → vault → project setup → duplicate check → integrations → monitoring preflight
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
Phase 5 — Ingestion
    invoke-skill("ingest") → writes vault notes, registries, _INDEX.md
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
3. **OSINT skill availability** — confirm `osint`, `investigate`, `follow-the-money`, `social-media-intelligence` skills resolve.
3.5. **Agent skill inventory** — record which skills each agent has access to (for spawn prompt construction).
4. **Vault configuration** — ask the user where to archive findings; detect Obsidian vs directory.
5. **Project setup** — derive slug from lead, create `cases/{project}/{data,research}/`.
6. **Duplicate project check** — if `cases/{project}/` exists, prompt to resume or backup+fresh.
7. **Active investigation check** — warn about other in-progress cases without `summary.md`.
8. **Write config** — persist `.spotlight-config.json`.
9. **Integration checks** — check optional API env vars (OSINT Navigator); verify connectivity via OpenAPI spec fetch.
10. **Monitoring preflight** — `python3 monitoring/feeds/preflight.py --json` reports per-source status. Display to user; do not block on failures (feeds are supplementary).

## Phase 1 — Brief

A conversation between orchestrator and user. No agents spawn here.

1. If the lead is a URL: `fetch(url, cases/{project}/research/lead-source.md)` and read the scraped content.
2. Restate the lead in one sentence.
3. Ask 1–3 clarifying questions if scope, angle, or priority is unclear. Keep it tight — the investigator handles planning, not the orchestrator.
4. Summarize the agreed direction.
5. **Gate:** user approves the brief.
6. `write-file("cases/{project}/brief-directions.txt", ...)`.

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

- **Entities** — people, organizations, companies, places mentioned in the brief. Prior investigation roles matter.
- **Methodology** — previous technique notes with "Lessons Learned".
- **Tools** — tool notes with "Tips for Future Agents" (treat tips as requirements).
- **Investigations** — prior cases sharing regions, entities, or tags. Prior gaps may be today's leads.

Uses `read-file` on registries + `query-vault` for semantic search. Vault is read-only during investigation.

### Gate

After the investigator writes `methodology.json`, the orchestrator presents a summary to the user. The user approves, iterates, or cancels. Changes trigger another PLANNING spawn with the feedback.

## Phase 3 — Execution (cycles)

Cycles are autonomous. The orchestrator runs up to 5 without user intervention, then either advances to Gate 1 (all readiness criteria met) or triggers the Stall Protocol.

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
     - Configure approved monitors in data/monitoring.json

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

Human-readable markdown at `cases/{project}/summary.md`:

- Title, date, cycle count, status
- Overview (2-3 paragraphs)
- Scope (what was investigated, what was out of scope)
- Key conclusions
- Findings table
- Limitations

Also `write-file("cases/{project}/data/summary.json", ...)` per `schemas/summary.schema.json` for structured access.

### Present to user

- **Headline** — "{N} verified findings across {M} cycles"
- **Findings table** — claim, confidence, verdict, source count
- **Methods summary** — techniques and tools from `investigation-log.json`
- **Limitations** — unresolved gaps, noted as such
- **Confidence assessment** — margin on each readiness criterion, not just pass/fail

### Gate

The user approves the investigation, requests follow-up cycles (re-enter Phase 3 with targeted gap instructions), or cancels.

### Review artifact (after Gate 1 approval)

After the Gate 1 approval, the orchestrator invokes the `review` skill to produce `cases/{project}/review.html`. This is a self-contained HTML file — no server, no CDN — that the journalist can open in any browser to inspect findings and submit structured feedback. Feedback downloads as `review-feedback.json`; dropping it into `cases/{project}/data/` and re-running `/spotlight` triggers Mode B of the review skill, which re-spawns the investigator with feedback-targeted instructions and regenerates the HTML.

This turns Gate 1 from a one-shot approval into an iterative editorial loop. Skip it to proceed straight to ingestion.

See `skills/review/SKILL.md` for full details.

## Phase 5 — Ingestion

After Gate 1 approval:

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes: `invoke-skill("ingest")` — 7-step archival into vault notes + registries + `_INDEX.md`. See the ingest skill for details.
- If no: pipeline ends. Case files remain in `cases/{project}/` for future reference or later ingestion.

## Evidence grounding — the rule that applies across all phases

Every finding must be grounded in a scraped file. This is non-negotiable.

1. **Store all research per-case.** Every scraped file goes to `cases/{project}/research/`.
2. **Scrape before cite.** A finding without a scraped file is a claim, not a finding.
3. **Quote verbatim from primary sources.** The `evidence` field contains direct quotes, not paraphrases.
4. **Link every finding to file.** Every source entry includes `local_file` pointing to `cases/{project}/research/`.
5. **If you cannot scrape, explain why.** Document the barrier, downgrade confidence. A search snippet alone caps at `low` confidence.

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
- All evidence must come from material pre-scraped into `cases/{project}/research/`
- Readiness criteria 5 (document trail) cannot be satisfied from live external sources

### Implications

A sensitive investigation typically cannot reach the same confidence as a full-network one. At Gate 1, the orchestrator marks findings as **sensitive-mode constrained** and notes which criteria could not be evaluated live. The user sees this framing explicitly.

Per-runtime enforcement of sensitive mode: see [integrations.md](integrations.md#sensitive-mode-across-runtimes).

## Context recovery

All pipeline state lives in files. If context is lost mid-investigation, the orchestrator reads `cases/{project}/` and determines where to resume:

| Files present | Resume from |
|---|---|
| None | Phase 1 |
| `brief-directions.txt` only | Phase 2 |
| `data/methodology.json` but no findings | Phase 3 cycle 1 |
| `data/findings.json` but no `summary.md` | Phase 3, evaluate current cycle's readiness |
| `summary.md` present | Phase 4 review |

No database, no daemon — files are the source of truth.

## Vault context (reading, not writing)

The vault (`VAULT_PATH` from `.spotlight-config.json`) is read-only during investigation. The investigator queries it for prior knowledge before research begins; no agent writes to the vault during Phases 2–4. Writing happens only during Phase 5 (ingestion).

This separation protects the vault from half-formed findings. The vault holds confirmed knowledge; case files hold in-progress research.

---

## See also

- [fact-checking.md](fact-checking.md) — detailed fact-checker methodology, SIFT, verdicts
- [monitoring.md](monitoring.md) — feed framework, preflight, scout lifecycle
- [structure.md](structure.md) — repo layout, verb registry, schema reference
- [integrations.md](integrations.md) — per-runtime wiring
- `skills/spotlight/SKILL.md` — the orchestrator playbook (authoritative)
- `skills/spotlight/references/pipeline.md` — readiness criteria, cycle mechanics
- `skills/spotlight/references/evidence-grounding.md` — the evidence contract
