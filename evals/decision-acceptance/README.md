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
| `company-a.json` | Synthetic Company A case with planted clues (dev) |
| `company-b.json` | Synthetic Company B case, the same clue groups on another invented firm (held-out). With Company A, the only test for C2 and C3 |
| `targets.json` | Pass targets and trial thresholds. `status: proposed` until the maintainer approves them |
| `acceptance.py` | The harness |
| `build_claims_fixture.py` | Builds the private fixture from fact-checked cases, writes the labelling sheet, merges one approved sheet or two labellers' sheets |

## Fixture format

One fixture is one case: the brief (`case`: premise, approved directions, topic
briefs, timing window), its `items` and its `planted_clues`. Each item holds a
research lead (`schemas/research-lead.schema.json`, checked when loaded), a
`split` (`dev` or `heldout`) and `gold`. A whole case is in one half: every
item's `split` must be its case's, a salted hash of the case's `case_key`
(`case_split` in `build_claims_fixture.py`), or the fixture is refused. The
key is the source case's folder, written once at build time, so renaming a
case does not move it; a fixture without one is refused. A case
tuned on is never tested on. Company A hashes to dev and Company B to held-out,
so clue recall, C2 and C3 are measured in both halves on different cases.
A run is refused when the two halves share a stable identifier: an entity
(lead subject), a source URL, a registry id, phone, email, address, director or
agent.

- `keep`: `true` means setting the lead aside loses it (a **lost lead**, the
  costliest error; a lead kept as circumstantial is not lost and is counted
  apart); `false` means setting it aside is right; `null` is not scored.
- `answers`: the gold option per question (`q1_identity`, `q3_source` ...
  `c1_premise`; `c2_new_actor` and `c3_change` as true/false). A question left
  out is not scored on that item, and a clue from it there is not counted as
  false. There is no direction question, so no direction gold; Q1 is asked
  and scored only for the `jev` contender.
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

Case names are replaced by a hash alias; the `case_key` (the folder holding
`data/fact-check.json`, or the fact-check's own folder) puts the whole case in
one half. The halves are not balanced by construction: count the
cases, items and failures in each before trusting a result.

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
With one person, pass only `--a`: the approved sheet is applied and each item
records `label_source: single_labeller_approved`, which a second labeller can
replace later.

A fixture built before the whole-case split (items of one case in both halves)
is refused, as is one without a `case_key`. `resplit` copies it with every
item in its case's half, labels untouched, taking the key from the source
fact-check when the fixture has none. Inputs need distinct file names, and
existing output files are refused, so write to a new directory:

```bash
python3 evals/decision-acceptance/build_claims_fixture.py resplit \
  --fixture /private/dir/case-xxxxxx.json=path/to/case/data/fact-check.json ... --out /private/resplit
```

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
`run_triage`, in full mode with the trial thresholds. No answer sets a lead
aside, so lost leads stay 0 unless a change reintroduces a set-aside effect.

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
