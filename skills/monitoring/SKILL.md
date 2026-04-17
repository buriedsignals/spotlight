---
name: monitoring
description: Persistent feed monitoring during OSINT investigations — pluggable sources (GDELT, RSS, GDACS, ACLED, …), preflight checks, and recommendation ingest. Agents recommend; orchestrator configures.
version: "1.0"
invocable_by: [orchestrator]
requires: []
---

# Investigation Monitoring

Manage persistent monitoring during and between Spotlight investigations using the pluggable **feed framework**. This skill defines the lifecycle for recommending, configuring, and checking monitoring feeds.

The feed framework lives at `monitoring/feeds/` in this repo. Each source is a directory under `monitoring/feeds/sources/` containing a `manifest.json` (metadata + env var contract) and a `fetch.py` (query implementation). Adding a new source is a drop-in file operation — see the "Adding a New Source" section.

---

## Conventions

- **Case attribution:** All monitoring queries and feed configs use `topic: spotlight:{project}` (e.g., `spotlight:chat-control-denmark`) when the underlying source supports topic filtering.
- **Auth:** All API calls use env vars declared in the source's `manifest.json` under `required_env_vars`. Never hardcode keys.
- **User gate:** The orchestrator never enables new feed monitoring autonomously. All monitoring configuration requires user approval.
- **Preflight:** Before any investigation starts, the orchestrator runs `monitoring/feeds/preflight.py` to check which sources are green (ready), yellow (key set but smoke test failed), or red (missing env vars).

---

## The Monitoring Lifecycle

### 1. Recommend (Agents → findings.json)

During execution cycles, agents identify targets worth persistent monitoring. They record structured recommendations in `monitoring_recommendations[]` in `data/findings.json`. See `references/recommendation-schema.md` for the full schema.

Agents recommend when they observe:

- A page that updated during the investigation window
- A social account posting relevant content before press releases
- A news topic in a location that's underreported
- A government page that may publish updated documents
- A conflict event pattern in a region (→ ACLED)

Agents do NOT configure feeds. They only recommend.

### 2. Configure (Orchestrator → monitoring.json)

Between investigation cycles, the orchestrator:

1. Reads `monitoring_recommendations[]` from `data/findings.json`
2. Presents recommendations to the user, ordered by priority
3. For each approved recommendation, maps it to a concrete feed query:
   - `scout_type: web` → `rss_investigative` or `rss_regional` feed with URL filter, OR direct `fetch(url, ...)` on schedule
   - `scout_type: pulse` → `gdelt` keyword + location query
   - `scout_type: civic` → `gdacs` (disasters) or `acled` (conflict) by region
   - `scout_type: social` → delegated to the external Apify/social scraping workflow (not a feed)
4. Logs configured monitors to `data/monitoring.json`

### 3. Check (Orchestrator → feed preflight + query)

At the start of any `/spotlight` resume (Phase 0 step 10), the orchestrator runs:

```
execute-shell("python3 monitoring/feeds/preflight.py --json")
```

This reports which sources are healthy. Then for each configured monitor in `data/monitoring.json`, query new results:

```
execute-shell("python3 monitoring/feeds/monitor.py query <source_id> --project {project} --since 24h --json")
```

Present a monitoring briefing to the user before the next cycle:

> "Monitoring check — your [source] monitor on [target] has returned N new results since your last cycle:
> - [date] [title] ({source})
> - [date] [title] ({source})
>
> These may be relevant to your investigation. Want to review before starting the next cycle?"

### 4. Ingest (Orchestrator → investigation context)

If the user wants to use monitoring results, the orchestrator folds relevant information units into the investigation context for the next cycle. The investigator receives them as additional text in the spawn prompt under `Monitoring results since last cycle:`.

---

## Feed Sources

See `references/source-catalog.md` for the full registry of monitoring sources and their capabilities.

Current sources (live as of repo state):

