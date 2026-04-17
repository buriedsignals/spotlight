# Monitoring Recommendation Schema

How agents format monitoring recommendations in `data/findings.json`.

---

## Schema

Add a `monitoring_recommendations` array to `data/findings.json` when you identify targets worth persistent monitoring. This array is optional — only populate it when you observe something genuinely worth watching.

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

---

## Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Sequential ID: M1, M2, M3… within the cycle |
| `target` | Depends | URL for web scouts, handle for social scouts, null for pulse/civic scouts |
| `scout_type` | Yes | `web`, `pulse`, `social`, or `civic` — see mapping below |
| `criteria` | Yes | What to watch for (max 500 chars) |
| `rationale` | Yes | Why this target is worth monitoring — reference finding IDs |
| `priority` | Yes | `high`, `medium`, or `low` |
| `finding_refs` | Yes | Array of finding IDs that motivated this recommendation |
| `platform` | Social only | `instagram`, `x`, or `facebook` |
| `monitor_mode` | Social only | `summarize` (notify on any new posts) or `criteria` (match against criteria) |
| `location` | Pulse/Civic only | Object: `{"displayName": "…", "city": "…", "country": "XX"}` |
| `root_domain` | Civic only | Top-level domain to track (e.g. `europarl.europa.eu`) |
| `tracked_urls` | Civic only | Specific URLs under root_domain to watch for updates |

---

## Scout Type → Feed Source Mapping

When the orchestrator processes a recommendation, it maps `scout_type` to a concrete feed source:

| scout_type | Primary feed source(s) | Query shape |
|---|---|---|
| `web` | `rss_investigative`, `rss_regional`, or direct `fetch(url)` on schedule | URL + criteria keywords |
| `pulse` | `gdelt` | Keywords + optional location |
| `civic` | `gdacs` (disasters), `acled` (conflict), or `rss_regional` filtered by government outlets | Region + event type |
| `social` | External workflow (Apify-based or platform APIs) — not a feed | Handle + platform + criteria |

See `source-catalog.md` for feed source details.

---

## Examples by Scout Type

### Web Scout

```json
{
  "id": "M1",
  "target": "https://eu-council.europa.eu/chat-control",
  "scout_type": "web",
  "criteria": "new amendments or voting schedule changes",
  "rationale": "F3 — page updated twice during investigation",
  "priority": "high",
  "finding_refs": ["F3"]
}
```

### Social Scout

```json
{
  "id": "M2",
  "target": "@eu_council",
  "scout_type": "social",
  "platform": "x",
  "monitor_mode": "criteria",
  "criteria": "chat control OR child safety regulation",
  "rationale": "F7 — official account posted policy shifts before press",
  "priority": "medium",
  "finding_refs": ["F7"]
}
```

### Pulse Scout (News Monitoring)

```json
{
  "id": "M3",
  "target": null,
  "scout_type": "pulse",
  "location": {
    "displayName": "Copenhagen, Denmark",
    "city": "Copenhagen",
    "country": "DK"
  },
  "criteria": "chat control Denmark minister",
  "rationale": "F12 — Danish political response underreported in English media",
  "priority": "low",
  "finding_refs": ["F12"]
}
```

### Civic Scout (Conflict / Disaster / Government)

```json
{
  "id": "M4",
  "target": null,
  "scout_type": "civic",
  "location": {
    "displayName": "Khartoum, Sudan",
    "city": "Khartoum",
    "country": "SD"
  },
  "criteria": "RSF clashes, civilian casualties",
  "rationale": "F15 — tracking escalation patterns for week-over-week comparison",
  "priority": "high",
  "finding_refs": ["F15"]
}
```

```json
{
  "id": "M5",
  "target": null,
  "scout_type": "civic",
  "location": {
    "displayName": "Brussels, Belgium",
    "city": "Brussels",
    "country": "BE"
  },
  "criteria": "data retention legislation",
  "rationale": "F18 — EU Parliament committee reviewing related provisions",
  "priority": "medium",
  "finding_refs": ["F18"],
  "root_domain": "europarl.europa.eu",
  "tracked_urls": ["https://europarl.europa.eu/committees/en/libe/meetings"]
}
```

---

## When NOT to Recommend

Do not force recommendations. Skip `monitoring_recommendations` entirely when:

- No sources showed signs of ongoing change during the investigation
- The investigation is about a past event, not an evolving situation
- The case is being closed, not continued
