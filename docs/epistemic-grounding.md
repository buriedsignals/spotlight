# Epistemic Grounding

Spotlight treats a source as an anchor, not as proof by itself.

Epistemic grounding is the claim-to-evidence relationship: the reason a specific piece of evidence justifies believing a specific claim.

## Why It Matters

Investigative errors often happen even when sources exist. Common failures:

- the source mentions the topic but not the claim,
- the source supports a weaker claim,
- a claim combines facts from several places without naming assumptions,
- repeated coverage is mistaken for independent evidence,
- OCR, layout, or table extraction changes the meaning,
- a contradiction is smoothed over instead of preserved.

Spotlight's older grounding rule focused on source hygiene: scrape before cite, archive the source, keep a local file, and quote the evidence. That remains mandatory, but v2 adds claim-level grounding.

## Grounding Ladder

1. Unsourced signal
2. Source-adjacent lead
3. Partially grounded claim
4. Directly grounded claim
5. Independently verified finding

Only levels 4-5 should become high-confidence findings. Weakly grounded material stays a lead or a limitation.

## Required Trace

Each finding records:

- exact claim,
- exact quote or source span,
- source role: primary, secondary, contextual,
- support type: direct, indirect, inferred, contradicted, insufficient,
- missing assumptions,
- contradiction search,
- confidence cap,
- misgrounding risk,
- grounding rationale.

The investigator writes this as `grounding` in `findings.json`. The fact-checker independently writes `grounding_assessment` in `fact-check.json`.

## Evidence Bundles

Evidence bundles are acquisition-centered. Findings are claim-centered.

`cases/{project}/data/evidence-bundle.json` preserves how source material was obtained:

- acquisition method,
- source URL,
- access timestamp,
- raw source path,
- screenshot path,
- downloaded file path and hash,
- missing-source gate notes,
- claim/source links,
- human-verification flag.

This lets editors review both the claim and the acquisition trail.

## Sources

- WFGY repo: https://github.com/onestardao/WFGY
- WFGY Problem Map 3.0 Troubleshooting Atlas: https://github.com/onestardao/WFGY/blob/main/ProblemMap/wfgy-ai-problem-map-troubleshooting-atlas.md
- Stanford Encyclopedia of Philosophy, "Metaphysical Grounding": https://plato.stanford.edu/entries/grounding/#Prel
