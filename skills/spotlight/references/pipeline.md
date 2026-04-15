# Pipeline Reference

Readiness criteria, cycle mechanics, stall protocol, and Gate 1 presentation format. The orchestrator evaluates these after each investigation cycle to decide whether to loop or advance.

---

## Readiness Criteria

| Criterion | Threshold | How to Check |
|-----------|-----------|-------------|
| Minimum findings | 3+ at high confidence | Count findings where confidence == "high" |
| Source independence | 2+ independent sources per key claim | Check `data/fact-check.json` `evidence_for` arrays |
| No unresolved disputes | 0 claims with "disputed" verdict and no resolution path | Check `data/fact-check.json` for disputed verdicts |
| Affected perspective | At least 1 finding from affected community/person | Check `data/findings.json` `perspective` field |
| Document trail | Primary source documents cited (not just news reports) | Check source types include court_filing, registry, government |
| Gap assessment | All gaps resolved or explicitly noted as limitations | Check `data/findings.json` `gaps` array is empty or items are noted as limitations |

---

## Cycle Evaluation Logic

After each investigation cycle, run this evaluation:

1. `read-file(cases/{project}/data/findings.json)`
2. `read-file(cases/{project}/data/fact-check.json)`
3. For each criterion in the readiness table:
   - Check the condition against the data
   - Report pass/fail with specifics (e.g., "PASS: 4 high-confidence findings" or "FAIL: only 1 independent source for claim about contract award")
4. **If ALL criteria pass:** advance to Gate 1
5. If `data/findings.json` contains `monitoring_recommendations[]` and monitoring is configured: process recommendations via `invoke-skill(monitoring)`
6. **If any fail and cycle < 5:** list specific gaps, recommend what the next cycle should focus on (e.g., "Next cycle: find second independent source for funding claim, use `fetch` to scrape court filing referenced in interview")
7. **If any fail and cycle >= 5:** trigger stall protocol

---

## Stall Protocol

When an investigation has completed 5 or more cycles without meeting all readiness criteria:

> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

Present this to the user and wait for direction. Do not auto-advance.

---

## Gate 1 Presentation Format

When all readiness criteria pass, present the investigation for review:

**Headline:** "{N} verified findings across {M} cycles"

**Findings table:**

| # | Claim | Confidence | Fact-Check Verdict | Source Count |
|---|-------|------------|-------------------|-------------|
| 1 | ... | high | verified | 3 |

**Methods summary:** Techniques and tools used, drawn from `cases/{project}/data/investigation-log.json` entries.

**Limitations:** Gaps from `cases/{project}/data/findings.json` — anything unresolved or noted as a limitation.

**Confidence assessment:** Overall investigation strength based on how strongly each readiness criterion was met (not just pass/fail, but margin).

---

## summary.json

Generated at Gate 1 and written to `cases/{project}/data/summary.json` per the summary schema (`schemas/summary.schema.json`). Contains the structured data for the investigation summary: title, overview, scope, conclusions, findings summary, limitations, and methodology summary.
