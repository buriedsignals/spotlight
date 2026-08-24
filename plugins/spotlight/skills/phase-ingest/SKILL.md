---
name: phase-ingest
description: "Spotlight Phase 6 — Ingestion: resolver-owned transition, ingestion.json status lifecycle, user confirmation, invoke-skill ingest or decline"
invocable_by: [orchestrator]
phase: ingest
---

# Phase 6 — Ingestion

Enter this phase only from a `spotlight_resolve` result with `phase: ingest`
and `owner: phase-ingest`. Follow its `resume.state` and `resume.resume_at`; do
not inspect `ingestion.json` to choose a substep.

### `pending` / `decision`


Remind the user before asking for ingestion confirmation:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes, call `spotlight_transition({operation: "decideIngest", payload:
  {decision: "requested"}})`, resolve again, and continue at the returned
  substep.
- If no, use the same operation with `payload: {decision: "declined"}`. The
  resolver writes the existing declined marker and the hash-bound decision.

### `requested` / `ingest`

The human decision is already durable. Do not ask again. Invoke `ingest` with
the project path and vault config from `.spotlight-config.json`. After it writes
its completed receipt, call `spotlight_resolve`; do not invoke ingestion a
second time.

### `completed` / `seal`

The projection receipt already exists. Do not ask or invoke `ingest` again.
Seal its current bytes:

```text
spotlight_transition({
  operation: "decideIngest",
  payload: {decision: "completed"}
})
```

Resolve again. `completed` / `complete` means the pipeline has ended.
