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

Arbiter uses each member's own API key through Data Navigator. There is no
shared Spotlight, Buried Signals, or hosted Arbiter key. Register through the
[Indicator partner signup link](https://arbiter.simppl.org/auth/register?eventSignup=5cce20c609334e538f07127322361862e3136e3d-324a-4c60-894a-5f42d2d57f8a),
create an API key in the member's Arbiter account, and store it locally with
`navigator keys set arbiter`.

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
catalogue entry. If it does not, report the current Navigator availability
problem and use saved material offline. If the member has no Arbiter key, send
them to the attributed signup link above, ask them to create their own key, and
have them run `navigator keys set arbiter` before offering browse or create
actions. Never ask them to paste the key into the conversation or a case file.

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
