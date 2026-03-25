# Spotlight — Local Environment

Local extensions for the Spotlight investigation skill (marketplace: `buriedsignals/skills`).
The marketplace skill handles the full pipeline. This file adds local monitoring infrastructure
and environment config.

## OSINT Monitoring

### Feed Framework (primary)

Pluggable feed framework with 5 sources. Full docs: `docs/osint-feed-framework.md`.

| Source | ID | What it provides |
|--------|----|------------------|
| GDELT | `gdelt` | Global news articles (150+ countries, keyword search) |
| RSS Investigative | `rss_investigative` | Bellingcat, ICIJ, The Intercept, Crisis Group |
| RSS Regional | `rss_regional` | 17 regional feeds (NDTV, Al Jazeera, BBC, Guardian, etc.) |
| GDACS | `gdacs` | Disaster alerts (floods, cyclones, earthquakes, etc.) |
| coJournalist | `cojournalist` | AI-curated local news units from scouts |

```bash
# Bulk check (for /loop monitoring)
python3 monitoring/feeds/monitor.py check-all --project {project} --since 35m --json

# Discovery
python3 monitoring/feeds/monitor.py list --project {project}

# Targeted query
python3 monitoring/feeds/monitor.py query gdelt --project {project} --json
python3 monitoring/feeds/monitor.py query rss_investigative --since 24h --json
python3 monitoring/feeds/monitor.py query rss_regional --project {project} --json
python3 monitoring/feeds/monitor.py query gdacs --json
python3 monitoring/feeds/monitor.py query cojournalist --project {project} --json
```

### World Monitor Intelligence (secondary)

```bash
tools/worldmonitor/check-signals.sh --json
tools/worldmonitor/check-signals.sh --json --category conflict
tools/worldmonitor/check-signals.sh --json --category news
tools/worldmonitor/check-signals.sh --json --category unrest
tools/worldmonitor/check-signals.sh --json --category intel
```

Requires Docker stack at `~/worldmonitor`. Dashboard: `http://localhost:3000`.

### Crucix Intelligence (fallback)

```bash
tools/crucix/check-briefing.sh --json
tools/crucix/check-briefing.sh --json --severity FLASH
```

### coJournalist Scout Management

```bash
python3 tools/cojournalist/scout.py create-web --name "name" --url "https://..." --criteria "what" --schedule daily --time 08:00
python3 tools/cojournalist/scout.py create-pulse --name "name" --topic "terms" --location '{"displayName":"Place","country":"XX"}' --schedule daily --time 06:00
python3 tools/cojournalist/scout.py list
python3 tools/cojournalist/scout.py status "name"
python3 tools/cojournalist/scout.py run "name"
python3 tools/cojournalist/scout.py units "name" --since 7d
python3 tools/cojournalist/scout.py delete "name"
```

Requires `COJOURNALIST_API_KEY` env var.

## Investigation Review & Production Handoff

After Gate 1, Tom reviews findings in the local investigation review app.

```bash
python serve.py          # http://localhost:8001/apps/investigations/
```

The review app reads from `cases/{project}/` and lets Tom review findings, provide
per-finding feedback, and approve the investigation. On approval, the server
automatically creates `../newsroom/production/{project}/` with:

- `investigation/` — symlinks to case files (findings.json, fact-check.json, summary.json, research/, geo/, data/)
- `script-assets/` — symlinks to curated media from `cases/{project}/script-assets/` and `research/media/`

This provides the Chronicler's scriptwriter (in newsroom) with structured access to
all investigation materials without duplicating files. The scriptwriter reads from
`production/{project}/investigation/` and curates visual assets in `production/{project}/script-assets/`.

The review app and serve.py are local-only (gitignored) — they exist on this device for the
handoff workflow. The marketplace skill handles the investigation pipeline itself.

## Sensitive Mode

When Tom says "switch to sensitive mode": remove WebFetch and WebSearch from agent spawn
prompts. All research becomes local-only. Acknowledge the switch explicitly.

To revert: Tom says "switch to normal mode."

## Machine Setup

Vault ingestion path is configured in `.spotlight-config.json` during first run (Phase 0 preflight).
