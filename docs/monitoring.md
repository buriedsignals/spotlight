# Monitoring

Persistent feed monitoring that runs alongside investigations. Agents recommend what to watch; the orchestrator configures; the framework delivers.

## The lifecycle

```
Cycle N investigator
        │
        │ adds monitoring_recommendations[] to findings.json
        ▼
Cycle N fact-checker
        │
        │ may add more recommendations
        ▼
Orchestrator reads recommendations
        │
        │ presents to user, orders by priority
        ▼
User approves / modifies / skips each
        │
        ▼
Orchestrator writes cases/{project}/data/monitoring.json
        │
        ▼
On next /spotlight resume:
    preflight.py  → checks source health
    monitor.py    → queries each configured source
        │
        │ new units since last check
        ▼
Orchestrator briefs user before cycle N+1
        │
        │ user chooses: review or proceed
        ▼
If reviewed: units fold into cycle N+1 investigator prompt
```

## Feed sources

Live sources in this repo:

| Source ID | Name | Category | Requires Key | Env Vars |
|---|---|---|---|---|
| `gdelt` | GDELT Document API | news | No | — |
| `rss_investigative` | Bellingcat, ICIJ, The Intercept, Crisis Group | news | No | — |
| `rss_regional` | 17 regional feeds (NDTV, Al Jazeera, BBC, Guardian, …) | news | No | — |
| `gdacs` | GDACS disaster alerts | disasters | No | — |
| `acled` | Armed Conflict Location & Event Data | conflict | Yes | `ACLED_API_KEY`, `ACLED_EMAIL` |

Each lives at `monitoring/feeds/sources/{id}/` with `manifest.json` + `fetch.py`.

### Per-source details

See `skills/monitoring/references/source-catalog.md` for the canonical source registry with scout-type mapping, rate limits, and registration URLs.

## Preflight

Before any investigation starts, the orchestrator runs:

```bash
python3 monitoring/feeds/preflight.py --json
```

The preflight:

1. Scans every `manifest.json` under `sources/`
2. Checks each source's `env_vars` against the process environment
3. Reports per-source status:
   - **green** — env vars set (or source needs no keys) → source is queryable
   - **yellow** — env vars set but smoke-test failed (API down, rate-limited, schema change) — reserved; preflight doesn't run smoke-tests by default, use `--smoke-test` to enable
   - **red** — one or more required env vars missing

### Example output

```bash
$ python3 monitoring/feeds/preflight.py --text
ID                       Status   Missing env
------------------------------------------------------------------------
acled                    red      ACLED_API_KEY, ACLED_EMAIL
gdacs                    green    —
gdelt                    green    —
rss_investigative        green    —
rss_regional             green    —

green=4  yellow=0  red=1
```

JSON output (default) gives the full schema:

```json
{
  "sources": [
    {
      "id": "acled",
      "name": "ACLED Conflict Events",
      "category": "conflict",
      "requires_key": true,
      "env_vars_required": ["ACLED_API_KEY", "ACLED_EMAIL"],
      "env_vars_set": [],
      "env_vars_missing": ["ACLED_API_KEY", "ACLED_EMAIL"],
      "status": "red",
      "smoke_error": null
    },
    {
      "id": "gdelt",
      "name": "GDELT Document API",
      "category": "news",
      "requires_key": false,
      "env_vars_required": [],
      "env_vars_set": [],
      "env_vars_missing": [],
      "status": "green",
      "smoke_error": null
    }
  ],
  "summary": {"green": 4, "yellow": 0, "red": 1}
}
```

Exit code: `0` if at least one source is green, `1` if all are red.

## Scout types → feed sources

Agents recommend using a `scout_type` vocabulary (`web`, `pulse`, `social`, `civic`). The orchestrator maps each to a concrete feed source at configuration time:

| scout_type | Primary feed source(s) | Query shape |
|---|---|---|
| `web` | `rss_investigative`, `rss_regional`, or direct `fetch(url)` on schedule | URL + criteria keywords |
| `pulse` | `gdelt` | Keywords + optional location filter |
| `civic` | `gdacs` (disasters), `acled` (conflict), or `rss_regional` filtered by government outlets | Region + event type |
| `social` | External workflow (Apify-based or platform APIs) — not a feed | Handle + platform + criteria |

Social monitoring is not a feed in this framework — it's platform scraping, handled separately via the `social-media-intelligence` skill and `PLATFORM_SCRAPER` config.

## Recommendation schema

