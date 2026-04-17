---
name: spotlight
description: Investigation orchestrator — pipeline phases, gates, cycle evaluation, and readiness criteria for the Spotlight OSINT system
version: "1.0"
invocable_by: [orchestrator]
requires: [investigator, fact-checker]
---

# Spotlight — Investigation Orchestrator

The Spotlight orchestrator skill governs the investigation pipeline: how cycles execute, when to advance or stall, and how findings are presented at Gate 1.

---

## Pipeline Overview

Investigations proceed through iterative **cycles**, each cycle running an investigator + fact-checker pass. After each cycle, the orchestrator evaluates readiness criteria to decide whether to:

1. **Loop** — Continue to another cycle, targeting specific gaps
2. **Advance** — Move to Gate 1 when all criteria are met
3. **Stall** — Trigger stall protocol after 5+ cycles without readiness

---

## Cycle Execution

### Starting an Investigation

1. Receive the investigation brief (project ID, scope, targets)
2. Initialize the case directory:
   ```
   cases/{project}/
   └── data/
       ├── findings.json          # Schema: schemas/findings.schema.json
       ├── fact-check.json        # Schema: schemas/fact-check.schema.json
       ├── methodology.json        # Schema: schemas/methodology.schema.json
       ├── investigation-log.json  # Schema: schemas/investigation-log.schema.json
       └── summary.json            # Schema: schemas/summary.schema.json (written at Gate 1)
   ```
3. Query the vault for prior work on related targets via `query-vault`
4. Invoke `investigator` in PLANNING mode to design methodology

### Running a Cycle

1. **Investigation Phase** — Invoke `investigator` in EXECUTION mode:
   - Follow the approved methodology
   - Execute research using `fetch` and `search`
   - Write findings to `cases/{project}/data/findings.json`
   - Append each action to `cases/{project}/data/investigation-log.json`

2. **Fact-Check Phase** — Invoke `fact-checker`:
   - Read `cases/{project}/data/findings.json`
   - Conduct independent verification research
   - Write verdicts to `cases/{project}/data/fact-check.json`

3. **Cycle Evaluation** — Run readiness criteria check (see `references/pipeline.md`)

---

## Readiness Criteria

After each cycle, evaluate ALL criteria before deciding next steps:

| Criterion | Threshold | How to Check |
|-----------|-----------|-------------|
| Minimum findings | 3+ at high confidence | Count findings where confidence == "high" |
| Source independence | 2+ independent sources per key claim | Check `data/fact-check.json` `evidence_for` arrays |
| No unresolved disputes | 0 claims with "disputed" verdict and no resolution path | Check `data/fact-check.json` for disputed verdicts |
| Affected perspective | At least 1 finding from affected community/person | Check `data/findings.json` `perspective` field |
| Document trail | Primary source documents cited (not just news reports) | Check source types include court_filing, registry, government |
| Gap assessment | All gaps resolved or explicitly noted as limitations | Check `data/findings.json` `gaps` array is empty or items are noted as limitations |

**Reference:** Full cycle evaluation logic is in `references/pipeline.md`.

---

## Gate Decisions

### If ALL criteria pass → Advance to Gate 1

Present the investigation summary per the Gate 1 presentation format (`references/pipeline.md`):
- Headline: "{N} verified findings across {M} cycles"
- Findings table with claim, confidence, verdict, source count
- Methods summary from investigation-log
- Limitations from findings gaps
- Confidence assessment (margin, not just pass/fail)

Generate and write `cases/{project}/data/summary.json` per `schemas/summary.schema.json`.

### If any criteria fail and cycle < 5 → Loop

List specific gaps. Recommend what the next cycle should focus on:
- "Next cycle: find second independent source for funding claim"
- "Use `fetch` to scrape court filing referenced in interview"

### If any criteria fail and cycle >= 5 → Stall

Present stall message:
> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

Wait for user direction. Do not auto-advance.

---

## Evidence Grounding

All research must follow evidence grounding rules defined in `references/evidence-grounding.md`:

- Store all research per-case in `cases/{project}/research/`
- Scrape before cite — no finding without a scraped file
- Quote verbatim from primary sources
- Link every finding to a local file
- If cannot scrape, document the reason and adjust confidence

---

## Skill Invocations

During an investigation, these skills may be invoked:

| Skill ID | When Invoked | Purpose |
|----------|--------------|---------|
| `ingest` | At Gate 1 or investigation close | Archive findings to knowledge vault |
| `monitoring` | If `monitoring_recommendations[]` exist in findings | Configure ongoing feed monitoring |
| `web-archiving` | When evidence URLs need archival | Wayback Machine, Archive.today |
| `content-access` | When encountering paywalls or access restrictions | Bypass hierarchy and access methods |

---

## Schema Reference

| Schema | Path | Purpose |
|--------|------|---------|
| Findings | `schemas/findings.schema.json` | Investigation findings with sources, confidence, connections |
| Fact-Check | `schemas/fact-check.schema.json` | Per-claim verdicts with evidence trails |
| Methodology | `schemas/methodology.schema.json` | Investigation plan with directions, steps, tools |
| Investigation Log | `schemas/investigation-log.schema.json` | Append-only cycle audit trail |
| Summary | `schemas/summary.schema.json` | Gate 1 summary for review |

---

## Sensitive Mode

When `sensitive: true` is set in AGENTS.md, the adapter strips `fetch` and `search` from all agent `allowed_verbs`. The orchestrator adjusts:

- Research phases become local-only (`read-file`, `grep-files`, `list-files`, `query-vault`)
- All evidence must come from pre-scraped material in `cases/{project}/research/`
- Readiness criteria requiring new sources cannot be met — flag explicitly

