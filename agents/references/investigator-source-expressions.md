### Source Expressions (opt-in pilot and activated cases)

This contract applies only when `SOURCE_EXPRESSION_MODE` is explicitly
`pilot` or `activated`. An absent mode remains a legacy `1.0` run: do not create
the expression artifact and do not change the default findings version.

The agent that acquires a source owns expression creation for that source. For
each exact passage used to support, contradict, or contextualize a finding:

1. Preserve the exact selected text in its original language. Do not translate,
   normalize, summarize, or silently fix spelling inside that expression.
2. Store the passage in a case-local text artifact and record an exact line
   range or JSON Pointer in `anchor_ref`. The selected range must reproduce
   `text` exactly.
3. Link the expression to every applicable finding with an explicit
   `supports`, `contradicts`, or `context` relation. Reuse one expression across
   findings when the same passage genuinely bears on each; use multiple
   expressions when a finding depends on multiple passages.
4. Record `created_by: investigator` and the acquisition cycle. Preserve
   expressions created by another actor; never rewrite their immutable passage
   core or claim authorship for them.
5. For audio, video, images, or scanned documents, keep the original artifact
   and its hash on the evidence-bundle item. Store the transcript, caption, OCR,
   or text extraction as a separate `text_derivatives[]` item with its own path,
   hash, language, and human-verification state. Anchor the expression to that
   derivative while retaining the original evidence-bundle ID and original
   artifact hash.
6. Store a translation only as a separate expression with
   `derivative_type: translation`, `derived_from_expression_id` pointing to the
   original-language expression, and `direct_quote: false`. A translation never
   replaces the original expression.

Never write `{CASE_DIR}/data/source-expressions.json` with file tools. Every
passage goes through the one writer, which merges by expression id, computes
every hash and fingerprint, validates, and publishes atomically under the case
lock:

```text
scripts/spotlight-python scripts/write-source-expressions.py {CASE_DIR} --actor investigator --cycle N < proposal.json
```

The proposal lists each passage's `id`, exact `text`, `anchor_ref`,
`original_evidence_bundle_id`, `language` (required: the passage's BCP 47 tag), `direct_quote`,
`attribution`, `derived_from_expression_id` and `derivative_type`, and its
`finding_links` (`finding_id` and `relation`). Supersede or withdraw a passage
with a `lifecycle` entry. Do not supply hashes or fingerprints; a refused
write changes nothing and says why.

Source positions go in the same proposal, under `positions`: one for every
finding attributed to someone ("the ministry said", "according to"), and one
for every materially different actor position, asserting or disputing it. Each
gives an `id`, the `finding_id`, `actor` as `{"label": "Ministry of Health"}`,
`stance` (`asserts` or `disputes`), the `source_expression_ids` of active
passages linked to that finding, and `attribution_verbatim`, the attribution
exactly as one of those passages words it, so cite the passage that carries
it. When you supersede a cited passage, re-propose the position with the
successor id in the same write; withdraw a position that no longer stands
with `withdraw_positions` (`id`, `reason`). Never write `{CASE_DIR}/data/source-positions.json` with
file tools.

After writing or merging `findings.json` in `activated` mode, set the finding
fingerprints with
`scripts/spotlight-python scripts/write-source-expressions.py {CASE_DIR} --fingerprint-findings`
(never compute them yourself). In `pilot` mode the passages are an
experimental side artifact and does not activate or upgrade a legacy case. In
`activated` mode, preserve the existing `1.1` contract; the orchestrator or
migration workflow owns the activation receipt.

Expression capture is deliberate, not automatic extraction. Select only a
passage you inspected in the stored artifact. Never invent text, a locator, a
hash, an attribution, or a derivative to make validation pass. If the source is
unavailable, a binary source has no inspectable text derivative, or the exact
passage cannot be recovered, emit no expression for it and record the gap and
confidence effect. File presence, snippets, model recall, and RLM output are not
source expressions.

