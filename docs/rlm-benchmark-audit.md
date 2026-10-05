---
title: RLM Benchmark Audit
status: current
created: 2026-06-09
benchmark_date: 2026-06-04
model: gemma4:e4b
mode: hybrid prefiltered RLM
---

# RLM Benchmark Audit

This audit records why Spotlight setup and methodology approval describe RLM as
beneficial but optional.

## Decision

RLM remains off by default. When a user enabled it during setup, Spotlight
proposes it during the methodology phase for each case. If the user approves,
Spotlight runs RLM before research execution and treats the output as
`needs_verification` leads only.

## Benchmark Scope

The benchmark compared Spotlight flow performance with and without RLM on
synthetic lead-methodology-cycle fixtures designed to expose context rot,
contradiction loss, and decoy-source contamination.

The tested RLM model was `gemma4:e4b` through Ollama, using hybrid
prefiltering:

1. deterministic extraction for simple source signals,
2. semantic chunk selection for contradiction/relationship candidates,
3. local Gemma4 E4B analysis only on the selected chunks,
4. merge and deduplication into `data/rlm-analysis.json`.

Raw suite output:

`evals/rlm-results/spotlight-rlm-flow-suite-real-gemma4-e4b-2026-06-04.json`

## Results

Four-fixture suite, real `gemma4:e4b` result on 2026-06-04:

| Variant | Avg recall | Avg precision | Contradictions satisfied | Decoy hits | Avg downstream lines | Avg wall time |
|---|---:|---:|---:|---:|---:|---:|
| Without RLM | 0.75 | 0.857 | 0/2 | 4 | 10.25 | 0.0s |
| Full Gemma RLM | 1.0 | 1.0 | 1/2 | 0 | 3.75 | 33.285s |
| Hybrid prefiltered Gemma RLM | 1.0 | 1.0 | 2/2 | 0 | 3.0 | 5.749s |

Single context-rot fixture, real `gemma4:e4b` result on 2026-06-04:

| Variant | Recall | Contradiction | Downstream lines | Wall time |
|---|---:|---|---:|---:|
| Without RLM | 0.25 | no | 12 | 0.0s |
| Full Gemma RLM | 1.0 | yes | 6 | 45.431s |
| Hybrid prefiltered Gemma RLM | 1.0 | yes | 4 | 11.314s |

## Practical Interpretation

Hybrid Gemma4 E4B RLM improved average recall from 0.75 to 1.0, improved
average precision from 0.857 to 1.0, satisfied both benchmark contradictions,
removed four decoy hits, and reduced average downstream lines from 10.25 to 3.0.

The tradeoff is runtime: no-RLM has no semantic model pass, while hybrid RLM
averaged 5.749 seconds on the four-fixture suite. For a full Spotlight case
lasting tens of minutes, this is expected to be a small additive cost when RLM
runs once during methodology/execution handoff.

## Limits

- These are synthetic fixtures, not a broad real-world live-source benchmark.
- RLM output is not evidence and not verification.
- Fact-checking still validates claims against canonical case files.
- Local Ollama availability and host sandbox settings can affect runtime.
- RLM can still miss leads, over-surface irrelevant associations, or produce
  misleading clusters; every artifact remains `needs_verification`.

## Validation Commands

Run the flow suite with a real local model:

```bash
python3 integrations/rlm/benchmark_flow_suite.py \
  --fixtures evals/rlm-flow-fixtures \
  --out /private/tmp/spotlight-rlm-flow-suite-real-gemma4-e4b.json \
  --no-rlm-chunk-budget 12
```
