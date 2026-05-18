# Phantom Tide Integration

Phantom Tide is a transport-intelligence API and analyst surface for maritime and airspace signals. Use it to turn cross-domain movement anomalies into leads, then corroborate them with primary sources such as flight/vessel registries, ADS-B/AIS history, official notices, sanctions lists, port records, satellite imagery, and archived source material.

## Confirmed Surface

The currently confirmed machine-consumable endpoint is:

```text
GET https://phantom.labs.jamessawyer.co.uk/api/public/aircraft/restricted-airspace-crossings
```

It returns restricted-airspace crossing candidates with event IDs, timestamps, aircraft identifiers, airspace metadata, coordinates, source freshness, reference-layer state, and contract notes.

Treat these records as **candidate movement context**, not as proof of wrongdoing, regulatory breach, or live enforcement alert. The endpoint contract explicitly says entries are replay/archive-derived candidates and should be used as ingestion or showcase context.

## Environment

Requires:

```bash
PHANTOM_TIDE_API_KEY=pt_...
```

Do not print the key. Store it in `.env` only.

## When To Use

Use Phantom Tide when an investigation involves:

- aircraft entering, exiting, or traversing restricted/special-use airspace;
- transport activity near conflict zones, maritime chokepoints, infrastructure, sanctions targets, or official warning areas;
- a need to distinguish live, stale, degraded, tier-limited, or partial transport context;
- a lead where multiple weak transport signals may converge into a stronger question.

Do not use Phantom Tide as a sole source for publication-grade findings. Use it to generate or prioritize leads, then corroborate with independent source trails.

## Aircraft Restricted-Airspace Query

Validate case slugs and output paths before constructing command strings. Do not interpolate untrusted user text into shell commands.

```bash
python3 scripts/spotlight_safe.py validate-slug "{project_slug}"
curl --get "https://phantom.labs.jamessawyer.co.uk/api/public/aircraft/restricted-airspace-crossings" \
  -H "Authorization: Bearer ${PHANTOM_TIDE_API_KEY}" \
  --data-urlencode "hours=24" \
  --data-urlencode "limit=100" \
  --data-urlencode "include_meta=true" \
  -o "cases/{project}/research/phantom-tide-airspace-{timestamp}.json"
```

For polling, use the returned watermark:

```bash
curl --get "https://phantom.labs.jamessawyer.co.uk/api/public/aircraft/restricted-airspace-crossings" \
  -H "Authorization: Bearer ${PHANTOM_TIDE_API_KEY}" \
  --data-urlencode "sample_after={poll_after}" \
  --data-urlencode "include_meta=true" \
  -o "cases/{project}/research/phantom-tide-airspace-after-{timestamp}.json"
```

## Output Handling

Save raw responses under:

```text
cases/{project}/research/phantom-tide-<query-kind>-<slug>-<timestamp>.json
```

When folding records into findings, preserve:

- `event_id`
- `when`
- `who.icao24`
- `who.callsign`
- `what.transition`
- `what.airspace.name`
- `what.airspace.restriction_label`
- `where.lat`
- `where.lon`
- `quality.status`
- `data_freshness.status`
- `reference_layer.source_snapshots[]`
- `contract.notes[]`

Evidence entries should state:

```json
{
  "access_method": "api",
  "access_notes": "Retrieved via Phantom Tide API as candidate transport context; corroboration required before publication."
}
```

Confidence caps:

- `low` if Phantom Tide is the only source.
- `medium` if an independent ADS-B/AIS/official-notice trail supports the event timing and geometry.
- `high` only if primary records or authoritative archives corroborate the movement and the claim wording avoids overstating intent or violation.

## Maritime And Area Intelligence

The public docs and repository describe maritime capabilities such as AIS context, vessel formations, DSC communications, vessel-linked detail context, chokepoint context, proximity query, Area Intelligence Report, and convergence zones. These are highly relevant to Spotlight, but no stable public API contract for those routes is confirmed yet.

Until Phantom Tide provides endpoint docs, do not claim direct maritime support beyond what a user can manually inspect in the UI. Record these as desired API capabilities:

- vessel lookup by MMSI, IMO, name, callsign;
- vessel position/history window;
- vessel-in-zone and chokepoint proximity;
- DSC communications linked to vessels or coast stations;
- convergence-zone query by bounding box, point/radius, or time window;
- Area Intelligence Report by point/radius;
- source health and tier state for every response.

## Source-State Reading

Phantom Tide distinguishes:

- `Live`: current ingest succeeded.
- `Degraded`: the source answered but completeness or quality fell.
- `Stale`: cached/old data remains visible for continuity.
- `Tier-limited`: the feature exists but the current access tier is capped.
- `Partial`: some sources or historical windows are incomplete.

Never treat an empty or partial response as proof of absence until source state, time window, zoom/area scope, access tier, and freshness have been checked.

## Sensitive Mode

Phantom Tide is a remote API. Do not call it when sensitive mode strips remote fetch/search behavior. Pre-cached JSON responses may still be read locally and cited as previously acquired material if the acquisition time and source state are preserved.

## Developer API Gaps

Ask Phantom Tide for:

- OpenAPI schema or endpoint docs for all keyed routes.
- Auth header format and key scopes.
- Rate limits, pagination, and backoff semantics.
- Stable response schemas for aircraft, vessel, DSC, convergence, proximity, and area-report routes.
- Watermark fields for polling.
- Freshness/source-health fields guaranteed on every endpoint.
- Error taxonomy for tier limits, stale data, partial data, empty windows, invalid geometry, and unavailable upstreams.
- Terms for storing raw responses in local Spotlight case folders.
