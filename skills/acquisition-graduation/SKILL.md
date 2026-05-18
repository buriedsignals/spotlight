---
name: acquisition-graduation
description: Convert repeated Browser Harness acquisition successes into durable Spotlight acquisition guidance. Use after a non-trivial browser acquisition works repeatedly and should become a reusable source/domain path without storing secrets, cookies, or fragile session details.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
---

# Acquisition Graduation

Use this skill after Browser Harness solves a non-trivial source acquisition and the path appears reusable.

Do not graduate one-off browsing. Graduate only when the acquisition path is likely to recur and can be validated without secrets or brittle session state.

## Graduation Criteria

All must be true:

- repeated source or domain class,
- stable URL, API, selector, or CDP/network pattern,
- success survived at least two acquisition attempts or has obvious deterministic structure,
- validation checks can detect drift,
- legal and rate-limit constraints are known,
- no access-control bypass,
- no secrets, cookies, tokens, or session artifacts are needed.

## What To Store

Store the map, not the diary:

- source/domain,
- task class,
- URL patterns,
- selectors or CDP/network observations,
- hidden API endpoints if discovered legally,
- waits, redirects, traps,
- screenshots or preservation steps,
- validation checks,
- legal/rate-limit notes,
- what not to automate.

## What Not To Store

Never store:

- passwords, cookies, bearer tokens, or session IDs,
- personal accounts,
- CAPTCHA bypass paths,
- fragile pixel coordinates,
- one-off narration,
- screenshots that expose secrets,
- instructions to evade rate limits or access controls.

## Output Location

For now, write reusable acquisition notes under:

```text
skills/integrations/references/acquisition-paths/{domain-or-source}.md
```

If the path becomes broad enough to deserve its own skill, propose a new skill rather than silently expanding this one.
