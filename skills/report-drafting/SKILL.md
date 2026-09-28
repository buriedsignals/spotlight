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

   When an existing structure would clarify the report, add an optional
   `diagrams` item. For relationship structures, select one of `flow`,
   `hierarchy`, `network`, or `loop`; cite its supporting finding IDs; and select
   its existing connections using exact `{from, to, relationship}` values from
   `findings.json`. Keep it to one question, at most nine nodes and twelve
   connections. A `loop` is one simple cycle and has exactly one focal starting
   entity. For data charts, either select `timeline` with 1–12 `indicator_ids`
   that resolve to entries in `findings.json` `technical_indicators` (the renderer
   draws each recorded first→last observation span in chronological order), or
   select `bar` with a `metric` of `verdict_tally`, `confidence_distribution`, or
   `source_types`, counted deterministically over your `finding_ids` or all
   findings. Never author Mermaid, SVG, HTML, JavaScript, CSS, URLs, coordinates,
   counts, or layout settings: the renderer owns those.

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
         "why_it_matters": "The editorial significance without adding facts.",
         "quote_selections": [{"expression_id": "SX2"}]
       },
       {
         "finding_id": "F1",
         "headline": "Editorial headline for this finding",
         "summary": "A concise synthesis of the validated record.",
         "why_it_matters": "The editorial significance without adding facts."
       }
     ],
     "diagrams": [
       {
         "id": "money-flow",
         "type": "flow",
         "title": "How funds moved through the structure",
         "caption": "The selected connections show the recorded route through two intermediaries.",
         "finding_ids": ["F2", "F1"],
         "connections": [
           {"from": "Payer", "to": "Intermediary", "relationship": "paid"},
           {"from": "Intermediary", "to": "Recipient", "relationship": "transferred"}
         ],
         "focal_entities": ["Recipient"]
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

   In an activated `1.1` case, an optional `quote_selections` item contains
   `expression_id` only. Never copy quote text or attribution into the draft. The
   deterministic renderer resolves the active canonical expression, escapes its exact
   text and attribution, and records its locator, hashes, relation, and lifecycle in
   `evidence-map.json`. Legacy `1.0` reports do not accept expression selections.

3. **Decision check (optional).** If this case ran decision checks at Gate 1
   (`data/decision-signals.json` exists), check the prose before finalizing:

   ```
   execute-shell("python3 scripts/decision-signals.py {CASE_DIR} --phase report")
   ```

   It asks the decision model whether each deck, headline, summary,
   why-it-matters and caveat says more than its findings and verdicts allow
   (added certainty, cause, motive, scope, actor, amount or date; a dropped
   "allegedly"; an unverified finding stated as fact). Revise only the flagged
   fields in `data/report-draft.json` and rerun the check (at most two rounds).
   If the editor judges a flag wrong, record an override in
   `data/decision-signals.json` with the flagged item's `target` and
   `input_sha256` plus `reviewer`, `reason` and `at`. It applies only to that
   exact sentence with those findings, verdicts and confidences, and lapses
   when any of them changes. The finalizer's
   `report_fidelity` stage reads these stored signals offline: in `enforce`
   mode it fails on unrevised flags or on prose changed after the check; in
   `advisory` mode it only warns.

4. Return the completed structured draft to the `phase-report` owner. That
   owner applies `spotlight_transition({operation: "decideReport", payload:
   {decision: "completed"}})`; the resolver invokes the deterministic
   finalizer, validates both inputs, renders all three artifacts, and records
   their hashes before advancing to Ingest.
5. If that transition reports a `report_draft` or `report_fidelity` failure, revise only
   `data/report-draft.json` using the exact failure. If fact-check fails, return
   to the fact-checker. **Never repair generated HTML or Markdown by hand.**
6. Present generated report artifacts only after the transition succeeds.

The renderer is byte-deterministic for identical inputs, HTML-escapes all case text, permits links only to HTTP(S) sources or existing files within the case, and caps every non-verified finding at Low confidence. Activated reports also require every reportable positive finding to retain an active supporting expression through the fact-check trail. Missing, dangling, tampered, superseded, or withdrawn expression references fail before valid report artifacts are replaced.

When decision checks ran, `findings-report.md` and `evidence-map.json` show each finding's decision-check line and flags; in `enforce` mode a fresh, judged grounding signal can lower displayed confidence (never raise it), and `validate-report.py` fails if any displayed confidence exceeds its applied cap.

The structural validator is deliberately language-neutral. It proves reference coverage and verdict placement; it does **not** pretend to prove semantic entailment from prose. Independent fact-checking and the final human editorial gate remain responsible for whether the model's synthesis accurately interprets the cited findings.


## Inputs / Outputs

**Model writes:** `case/data/report-draft.json`.
**Renderer reads:** `case/data/{findings,fact-check,report-draft,methodology}.json`, plus `case-contract.json` and `source-expressions.json` for activated `1.1` cases, and case-local sources.
**Renderer writes:** `case/findings-report.md`, `case/report.html`, `case/evidence-map.json`.

## References

- `references/report-template.html` — canonical stylesheet and legacy manual skeleton; the renderer reads its CSS.
- `references/citation-discipline.md` — editorial rationale behind source-closure rules.
- `references/design-discipline.md` — design semantics retained by the renderer.
- `references/diagram-design.md` — report-diagram type selection and visual grammar.
- `references/interactive-diagrams.md` — Mermaid/ELK rendering and canvas behavior.
- `references/anti-patterns.md` — historical failures that motivated deterministic finalization.
