### Source expressions for independently acquired evidence (opt-in)

When `SOURCE_EXPRESSION_MODE` is `pilot` or `activated`, create expressions for
sources you independently acquire during fact-checking. Do not recreate or
silently alter an investigator-owned expression: the writer keeps every
existing passage core and author, and adds your separately acquired
expressions beside them.

- Preserve exact original-language text and an exact case-local line range or
  JSON Pointer. Assign `created_by: fact-checker` and the current cycle.
- Link corroborating passages with `supports`, disconfirming passages with
  `contradicts`, and material background with `context`. Put expression refs for
  contradiction evidence on the corresponding `evidence_against` item.
- Reuse an expression across findings only when the same passage actually bears
  on each finding. A claim depending on several passages keeps several refs.
- A passage needs an evidence-bundle item for its stored source. For a source
  you acquired yourself, append your own item to `evidence-bundle.json` with
  `created_by: fact-checker` (same fields as the investigator's items: path,
  `sha256`, `claim_links` with the claim copied verbatim, missing-source gate,
  `human_verification_required`) and anchor the passage to it. Never edit,
  remove or re-link an investigator item; its record is the investigator's.
  Validator failures in your own items come back to you.
- Keep a non-text original and its hash on that item; store its OCR,
  transcript, caption, or extraction as a separately hashed
  `text_derivatives[]` item and anchor the expression to that derivative.
- Keep translations separate from the original-language expression. A
  translation uses `derivative_type: translation`, points to the original with
  `derived_from_expression_id`, and is not a direct quote from the source.

Never auto-extract or invent a passage, locator, hash, derivative, attribution,
or source. Search snippets, inaccessible sources, model recall, and RLM output
cannot become expressions. If an exact stored anchor is unavailable, reject the
expression, record the gap, and keep the verdict within the applicable
confidence cap.

Never write `{CASE_DIR}/data/source-expressions.json` with file tools. Every
passage goes through the one writer, which merges by expression id, computes
every hash and fingerprint, validates, and publishes atomically under the case
lock:

```text
scripts/spotlight-python scripts/write-source-expressions.py {CASE_DIR} --actor fact-checker --cycle N < proposal.json
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
file tools. If your check finds an actor disputing a finding that no position records, add the disputing position.

In `pilot` mode, expressions are an experimental side artifact and do not
activate or upgrade a legacy case. In `activated` mode, preserve the existing
`1.1` contract. The orchestrator or migration workflow, not the fact-checker,
owns the activation receipt.

