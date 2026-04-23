# Monitoring Recommendation Schema

How agents format monitoring recommendations in `data/findings.json`.

The schema is unchanged. What changed is how Spotlight routes approved recommendations after the user says yes.

## Schema

```json
{
  "monitoring_recommendations": [
    {
      "id": "M1",
      "target": "https://eu-council.europa.eu/chat-control",
      "scout_type": "web",
      "criteria": "new amendments or voting schedule changes",
      "rationale": "F3 — this page updated twice during our investigation window",
      "priority": "high",
      "finding_refs": ["F3", "F7"]
    }
  ]
}
```

## Field reference

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Sequential ID: `M1`, `M2`, `M3` within the cycle |
| `target` | Depends | URL for web, handle for social, nullable for pulse/civic |
| `scout_type` | Yes | `web`, `pulse`, `social`, or `civic` |
| `criteria` | Yes | What to watch for |
| `rationale` | Yes | Why this should be monitored |
| `priority` | Yes | `high`, `medium`, or `low` |
| `finding_refs` | Yes | Finding ids that motivated the recommendation |
| `platform` | Social only | `instagram`, `x`, `facebook`, or other supported platform |
| `monitor_mode` | Social only | `summarize` or `criteria` |
| `location` | Pulse/Civic only | Geography object |
| `root_domain` | Civic only | Root domain to monitor |
| `tracked_urls` | Civic only | Specific civic pages already identified |

## Routing after approval

Spotlight keeps the same recommendation schema and applies this routing:

| Recommendation | Passive path | Durable path |
|---|---|---|
| `web` | Optional Mycroft topic if broad ambient coverage helps | coJournalist `web` scout or runtime-native routine |
| `pulse` | Mycroft topic for passive feed coverage | coJournalist beat/pulse scout or runtime-native routine |
| `social` | None | coJournalist `social` scout or runtime-native routine |
| `civic` | Optional Mycroft topic when passive alerts help | coJournalist `civic` scout or runtime-native routine |

## Spotlight-side normalization

Keep any vendor naming mismatch inside Spotlight. For example, if a durable backend calls the same concept `beat` where Spotlight says `pulse`, translate it in the adapter rather than changing the agent schema.

## When NOT to recommend

Skip `monitoring_recommendations` entirely when:

- the source is static and unlikely to change;
- the investigation is about a closed historical event;
- the case is ending and no follow-up watch is useful.
