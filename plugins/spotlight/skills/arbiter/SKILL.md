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

Arbiter uses the member's own API key through Data Navigator once enabled.
Live access is currently blocked pending the member discount-code flow;
previously saved reports and themes remain usable offline.

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

Preflight must find the source with `"queryable": true`, not merely find its
catalogue entry. If it reports the source as blocked, explain that live access
is pending the discount-code flow and use saved material offline. Once enabled,
if the member has no Arbiter key, explain how to create one and run
`navigator keys set arbiter` before offering browse or create actions.

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
