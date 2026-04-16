---
name: monitoring
description: OSINT feed framework integration — coJournalist scouts, GDELT, RSS, GDACS for ongoing signal monitoring
version: "1.0"
invocable_by: [orchestrator]
requires: []
---

# Monitoring Skill

Integrates the OSINT feed framework for ongoing signal monitoring during and after investigations.

---

## Overview

The monitoring skill connects Spotlight investigations to real-time OSINT feeds. When monitoring_recommendations appear in investigation findings, this skill configures and manages ongoing feed monitoring to track developments related to the investigation targets.

## Feed Sources

| Source ID | Name | Category | What it Monitors |
|-----------|------|----------|------------------|
| gdelt | GDELT | News | Global news articles, 150+ countries, keyword search |
| rss_investigative | RSS Investigative | News | Bellingcat, ICIJ, The Intercept, Crisis Group |
| rss_regional | RSS Regional | News | 17 regional feeds (NDTV, Al Jazeera, BBC, Guardian, etc.) |
| gdacs | GDACS | Disasters | Disaster alerts (floods, cyclones, earthquakes) |
| cojournalist | coJournalist | Curated | AI-curated local news units from configured scouts |

## Usage

### During Investigation

If findings contain monitoring_recommendations, invoke this skill to:

1. Query feeds for the recommended keywords/terms
2. Generate scout configuration for ongoing monitoring
3. Write monitoring configuration to cases/{project}/data/monitoring.json

### Monitoring Commands

```bash
# Bulk check for /loop monitoring
python3 monitoring/feeds/monitor.py check-all --project {project} --since 35m --json

# Discovery — list available sources
python3 monitoring/feeds/monitor.py list --project {project}

# Targeted query
python3 monitoring/feeds/monitor.py query gdelt --project {project} --json
python3 monitoring/feeds/monitor.py query rss_investigative --since 24h --json
python3 monitoring/feeds/monitor.py query rss_regional --project {project} --json
python3 monitoring/feeds/monitor.py query gdacs --json
python3 monitoring/feeds/monitor.py query cojournalist --project {project} --json
```

## coJournalist Scout Management

coJournalist scouts provide targeted, AI-curated monitoring:

```bash
# Create web scout (monitors specific URL)
python3 tools/cojournalist/scout.py create-web --name "name" --url "URL" --criteria "what" --schedule daily --time 08:00

# Create topic scout (monitors keyword/phrase)
python3 tools/cojournalist/scout.py create-pulse --name "name" --topic "terms" --location LOC --schedule daily --time 06:00

# List and manage scouts
python3 tools/cojournalist/scout.py list
python3 tools/cojournalist/scout.py status "name"
python3 tools/cojournalist/scout.py run "name"
python3 tools/cojournalist/scout.py units "name" --since 7d
python3 tools/cojournalist/scout.py delete "name"
```

Requires COJOURNALIST_API_KEY env var.

## Monitoring Configuration Schema

```json
{
  "project": "string",
  "created_at": "ISO8601",
  "feeds": [
    {
      "source_id": "gdelt|rss_investigative|rss_regional|gdacs|cojournalist",
      "enabled": true,
      "query": "optional override query",
      "since": "duration (e.g., 24h, 7d)"
    }
  ],
  "scouts": [
    {
      "name": "string",
      "type": "web|pulse",
      "schedule": "daily|weekly|hourly",
      "last_run": "ISO8601"
    }
  ],
  "alert_on": ["keyword matches", "..."]
}
```

## Integration with Investigation Pipeline

1. Finding Generation: Investigator produces findings.json with monitoring_recommendations
2. Monitoring Invocation: Orchestrator invokes this skill when recommendations exist
3. Configuration: Skill writes cases/{project}/data/monitoring.json
4. Ongoing Monitoring: External monitor reads config and runs periodic checks
5. Signal Ingestion: New signals update the investigation research/ directory

## Sensitive Mode

In sensitive mode, monitoring still functions but:
- Uses pre-configured scout URLs only (no new web scouts)
- Topic scouts must use pre-approved keyword lists
- No new feed queries — relies on existing monitoring infrastructure

## Schema Reference

| Schema | Path | Purpose |
|--------|------|---------|
| Findings | schemas/findings.schema.json | Investigation findings with monitoring_recommendations |
| Monitoring Config | schemas/monitoring.schema.json | Feed and scout configuration |
