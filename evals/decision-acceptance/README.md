# Decision-model acceptance test

Research triage only flags until this test passes for the question bank's
version (`RESEARCH_BANK_VERSION` in `integrations/decisions/research_questions.py`).
Until then no model answer sets a lead aside, and the on-trial questions C2
(new actor) and C3 (change or absence) are not asked. A passing held-out run
writes `integrations/decisions/research-acceptance.json`, which
`research_rules.ACCEPTANCE` reads: the chosen backend, the bank version, the
frozen C2/C3 thresholds and the questions cut. A record for another bank
version changes nothing.

## Files

| File | What it is |
|---|---|
| `fixture.schema.json` | The fixture format (below) |
| `company-a.json` | Synthetic Company A case with planted clues: the only test for C2 and C3 |
| `targets.json` | Pass targets and trial thresholds. `status: proposed` until the maintainer approves them |
| `acceptance.py` | The harness |
| `build_claims_fixture.py` | Builds the private fixture from fact-checked cases, writes the labelling sheet, merges two labellers' sheets |

## Fixture format

One fixture is one case: the brief (`case`: premise, approved directions, topic
briefs, timing window), its `items` and its `planted_clues`. Each item holds a
research lead (`schemas/research-lead.schema.json`, checked when loaded), a
`split` (`dev` or `heldout`) and `gold`:

- `keep`: `true` means setting the lead aside loses it (a **lost lead**, the
  costliest error); `false` means setting it aside is right; `null` is not scored.
- `answers`: the gold option per question (`q1_identity` ... `c1_premise`;
  `c2_new_actor` and `c3_change` as true/false). A question left out is not
  scored on that item, and a clue from it there is not counted as false.
- `failure` (fact-checked claims that were not verified): `class`, one of
  `weak_source_chain`, `summary_wider`, `inference_as_fact`,
  `conflicting_values`, `wrong_subject`, `weak_relevance`, `allegation`, and
  `research_visible`: whether research could have seen it before Investigation.
- `acceptable_clues`: clue groups that are defensible on that lead though not planted.

A planted clue names its group (`watchlist`, `premise`, `conflict`,
`new_actor`, `change_or_absence`, `timing`, `shared_identifier`, the Research
page's groups), its leads (all in one half) and the rule or question meant to
find it.

### How a fact-checked claim becomes a lead

| Lead field | From |
|---|---|
| `summary` | the claim text |
| `excerpt.text` | the longest quoted passage (20+ characters) in the first supporting evidence with a web source; else that evidence's description, with `direct_quote: false` |
| `capture.source_url` | that evidence's http(s) URL, else the claim's first http(s) source; none: a manual capture |
| `direction_id` | the claim's finding (`F3-C1` becomes `f3`), labelled with the finding's first claim |
| brief premise | the case methodology's `lead`, else the fact-check's project |
| `gold.keep` | `true` for `verified`; otherwise not scored, and the item gets a `failure` left for the labellers |

Items alternate between halves by a salted hash, separately for verified and
not-verified claims. Case names are replaced by a hash alias.

## Private fixture

Fact-checked claims are case material, so their fixture never enters this
repository. Build it into a directory outside the checkout (the builder
refuses a path inside it):

```bash
python3 evals/decision-acceptance/build_claims_fixture.py build \
  --case NAME=path/to/fact-check.json:path/to/methodology.json ... --out /private/dir
export SPOTLIGHT_ACCEPTANCE_FIXTURES=/private/dir/case-xxxxxx.json:/private/dir/case-yyyyyy.json
```

`labelling-sheet.csv` lists every item with its summary, excerpt, source,
verdict and fact-check notes, and blank columns: `failure_class` and
`research_visible` (failed claims), and the gold options for Q1, Q3-Q6 and C1.
Two people fill copies independently, without seeing any model output. Then:

```bash
python3 evals/decision-acceptance/build_claims_fixture.py merge \
  --fixture /private/dir/case-xxxxxx.json ... --a a.csv --b b.csv [--adjudicated adj.csv]
```

Agreed labels go into the fixture; disagreements are listed in
`disagreements.csv` and stay empty until a row in the adjudicated sheet settles them.

## Running

```bash
python3 evals/decision-acceptance/acceptance.py --split dev \
  --contender rules --contender jev --openrouter-key-file KEYFILE \
  --out results.json --private-detail /private/dir/detail
```

Contenders: `rules` (no model), `jev` (Jev 1.13 on OpenRouter's Decisions API,
zero data retention), `clef` and `clef-flash` (Workers AI;
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_AUTH_TOKEN`), `local` (the local tune
behind llama-server, `--local-url`). Each runs the shipped path:
`research-merge.py`'s clue rules and ranking and `research-triage.py`'s
`run_triage`, in full mode with the trial thresholds, so the run shows what
each model would set aside once allowed to.

Reported per contender: lost leads (by fixture), research-visible failures
caught (by class; before labelling, an unlabelled proxy: the share of failed
and of verified claims that get any substantive flag), clue recall and false
clues per 100 leads, per-question agreement with gold, both-order agreement and
calibration (ECE; Brier and a threshold sweep for C2/C3), what each question
adds over the rules, and cost, latency and egress (bytes and hosts).

The held-out half runs only after `targets.json` is approved for the current
bank version; the results record the targets file's sha256. Add
`--write-acceptance` to a held-out run to write the acceptance record when a
contender meets every target.
