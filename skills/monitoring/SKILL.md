---
name: monitoring
description: Investigation-scoped monitoring orchestration. Agents recommend targets; the orchestrator links them to Mycroft passive topics, coJournalist projects/scouts, or runtime-native routines.
version: "2.0"
invocable_by: [orchestrator]
requires: []
---

# Investigation Monitoring

Spotlight no longer owns a local feed engine. Monitoring is now a coordination layer across three surfaces:

- **Mycroft** for passive open-intelligence signals (GDELT, RSS, GDACS, ACLED)
- **coJournalist** for durable scheduled scouts and later information units
- **Runtime-native routines** when coJournalist is unavailable or declined

Agents still recommend targets in `monitoring_recommendations[]`. The orchestrator handles approval, creation, persistence, and later checks.

## Conventions

- **Case reference:** use `spotlight:{project}` as the cross-tool handle wherever possible.
- **User gate:** no durable monitor is created without explicit approval.
- **Default durable target:** prefer `cojournalist` when it is available and green in integration preflight.
- **Passive signals:** if Mycroft is installed, Spotlight may register/update case-linked topics there; Spotlight never owns passive feed polling itself.

## Monitoring lifecycle

### 1. Recommend

Investigators and fact-checkers add `monitoring_recommendations[]` to `data/findings.json` when they identify something worth watching after the current cycle.

They recommend. They do not create monitors.

### 2. Configure

When the user approves a recommendation, the orchestrator chooses one or both of:

- **Passive topic in Mycroft** when the recommendation benefits from ambient feed coverage
- **Durable monitor in coJournalist** when the recommendation should keep running and return later updates

If coJournalist is unavailable or declined, the orchestrator falls back to a runtime-native routine:

- Codex: Codex Automation
- Claude: Claude routine
- Hermes / OpenClaw: cron or recipe

### 3. Check on resume

At the start of a resumed investigation, the orchestrator checks:

- **Mycroft topics** if `~/.mycroft/monitoring/monitor.py` exists and the case has linked topic slugs
- **coJournalist units and scout state** if `monitoring.json` has a stored `project_id` or `scout_id`
- **Runtime-native routines** by retrieving task output when supported, or by asking the user whether updates were detected

The orchestrator then presents a short monitoring briefing and asks whether to fold those updates into the next cycle.

### 4. Ingest into the next cycle

If the user wants to use the updates, include them in the next investigator prompt under `Monitoring results since last cycle:`.

## Commands and checks

### Normalize the case registry

```text
execute-shell('python3 monitoring/registry.py migrate --project "{project}"')
```

### Check Mycroft availability

```text
execute-shell('test -f ~/.mycroft/monitoring/monitor.py && echo true || echo false')
```

### Query Mycroft passive signals

```text
execute-shell('python3 ~/.mycroft/monitoring/monitor.py query --topic "{topic_or_target}" --since 7d --json')
```

### Check integration readiness

```text
execute-shell("python3 integrations/preflight.py --json")
```

### Preferred coJournalist path

Read the integration guide first:

```text
read-file("integrations/cojournalist/integration.md")
```

Then create or reuse a project, create scouts under that `project_id`, and later fetch updates back by `project_id`.

## `monitoring.json` v2

This file is Spotlight-owned case state. It is no longer a feed-config file.

```json
{
  "schema_version": "2",
  "case_ref": "spotlight:{project}",
  "mycroft": {
    "topic_slugs": ["example-topic"],
    "last_checked_at": "ISO 8601"
  },
  "cojournalist": {
    "project_id": "uuid",
    "project_name": "Spotlight: {project}",
    "scouts": [
      {
        "scout_id": "uuid",
        "monitor_kind": "web|pulse|social|civic",
        "target": "https://...",
        "criteria": "what to watch"
      }
    ],
    "last_checked_at": "ISO 8601"
  },
  "fallback_routines": [
    {
      "runtime": "codex|claude|hermes|openclaw",
      "handle": "provider-specific identifier",
      "monitor_kind": "web|pulse|social|civic",
      "target": "https://...",
      "criteria": "what to watch"
    }
  ],
  "checks": [
    {
      "checked_at": "ISO 8601",
      "source": "mycroft|cojournalist|runtime-routine",
      "summary": "human-readable summary",
      "items": []
    }
  ]
}
```

If Spotlight encounters the old feed-oriented `monitoring.json` shape, treat it as legacy and migrate or ignore it safely rather than assuming it is current.

## Sensitive mode

In sensitive mode:

- do not create new live Mycroft or coJournalist monitors;
- do not query live remote monitoring backends;
- you may still read previously exported or cached monitoring artifacts under the case directory.

## Reference

| File | Purpose |
|---|---|
| `monitoring/registry.py` | Normalizes and updates `cases/{project}/data/monitoring.json` |
| `integrations/cojournalist/integration.md` | Durable monitor creation and retrieval via existing coJournalist surfaces |
| `references/recommendation-schema.md` | Agent output schema for `monitoring_recommendations[]` |
| `references/source-catalog.md` | Passive feed sources now owned by Mycroft |
