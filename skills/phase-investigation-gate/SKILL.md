---
name: phase-investigation-gate
description: "Spotlight Investigation gate (end of Investigation): generate summary.md + data/summary.json, present findings to the user, iterate on request, user approval gate (ends the turn), provenance packaging, review artifact, feedback processing on resume"
invocable_by: [orchestrator]
phase: investigation-gate
---

# Investigation gate (end of Investigation)

Start or resume only from a `spotlight_resolve` result passed by the parent
with `owner: phase-investigation-gate`. When `phase: investigation_gate_approval`, regenerate both
summary artifacts below from current execution outputs, then call
`spotlight_resolve({})` before presenting the human gate. This applies whether
the summaries were missing or stale. For finalization, follow `resume` without
inferring a substep from artifact presence.


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

## Decision checks (optional)

Run only when `.spotlight-config.json` has `integrations.decisions.enabled: true`,
the case is not sensitive, an OpenRouter key is available, and the user agreed
to decision checks for this case at methodology approval (first run only: add
`--case-opt-in`). `python3 scripts/decision-signals.py {CASE_DIR} --phase investigation-gate --check`
reports readiness without making a request.

```
execute-shell("python3 scripts/decision-signals.py {CASE_DIR} --phase investigation-gate")
```

It asks the decision model (OpenRouter Decisions API, zero data retention) whether
each finding's quoted evidence, located in the stored sources, grounds the
claim, and writes `data/decision-signals.json`. Findings whose quote is not
found in any stored source, or that need arithmetic, are `routed` back to the
existing fact-check process instead of judged. If the command prints
`"ran": false`, skip this section without blocking the Investigation gate.

Add a **Decision check** column to the findings table (flags or `none`,
`routed`, `unavailable`) and list each flag with its reason under
**Limitations**. State that these are classification signals, not
verification: they never raise confidence. In `enforce` mode the report and
ingest apply the recorded cap; in `advisory` mode they only show it. When the
user disagrees with a flag, append `{target: "finding:F1", input_sha256:
<that finding entry's input_sha256>, reviewer, reason, at}` to `overrides` in
`data/decision-signals.json`; the override lapses automatically when the
finding or its located source changes. A contradiction or
incomplete-support flag is a good reason to offer another fact-check cycle
(see Iterate).

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
spotlight_transition({
  operation: "requestFollowUp",
  payload: {instructions: "<targeted gap instructions>"}
})
```

Resolve again and pass `resume.instructions` to the returned execution owner.

Before asking the user to approve the investigation as ready for report drafting and ingestion, remind them:

> **AI assistance notice:** Spotlight is designed to help surface, organize, and cross-check information, but AI can make mistakes. You are responsible for verifying sources, confirming authenticity, assessing risks, and deciding what is publishable.

**Gate: user approves the investigation.**

Present the synthesis, then open the review page: `open_review` in the Pi
harness, or the `open_spotlight_review` tool where the review server is
registered (`harness/review/README.md`). End the turn. When the member says
they are done, call `spotlight_resolve` again; never record the decision yourself.

Only in a runtime with neither tool, ask in chat and, after the user explicitly
approves, record it through the command-line adapter (its `direct` channel)
before any downstream work:

```text
spotlight_transition({
  operation: "approve",
  payload: {gate: "investigation_gate", approvedBy: "<human identity>", approvedAt: "<ISO 8601 timestamp>"}
})
```

This binds the current dependency digest owned by the provenance builder's
annotated registry, including activated validation inputs and referenced
evidence artifacts. Pending summary artifacts are not approval. If a dependency
changes, the next resolution returns the Investigation gate approval; re-run the existing
validators and obtain a new human approval before report or ingestion.

The human gate ends the turn. On resume, resolve and follow `resume.resume_at`;
do not skip directly to report.

## Finalize an approved Investigation gate

### `resume_at: provenance`

Invoke `provenance-signing`:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

This creates `{CASE_DIR}/data/provenance-manifest.json` with hashes for the case
artifacts, claim-to-verdict links, evidence bundle refs, and
`requires_api_key: true`.

If `NOOSPHERE_C2PA_URL` is configured, signing remains optional:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\"")
```

Signing failures do not block review. Preserve the unsigned manifest and report
the failure clearly. Call `spotlight_resolve`; its `resume.resume_at` must be
`review`.

### `resume_at: review`

Invoke `editorial-review` to produce `{CASE_DIR}/review.html`, a self-contained artifact
for inspecting findings and exporting structured feedback. Then call
`spotlight_resolve`; its `resume.resume_at` must be `seal`.

Offer the user:

> "Review artifact written to `{CASE_DIR}/review.html`. Open it in any browser to inspect findings. Request a targeted follow-up, or proceed to the public report."

For a follow-up, validate the feedback, convert it to targeted instructions,
and apply `requestFollowUp` as shown above before dispatching execution. Do not
derive the transition from feedback-file presence.

### `resume_at: seal`

If the user proceeds, seal both current finalization outputs:

```text
spotlight_transition({operation: "sealInvestigationGate", payload: {}})
```

Resolve again. Only a successful seal returns `phase: report`.
