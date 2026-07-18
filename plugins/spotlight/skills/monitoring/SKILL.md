---
name: monitoring
description: Turn investigation findings into explicit, user-approved monitoring recommendations. Spotlight recommends; Mycroft owns any Scoutpost action.
version: "3.0"
invocable_by: [orchestrator]
requires: []
---

# Investigation Monitoring

Spotlight owns active casework, not persistent monitoring. It may identify
targets worth watching and record them in `monitoring_recommendations[]` in
`data/findings.json`. It never installs Scoutpost, reads its credentials,
creates a scout, queries a scout, or stores Scoutpost identifiers in a case.

## Lifecycle

1. Investigators and fact-checkers recommend a target, rationale, priority, and
   proposed monitoring shape.
2. The orchestrator presents those recommendations to the user. Nothing is
   created automatically.
3. If approved and Mycroft is available, hand the recommendation to Mycroft in
   plain language. Mycroft's selected integrations decide whether a Scoutpost
   scout is appropriate and ask for any further confirmation.
4. Otherwise, leave the recommendation in the case for the user to revisit or
   create a runtime-native reminder themselves.

## User handoff

Use one clear handoff, never a direct Scoutpost command:

> "This investigation suggests monitoring **{target}** for **{criteria}**.
> Would you like to hand this to Mycroft for monitoring setup?"

When the answer is yes, include the target, rationale, cadence preference, and
case reference in the Mycroft prompt. The Mycroft skill owns authentication,
Scoutpost CLI/API selection, scout creation, and durable monitoring state.

## Case record

`monitoring_recommendations[]` is evidence-adjacent case context, not a live
monitor registry. Keep only the recommendation and the user's decision:

```json
{
  "target": "Example organisation",
  "priority": "high",
  "rationale": "New procurement notices could change the finding.",
  "criteria": "new contracts, ownership changes, enforcement action",
  "status": "recommended|handed_to_mycroft|declined"
}
```

Do not write API keys, project IDs, scout IDs, remote-monitor results, or
runtime task handles into Spotlight case state.

## Sensitive mode

In sensitive mode, record recommendations only. Do not hand anything to an
external monitoring system unless the user explicitly resumes that choice.
