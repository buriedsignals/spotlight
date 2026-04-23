# Monitoring

Spotlight's monitoring layer is now an **orchestrator**, not a local feed engine.

## Ownership split

| Tool | Owns |
|---|---|
| `mycroft` | Passive open-intelligence signals: feed polling, scoring, dedup, topic registry, local signal index |
| `spotlight` | Investigation-scoped monitor recommendations, user approval, case linkage, resume-time briefing |
| `cojournalist` | Durable scheduled scouts, notifications, projects, and information units |

This lets the three tools coexist cleanly while still working independently.

## Lifecycle

```text
Cycle N investigator / fact-checker
        │
        │ add monitoring_recommendations[] to findings.json
        ▼
Spotlight orchestrator
        │
        │ asks user which recommendations to approve
        ▼
Registers passive topics in Mycroft when useful
Creates durable monitors in coJournalist by project_id when approved
Falls back to runtime-native routines when coJournalist is unavailable
        ▼
Writes cases/{project}/data/monitoring.json
        ▼
On resume:
  - query Mycroft signals for linked topics
  - query coJournalist units/scouts for linked project
  - ask about runtime-native routine output if needed
        ▼
Show monitoring briefing before next cycle
```

## What changed

- Spotlight no longer ships `monitoring/feeds/`.
- Spotlight no longer owns source manifests, feed preflight, or feed polling code.
- Spotlight still owns the **case memory** of monitoring: what was approved, where it lives, and what changed since the last cycle.

## `monitoring.json`

The file now acts as a case registry for external monitors.

- `mycroft.topic_slugs[]` tracks linked passive topics
- `cojournalist.project_id` is the preferred grouping primitive for durable monitors
- `cojournalist.scouts[]` stores created monitor ids for the case
- `fallback_routines[]` stores runtime-native routine handles when coJournalist is not used
- `checks[]` stores what Spotlight surfaced back to the user over time

### Registry helper

Spotlight ships a small helper to manage the file shape safely:

```bash
python3 monitoring/registry.py show --project "<project>" --json
python3 monitoring/registry.py init --project "<project>"
python3 monitoring/registry.py migrate --project "<project>"
```

Use it to normalize or migrate case state before appending:

- Mycroft topic links
- coJournalist `project_id` and `scout_id` links
- runtime-native fallback routine handles
- resume-time `checks[]`

## Resume-time checks

### Mycroft passive signals

If `~/.mycroft/monitoring/monitor.py` exists and the case has linked topics, Spotlight should query recent signals with:

```bash
python3 ~/.mycroft/monitoring/monitor.py query --topic "<topic or target>" --since 7d --json
```

### coJournalist durable monitors

If the case has a stored `project_id`, Spotlight should fetch updates from coJournalist by that `project_id`, not by brittle scout-name matching.

Preferred path:

```bash
cojo units list --project "<project_id>" --since 7d
```

HTTP fallback:

```bash
curl -s "https://www.cojournalist.ai/api/v1/units?project_id=<project_id>&limit=20" \
  -H "Authorization: Bearer $COJOURNALIST_API_KEY"
```

### Runtime-native routines

If the case used a Codex Automation, Claude routine, or Hermes/OpenClaw cron fallback, Spotlight should either:

- read retrievable task output when the runtime exposes it; or
- ask the user whether updates were detected and whether to review them before the next cycle.

## Recommendation schema

Agents still emit the same `monitoring_recommendations[]` shape in `findings.json`. The schema did not move. Only the orchestrator's downstream handling changed.

## Passive feed sources

The passive source catalog still exists conceptually:

- GDELT
- RSS investigative
- RSS regional
- GDACS
- ACLED

Those sources are now owned by Mycroft, not Spotlight. See `skills/monitoring/references/source-catalog.md`.
