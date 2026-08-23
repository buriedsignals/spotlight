---
name: phase-report
description: Spotlight Phase 5 — Public-facing report drafting via report-drafting + finalize-report.py deterministic rendering; hybrid data-detective handover mode
invocable_by: [orchestrator]
phase: report
---

# Phase 5 — Report drafting (public-facing)

Before asking whether to draft the public-facing report, remind the user:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

Enter this phase only when
`python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}` returns
`next_phase: report`. Offer the user the public-facing report:

> "Draft the public-facing journalist-grade report now?
> (a) Yes — invoke report-drafting to produce report.html + findings-report.md + evidence-map.json.
> (b) No — record a deterministic decline, then skip to ingestion. (`review.html` already covers editorial review.)"

If (a): invoke `report-drafting`. The orchestrator authors
`data/report-draft.json` to choose localized title, deck, finding order,
editorial summaries, emphasis, caveats, and next steps. Then use the one
case-state seam below; it invokes the existing deterministic
`finalize-report.py`, which validates finding references, attaches canonical
verdict/confidence, safely renders `report.html`, `findings-report.md`, and
`evidence-map.json`, and records hashes of those outputs. Semantic accuracy
remains part of the independent fact-check and final human editorial gate. Do
not hand-edit generated HTML or Markdown.

```text
python3 scripts/spotlight-orchestration.py decide-report completed {CASE_DIR}
```

If (b), use the same seam; it invokes the existing hash-bound
`decline-report.py` helper and records that output:

```text
python3 scripts/spotlight-orchestration.py decide-report declined {CASE_DIR}
```

`technical_indicators` present: invoke `technical-investigation`; offer verified JSON, CSV, or STIX.

## Hybrid mode (data-detective handover)

When `{CASE_DIR}/data-detective-handover/` exists (i.e. this Spotlight run was triggered by a data-detective formal handover, not a standalone lead), `report-drafting` runs in hybrid mode: the methodology section spans both orchestrators in phase order — upstream data-detective phases (P0/P1 ingest, P3 detect, P6 handover) followed by Spotlight phases (P1 brief, P2 method, P3 cycles + fact-check, P4 Gate 1, P5 report-drafting). Each finding's `.path` block walks the actual trail from upstream detector to downstream fact-checker. Skip the offer above — drafting is the whole point of the handover. Invoke `report-drafting` automatically.
