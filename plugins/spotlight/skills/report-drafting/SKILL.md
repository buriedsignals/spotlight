---
name: report-drafting
description: "Phase 5 hybrid synthesis — the model authors localized report framing and priority in report-draft.json; deterministic code validates finding references and renders the final Markdown, HTML, and evidence ledger."
license: MIT
metadata:
  type: orchestration-subskill
  parent: data-detective
  phase: 5
invocable_by: [orchestrator, user]
---

# report-drafting — Phase 5 synthesis

You are at Phase 5. Gate 1 has approved the structured findings and independent verdicts. You own the editorial synthesis; deterministic code owns file construction and evidence/confidence enforcement.

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

1. Read `{CASE_DIR}/data/findings.json` and `{CASE_DIR}/data/fact-check.json`.
2. Author `{CASE_DIR}/data/report-draft.json` in the report's natural language. Choose the title, deck, finding order, headlines, concise summaries, why each finding matters, caveats, and next steps. Cover every finding exactly once. Every prose block must reference the fact-checked finding IDs it interprets; do not author verdicts or confidence.

   ```json
   {
     "schema_version": "1.0",
     "title": "A clear journalist-grade title",
     "deck": "The central synthesis and its most important qualification.",
     "framing_finding_ids": ["F2", "F1"],
     "finding_order": ["F2", "F1"],
     "finding_treatments": [
       {
         "finding_id": "F2",
         "headline": "Editorial headline for this finding",
         "summary": "A concise synthesis of the validated record.",
         "why_it_matters": "The editorial significance without adding facts."
       },
       {
         "finding_id": "F1",
         "headline": "Editorial headline for this finding",
         "summary": "A concise synthesis of the validated record.",
         "why_it_matters": "The editorial significance without adding facts."
       }
     ],
     "caveats": [
       {"text": "Important limitation for readers.", "finding_ids": ["F2"]}
     ],
     "next_steps": [
       {"text": "A reporting step that would close a gap.", "finding_ids": ["F2"]}
     ]
   }
   ```

3. Run:

   ```sh
   python3 scripts/finalize-report.py {CASE_DIR}
   ```

   The finalizer validates the fact-check, validates your report draft, renders all three artifacts, then runs the report gate. It will not overwrite deliverables when either input gate fails.
4. If `report_draft` fails, revise only `data/report-draft.json` using the exact failure. If fact-check fails, return to the fact-checker. **Never repair generated HTML or Markdown by hand.**
5. Present the final gate only when the command prints `report finalizer: PASSED`.

The renderer is byte-deterministic for identical inputs, HTML-escapes all case text, permits links only to HTTP(S) sources or existing files within the case, and caps every non-verified finding at Low confidence.

The structural validator is deliberately language-neutral. It proves reference coverage and verdict placement; it does **not** pretend to prove semantic entailment from prose. Independent fact-checking and the final human editorial gate remain responsible for whether the model's synthesis accurately interprets the cited findings.

The model/code split is the same on local and frontier models. Flue runs the finalizer
from its per-turn launcher; installed frontier CLIs must run it before presenting the
final gate, with a process-exit hook as a backstop. A custom API host must call
`scripts/finalize-report.py` in its own response middleware; Spotlight cannot intercept
an arbitrary external API loop.

## Inputs / Outputs

**Model writes:** `case/data/report-draft.json`.
**Renderer reads:** `case/data/{findings,fact-check,report-draft,methodology}.json` and case-local sources.
**Renderer writes:** `case/findings-report.md`, `case/report.html`, `case/evidence-map.json`.

## References

- `references/report-template.html` — canonical stylesheet and legacy manual skeleton; the renderer reads its CSS.
- `references/citation-discipline.md` — editorial rationale behind source-closure rules.
- `references/design-discipline.md` — design semantics retained by the renderer.
- `references/anti-patterns.md` — historical failures that motivated deterministic finalization.
