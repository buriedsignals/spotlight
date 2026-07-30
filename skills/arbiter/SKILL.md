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

Arbiter network access is temporarily unavailable for every member while
Indicator and Arbiter agree how paid-member study ownership and privacy should
work. Do not call Arbiter directly or attempt to bypass Data Navigator's block.
Previously saved reports and themes remain usable offline.

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

The hosted key currently shares one created-study namespace and credit pool.
Preflight must find the source with `"queryable": true`, not merely find its
catalogue entry. Until that check is green, explain that partnership access is
paused pending the ownership decision and use another research source. Do not
offer browse or create actions while it is paused.

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
