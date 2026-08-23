---
name: phase-ingest
description: Spotlight Phase 6 — Ingestion: finalize-report --if-ready transition marker, ingestion.json status lifecycle, user confirmation, invoke-skill ingest or decline
invocable_by: [orchestrator]
phase: ingest
---

# Phase 6 — Ingestion

Run:

```text
python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}
```

Enter this phase only for `next_phase: ingest`, and follow the returned
`ingest.state` and `ingest.resume_at`. Do not inspect `ingestion.json` to choose
a substep.

### `pending` / `decision`

Run `python3 scripts/finalize-report.py {CASE_DIR} --if-ready`. The existing
finalizer verifies the current completed or declined report path. Stop if it
fails.

Remind the user before asking for ingestion confirmation:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes, run
  `python3 scripts/spotlight-orchestration.py decide-ingest requested {CASE_DIR}`,
  re-run status, and continue at the returned substep.
- If no, run
  `python3 scripts/spotlight-orchestration.py decide-ingest declined {CASE_DIR}`.
  The seam writes the existing declined marker and the hash-bound decision.

### `requested` / `ingest`

The human decision is already durable. Do not ask again. Invoke `ingest` with
the project path and vault config from `.spotlight-config.json`. After it writes
its completed receipt, re-run orchestration status; do not invoke ingestion a
second time.

### `completed` / `seal`

The projection receipt already exists. Do not ask or invoke `ingest` again.
Seal its current bytes:

```text
python3 scripts/spotlight-orchestration.py decide-ingest completed {CASE_DIR}
```

Re-run status. `completed` / `complete` means the pipeline has ended.
