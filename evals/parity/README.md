# Harness parity on the gold cases

`baseline.json` records what the Flue harness produced on eight gold cases
(July 2026): each brief, its findings count, the fact-checker's verdicts and
the number of investigation cycles. The case folders themselves stay out of the
repository; only these measures are tracked.

A new harness passes when, on the same briefs and the same models:

| Measure | Tolerance |
|---|---|
| Validators | `scripts/validate-case.py` passes on every run, against today's contract |
| Findings per case | between baseline − 1 (at least 1) and baseline + 2 |
| Findings in total | no more than 3 below the baseline total |
| Verdicts | at least 75% of all fact-checked claims `verified` |
| Cycles | no case uses more cycles than its baseline |

The tolerances were fixed before the first run and live in `compare.py`.
Changing them after seeing results defeats the comparison.

The recorded Flue outputs fail today's validators (fields added to the
evidence-bundle and investigation-log contracts since July), so the validator
row judges the new run only.

Run the new harness on each brief into `RUNS_ROOT/gold-<id>`, then:

```sh
python3 evals/parity/compare.py RUNS_ROOT
```
