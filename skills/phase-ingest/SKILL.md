---
name: phase-ingest
description: Spotlight Phase 6 — Ingestion: finalize-report --if-ready transition marker, ingestion.json status lifecycle, user confirmation, invoke-skill ingest or decline
invocable_by: [orchestrator]
phase: ingest
---

# Phase 6 — Ingestion

Enter this phase only when
`python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}` returns
`next_phase: ingest`.

Before asking for the ingestion decision, run
`python3 scripts/finalize-report.py {CASE_DIR} --if-ready`. The existing
finalizer verifies the current completed or declined report path. Stop if it
fails.

Remind the user before asking for ingestion confirmation:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes, run
  `python3 scripts/spotlight-orchestration.py decide-ingest requested {CASE_DIR}`,
  then `invoke-skill("ingest")` with the project path and vault config from
  `.spotlight-config.json`. After the ingest skill writes its receipt, run
  `python3 scripts/spotlight-orchestration.py decide-ingest completed {CASE_DIR}`;
  the seam preserves existing ingestion fields while sealing their current
  bytes.
- If no, run
  `python3 scripts/spotlight-orchestration.py decide-ingest declined {CASE_DIR}`.
  The seam writes the existing declined `data/ingestion.json` marker and the
  hash-bound case decision; the pipeline ends.
