---
name: arbiter
description: Use Arbiter through Data Navigator to browse or create bounded social-media case studies and attach its analytics to a Spotlight report.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
requires: [navigator, integrations]
---

# Arbiter

Use this skill when the operator invokes `/arbiter` or asks Spotlight for an
Arbiter case study, archived social posts, entity stance, themes,
actor/community analysis, or Arbiter report visuals.

Arbiter is remote and disabled in sensitive mode. Previously saved Arbiter JSON
may still be rendered offline.

## Start

Run the integration preflight and require Arbiter to be green:

```bash
python3 integrations/preflight.py --json
```

Then load the current Navigator and Arbiter contracts:

```text
invoke-skill("navigator")
read-file("integrations/arbiter/integration.md")
```

Arbiter is currently a trusted-operator source because its hosted key shares
one created-study namespace and credit pool. If preflight reports yellow
because the connected Navigator account cannot see
`global/arbiter/case-studies`, explain that operator access is required and use
the documented fallback.

## Prompt

When the source is available, ask whether the operator wants to:

1. Browse existing studies and match one to the investigation; or
2. Create a study through the reviewed create → search-plan → human review →
   finalize workflow.

Follow `integrations/arbiter/integration.md` exactly. It is the single source of
truth for all operation inputs, credit disclosures, confirmation gates, error
handling, evidence rules, and polling behavior. Every network call goes through
`navigator query global/arbiter/case-studies`; never call Arbiter directly or
request its credential.

For a report, save the raw Navigator response from the `report` operation under
`{CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.json`. That filename and
top-level response shape activate Spotlight's deterministic Arbiter appendix.