| Source ID | Name | Category | Requires Key |
|---|---|---|---|
| `gdelt` | GDELT Document API | News | No |
| `rss_investigative` | RSS (Bellingcat, ICIJ, The Intercept, Crisis Group) | News | No |
| `rss_regional` | RSS (17 regional feeds) | News | No |
| `gdacs` | GDACS disaster alerts | Disasters | No |
| `acled` | Armed Conflict Location & Event Data | Conflict | Yes (`ACLED_API_KEY`, `ACLED_EMAIL`) |

Each source's `manifest.json` declares the `required_env_vars`. The preflight tool uses this contract to report readiness.

---

## Commands

```
# Preflight — which feeds are ready?
execute-shell("python3 monitoring/feeds/preflight.py --json")

# Bulk check across all feeds for a project
execute-shell("python3 monitoring/feeds/monitor.py check-all --project {project} --since 35m --json")

# Discovery — list available sources
execute-shell("python3 monitoring/feeds/monitor.py list --project {project}")

# Targeted query
execute-shell("python3 monitoring/feeds/monitor.py query gdelt --project {project} --json")
execute-shell("python3 monitoring/feeds/monitor.py query acled --project {project} --since 7d --json")
execute-shell("python3 monitoring/feeds/monitor.py query rss_investigative --since 24h --json")
```

---

## Data Files

### `cases/{project}/data/monitoring.json`

Case-level monitoring state. Created by the orchestrator when the first monitor is approved.

```json
{
  "topic": "spotlight:{project}",
  "monitors": [
    {
      "source_id": "gdelt|rss_investigative|rss_regional|gdacs|acled",
      "query": "keywords or filter",
      "since": "duration (e.g., 24h, 7d)",
      "created_at": "ISO 8601",
      "source_recommendation": "M1",
      "source_cycle": 1,
      "priority": "high|medium|low"
    }
  ],
  "checks": [
    {
      "checked_at": "ISO 8601",
      "cycle": 2,
      "units": [
        {
          "title": "Human-readable summary",
          "source_url": "https://...",
          "source_id": "gdelt",
          "created_at": "ISO 8601"
        }
      ]
    }
  ]
}
```

---

## Adding a New Source

Extending the feed framework is a 4-step operation:

1. Create directory: `monitoring/feeds/sources/{source_id}/`
2. Write `manifest.json` with:
   ```json
   {
     "id": "{source_id}",
     "name": "Human-readable name",
     "description": "What this source provides",
     "category": "news|disasters|conflict|social|regulatory|...",
     "regions": ["global" | specific],
     "requires_key": true|false,
     "required_env_vars": ["ENV_VAR_1", "ENV_VAR_2"],
     "rate_limit_note": "max requests per period",
     "default_since": "24h"
   }
   ```
3. Write `fetch.py` implementing a single function:
   ```python
   def fetch(since: str, query: str = None, project: str = None) -> list[dict]:
       """Return a list of normalized items:
       [{id, title, url, date, summary, source_id}, ...]
       """
   ```
4. Verify with `python3 monitoring/feeds/preflight.py --json` — the new source should appear in the output.

No changes required to `monitor.py` — it discovers sources by scanning `monitoring/feeds/sources/*/manifest.json`.

---

## Reference Files

| File | Contents |
|------|---------|
| `references/source-catalog.md` | Registry of all monitoring sources with capabilities and env vars |
| `references/recommendation-schema.md` | Schema for `monitoring_recommendations[]` in `findings.json` |

---

## Sensitive Mode

In sensitive mode, monitoring still functions but with restrictions:

- No new feed queries against remote APIs (`fetch`/`search` are stripped)
- Preflight still runs but reports all sources as `yellow` (preflight is read-only metadata)
- Existing `data/monitoring.json` is honored for check-only scans against locally-cached feed archives in `cases/{project}/research/monitoring/`
- If no local cache exists, monitoring is a no-op; flag this explicitly to the user
