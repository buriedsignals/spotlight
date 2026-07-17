---
name: epistemic-grounding
description: Portable claim-to-evidence grounding for knowledge work, fact-checking, and Spotlight investigations. Use when extracting claims or findings, assigning confidence, diagnosing weak support, or deciding whether material is a lead, partially grounded claim, verified finding, disputed claim, or false claim.
version: "2.0"
invocable_by: [orchestrator, investigator, fact-checker]
---

# Epistemic Grounding

Use this skill whenever an agent turns source material into a claim or decides
whether a claim is ready to file, brief, investigate, fact-check, or publish.

The core question is:

> Does this exact evidence justify believing this exact claim?

A source is only an anchor. Grounding is the relationship between the claim and the anchor.

## Task profiles

The grounding doctrine below is universal. Select the output profile from the
calling task; do not replace this skill with a runtime-specific variant.

- **knowledge-work** — routine Mycroft vault, brief, ingestion, and synthesis
  work. Apply the ladder and caps; emit the task's light `confidence:` tag and
  source reference. Do not manufacture a heavyweight schema for ordinary notes.
- **fact-check** — publication or editor-review work. Emit the task's complete
  grounding/provenance object, evidence references, and human-review state.
- **investigation-findings** — Spotlight casework. Every `findings.json`
  finding carries the grounding object below; preserve contradiction and
  source-expression obligations when that case mode is enabled.

Generated URLs, citations, statute IDs, and database paths are leads—not
grounds—until fetched or otherwise verified. This is especially important for
local or fine-tuned models, but applies to every model.

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

## Contradiction Categories

Classify a material contradiction before investigating it. Categories help route the next check; they never choose a winner automatically.

| Category | Question to test |
|---|---|
| `value_mismatch` | Do sources give incompatible values for the same attribute, scope, and time? |
| `time_impossible` | Can the asserted events coexist in the stated chronology, allowing for clock and publication differences? |
| `existence_dispute` | Does one source assert presence while another asserts absence—and is the latter complete enough to establish absence? |
| `connection_paradox` | Are two asserted relationships genuinely mutually exclusive, or merely different roles/scopes/times? |
| `location_impossible` | Could the subject plausibly occupy both locations, accounting for travel, scheduled publication, proxies, relays, and metadata error? |

Preserve both evidence trails. Check whether normalization, scope, identity resolution, time windows, extraction errors, or source dependence explain the conflict. Do not resolve a contradiction by counting sources, ranking providers, or applying a trust score. If the material conflict remains, mark the claim disputed and cap confidence accordingly.

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

## Investigation-Findings Output Contract

Every `findings.json` finding must include:

```json
"grounding": {
  "support_type": "direct|indirect|inferred|contradicted|insufficient",
  "source_role": "primary|secondary|contextual",
  "claim_elements_supported": ["actor", "action", "date"],
  "missing_assumptions": [],
  "confidence_cap": "high|medium|low",
  "misgrounding_risk": "short risk statement",
  "grounding_rationale": "why the evidence does or does not ground the claim; include the contradiction-search outcome here"
}
```

Every fact-check claim must include a `grounding_assessment` explaining whether
the cited evidence actually grounds the claim. The **fact-check** profile may
add its task-specific provenance fields; the **knowledge-work** profile may use
the lighter confidence/source form instead.

## Source-Expression Discipline

When an investigation case explicitly enables source-expression pilot or
activated mode, preserve the inspectable layer between source artifacts and
normalized findings:

- The acquiring agent records the expression; another agent may add its own
  independently acquired expression but must not rewrite the first agent's
  passage core.
- `text` is the exact original-language selection at `anchor_ref`. Translation,
  normalization, and paraphrase are separate derivatives, never replacements.
- The expression-to-finding relation is explicit: `supports`, `contradicts`, or
  `context`. One expression may link to several findings, and one finding may
  depend on several expressions.
- For non-text evidence, hash and preserve the original artifact separately
  from its OCR, transcript, caption, or text extraction. The expression anchors
  the inspectable derivative and retains the original evidence identity.
- If the exact stored selection is unavailable, do not produce an expression.
  Record a gap and apply the confidence cap. Search snippets, model recall, and
  RLM output are not recoverable anchors.

This is human- or agent-selected capture, not automatic extraction or semantic
entailment. Deterministic validation can prove identity, integrity, relation,
and lifecycle state; it cannot prove that a passage makes a claim true.

## Failure Routing

When a finding feels wrong, do not patch the wording first. Diagnose the grounding failure:

- Evidence mentions the topic but not the claim: source-adjacent lead.
- Evidence supports a weaker claim: narrow the claim.
- Evidence supports only some elements: mark partial and name missing assumptions.
- Evidence depends on OCR/layout extraction: verify against the original document or image.
- Evidence chains through another citation: trace to the origin.
- Evidence conflicts with another reliable source: mark disputed and preserve both trails.

Read `references/failure-router.md` for deeper failure classes. Read `references/grounding-theory.md` when designing or revising grounding policy.

## Attribution

The contradiction category prompts are adapted from [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo / chongluadao.vn (GitHub: 7onez; MIT License with upstream Ethical Use Addendum). Spotlight intentionally omits its trust scores, severity ranking, majority preference, and automatic conflict resolution.
