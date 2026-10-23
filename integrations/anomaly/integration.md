# Anomaly clue methods

Optional. [Anomaly](https://github.com/buriedsignals/Anomaly) publishes a
versioned catalogue of detectors (`detectors/catalogue.json`) and a one-shot
runner (`python -m anomaly.oneshot`) that runs catalogued detectors on one
supplied table. Spotlight uses them as Research clue methods. Anomaly stays a
separate install; Spotlight never changes it.

## Setup

1. Clone Anomaly at the release pinned in `manifest.json`
   (`catalogue_pin.release`) and run `uv sync` in it, so its `.venv` exists.
2. Set `ANOMALY_HOME` to that checkout (environment, the checkout `.env`, or
   the install's `env_file`).

Preflight reports `unconfigured` without `ANOMALY_HOME`, and `yellow` with the
reason when the installed catalogue is not the pinned one. The catalogue is
used only when its `catalogue_hash` equals the pin and matches the file's
content (sha256 over the canonical JSON of the other keys, Anomaly's own
rule). Any other catalogue hides the feature: no methods are offered, and a
plan that names one fails Methodology validation.

To move to a newer Anomaly release, update `catalogue_pin` (release commit,
`schema_version`, `catalogue_hash`) in a reviewed change.

## Methods

A method is `method:<detector id>@<version>`. Only detectors that run on one
supplied table (`required_tables: ["*"]`) are offered; detectors that need a
prepared Anomaly case are not.

```text
execute-shell("scripts/spotlight-python integrations/anomaly/adapter.py methods")
```

prints the state and the methods with their required fields, assumptions and
known false positives. The orchestrator attaches fitting methods to a topic
brief's `anomaly_methods`; D1 allows at most 10 across the plan, and the
methodology validator refuses a method that is not in the pinned catalogue.
The Methodology page view (`review-view methodology`) carries the same data
under `content.anomaly_methods`: the methods that fit and each topic's
attached methods.

## Lane tables: `method-inputs.json`

A lane that collected a table for its topic's methods lists it in its folder,
`research/swarm/<topic-id>/method-inputs.json`:

```json
{
  "schema_version": "1.0",
  "inputs": [
    {
      "method": "method:numeric.zscore_outliers@1.1.0",
      "lead_id": "lead-7",
      "format": "csv",
      "fields": {"id": "record_id", "amount": "value"}
    }
  ]
}
```

`lead_id` is the lead whose stored original (`content_ref`, under `sources/`)
is the table; `format` is `csv`, `jsonl` or `parquet`; `fields` maps each of
the method's required fields to a table column. Parameters stay at the
catalogue defaults.

## When methods run

`scripts/research-merge.py` runs them after the merge, before writing
`data/clues.json`. An input runs only when:

- its method is in that topic's approved `anomaly_methods` and in the pinned
  catalogue;
- its lead is among the merged leads, a stored page whose excerpt passed D3,
  and the table's sha256 still matches the lead.

Each table gets one runner call with up to 10 methods. The runner receives a
read-only copy of the table in a private temporary folder, an environment with
no keys, tokens, proxies or Python hooks, and a request pinned to each
method's catalogue version and implementation hash, with `--approved` (the
member's Methodology approval is the approval). Anomaly loads only the mapped
columns into an in-memory DuckDB and disables external access before any
method SQL runs; the method SQL must pass Anomaly's read-only validation.

Every output line is checked before it becomes a clue: the exact keys of the
catalogue's `signal_schema`, `status: lead`, the requested method's id,
version, implementation hash and category, the supplied table's hash,
`approval: caller-recorded`, and the method's `expected_output` columns in
`values`. One bad line drops that table's run.

## Clues

Signals for one method on one table become one clue: `type: method_signal`,
`source: method:<id>@<version>`, the table's lead, the flagged candidates in
the summary and the method's known false positives in the warning. Method
clues rank after the clue rules' types. The clue id depends only on the
method, the lead and the candidates, so a re-run with the same lanes writes
the same `data/clues.json`.

A method that cannot run (no install, unpinned catalogue, a refusal, bad
output) is listed in the merge report and raises no clue; the merge still
writes.

## Limits

- Spotlight does not sandbox the runner's network itself. The guarantees are
  Anomaly's: methods are SQL, validated read-only, run with external access
  disabled after the table is loaded.
- The lane runner (`harness/pi/src/lanes.ts`) has no typed tool that writes
  `method-inputs.json` yet; in subscription mode the researcher writes it
  with `write-file`.
