# Decision checks (OpenRouter Decisions API)

Optional. Uses the member's OpenRouter key, `OPENROUTER_API_KEY`, read from the
environment or from the file named by `integrations.decisions.env_file` in
`.spotlight-config.json` (for example `~/buried_signals/investigations/.env`).
`python3 scripts/decision-signals.py CASE_DIR --phase gate1 --check` reports whether a run
would happen, without any request. The default model is `typesafe/jev-1.13` (TypeSafe's
decision model served by OpenRouter); change it with `integrations.decisions.model` only
after re-running the evaluation, because thresholds are measured against that model.

## What it does

`scripts/decision-signals.py` asks the decision model narrow, typed questions and
writes `data/decision-signals.json` (Gate 1 and report) or
`data/decision-signals-ingest.json` (ingest, kept separate so ingest never
invalidates a finalized report). Deterministic code turns the answers into signals:

| Phase | Check | Effect |
|---|---|---|
| Gate 1 prep (`--phase gate1`) | Grounding of each finding against its located source excerpt | Flags with reasons (partial support, contradiction, announced-not-current, incomplete list, address used as legal domicile, allegation stated as fact, reversed payer or owner, role presented as ownership, proposed or approved presented as paid, amount or date mismatch, scope or causation added); a confidence cap; claim facets |
| Report (`--phase report`) | Fidelity of each deck, headline, summary, why-it-matters and caveat against its findings and verdicts | Flags overstated prose with a type (certainty, causation, scope, actor, amount, allegation as fact, …) |
| Ingest (`--phase ingest`) | Proposition vs finding claim; claim-to-event relation; entity type; matching against existing claims | Blocks broadened propositions, flags relation disagreements, types entities or marks them `unclear` |

## Boundaries

- Signals only lower confidence, add review flags or route to the lead layer. They never raise
  confidence, set a verdict, or count as a source or as corroboration.
- Checks that the model cannot do reliably stay with the existing process and are marked
  `routed`: arithmetic (sums, counts, conversions), quotes that are not found in any stored
  source, missing source files, and anything above the 32k-token request budget.
- Sensitive mode, a case without opt-in, or a missing key means no request is made.
  An outage records `unavailable`; nothing is inferred.
- Every request carries `provider: {"zdr": true, "allow_fallbacks": false}`.
- A choice counts only when both option orders agree; disagreement is reported
  as an `inconclusive` flag, never as a clean result.
- Reviewer overrides are bound to the exact `input_sha256` they reviewed.
- Writes are locked and symlink-safe; an invalid signals file fails loudly.
