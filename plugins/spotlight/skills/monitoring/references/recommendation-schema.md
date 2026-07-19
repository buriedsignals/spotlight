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

| Recommendation | Handoff after explicit approval |
|---|---|
| `web` | Ask Mycroft to consider a durable web monitor. |
| `pulse` | Ask Mycroft to consider passive beat coverage. |
| `social` | Ask Mycroft to consider an appropriate social monitor. |
| `civic` | Ask Mycroft to consider a civic watch. |

## Spotlight-side normalization

Spotlight records the journalist-facing recommendation only. Mycroft owns any
vendor-specific normalization, authentication, and durable-monitor state.

## When NOT to recommend

Skip `monitoring_recommendations` entirely when:

- the source is static and unlikely to change;
- the investigation is about a closed historical event;
- the case is ending and no follow-up watch is useful.
