# Monitoring Source Catalog

Registry of monitoring sources available through the feed framework (`monitoring/feeds/`).

Each source is a self-contained directory at `monitoring/feeds/sources/{source_id}/` with a `manifest.json` (contract) and `fetch.py` (implementation). The framework discovers sources by scanning manifests — no central registration required.

---

## Active Sources

| Source ID | Name | Category | Scout Types Supported | Agent Can Recommend? | Requires Key | Env Vars |
|---|---|---|---|---|---|---|
| `gdelt` | GDELT Document API | news | pulse | Yes | No | — |
| `rss_investigative` | Bellingcat, ICIJ, The Intercept, Crisis Group | news | web (URL-filtered) | Yes | No | — |
| `rss_regional` | 17 regional feeds (NDTV, Al Jazeera, BBC, Guardian, …) | news | pulse, web | Yes | No | — |
| `gdacs` | GDACS disaster alerts | disasters | civic | Yes | No | — |
| `acled` | Armed Conflict Location & Event Data | conflict | civic | Yes | Yes | `ACLED_API_KEY`, `ACLED_EMAIL` |

---

## Per-Source Details

### GDELT (`gdelt`)

**What it monitors:** Global news articles, 150+ countries, 21 languages. Full-text search via GDELT Document API.

**Scout mapping:** Best for `pulse` scouts — broad news keyword queries with optional location filter.

**Rate limit:** Max 1 request per 5 seconds (aggressive 429 enforcement).

**Manifest:** `monitoring/feeds/sources/gdelt/manifest.json`

---

### RSS Investigative (`rss_investigative`)

**What it monitors:** Publication feeds from Bellingcat, ICIJ, The Intercept, Crisis Group.

**Scout mapping:** `web` scouts where the target URL is itself an RSS feed, or keyword-filtered new items.

**Rate limit:** Polite polling — recommended max 1 fetch per feed per 10 min.

**Manifest:** `monitoring/feeds/sources/rss_investigative/manifest.json`

---

### RSS Regional (`rss_regional`)

**What it monitors:** 17 regional news feeds (NDTV, Al Jazeera, BBC, Guardian, etc.).

**Scout mapping:** `pulse` scouts filtered by region, or `web` scouts watching specific outlets.

**Rate limit:** Same as `rss_investigative`.

**Manifest:** `monitoring/feeds/sources/rss_regional/manifest.json`

---

### GDACS (`gdacs`)

**What it monitors:** Disaster alerts — floods, cyclones, earthquakes, wildfires.

**Scout mapping:** `civic` scouts tracking specific regions for event onset.

**Rate limit:** Free, no auth, but polite polling expected.

**Manifest:** `monitoring/feeds/sources/gdacs/manifest.json`

---

### ACLED (`acled`)

**What it monitors:** Armed conflict events — battles, violence against civilians, protests, riots. Global coverage with daily updates.

**Scout mapping:** `civic` scouts tracking specific regions for conflict patterns, or `pulse` scouts with actor/event-type filters.

**Auth:** Requires free registration at https://developer.acleddata.com/ — get API key + email.

**Env vars:** `ACLED_API_KEY`, `ACLED_EMAIL`

**Rate limit:** 1,000 requests/day on free tier.

**Manifest:** `monitoring/feeds/sources/acled/manifest.json`

---

## Adding a New Source

See `skills/monitoring/SKILL.md` § Adding a New Source for the full drop-in procedure. Summary:

1. Create `monitoring/feeds/sources/{new_id}/`
2. Write `manifest.json` with `id`, `name`, `category`, `required_env_vars`, etc.
3. Write `fetch.py` exposing a `fetch(since, query, project)` function returning a list of normalized items
4. Run `python3 monitoring/feeds/preflight.py --json` — the new source should appear

No changes to `monitor.py` or `preflight.py` required — both discover sources via manifest scanning.

---

## Preflight Status Semantics

`preflight.py` reports a status per source:

| Status | Meaning |
|---|---|
| `green` | `required_env_vars` are set (if any) AND smoke query succeeded |
| `yellow` | `required_env_vars` are set BUT smoke query failed (API down, rate-limited, etc.) |
| `red` | At least one `required_env_vars` is missing |

`green` sources can be queried with confidence. `yellow` sources may work intermittently. `red` sources require env var setup before use — the preflight output lists the missing vars.
