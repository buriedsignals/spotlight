# Decision checks (OpenRouter Decisions API)

Optional. Uses the member's OpenRouter key, `OPENROUTER_API_KEY`, read from the
environment or from the file named by `integrations.decisions.env_file` in
`.spotlight-config.json` (for example `~/buried_signals/investigations/.env`).
`scripts/spotlight-python scripts/decision-signals.py CASE_DIR --phase investigation-gate --check` reports whether a run
would happen, without any request. The default model is `typesafe/jev-1.13` (TypeSafe's
decision model served by OpenRouter); change it with `integrations.decisions.model` only
after re-running the evaluation, because thresholds are measured against that model.

Another server that speaks the same Decisions API, such as a newsroom's own
inference, can answer instead. Engine names it in the install config's
`integrations.decisions.endpoint`, as `{"url", "credential_env", "api"}`.
Spotlight reads it only from the install config (`$SPOTLIGHT_CONFIG`, else the
checkout's `.spotlight-config.json`), never from `--config`, case files or
agent arguments. The URL must be https, or plain http on loopback. The
credential is the one named in `credential_env`, never `OPENROUTER_API_KEY`,
and the zero-retention provider body is sent only to openrouter.ai. `api`
must be `openrouter-decisions`; a server with another API needs an adapter in
`client.py`.

In Ninja mode the decision model is Kev 4B on this computer, served by its own
`kev serve` on a literal loopback address (`127.0.0.1` or `::1`, never the
name `localhost`). Its endpoint names no `credential_env`: such an endpoint
takes no key, never goes through a proxy, sensitive mode allows it (nothing leaves
the computer), and research triage uses it as the local model. Frontier mode
leaves the endpoint out and uses Jev on OpenRouter.

## What it does

`scripts/decision-signals.py` asks the decision model narrow, typed questions and
writes `data/decision-signals.json` (Investigation gate and report) or
`data/decision-signals-ingest.json` (ingest, kept separate so ingest never
invalidates a finalized report). Deterministic code turns the answers into signals:

| Phase | Check | Effect |
|---|---|---|
| Investigation gate prep (`--phase investigation-gate`) | Grounding of each finding against its located source excerpt | Flags with reasons (partial support, contradiction, announced-not-current, incomplete list, address used as legal domicile, allegation stated as fact, reversed payer or owner, role presented as ownership, proposed or approved presented as paid, amount or date mismatch, scope or causation added); a confidence cap; claim facets |
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
- Every case input that can reach a request is read through descriptors pinned
  to the case root, never following symlinks. Platforms without that support
  (Windows) report the integration as `unavailable`; it never runs there.

## Research triage

`scripts/research-triage.py CASE_DIR` asks the research questions
(`research_questions.py`, versioned on its own, outside `questions.ALL`) about
each merged lead in `data/leads.json` and writes `data/research-triage.json`.
Rules S1, S2 and N1 and the questions' effects are in `research_rules.py`.
No answer sets a lead aside: answers label, flag or raise clues. Whether the
stored source holds a lead's excerpt is D3's check (`research_checks.py`),
which marks a failing lead unverifiable.

- **Backend** (install config only): `integrations.decisions.research_triage`
  = `{"backend": "jev" | "clef" | "clef-flash" | "local" | "rules",
  "local_url": "http://127.0.0.1:PORT"}`. Clef reads `CLOUDFLARE_ACCOUNT_ID`
  and `CLOUDFLARE_AUTH_TOKEN`; the local tune is llama-server on loopback.
  Cloud backends also need `integrations.decisions.enabled`.
- **Routing:** journalist-input leads and sensitive cases are triaged only by
  the local tune or by rules. In newsroom mode cloud backends are off unless
  the decision endpoint is the newsroom's own. An unavailable backend falls
  back to rules, and the output says why.
- **Flag-only until the acceptance test passes:** C2 and C3 are not asked and
  no probability band is set. The output
  carries a notice the Research page shows.
- A result that breaks the triage invariants (D7) is never written.
