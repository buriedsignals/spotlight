---
name: phase-gate1
description: Spotlight Phase 4 — Gate 1: generate summary.md + data/summary.json, present findings to the user, iterate on request, user approval gate (ends the turn), provenance packaging, review artifact, feedback processing on resume
invocable_by: [orchestrator]
phase: gate-1
---

# Phase 4 — Gate 1

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

The user can request follow-up cycles targeting specific findings. If so, re-enter the execution loop with targeted gap instructions.

Before asking the user to approve the investigation as ready for report drafting and ingestion, remind them:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

**Gate: user approves the investigation.**

After the user explicitly approves, persist the attributable receipt before any
downstream work:

```text
python3 scripts/spotlight-orchestration.py approve gate1 --approved-by "<human identity>" --approved-at "<ISO 8601 timestamp>" {CASE_DIR}
```

This binds the current `summary.md`, `data/summary.json`,
`data/findings.json`, `data/fact-check.json`, `data/evidence-bundle.json`, and
`data/investigation-log.json`. The pending summary artifacts are not approval.
If any bound input changes, a fresh status returns `gate1_approval`; re-run the
existing validators and obtain a new human approval before report or ingestion.

## Package provenance before HTML review

After approval and before invoking the review skill, invoke `provenance-signing`:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

This creates `{CASE_DIR}/data/provenance-manifest.json` with hashes for the case artifacts, claim-to-verdict links, evidence bundle refs, and `requires_api_key: false`.

If `NOOSPHERE_C2PA_URL` is configured, optionally request signing:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\"")
```

Signing failures do not block review. Preserve the unsigned manifest and report the failure clearly.

## Generate review artifact

After approval, `invoke-skill("review")` to produce `{CASE_DIR}/review.html` — a self-contained HTML artifact the user can open in any browser to inspect findings and submit structured feedback. See `skills/review/SKILL.md`.

Offer the user:

> "Review artifact written to `{CASE_DIR}/review.html`. Open it in any browser to inspect findings and submit feedback (optional). If you submit feedback, save the exported `review-feedback.json` into `{CASE_DIR}/data/` and re-run `/spotlight` to process it. Or proceed to drafting the public report now."

## Feedback processing (on resume)

When `/spotlight` is resumed and `{CASE_DIR}/data/review-feedback.json` exists without a matching `review-feedback-processed.json` marker, the Preflight skill (`skills/phase-preflight`) invokes the review skill in process mode before advancing. This re-spawns the investigator with feedback-targeted instructions, updates findings, and regenerates the review artifact. See `skills/review/SKILL.md` § Mode B.
