---
name: phase-ingest
description: Spotlight Phase 6 — Ingestion: finalize-report --if-ready transition marker, ingestion.json status lifecycle, user confirmation, invoke-skill ingest or decline
invocable_by: [orchestrator]
phase: ingest
---

# Phase 6 — Ingestion

After report drafting (or after Gate 1 if drafting was skipped):

Before entering this phase, write `data/ingestion.json` with
`{"schema_version":"1.0","status":"pending"}` and run
`python3 scripts/finalize-report.py {CASE_DIR} --if-ready`. This case-local transition
marker makes a skipped Phase 5 visible even if ingestion writes only to an external
vault. Stop if the finalizer fails.

Remind the user before asking for ingestion confirmation:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

> "Investigation complete. Ingest confirmed findings into your knowledge base?"

- If yes: update `data/ingestion.json` to status `requested`, then `invoke-skill("ingest")` — pass project path and vault config from `.spotlight-config.json`.
- If no: update `data/ingestion.json` to status `declined`; pipeline ends.
