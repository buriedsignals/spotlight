---
name: report-drafting
description: "Phase 5 deterministic synthesis — finalize findings-report.md, report.html, and evidence-map.json from validated findings.json and fact-check.json. The renderer, not the language model, owns report file construction and confidence mapping."
license: MIT
metadata:
  type: orchestration-subskill
  parent: data-detective
  phase: 5
invocable_by: [orchestrator, user]
---

# report-drafting — Phase 5 synthesis

You are at Phase 5. Gate 1 has approved the structured findings and independent verdicts. Report construction is a **deterministic build step**. Do not copy, populate, regex, or hand-edit the HTML template.

## Deliverables (all three required)

| File | Audience | What it is |
|---|---|---|
| `case/findings-report.md` | editor / fact-checker | canonical claim-by-claim audit generated from structured inputs |
| `case/report.html` | publication / reader | designed artifact using the canonical template stylesheet |
| `case/evidence-map.json` | audit / replication | machine-readable claim → verdict → source ledger |

## Mandatory AI-assistance notice (verbatim, in report.html)

The renderer places this at the top of the page. Do not soften:
> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

## Workflow

1. Confirm `{CASE_DIR}/data/findings.json` and `{CASE_DIR}/data/fact-check.json` exist. Do not synthesize missing fields.
2. Run:

   ```sh
   python3 scripts/finalize-report.py {CASE_DIR}
   ```

   The finalizer first validates the fact-check evidence chain, then renders all three artifacts, then runs the report gate. It will not overwrite existing deliverables when the evidence chain fails.
3. If the fact-check stage fails, return to the fact-checker or downgrade the unsupported verdict. If rendering fails, fix only the named structured input. **Never repair `report.html` by hand.**
4. Present the final gate only when the command prints `report finalizer: PASSED`. Completion is the finalizer result, not the model's narrative.

The renderer is byte-deterministic for identical inputs, HTML-escapes all case text, permits links only to HTTP(S) sources or existing files within the case, and caps every non-verified finding at Low confidence.

## Inputs / Outputs

**Reads:** `case/data/{findings,fact-check,methodology}.json` and case-local source paths named by those files.
**Writes:** `case/findings-report.md`, `case/report.html`, `case/evidence-map.json`.

## References

- `references/report-template.html` — canonical stylesheet and legacy manual skeleton; the renderer reads its CSS.
- `references/citation-discipline.md` — editorial rationale behind source-closure rules.
- `references/design-discipline.md` — design semantics retained by the renderer.
- `references/anti-patterns.md` — historical failures that motivated deterministic finalization.
