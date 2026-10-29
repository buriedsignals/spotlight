---
name: phase-report
description: Spotlight Report — public-facing report drafting via report-drafting + finalize-report.py deterministic rendering; hybrid data-detective handover mode
invocable_by: [orchestrator]
phase: report
---

# Report (public-facing drafting)

Before asking whether to draft the public-facing report, remind the user:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

Enter this phase only from a `spotlight_resolve` result with `phase: report` and
`owner: phase-report`. Offer the user the public-facing report:

> "Draft the public-facing journalist-grade report now?
> (a) Yes — invoke report-drafting to produce report.html + findings-report.md + evidence-map.json.
> (b) No — record a deterministic decline, then skip to ingestion. (`review.html` already covers editorial review.)"

If every finding was set aside at the Investigation gate, there is nothing to
report: say so and offer only (b). A draft cannot validate (the report check
answers `NO_REPORTABLE_FINDINGS`).

If (a): invoke `report-drafting`. The orchestrator authors
`data/report-draft.json` to choose localized title, deck, finding order,
editorial summaries, emphasis, caveats, and next steps. The `decideReport`
transition then has the resolver module invoke the existing
deterministic `finalize-report.py`, validates finding references, attaches
canonical verdict/confidence, safely renders `report.html`,
`findings-report.md`, and `evidence-map.json`, and records hashes of those
outputs. Semantic accuracy remains part of the independent fact-check and final
human editorial gate. Do not hand-edit generated HTML or Markdown.

The member records the decision on the review page, after the draft exists
for (a). Open the page: `open_review` in the Pi harness, or the
`open_spotlight_review` tool where the review server is registered
(`harness/review/README.md`). End the turn. When the member says they are
done, call `spotlight_resolve` again; never record the decision yourself. If
the page is refused because the draft does not validate, fix the named fields
in `data/report-draft.json`; after a few refusals, stop and tell the member
what the check still reports.

Only in a runtime with neither tool, apply the transition through the
command-line adapter (its `direct` channel) after the user answers in chat:

```text
spotlight_transition({
  operation: "decideReport",
  payload: {decision: "completed"}
})
```

If (b), use the same operation with `payload: {decision: "declined"}`. The
resolver invokes the existing hash-bound decline helper and records its output.

`technical_indicators` present: invoke `technical-investigation`; offer verified JSON, CSV, or STIX.

## Hybrid mode (data-detective handover)

When `{CASE_DIR}/data-detective-handover/` exists (i.e. this Spotlight run was triggered by a data-detective formal handover, not a standalone lead), `report-drafting` runs in hybrid mode: the methodology section spans both orchestrators in phase order — upstream data-detective phases (P0/P1 ingest, P3 detect, P6 handover) followed by Spotlight phases (P1 brief, P2 method, P3 cycles + fact-check, P4 Investigation gate, P5 report-drafting). Each finding's `.path` block walks the actual trail from upstream detector to downstream fact-checker. Skip the offer above — drafting is the whole point of the handover. Invoke `report-drafting` automatically.
