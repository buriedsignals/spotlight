---
name: phase-gate1
description: Spotlight Phase 4 — Gate 1: generate summary.md + data/summary.json, present findings to the user, iterate on request, user approval gate (ends the turn), provenance packaging, review artifact, feedback processing on resume
invocable_by: [orchestrator]
phase: gate-1
---

# Phase 4 — Gate 1

Start or resume by running:

```text
python3 scripts/spotlight-orchestration.py status --json {CASE_DIR}
```

On the execution owner's direct readiness handoff, generate the two summary
artifacts below and run status again. Otherwise act only on
`next_phase: gate1_approval` or `next_phase: gate1_finalization`; never infer a
Gate 1 substep from artifact presence.


## Generate summary

`write-file("{CASE_DIR}/summary.md", <content>)` as a human-readable markdown document:

```markdown
# {Investigation Title}

**Date:** YYYY-MM-DD | **Cycles:** N | **Status:** Pending review

## Overview

2-3 paragraph narrative overview.

## Scope

What was investigated and what was out of scope.

## Key Conclusions

- Conclusion 1
- Conclusion 2

## Findings

| # | Claim | Confidence | Verdict | Sources |
|---|-------|------------|---------|---------|
| F1 | ... | high | verified | 3 |

## Limitations

- Limitation 1
- Limitation 2
```

Also write `{CASE_DIR}/data/summary.json` as the machine contract for
review, report drafting, and ingest:

```json
{
  "schema_version": "1.0",
  "project": "{project}",
  "title": "{Investigation Title}",
  "generated_at": "ISO 8601 timestamp",
  "status": "pending_review",
  "cycles": 3,
  "verified_findings": 3,
  "summary": "2-3 paragraph narrative overview.",
  "key_conclusions": ["Conclusion 1", "Conclusion 2"],
  "limitations": ["Limitation 1", "Limitation 2"],
  "methodology_summary": "Techniques and tools used, drawn from data/investigation-log.json.",
  "findings": [
    {
      "id": "F1",
      "claim": "specific finding claim",
      "confidence": "high",
      "fact_check_verdict": "verified",
      "source_count": 3
    }
  ]
}
```

`summary.md` is the human artifact; `data/summary.json` is the machine
contract. Generate both.

## Present to user

**Headline:** "{N} verified findings across {M} cycles"

**Findings table:**

| # | Claim | Confidence | Fact-Check Verdict | Source Count |
|---|-------|------------|-------------------|-------------|

**Methods summary:** Techniques and tools used, drawn from data/investigation-log.json.

**Limitations:** Unresolved gaps from data/findings.json, noted as limitations.

**Confidence assessment:** Overall investigation strength — not just pass/fail on criteria, but how strongly each was met.

## Iterate

If the user requests a follow-up cycle, persist the transition before returning
to execution:

```text
python3 scripts/spotlight-orchestration.py request-follow-up --instructions "<targeted gap instructions>" {CASE_DIR}
```

Re-run status and pass `follow_up.instructions` to `skills/phase-execution`.

Before asking the user to approve the investigation as ready for report drafting and ingestion, remind them:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

**Gate: user approves the investigation.**

After the user explicitly approves, persist the attributable receipt before any
downstream work:

```text
python3 scripts/spotlight-orchestration.py approve gate1 --approved-by "<human identity>" --approved-at "<ISO 8601 timestamp>" {CASE_DIR}
```

This binds the current dependency digest owned by the provenance builder's
annotated registry, including activated validation inputs and referenced
evidence artifacts. Pending summary artifacts are not approval. If a dependency
changes, a fresh status returns `gate1_approval`; re-run the existing validators
and obtain a new human approval before report or ingestion.

The human gate ends the turn. On resume, run status and follow
`gate1.resume_at`; do not skip directly to report.

## Finalize an approved Gate 1

### `resume_at: provenance`

Invoke `provenance-signing`:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

This creates `{CASE_DIR}/data/provenance-manifest.json` with hashes for the case
artifacts, claim-to-verdict links, evidence bundle refs, and
`requires_api_key: false`.

If `NOOSPHERE_C2PA_URL` is configured, signing remains optional:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\"")
```

Signing failures do not block review. Preserve the unsigned manifest and report
the failure clearly. Re-run orchestration status; it must return
`gate1.resume_at: review`.

### `resume_at: review`

Invoke `review` to produce `{CASE_DIR}/review.html`, a self-contained artifact
for inspecting findings and submitting structured feedback. Then re-run
orchestration status; it must return `gate1.resume_at: seal`.

Offer the user:

> "Review artifact written to `{CASE_DIR}/review.html`. Open it in any browser to inspect findings. Request a targeted follow-up, or proceed to the public report."

For a follow-up, validate the feedback, convert it to targeted instructions,
and run `request-follow-up` as shown above before dispatching execution. Do not
derive the transition from feedback-file or processed-marker presence.

### `resume_at: seal`

If the user proceeds, seal both current finalization outputs:

```text
python3 scripts/spotlight-orchestration.py seal-gate1 {CASE_DIR}
```

Re-run status. Only a successful seal returns `next_phase: report`.
