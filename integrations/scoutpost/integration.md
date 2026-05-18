# Scoutpost — Durable Monitoring Integration

**What:** Scoutpost is the default durable-monitoring target for Spotlight. Spotlight does not modify Scoutpost; it uses the existing project, scout, and unit surfaces exactly as they exist today.

**When to use:**

- The user approved ongoing monitoring beyond the current investigation cycle
- A target should keep running while Spotlight is idle
- A monitor should return structured updates later as information units

**When NOT to use:**

- Passive open-intelligence feed polling across GDELT/RSS/GDACS/ACLED. That belongs in Mycroft.
- One-off investigative research inside the current cycle. Use Spotlight's normal verbs.
- Monitoring without explicit user approval.

## Preferred flow

1. Create or reuse **one Scoutpost project per Spotlight case**
2. Create the approved scout(s) under that `project_id`
3. Store the returned `project_id` and `scout_id` values in `cases/{project}/data/monitoring.json`
4. On later resumes, fetch updates back by `project_id`

This keeps case linkage in Spotlight while leaving Scoutpost unchanged.

## Preferred invocation: `scout` CLI

If `scout` is installed and already configured, prefer it over raw HTTP.

Create or reuse a project:

```sh
scout projects list
scout projects add --name "Spotlight: {project}" --description "Linked Spotlight investigation"
```

Create a scout under that project:

```sh
scout scouts add \
  --name "{monitor_name}" \
  --type <web|pulse|social|civic> \
  [--url "{url}"] \
  [--criteria "{criteria}"] \
  --project "{project_id}"
```

Read updates later:

```sh
scout units list --project "{project_id}" --since 7d
```

## API fallback

If the CLI is unavailable but `SCOUTPOST_API_KEY` is set, use the existing HTTP API.

Invoke `shell-safety` first. Write request bodies as JSON files with `write-file` or a real serializer; do not interpolate project names, criteria, or monitor names into curl strings.

Create a project:

```sh
write-file("cases/{project}/research/scoutpost-project-body.json", <serialized project JSON>)
curl -s https://www.scoutpost.ai/api/v1/projects \
  -H "Authorization: Bearer $SCOUTPOST_API_KEY" \
  -H "Content-Type: application/json" \
  --data @cases/{project}/research/scoutpost-project-body.json
```

Create a scout:

```sh
write-file("cases/{project}/research/scoutpost-scout-body.json", <serialized scout JSON>)
curl -s https://www.scoutpost.ai/api/v1/scouts \
  -H "Authorization: Bearer $SCOUTPOST_API_KEY" \
  -H "Content-Type: application/json" \
  --data @cases/{project}/research/scoutpost-scout-body.json
```

Read units:

```sh
curl -s --get "https://www.scoutpost.ai/api/v1/units" \
  --data-urlencode "project_id={project_id}" \
  --data-urlencode "limit=20" \
  -H "Authorization: Bearer $SCOUTPOST_API_KEY"
```

## Mapping notes

- Spotlight's `pulse` recommendation maps to Scoutpost's current beat/pulse creation surface. Keep that normalization inside Spotlight; do not require a Scoutpost change.
- Use `project_id` as the case grouping primitive. Do not rely on scout names for linkage.
- Spotlight stores the linkage locally in `monitoring.json`; Scoutpost remains generic.

## Evidence and logging

- Log every created `project_id` and `scout_id` in `cases/{project}/data/monitoring.json`
- Log every later unit check in `monitoring.json` `checks[]`
- Treat returned units as candidate investigation context, not auto-verified facts

## Sensitive mode

In sensitive mode, do not create or query live Scoutpost monitors. Existing local copies of prior unit exports may still be read from `cases/{project}/research/`.
