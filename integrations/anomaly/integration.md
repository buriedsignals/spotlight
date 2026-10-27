# Anomaly detectors

Optional. [Anomaly](https://github.com/buriedsignals/Anomaly) is context for
an investigation plus a deterministic runner. It answers three questions:
which data sources could help, how to get and use each one, and which
detectors fit that type of data. It then runs the chosen detectors on tables
the investigator already fetched. Anomaly stays a separate install that
Spotlight never changes, and it fetches nothing for Spotlight.

## Setup

1. Clone Anomaly's main branch and run `uv sync` in it, so its `.venv` exists.
   Engine installs and updates this checkout; Spotlight never changes it.
2. Set `ANOMALY_HOME` to that checkout (environment, the checkout `.env`, or
   the install's `env_file`).

Preflight reports `unconfigured` without `ANOMALY_HOME`, and `yellow` with the
reason when the installed catalogue cannot be used. Spotlight uses whatever
catalogue is installed, but only when its `schema_version` is one it reads
(currently 1) and its `catalogue_hash` matches the file's content (sha256 over
the canonical JSON of the other keys, Anomaly's own rule). Any other catalogue
hides the feature: the lookup lists nothing, a plan that names a domain fails
Methodology validation, and nothing runs.

Without a pinned release, reproducibility comes from each detector's
`implementation_hash`. Approving a methodology with an `anomaly` section
records the hash of every detector the plan allows (the core detectors and
those of its domains) in the approval. A run refuses a detector whose hash
differs ("detector changed since approval; re-approve") or that was not in the
catalogue at approval. Each run in `data/anomaly-signals.json` records the
`catalogue_hash` and the detector's `implementation_hash`.

## Methodology: the lookup

```text
execute-shell("scripts/spotlight-python integrations/anomaly/adapter.py context [--domain D | --source ID]")
```

runs Anomaly's read-only `python -m anomaly.context` from the installed checkout,
with no keys. It prints each domain (a type of data such as lobbying or
procurement), with its data sources (status, access, cost, licence, the
SKILL.md that says how to get and use the data) and the detectors that read
that domain, plus the core detectors that run on any table under
`any_table`. `--source ID` adds that source's SKILL.md text. When the feature
is hidden it prints `available: false` with the reason and nothing else.

When the member opts in during the brief conversation, the methodology
records the domains and sources judged useful, and why:

```json
"anomaly": {"domains": [{"domain": "procurement", "sources": ["<source id>"], "why": "..."}]}
```

(`any_table` for the core detectors, whose tables may come from anywhere). It
is a rough plan, with no column mappings and no detector choice; the member's
Methodology approval confirms it. The methodology validator checks every
domain and source against the lookup. The Methodology page shows the plan
with the detectors each domain offers.

## Investigation: the runner

The investigator fetches each table as its source's SKILL.md says, records it
in the evidence bundle like any source, and runs a detector on it:

```text
execute-shell("scripts/spotlight-python integrations/anomaly/adapter.py run {CASE_DIR}")   # request on stdin
```

A method is `method:<detector id>@<version>`.

- A core detector takes one table and its field roles:
  `{"method": "method:numeric.zscore_outliers@1.1.0", "table": "research/payments.csv", "fields": {"id": "record_id", "amount": "value"}}`.
- A domain detector takes one file per contract table it reads, and optional
  column maps: `{"method": "method:lobbying.<id>@<version>", "tables": {"<contract table>": "research/<file>.csv"}, "columns": {"<contract table>": {"<contract column>": "<file column>"}}}`.

The Pi harness offers the same as the investigator's `anomaly_run` tool. A
run is refused, and nothing runs, unless:

- the approved methodology has an `anomaly` section (the member opted in),
  and a domain detector's data type is one of its domains;
- the method is in the installed catalogue, with the implementation hash the
  methodology approval recorded for it, exactly the inputs its kind takes and
  a column for every field role it needs;
- every table is a regular case file under `research/` or `evidence/`, in CSV,
  JSONL or Parquet.

The runner gets read-only copies of the tables in a private temporary folder,
an environment with no keys, tokens, proxies or Python hooks, and a request
that names the method's catalogue version and implementation hash, with
`--approved` (the member's Methodology approval is the approval). Anomaly
loads only the mapped columns into an in-memory DuckDB and disables external
access before any method SQL runs; the method SQL must pass Anomaly's
read-only validation.

Every output line is checked before it is kept: the exact keys of the
catalogue's `signal_schema` (a domain detector's provenance has `column_map`
for `field_map` and the per-table `input_hashes`), `status: lead`, the
requested method's id, version, implementation hash and category, the
supplied tables' hashes, `approval: caller-recorded`, and the method's
`expected_output` columns in `values`. One bad line drops the run.

The run is appended to `data/anomaly-signals.json` (under the case lock):
the method, the tables with their paths and hashes, the field or column map,
the method's assumptions and known false positives, and every signal.

## Investigation gate

The Investigation page lists each run and up to 100 signals, each with the
method's assumptions and known false positives. The member accepts each one
as a lead or dismisses it. A signal is never a finding, and accepting one
does not make it one: only what the investigator then verifies becomes a
finding. The gate approval binds `data/anomaly-signals.json`.

## Limits

- Spotlight does not sandbox the runner's network itself. The guarantees are
  Anomaly's: methods are SQL, validated read-only, run with external access
  disabled after the tables are loaded.
- Research lanes no longer collect tables for Anomaly. A plan approved before
  the move may carry an empty `anomaly_methods` on a topic; D1 refuses a
  non-empty one.
