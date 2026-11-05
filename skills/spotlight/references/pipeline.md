# Pipeline Reference

Readiness criteria, cycle mechanics, stall protocol, and Investigation gate presentation format. The orchestrator evaluates these after each investigation cycle to decide whether to loop or advance.

---

## Readiness Criteria

These are defaults for open-ended investigations. Read the approved brief and
methodology before applying them. For an explicitly authorized documentary or
single-register audit, judge completion against that scope rather than adding
interviews, independent-source counts, or a minimum number of high-confidence
findings outside the agreement. A fresh check of the same register does not
establish source independence. Keep verdict definitions, confidence caps, strict
validators, and resolver checks unchanged. A completed audit may go to human
review with unverified claims and explicit gaps; do not describe that as
publication verification or as a complete ownership reconstruction. In the
summaries, identify adapted criteria and their basis in the approved scope, and
disclose unresolved links and missing filing-level checks. An explicit
requirement of the approved brief or methodology cannot be waived: incomplete
work must be named for the member to assess, never represented as completed.

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

Finish missing verdicts and fact-check validator repairs within the current
cycle first. They belong to the fact-checker, not a new investigator cycle.
Valid unverified verdicts preserve evidence gaps without inventing verification.

1. `read-file({CASE_DIR}/data/findings.json)`
2. `read-file({CASE_DIR}/data/fact-check.json)`
3. For each applicable criterion, including requirements of the approved brief and methodology:
   - Check the condition against the data
   - Report pass/fail with specifics (e.g., "PASS: 4 high-confidence findings" or "FAIL: only 1 independent source for claim about contract award")
4. **If all applicable criteria pass:** advance to the Investigation gate
5. If `data/findings.json` contains `monitoring_recommendations[]` and monitoring is configured: process recommendations via `invoke-skill(monitoring)`
6. **If any fail and the resolver permits another cycle:** list specific gaps, recommend what the next cycle should focus on (e.g., "Next cycle: find second independent source for funding claim, use `fetch` to scrape court filing referenced in interview")
7. **If any fail and the resolver returns blocked:** trigger the stall protocol. The resolver owns the cycle cap and any targeted Research round; follow `phase-investigation` rather than counting five cycles here.

---

## Stall Protocol

When the resolver returns `status: blocked` after the permitted cycles and any targeted Research round:

> "Investigation stalled after {N} cycles. Missing: {gaps}. Options: continue with more cycles, pivot angle, or review current findings as-is."

Present this to the user and wait for direction. Do not auto-advance.

---

## Investigation Gate Presentation Format

When the applicable readiness criteria pass and the strict validators and resolver permit review, present the investigation for review:

**Headline:** State the actual outcome and scope, such as "Documentary audit: {N} assessed claims across {M} cycles". Use "verified findings" only for claims whose fact-check verdict is actually `verified`.

**Findings table:**

| # | Claim | Confidence | Fact-Check Verdict | Source Count |
|---|-------|------------|-------------------|-------------|
| 1 | ... | high | verified | 3 |

**Methods summary:** Techniques and tools used, drawn from `{CASE_DIR}/data/investigation-log.json` entries.

**Limitations:** Gaps from `{CASE_DIR}/data/findings.json` — anything unresolved or noted as a limitation.

**Confidence assessment:** Overall investigation strength based on how strongly each readiness criterion was met (not just pass/fail, but margin).

---

## summary.json

Generated at the Investigation gate and written to `{CASE_DIR}/data/summary.json` per the summary schema (`schemas/summary.schema.json`). Contains the structured data for the investigation summary: title, generation time, status, cycles, narrative summary, key conclusions, limitations, methodology summary, and one row per finding with its confidence and fact-check verdict. `scripts/validate-case.py` checks it.
