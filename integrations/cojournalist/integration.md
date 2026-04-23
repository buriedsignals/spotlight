# coJournalist — Durable Monitoring Integration

**What:** coJournalist is the default durable-monitoring target for Spotlight. Spotlight does not modify coJournalist; it uses the existing project, scout, and unit surfaces exactly as they exist today.

**When to use:**

- The user approved ongoing monitoring beyond the current investigation cycle
- A target should keep running while Spotlight is idle
- A monitor should return structured updates later as information units

**When NOT to use:**

- Passive open-intelligence feed polling across GDELT/RSS/GDACS/ACLED. That belongs in Mycroft.
- One-off investigative research inside the current cycle. Use Spotlight's normal verbs.
- Monitoring without explicit user approval.

## Preferred flow

1. Create or reuse **one coJournalist project per Spotlight case**
2. Create the approved scout(s) under that `project_id`
3. Store the returned `project_id` and `scout_id` values in `cases/{project}/data/monitoring.json`
4. On later resumes, fetch updates back by `project_id`

This keeps case linkage in Spotlight while leaving coJournalist unchanged.

## Preferred invocation: `cojo` CLI

If `cojo` is installed and already configured, prefer it over raw HTTP.

Create or reuse a project:

```sh
cojo projects list
cojo projects add --name "Spotlight: {project}" --description "Linked Spotlight investigation"
```

Create a scout under that project:

```sh
cojo scouts add \
  --name "{monitor_name}" \
  --type <web|pulse|social|civic> \
  [--url "{url}"] \
  [--criteria "{criteria}"] \
  --project "{project_id}"
```

Read updates later:

```sh
cojo units list --project "{project_id}" --since 7d
```

## API fallback

If the CLI is unavailable but `COJOURNALIST_API_KEY` is set, use the existing HTTP API.

Create a project:

```sh
curl -s https://www.cojournalist.ai/api/v1/projects \
  -H "Authorization: Bearer $COJOURNALIST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Spotlight: {project}","description":"Linked Spotlight investigation","visibility":"private"}'
```

Create a scout:

```sh
curl -s https://www.cojournalist.ai/api/v1/scouts \
  -H "Authorization: Bearer $COJOURNALIST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"{monitor_name}",
    "type":"pulse",
    "criteria":"{criteria}",
    "project_id":"{project_id}",
    "schedule":{"regularity":"daily","time":"08:00","day_number":1}
  }'
```

Read units:

```sh
curl -s "https://www.cojournalist.ai/api/v1/units?project_id={project_id}&limit=20" \
  -H "Authorization: Bearer $COJOURNALIST_API_KEY"
```

## Mapping notes

- Spotlight's `pulse` recommendation maps to coJournalist's current beat/pulse creation surface. Keep that normalization inside Spotlight; do not require a coJournalist change.
- Use `project_id` as the case grouping primitive. Do not rely on scout names for linkage.
- Spotlight stores the linkage locally in `monitoring.json`; coJournalist remains generic.

## Evidence and logging

- Log every created `project_id` and `scout_id` in `cases/{project}/data/monitoring.json`
- Log every later unit check in `monitoring.json` `checks[]`
- Treat returned units as candidate investigation context, not auto-verified facts

## Sensitive mode

In sensitive mode, do not create or query live coJournalist monitors. Existing local copies of prior unit exports may still be read from `cases/{project}/research/`.
