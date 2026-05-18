---
name: epistemic-grounding
description: Claim-to-evidence grounding for Spotlight investigations. Use when extracting findings, assigning confidence, fact-checking claims, diagnosing weak support, or deciding whether a signal is a lead, partially grounded claim, verified finding, disputed claim, or false claim.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
---

# Epistemic Grounding

Use this skill whenever Spotlight turns source material into claims or evaluates whether a claim is ready for editorial use.

The core question is:

> Does this exact evidence justify believing this exact claim?

A source is only an anchor. Grounding is the relationship between the claim and the anchor.

## Grounding Ladder

Classify each candidate finding on this ladder:

1. **Unsourced signal** — interesting, but no source anchor yet.
2. **Source-adjacent lead** — a source mentions the topic, but does not support the claim.
3. **Partially grounded claim** — evidence supports some claim elements, but missing assumptions remain.
4. **Directly grounded claim** — source text directly supports all material claim elements.
5. **Independently verified finding** — direct grounding plus independent corroboration and no unresolved contradiction.

Only levels 4-5 can be high confidence. Levels 1-2 are leads, not findings. Level 3 is at most medium confidence and often low.

## Required Grounding Check

For every finding or claim:

1. Break the claim into material elements: actor, action, object, time, place, amount, relationship, status.
2. Identify the exact quote, table row, record, image frame, metadata field, or document passage that supports each element.
3. Classify the source role:
   - `primary` — original record, document, direct statement, data source, filing, archived page, or observable artifact.
   - `secondary` — reporting, analysis, database aggregation, third-party summary.
   - `contextual` — useful background, but not evidence for the claim.
4. Classify support type:
   - `direct` — evidence states the claim elements plainly.
   - `indirect` — evidence supports the claim through a short, explicit inference.
   - `inferred` — evidence requires unstated assumptions or synthesis across sources.
   - `contradicted` — reliable evidence conflicts with the claim.
   - `insufficient` — source does not support the claim.
5. Name missing assumptions and misgrounding risks.
6. Search for contradictions before raising confidence.
7. Apply the confidence cap.

## Confidence Caps

Use these caps even if the claim sounds plausible:

| Condition | Maximum Confidence |
|---|---|
| No scraped local file | low |
| Search snippet only | low |
| Source is contextual or adjacent | low |
| Evidence is inaccessible, abstract-only, or materially redacted | low |
| Claim requires unstated assumptions | medium |
| Only secondary sources support the claim | medium |
| Single primary source, no contradiction search yet | medium |
| Direct primary source plus independent corroboration | high |
| Credible unresolved contradiction | low or disputed |

Never upgrade a claim beyond the weakest material element. If the amount is directly supported but the date is inferred, the whole claim is only partially grounded.

## Output Contract

Every `findings.json` finding must include:

```json
"grounding": {
  "support_type": "direct|indirect|inferred|contradicted|insufficient",
  "grounding_strength": "full|partial|weak|none",
  "source_role": "primary|secondary|contextual",
  "quote_match": "exact|paraphrase|contextual|none",
  "claim_elements_supported": ["actor", "action", "date"],
  "missing_assumptions": [],
  "contradictions": [],
  "confidence_cap": "high|medium|low",
  "misgrounding_risk": "short risk statement",
  "grounding_rationale": "why the evidence does or does not ground the claim"
}
```

Every fact-check claim must include a `grounding_assessment` explaining whether the cited evidence actually grounds the claim.

## Failure Routing

When a finding feels wrong, do not patch the wording first. Diagnose the grounding failure:

- Evidence mentions the topic but not the claim: source-adjacent lead.
- Evidence supports a weaker claim: narrow the claim.
- Evidence supports only some elements: mark partial and name missing assumptions.
- Evidence depends on OCR/layout extraction: verify against the original document or image.
- Evidence chains through another citation: trace to the origin.
- Evidence conflicts with another reliable source: mark disputed and preserve both trails.

Read `references/failure-router.md` for deeper failure classes. Read `references/grounding-theory.md` when designing or revising grounding policy.