Agents write recommendations into `monitoring_recommendations[]` in `findings.json`:

```json
{
  "id": "M1",
  "target": "https://eu-council.europa.eu/chat-control",
  "scout_type": "web",
  "criteria": "new amendments or voting schedule changes",
  "rationale": "F3 — this page updated twice during our investigation window",
  "priority": "high",
  "finding_refs": ["F3", "F7"]
}
```

Full schema reference: `skills/monitoring/references/recommendation-schema.md` with examples for each scout type.

## The monitoring.json file

Case-level state. Created when the first monitor is approved.

```json
{
  "topic": "spotlight:{project}",
  "monitors": [
    {
      "source_id": "gdelt",
      "query": "chat control AND Denmark",
      "since": "24h",
      "created_at": "ISO 8601",
      "source_recommendation": "M1",
      "source_cycle": 1,
      "priority": "high"
    }
  ],
  "checks": [
    {
      "checked_at": "ISO 8601",
      "cycle": 2,
      "units": [
        {
          "title": "...",
          "source_url": "...",
          "source_id": "gdelt",
          "created_at": "ISO 8601"
        }
      ]
    }
  ]
}
```

`monitors[]` is append-only during the investigation (user may disable monitors between cycles; disabling sets a `disabled_at` field rather than removing the entry).

## Commands

### Preflight

```bash
python3 monitoring/feeds/preflight.py [--json|--text] [--smoke-test] [--project <slug>]
```

### Bulk check across all sources

```bash
python3 monitoring/feeds/monitor.py check-all --project <slug> --since 35m --json
```

Runs every enabled source (filtered by project topic relevance), deduplicates, scores, returns sorted signals.

### Single-source query

```bash
python3 monitoring/feeds/monitor.py query gdelt --project <slug> --json
python3 monitoring/feeds/monitor.py query acled --project <slug> --since 7d --json
python3 monitoring/feeds/monitor.py query rss_investigative --since 24h --json
```

### Source discovery

```bash
python3 monitoring/feeds/monitor.py list --project <slug>
```

## Adding a new source

4 steps, no central registration:

1. **Directory:** `mkdir -p monitoring/feeds/sources/<new_id>/`

2. **Manifest** (`manifest.json`):

   ```json
   {
     "id": "<new_id>",
     "name": "Human-readable name",
     "description": "What this source provides",
     "category": "news|conflict|disasters|regulatory|environmental|…",
     "regions": ["global" | specific],
     "requires_key": true|false,
     "env_vars": ["ENV_1", "ENV_2"],
     "rate_limit_note": "max requests per period",
     "default_since": "24h"
   }
   ```

3. **Fetch script** (`fetch.py`):

   ```python
   def fetch(query: str | None, topics: list[dict], since: str) -> list[dict]:
       """Return a list of normalized signal dicts:
       [{title, url, source_name, source_domain, date, summary,
         category, relevance_score, matched_keywords, language}, ...]
       """
   ```

   Follow the pattern in `monitoring/feeds/sources/gdelt/fetch.py` or `acled/fetch.py`.

4. **Verify:** `python3 monitoring/feeds/preflight.py --text` — the new source should appear. If `requires_key: true` and env vars set, try a live query: `python3 monitoring/feeds/monitor.py query <new_id> --since 24h --json`.

Also update `skills/monitoring/references/source-catalog.md` with the human-readable entry.

## Scheduling

The framework is a pull model — feeds are queried on demand or on a schedule owned by the host:

- **Ad hoc** — user runs `monitor.py check-all` manually
- **Host cron** — add a cron job that runs `check-all` every N minutes and writes results to a monitoring log
- **Hermes LaunchAgent** — Mycroft runs the check on a schedule (Mac Mini); see `mycroft-config/` for LaunchAgent plists
- **pi `/loop`** — when running interactively, use pi's loop support to poll at an interval

No source is polled automatically by the framework itself. The orchestrator only triggers `monitor.py` during `/spotlight` runs (Phase 0 step 10 preflight briefing).

## Sensitive mode

In sensitive mode:

- `fetch` and `search` verbs are stripped (per `AGENTS.md`)
- Feed sources that require remote API calls (`gdelt`, `acled`, API-backed RSS) cannot be queried
- Preflight still runs but reports all remote sources as `yellow` (env vars present but effectively unreachable)
- Monitoring relies on locally-cached feed archives in `cases/{project}/research/monitoring/` if they exist
- If no local cache exists, monitoring is a no-op; the orchestrator flags this explicitly at the monitoring briefing step
