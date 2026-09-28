---
title: "feat: Jev decision signals for confidence and categories in report drafting and ingest"
type: feat
status: proposed
date: 2026-09-28
---

# feat: Jev decision signals for confidence and categories

## Status — implemented 2026-09-28 (uncommitted working copy)

Built:

- `integrations/decisions/` (client, frozen question bank, rules, manifest)
- `scripts/decision-signals.py`, `scripts/decision_signals_lib.py`, `scripts/check-report-fidelity.py` (a new finalizer stage) and `scripts/ingest-eligibility.py`
- `schemas/decision-signals.schema.json`
- renderer and validator wiring
- skill updates for methodology opt-in, Gate 1, report drafting, ingest and integrations
- `tests/decision-signals-check.py`

`tests/smoke.sh` (87 checks) and `tests/eval.sh` (68) pass.

**Evaluation.** Agent-written datasets were split 50/50 before any calls. Rules were tuned on the dev half and frozen before the test half. Everything lives in `tools/decision-model-evidence/jev-2026-09-28/v2-eval/`.

| Check (held-out half) | Result |
|---|---|
| Grounding, money/control, EN/FR/DE/IT/ES (96) | 49/51 caught, 4/30 false alarms (2/30 after a post-hoc beneficial-owner threshold that the dev half supports); 15 routed to the existing process (13 need arithmetic) |
| Report prose (46) | 22/23 overstatements caught, 0/23 false alarms |
| Proposition fidelity (35) | 18/18 bad blocked, 1/17 good blocked; 7/7 cross-language passed |
| Claim–event relation (22) / matching (23) / entity type (24) | 22/22 / 21/23 / 23/24 |
| Contradiction category (17) | 15/17; 0/6 false contradictions |
| Re-run stability (142 fresh calls) | 1/96 grounding and 1/46 report flag flips |

Facets are measured on the held-out half:

- temporal status 90%
- source assertion 88%
- claim type 77%, recorded as a suggested tag only

The following are deliberately left to the existing process:

- arithmetic;
- staleness of "current" claims, which needs source dates;
- quotes not found in stored sources.

Everything below is the original plan. Where the build differs, the build governs.

## Summary

Add TypeSafe's Jev, called through the OpenRouter Decisions API, as an **independent semantic check** in Spotlight. Jev classifies how well cited evidence supports each finding, whether model-written report prose overstates the approved findings, and how ingested records relate to their source findings. Deterministic code turns those classifications into confidence caps, review flags and ingest eligibility.

Jev must **not assign confidence**. Spotlight's confidence is evidential: it depends on the source role, corroboration and contradiction search. Jev's `confidence` value only describes how concentrated its probability distribution is. Jev may therefore only **lower** a confidence level or raise a flag. It may never raise confidence, choose a verdict, or count as corroboration.

Why this can matter: no code in Spotlight today checks that evidence supports a claim or that prose stays within the findings. Most confidence caps exist only in prose. In an exploratory probe, Jev flagged all 12 real misgroundings hidden among the local cases' "direct/high/verified" findings, and all 20 seeded errors. The same probe also shows why it cannot be switched on as a gate yet: it downgraded 18 of 30 correct findings when given only the finding's short quote. Enforcement waits on a human-labelled evaluation.

Rollout: `off` → `shadow` → `advisory` → `enforce`, per check, gated on measured recall and false-alarm rates. Two conditions hold at every stage:

- The deterministic renderer stays offline and byte-deterministic.
- Sensitive and sovereign cases never call Jev. The sovereign path gets a provider seam for a future open model; that model is out of scope here.

---

## Verified facts about Jev on OpenRouter (2026-09-28)

