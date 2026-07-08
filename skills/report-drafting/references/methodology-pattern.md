# Methodology section pattern (the highest-leverage learning)

The methodology section serves a dual purpose: it documents the skill (the algorithm) AND it logs the actual run. It is NOT a separate generic methodology — it is the audit trail of THIS investigation, in phase order.

Structure: one `<div class="phase">` per phase (P0 through P7).

**Critical — do NOT break the adversarial fact-check verdict table and the spotlight-handoff outcomes table into separate top-level sections.** They read out of phase order. Instead:

- Adversarial fact-check verdicts table → **INSIDE the Phase 3** `<div class="phase">`.
- Spotlight-handoff outcomes table (briefs OS-001..OS-N, what they did, what they promoted) → **INSIDE the Phase 6** `<div class="phase">`.

This way a reader scrolling the methodology gets the full run in phase order: ingest → resolve → detect+factcheck → gate → synthesize → handoff → vault. Past investigations broke these out and the result read out-of-order; the restructuring at the end is what gave the report its final shape.
