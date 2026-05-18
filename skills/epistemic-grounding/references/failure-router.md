# Grounding Failure Router

This reference adapts the WFGY problem-map idea into Spotlight's claim-verification workflow.

Sources:

- WFGY, "Problem Map 3.0 Troubleshooting Atlas"
  https://github.com/onestardao/WFGY/blob/main/ProblemMap/wfgy-ai-problem-map-troubleshooting-atlas.md
- WFGY router pack, "troubleshooting-atlas-router-v1"
  https://raw.githubusercontent.com/onestardao/WFGY/main/ProblemMap/Atlas/troubleshooting-atlas-router-v1.txt

## Primary Failure Family

For Spotlight, the critical family is **Grounding & Evidence Integrity**: the claim has lost reliable tie to anchors, referents, or evidence.

## Routing Table

| Symptom | Likely Failure | First Fix | Confidence Effect |
|---|---|---|---|
| Source mentions entity but not action | Source adjacency | Narrow claim or find direct source | Cap low |
| Quote supports weaker claim than finding | Overclaiming | Rewrite claim to match evidence | Cap to evidence strength |
| Source is a search result or snippet | Unstable anchor | Fetch and archive source | Cap low until fetched |
| OCR/table extraction may be wrong | Carrier distortion | Verify against original PDF/image/table | Cap low/medium |
| Two sources repeat same upstream claim | False independence | Trace original source chain | Do not count as independent |
| Secondary report cites missing document | Broken provenance | Find cited original or mark inaccessible | Cap medium/low |
| Actor names similar but not identical | Entity collision | Disambiguate IDs, dates, jurisdictions | Cap low until resolved |
| Reliable sources conflict | Disputed grounding | Preserve both evidence trails | Mark disputed |
| Claim depends on future event | Not checkable yet | Convert to monitoring recommendation | Do not verify |
| Evidence exists but archive failed | Chain-of-custody gap | Try archive hierarchy or local archival | Cap medium unless local copy is strong |

## Misrepair Risks

Avoid these common bad fixes:

- Adding more adjacent sources instead of finding direct support.
- Treating repeated syndicated coverage as source independence.
- Rewriting a claim to sound cautious while keeping unsupported elements.
- Hiding uncertainty in `confidence_rationale` instead of encoding it in `grounding`.
- Letting a fact-check verdict override the claim-to-evidence mismatch.

## Escalation

Escalate to follow-up investigation when:

- a high-value claim is only partially grounded,
- the contradiction changes the story direction,
- entity disambiguation is unresolved,
- source access is blocked but likely obtainable,
- the claim would be ingested into the knowledge vault.