| Fact | Evidence |
|---|---|
| Jev is on OpenRouter as a **Decisions API**, separate from chat: `POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13` (alias `~typesafe/jev-latest`). No waitlist and no TypeSafe account needed. | [Jev guide](https://openrouter.ai/docs/guides/community/jev); live calls with Tom's key returned `model: typesafe/jev-1.13-20260917`, `provider: TypeSafe` |
| `typesafe/jev-router` is a **different product**. It forwards chat prompts to other LLMs (DeepSeek, GPT, Gemini…) and is not the decision model. | [Jev Router page](https://openrouter.ai/typesafe/jev-router) |
| Three primitives. `choice` returns an option, per-option probabilities and a `confidence`. `noul` returns P(yes) and has no confidence. `score` returns an ordinal position, probabilities and a confidence. Score accepts at most 10 levels. Jev returns no text or explanations. Questions in one request are evaluated independently and in parallel. | [Jev tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial); [Score docs](https://docs.typesafe.ai/primitives/score) |
| 32,000-token context covering the state and the questions. Text only. $0.042 per million input tokens; output is free. Every response carries `usage.cost`. | [Jev 1.13 model page](https://openrouter.ai/typesafe/jev-1.13); endpoints API |
| `confidence` is a statistic of the distribution's **shape**, not the probability that the answer is correct. One OpenRouter cookbook says otherwise; TypeSafe's own documentation is treated as authoritative. | [TypeSafe confidence](https://docs.typesafe.ai/confidence) vs [cascade cookbook](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-verified-cascade) |
| Zero data retention can be enforced. `provider: {zdr: true}` is accepted. OpenRouter enforces provider filters on this endpoint: `ignore:["typesafe"]` and `only:["openai"]` both return 404. The TypeSafe endpoint appears in OpenRouter's ZDR endpoint list. | Live tests; `GET /api/v1/endpoints/zdr` |
| TypeSafe's privacy policy commits to not training on Input. Retention is "as long as reasonably necessary". The ZDR route above is the stronger guarantee. The company is US-based. | [TypeSafe privacy policy](https://typesafe.ai/legal/privacy-policy) |
| The endpoint path is `/api/alpha/`, so its shape may change. There is a single provider and the weights are closed. | Decisions API reference |

My 2026-09-16 notes ("not on OpenRouter, waitlist") are superseded.

## Probe results (exploratory; not a production estimate)

Evidence and a reproducible scorer are in `tools/decision-model-evidence/jev-2026-09-28/`.

| Measure | Result |
|---|---|
| Real misgroundings flagged (not `direct`) | **12/12**. Examples: "volunteer council" claimed from evidence that says "board of directors"; a mailing address used as legal domicile; Signal Messenger LLC's address cited for the Foundation; a date absent from the quote. |
| Seeded errors flagged (wrong number, role, legal form, date, entity) | **20/20** |
| False alarms on findings labelled `direct` | **18/30**. Almost all are `partial` because the quote alone lacks the entity name or legal context (for example "seat \| Zug"). |
| Same answer with option order reversed | 59/62 support; 14/14 prose fidelity |
| Report prose, faithful vs overstated | **13/14**. The one "miss" adds an actor ("the board") that is not in the finding, so it is arguably correct. |
| A single "establishes all elements?" noul question | Poor separation: labelled-direct items ranged from 0.07 to 0.95. Do not use one overall noul as a gate. |
| Latency / cost | p50 336 ms, max 670 ms; $0.00262 for 82 calls (about $0.00003 each) |

**Limits of this evidence.**

- The labels are mine, not human gold.
- The sample is 62 grounding items. The 95% lower bound on real-misgrounding recall is only about 0.76 (12/12).
- Seeded mutations are easier than real errors.
- Beyond three ad-hoc French/German checks, all items were English.
- Evidence was quote-only. The follow-up with source identity in the state was not run, because sending local research-file headings to the API was blocked.

**Run 2 (same day), adding source URL, heading and an excerpt around the quote, with revised wording.**

- With action only when p ≥ 0.6, it caught 8 of 11 real problems and 18 of 20 seeded errors, with **0 of 31 false alarms**. The other 2 seeded errors were flagged but below the threshold.
- Adding a question on whether the situation is current rather than announced or planned raised this to 9 of 11. That rule was chosen after seeing the results, so it needs confirming on fresh data.
- Jev also caught a stale finding my first labels missed. The case's own stored article says both co-directors stepped down, yet the finding says they are "current" and was marked verified/high.
- The two remaining misses are strict labelling-policy calls. One treats an address as legal domicile; the other lists only part of the leadership.
- **Context is the fix for false alarms, so R1 must always include source identity and a located excerpt.**
- A deterministic by-product: 13 of 53 quotes appear only in a case file other than the finding's listed source, 1 appears nowhere, and 1 source path is broken. These are cheap checks to add to U0.

**Run 3 (same day), targeted questions, a frozen rule and a fresh held-out set.**

- The rule flags a finding when any of these hold:
  - the support choice is not `direct` with p ≥ 0.6;
  - the mean P(direct) across both option orders is below 0.1;
  - "announced rather than current" is below 0.3;
  - "complete list" is below 0.2;
  - a three-way legal-basis choice returns `address_only` in both orders with p ≥ 0.6, and the claim uses legal-domicile wording.
- On the tuning set it caught 11 of 11 real problems and 20 of 20 seeded errors, with 1 of 31 false alarms (an arguable label).
- On **40 fresh items written before tuning, it caught 19 of 20 errors with 0 of 20 false alarms**. The miss was a counting error: "five members" where the source lists four.
- This is the starting question set for R1. Treat the numbers as encouraging but small-sample.

Cost is not the argument for Jev. The argument is an independent, typed, cheap check where Spotlight currently has none.

---

## Problem frame: how confidence and categories are set today

This comes from a read-only map of the current checkout.

**Confidence.**

- The values are `high | medium | low`; findings also allow `disputed`.
- The investigator LLM writes `confidence` and `grounding.confidence_cap`. The fact-checker LLM writes `claims[].confidence` and `grounding_assessment.confidence_cap`. The report model may not write confidence at all (`report-draft.schema.json`, `additionalProperties:false`).
- **Only four rules are enforced in code**, all in `scripts/render-report.py:130-168` (`aggregate_verdict`):
  - take the minimum of the finding and fact-check confidences and caps;
  - `partially_verified` → at most medium;
  - other non-verified statuses → low;
  - a missing fact-check confidence counts as low.
- The rest of the cap table (`skills/epistemic-grounding/SKILL.md:83-99`) and the grounding ladder are **prose only**.

**Deterministic defects found along the way.** None of these need a model:

- `validate-case.py:7` claims to check "confidence-vs-cap mismatches" but does not. `tests/fixtures/findings.sample.json` F2 is `high` with cap `medium` and passes.
- `support_type` and the cap are never checked against each other. `inferred` or `contradicted` can render as High (for example the local `newsatom-information-units` F4).
- Nothing requires a caveat for a finding that is not verified.
- `grounding` is optional in the schema, although the skill says it is mandatory.

**Categories.**

- Verdicts (6 values), `support_type` (5), `source_role` (3) and `access_method` (5) are validated as enums only.
- The grounding ladder and the contradiction categories exist only in prose.
- Report `finding_order` and `framing_finding_ids` are model-chosen and checked only for coverage.

**Semantic checks.** None anywhere. This is stated in `validate-fact-check.py:4`, `validate-report-draft.py:4-7` and `report-drafting/SKILL.md:111`.

**Ingest.**

- The default legacy path is the agent writing Markdown by hand. That covers the eligibility gate, entity `type` (`person|organization|company|place`, from a pattern table), free-text tags and categories, and cross-project "same claim" decisions, none of them with guards.
- The reviewed graph path (`knowledge-batch.json` → SQLite → signed projection) has **no confidence, verdict, type or category fields**. It never compares a record's `proposition` to its `finding.claim`: the committed sample approves "Acme paid Doe." as `supports` for a river-discharge event.
- On this machine, no vault or graph database exists yet.
- Cross-case claim lookup is blocked by design. `query_vault.claim_authorized` restricts results to the current case.

**Local data.** `cases/` is gitignored. It holds 57 findings, almost all `direct/high/verified`, with no `report-draft.json`. It can measure false alarms, but it cannot supply realistic negatives.

---

## Boundaries (non-negotiable)

1. **Ratchet down only.** A Jev signal can lower a displayed or ingested confidence, add a caveat requirement, block overstated prose, or route a record to `lead`. It never raises confidence, never sets a verdict, and never counts as corroboration or as a source.
2. **Two kinds of confidence, kept apart.** Jev's distribution confidence is stored alongside the evidential confidence, never mixed into it. Every cap names its rule, for example `jev_grounding_partial`.
3. **Independence.** Jev sees the claim, the evidence passage(s) and the source identity. It does not see the investigator's rationale, confidence or support type, mirroring the fact-checker's independence.
4. **Raw answers persisted.** Full probability distributions, the question-bank version and the served model snapshot are stored, so thresholds can be retuned without new calls.
5. **Offline determinism.** Jev calls happen in a separate script before finalization. The renderer reads the stored signals as an input, and hashes that input into `evidence-map.json`.
6. **Egress policy.**
   - Every call uses `provider: {zdr: true, allow_fallbacks: false}`; the client refuses to send without it.
   - Jev is hard-off under `sensitive: true`, in the sovereign configuration, and for cases not opted in.
   - An outage produces `unavailable`, never an inferred answer.
7. **Stable before actionable.** A signal is actionable only when both option orders agree and the chosen option's probability is at or above that check's threshold. Otherwise it is `uncertain`, which is a flag only.

---

## Architecture

```
findings.json ─┐                                   ┌─> render-report.py (offline) ─> report.html
fact-check.json├─> scripts/decision-signals.py ─> data/decision-signals.json ─┤   findings-report.md, evidence-map.json
report-draft ──┤      (only networked step)                                   └─> ingest gate / knowledge_destination stage
knowledge-batch┘      DecisionProvider: jev-openrouter | (future) local open model
```

- **Provider seam:** `integrations/decisions/`. It defines a `DecisionProvider` interface, `decide(state, questions) -> answers`, whose first implementation is `jev_openrouter.py`, a stdlib `urllib` client following `integrations/rlm/run_rlm.py`. It has a fake transport for tests. A local open model (Shisa DE1, Ekos or similar) can be added later behind the same interface and evaluated on the same set.
- **Integration manifest and preflight:** `integrations/decisions/manifest.json` follows the `_preflight_base.py` pattern.
  - Statuses: `green | unconfigured | red | dismissed`.
  - Activation env var: `SPOTLIGHT_JEV_API_KEY`, loaded from the gitignored `.env`.
  - The `risk_note` says: "classification signal, never evidence".
- **Config:** `.spotlight-config.json` → `integrations.decisions`:
  - `enabled`, `provider`, `model` (pinned `typesafe/jev-1.13`);
  - per-check `mode` (`off|shadow|advisory|enforce`);
  - per-check thresholds.
  - Phase 0 records the preflight result, and Phase 2 records the case opt-in.
- **Question bank:** `integrations/decisions/questions.py` holds frozen instructions and criteria text. Each question has an id and a version (the sha256 of its canonical JSON), and each is asked in both option orders in one request.
- **Artifact:** `data/decision-signals.json`, with a new `schemas/decision-signals.schema.json`. Each entry records:
  - subject (`finding:F2`, `treatment:F2.headline`, `batch:claim:<id>@<v>`);
  - question id and version;
  - input sha256;
  - served model snapshot, request id and timestamp;
  - both answers (choice, probabilities, confidence);
  - derived state (`actionable | uncertain | unavailable | stale`);
  - an `overrides[]` list, each with reviewer and reason.
  - Stale detection: if the input hash no longer matches the current finding or prose, the signal is `stale`.

---

## Report drafting integration (Phase 5)

**Recommendation:** compute the grounding signals (R1) once, after the final fact-check cycle, while preparing Gate 1. Phases 5 and 6 then reuse them. At report time a downgrade would surprise an editor who has already approved the findings. Showing it at Gate 1 lets the editor decide with it in view. This extends one step beyond "report drafting", so it is listed under Decisions.

### R1. Evidence grounding category per finding

- **State:**
  - the claim;
  - the evidence passages: active source-expression text for 1.1 cases; otherwise the finding's `evidence` plus a bounded excerpt around it from the local source file;
  - source identity: URL, document heading, publisher and date.
- **Questions:**
  - Choice `direct | partial | insufficient | contradicted`, asked in both option orders. The criteria text is taken from the probe.
  - Candidate addition, pending evaluation: one noul per material claim element (actor, role or action, date, amount, place, legal status), following TypeSafe's decomposition guidance, because the single overall noul was weak.
- **Mapping to Spotlight categories and caps** (deterministic, in `render-report.py:aggregate_verdict` as an extra `cap_confidence` input):

| Jev (actionable) | Maps to | Cap | Flag |
|---|---|---|---|
| `direct` | `direct` | none | — |
| `partial` | `indirect` / `inferred` (ladder level 3) | medium | `jev_grounding_partial` |
| `insufficient` | `insufficient` (source-adjacent lead) | low | `jev_grounding_insufficient` |
| `contradicted` | `contradicted` | low | `jev_grounding_contradicted`: routes to fact-checker re-review; never sets `false` or `disputed` automatically |
| `uncertain` / `unavailable` | — | none | shown as "semantic check inconclusive / unavailable" |

- **Display:**
  - `findings-report.md` and `evidence-map.json` (editor and audit audiences) show the Jev category, probability, rule applied and model snapshot.
  - `report.html` shows only the resulting confidence, which is already rendered. Whether a public "independent check" line appears is an editorial choice.

### R2. Prose fidelity (overstatement)

- **Subjects:** `deck` (against `framing_finding_ids`), and each `headline`, `summary` and `why_it_matters` against its finding's claim, verdict and displayed confidence.
- **Question:** Choice `faithful | overstated | unrelated`, in both orders.
- **Where it runs:** a new `report_fidelity` stage in `finalize-report.py`, between `report_draft` and `render`. It reads precomputed signals only and makes no network call.
  - `advisory`: lists the flagged fields to the drafting model and the editor.
  - `enforce`: fails with a named-field `report_fidelity` error. The model revises only `data/report-draft.json`, following the existing failure path (`report-drafting/SKILL.md:104-106`). Signals are recomputed only for changed fields, which are keyed by hash.
  - After two rounds, the editor either accepts the draft (an override recorded in `decision-signals.json`) or rewrites it.

### R3. Caveat coverage (deterministic, no model)

`validate-report-draft.py` requires every finding whose displayed status is not `verified` to be referenced by at least one caveat. This is plain code; no Jev is needed.

## Ingest integration (Phase 6)

- **I1. Eligibility and layer.** A new `scripts/ingest-eligibility.py` reuses the R1 signals and emits the list the agent must follow:
  - actionable `insufficient` or `contradicted` → not a durable claim;
  - `partial` → `layer: lead`, `needs_verification: true`.
  - The ingested confidence is `min(existing, jev cap)`, with the rule name written into the claim note's frontmatter.
  - `ingest-source-expressions.py` re-checks the same list, as it already does for other eligibility rules.
- **I2. Proposition fidelity (graph path).** Before `knowledge_destination.py stage`, `decision-signals.py --phase ingest` compares each batch claim's `proposition` with its origin `finding.claim` (Choice `equivalent | narrower | broader_or_changed | different`).
  - The results go into the review manifest as `semantic_flags`, so the journalist's SSH signature covers them.
  - Only `equivalent` and `narrower` pass in `enforce` mode.
  - Model output is recorded without a database migration, using the existing `provenance.method: agent_candidate`, `status: candidate` and `provenance.model`.
- **I3. Membership relation check.** A Choice over `supports | contradicts | contextualizes | mentions` for each claim→event membership. Disagreement with the author's relation becomes a manifest flag. This check stays flag-only, because relations are editorial.
- **I4. Entity type (legacy path).** A Choice over `person | organization | company | place | unclear`, with a context sentence. Below the threshold, the agent marks the type `unclear` and asks. This is low stakes and optional.
- **I5. Cross-case "same claim / contradicts existing claim" (deferred).** Three things block it:
  - there is no claim-to-claim relation type;
  - `query_vault` isolates cases by design;
  - no vault exists locally.
  It needs its own design and a policy decision on cross-case reads before any Jev work.

---

## Evaluation and promotion gate

- **Labelled set.** Store it in `evals/fixtures/{train,selection,test}/decision-signals/` with a new comparing grader, `evals/graders/decision-signals.py`. The current graders only check that fields are present. Target 150–200 grounding items and 60 or more prose items.
  - Include hard negatives: proposed vs approved, negation and qualification, wrong entity/date/amount, an address used as legal status, stale current-tense claims, copies of one original, OCR noise.
  - Include French and German.
  - **Tom, or an editor, adjudicates the labels.** The probe labels are a starting draft only.
- **Metrics per check and language:**
  - recall on non-direct/overstated items (the protected category, using the existing `false_high_confidence` grader category);
  - false-alarm rate on correct items;
  - order stability;
  - `uncertain`/`unavailable` rate;
  - cost and latency.
- **Thresholds** are fitted on `selection` only and reported on the held-out `test` split. Freeze the question versions before the test run.
- **Proposed promotion criteria** (Tom decides the final numbers):
  - `shadow → advisory`: recall ≥ 0.9 with a false-alarm rate that editors will tolerate in review. The probe's 0.6 on quote-only evidence would not pass.
  - `advisory → enforce`: recall ≥ 0.95 on the protected set and false alarms ≤ 0.10, holding on both the test split and at least 5 real shadow-mode cases.
  - Because enforcement only lowers confidence, false alarms cost trust and editor time, not wrong publications.

---

## Implementation units

| Unit | Scope | Key files | Verification |
|---|---|---|---|
| U0 | Deterministic confidence fixes, no Jev: confidence ≤ own cap; `support_type` ↔ cap consistency (`inferred`/`contradicted`/`insufficient` never High); caveat coverage (R3); correct the `validate-case.py` docstring; tests for the `aggregate_verdict` rules | `scripts/validate-case.py`, `scripts/validate-report-draft.py`, `scripts/render-report.py`, `tests/validate-case-check.py`, `tests/render-report-check.py` | New tests fail before the change and pass after; `tests/smoke.sh`, `tests/eval.sh` |
| U1 | Decision provider seam, Jev client (ZDR pinned, fake transport), manifest, preflight, config keys, sensitive/sovereign/opt-in hard-off | `integrations/decisions/*`, `integrations/preflight.py`, `skills/phase-preflight/SKILL.md`, `skills/integrations/SKILL.md` | Unit tests with fake transport (no network in CI); a request without `zdr:true` is rejected; sensitive mode yields `dismissed`; one live smoke run by Tom |
| U2 | Question bank, `decision-signals` schema, `scripts/decision-signals.py` (`--phase gate1\|report\|ingest`), stale detection, `shadow` mode | `schemas/decision-signals.schema.json`, `scripts/decision-signals.py`, `tests/decision-signals-check.py` | Schema validation; re-run idempotence (same inputs → no new calls); a change to a finding produces `stale` |
| U3 | Labelled set, comparing grader, calibration report (thresholds from `selection`, results on `test`) | `evals/fixtures/*/decision-signals/`, `evals/graders/decision-signals.py`, `evals/README.md` | Grader self-test; calibration report committed; Tom signs off the labels |
| U4 | R1 consumption: extra cap input in `aggregate_verdict`, editor/audit display, `evidence-map` hashing of signals | `scripts/render-report.py`, `scripts/validate-report.py`, `skills/report-drafting/SKILL.md` | Byte-determinism test with signals present and absent; each mapping row covered; High can never rise |
| U5 | R2 `report_fidelity` stage, revise loop, override record | `scripts/finalize-report.py`, `scripts/spotlight_orchestration/transitions.py`, `skills/phase-report/SKILL.md` | Fixture draft with an overstated headline fails in `enforce` and passes in `advisory` with a flag; override path tested |
| U6 | Gate 1 surfacing of R1 flags (if Tom agrees) | `skills/phase-gate1/SKILL.md`, `skills/editorial-review/SKILL.md` | Review artifact shows the flags; Gate 1 summary lists capped findings |
| U7 | Ingest I1–I4 | `scripts/ingest-eligibility.py`, `scripts/knowledge_destination.py` (manifest `semantic_flags`), `skills/ingest/SKILL.md`, `skills/phase-ingest/SKILL.md` | `tests/ingest-check.py` covers the eligibility list; `knowledge-destination-check.py` covers flags inside the signed manifest; the sample batch's "Acme paid Doe" proposition is flagged |

Order: U0 and U1 can proceed now. U2, then U3, before any mode above `shadow`. U4, U5 and U7 ship in `advisory` first; `enforce` follows only after the gate is passed. I5 is a separate plan.

## Risks and open questions

- **Thin evidence produces false alarms.** Quote-only states produced most of the probe's errors. Jev works best with exact passages and source identity. This connects to the source-expression pilot, which is currently NOT APPROVED. Legacy cases need bounded excerpts, and the untested follow-up must run on consented data.
- **Vendor and API risk.** An alpha endpoint, a single provider, closed weights. Mitigations:
  - pin `typesafe/jev-1.13` and record the dated snapshot;
  - store raw answers;
  - keep the provider seam;
  - an outage yields `unavailable` and never blocks publication.
- **Egress.** Claims and evidence passages leave the machine for OpenRouter/TypeSafe (US) under ZDR routing. Unpublished investigative material deserves a per-case opt-in, not a global default. Is a case-level opt-in enough, or should Jev be limited to cases whose evidence is already public?
- **Anchoring and over-reliance.** Editors may treat "Jev: direct 0.95" as verification. Display wording must say "classification signal, not verification". This follows the existing RLM `risk_note` precedent.
- **Distribution shift.** The local cases are corporate-registry facts. Investigative claims (payments, intent, causation) are harder, so accuracy must be measured on them, not assumed.
- **Calibration.** Jev's `confidence` is not correctness. Thresholds come only from the labelled `selection` split.

## Decisions needed from Tom

1. Run R1 at Gate 1 preparation (recommended) or only at Phase 5?
2. May `enforce` mode block finalization on overstated prose, or only flag it?
3. Who adjudicates the evaluation labels, and what false-alarm rate editors will accept for `advisory`?
4. Egress scope: per-case opt-in for any case, or only cases whose cited evidence is already public?
5. Should U0 (the deterministic fixes) ship independently first? It is recommended: it closes part of the gap without any model.
