# Spotlight RLM Integration

RLM is an optional case-corpus helper. It inspects case-contained files and writes `{CASE_DIR}/data/rlm-analysis.json`. Investigators may treat the output as leads. Fact-checking still validates claims from canonical case files, not raw RLM artifacts.

## Modes

| Mode | Meaning |
|---|---|
| `off` | No RLM. Writes nothing unless `write_off_marker` is explicitly set. |
| `lite` | Deterministic extraction only: emails, domains, URLs, handles, dates, and source refs. |
| `local_gemma4_e4b` | Experimental Ollama `gemma4:e4b` structured extraction. Defaults to hybrid prefiltering and fails closed if the model is unavailable. |

Do not add alternate models to the first benchmark. The benchmark asks whether Gemma4 E4B adds value over RLM-off, not which local model is best.

## Default Strategy

Use hybrid prefiltering by default for `local_gemma4_e4b`:

1. Deterministically select source lines with lead-like signals.
2. Extract simple entities, URLs, handles, and dates without an LLM.
3. Send only semantic/contradiction candidate lines to Gemma4 E4B.
4. Merge and deduplicate deterministic + model artifacts.

Full-corpus Gemma remains available for benchmarking with:

```json
{
  "mode": "local_gemma4_e4b",
  "prefilter": false,
  "hybrid": false
}
```

## Request

```bash
scripts/spotlight-python integrations/rlm/run_rlm.py path/to/request.json
```

Codex adapter note: local Gemma RLM talks to Ollama at
`http://127.0.0.1:11434`. If Codex is running with a sandbox that blocks
localhost/network access, `run_rlm.py` can report `gemma4:e4b` as unavailable
even when Ollama is running. Before treating that as a model failure, rerun the
RLM preflight/call with host-approved localhost access or equivalent
unsandboxed execution.

```json
{
  "project": "case-slug",
  "run_id": "20260604-rlm",
  "mode": "local_gemma4_e4b",
  "corpus_paths": ["research/source-a.md", "research/source-b.md"]
}
```

`corpus_paths` must stay inside `{CASE_DIR}/`.

Optional tuning fields:

```json
{
  "prefilter": true,
  "hybrid": true,
  "num_ctx": 4096,
  "num_predict": 0
}
```

`num_predict: 0` means do not set an Ollama output cap. In local testing, adding
an output cap caused empty/non-JSON responses on the extraction prompt; keep it
off until a dedicated retry/repair loop proves otherwise.

## Benchmarks

The shipped benchmark audit lives at `docs/rlm-benchmark-audit.md`.

Run the end-to-end dummy flow benchmark:

```bash
scripts/spotlight-python integrations/rlm/benchmark_flow.py \
  --fixture evals/rlm-flow-fixtures/fixture-context-rot-001 \
  --out /private/tmp/spotlight-rlm-flow-hybrid-gemma4-e4b.json \
  --no-rlm-chunk-budget 12
```

Run the full dummy flow suite:

```bash
scripts/spotlight-python integrations/rlm/benchmark_flow_suite.py \
  --fixtures evals/rlm-flow-fixtures \
  --out /private/tmp/spotlight-rlm-flow-suite-real-gemma4-e4b.json \
  --no-rlm-chunk-budget 12
```

Single context-rot fixture, real `gemma4:e4b` result on 2026-06-04:

| Variant | Recall | Contradiction | Downstream lines | Wall time |
|---|---:|---|---:|---:|
| Without RLM | 0.25 | no | 12 | 0.0s |
| Full Gemma RLM | 1.0 | yes | 6 | 45.431s |
| Hybrid prefiltered Gemma RLM | 1.0 | yes | 4 | 11.314s |

Four-fixture suite, real `gemma4:e4b` result on 2026-06-04:

| Variant | Avg recall | Avg precision | Contradictions satisfied | Decoy hits | Avg downstream lines | Avg wall time |
|---|---:|---:|---:|---:|---:|---:|
| Without RLM | 0.75 | 0.857 | 0/2 | 4 | 10.25 | 0.0s |
| Full Gemma RLM | 1.0 | 1.0 | 1/2 | 0 | 3.75 | 33.285s |
| Hybrid prefiltered Gemma RLM | 1.0 | 1.0 | 2/2 | 0 | 3.0 | 5.749s |

Raw suite output is preserved at
`evals/rlm-results/spotlight-rlm-flow-suite-real-gemma4-e4b-2026-06-04.json`.

This supports hybrid prefiltering as the current proposal. It improves recall
and reduces downstream context load on synthetic lead-methodology-cycle fixtures
while avoiding decoy artifacts. It is still slower than no RLM when a semantic
model pass is required, and it remains unproven on real live-source
investigations.

## Output

`data/rlm-analysis.json` validates against `schemas/rlm-analysis.schema.json`. Every artifact is `needs_verification` and every non-discarded artifact has source refs.
