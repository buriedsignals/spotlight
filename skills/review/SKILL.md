---
name: review
description: Generate a self-contained HTML review artifact at the end of an approved investigation cycle; accept structured feedback; re-trigger the investigator to process it. No server required.
version: "1.0"
invocable_by: [orchestrator, user]
requires: []
---

# Review — Post-Gate-1 Editorial Review Loop

The review skill closes the editorial feedback loop on a completed investigation. It produces a **single HTML file** that the journalist can open in any browser, inspect findings + verdicts, and submit structured feedback. When feedback is submitted, the orchestrator re-spawns the investigator to address it and regenerates the review artifact.

**No server required.** The HTML is fully self-contained (inline CSS + JS). Feedback is exported as a downloadable `review-feedback.json` file that the user drops into the case's `data/` directory.

---

## Two Modes

The skill operates in one of two modes based on case state.

### Mode A — `generate`

Triggered by the orchestrator at the end of Phase 4 (Gate 1 approved), OR invoked directly by the user.

Inputs:

- `{CASE_DIR}/data/findings.json`
- `{CASE_DIR}/data/fact-check.json`
- `{CASE_DIR}/data/source-expressions.json` (optional for legacy cases; required by activated cases)
- `{CASE_DIR}/data/summary.json` (optional)
- `{CASE_DIR}/data/provenance-manifest.json` (optional)
- `{CASE_DIR}/summary.md` (optional)

Outputs:

- `{CASE_DIR}/review.html` — self-contained review artifact

### Mode B — `process`

Triggered when `{CASE_DIR}/data/review-feedback.json` exists and has not yet been processed (no matching `data/review-feedback-processed.json` marker).

Inputs:

- `{CASE_DIR}/data/review-feedback.json`
- `{CASE_DIR}/data/findings.json` (current state)
- `{CASE_DIR}/data/fact-check.json` (current state)
- `{CASE_DIR}/data/source-expressions.json` (when present)

Outputs:

- Targeted investigator spawn prompt (constructed by the skill)
- Updated `{CASE_DIR}/data/findings.json` (after investigator runs)
- Updated `{CASE_DIR}/data/source-expressions.json` (after investigator runs, when expression feedback requires supersession or withdrawal)
- Updated `{CASE_DIR}/data/fact-check.json` (after fact-checker runs)
- Regenerated `{CASE_DIR}/review.html` (Mode A runs automatically after processing)
- `{CASE_DIR}/data/review-feedback-processed.json` — marker file recording when feedback was processed

---

## Mode A — `generate` Steps

### 1. Read case files

```
read-file("{CASE_DIR}/data/findings.json")
read-file("{CASE_DIR}/data/fact-check.json")
read-file("{CASE_DIR}/data/source-expressions.json")  # may not exist for legacy cases
read-file("{CASE_DIR}/data/summary.json")      # may not exist
read-file("{CASE_DIR}/data/provenance-manifest.json")  # may not exist
read-file("{CASE_DIR}/summary.md")              # may not exist
```

### 2. Read the HTML template

```
read-file("skills/review/references/template.html")
```

### 3. Build the injection payload

Assemble a single JSON object with the shape expected by the template (see `references/feedback-schema.md`):

```json
{
  "project": "<slug>",
  "generated_at": "<ISO 8601>",
  "summary": {
    "headline": "N verified findings across M cycles",
    "overview": "<2-3 paragraph synthesis from summary.md or derived>",
    "scope": "<what was investigated, what was out of scope>",
    "conclusions": ["..."],
    "limitations": ["..."],
    "confidence_assessment": "<margin narrative>"
  },
  "findings": [
    {
      "id": "F1",
      "claim": "...",
      "evidence": "...",
      "confidence": "high|medium|low",
      "confidence_rationale": "...",
      "grounding": {
        "support_type": "direct|indirect|inferred|contradicted|insufficient",
        "source_role": "primary|secondary|contextual",
        "claim_elements_supported": ["..."],
        "missing_assumptions": ["..."],
        "confidence_cap": "high|medium|low",
        "misgrounding_risk": "...",
        "grounding_rationale": "..."
      },
      "evidence_bundle_refs": ["E1"],
      "perspective": "...",
      "sources": [{"url": "...", "type": "...", "archive_url": "...", "access_method": "..."}],
      "verdict": {
        "verdict": "verified|unverified|disputed|false",
        "confidence": "high|medium|low",
        "grounding_assessment": {
          "support_type": "direct|indirect|inferred|contradicted|insufficient",
          "claim_elements_checked": ["..."],
          "missing_assumptions": ["..."],
          "confidence_cap": "high|medium|low",
          "assessment": "..."
        },
        "evidence_for": [{"description": "...", "source": "...", "source_type": "primary|secondary"}],
        "evidence_against": [{"description": "...", "source": "..."}],
        "notes": "..."
      }
    }
  ],
  "source_expressions": [
    {
      "id": "SX1",
      "text": "Exact source wording",
      "anchor_ref": {"path": "research/source.txt", "line_start": 4, "line_end": 4},
      "anchor_sha256": "<sha256>",
      "original_evidence_bundle_id": "E1",
      "expression_fingerprint": "<sha256>",
      "language": "en",
      "attribution": "Printed attribution",
      "finding_links": [{"finding_id": "F1", "relation": "supports", "link_fingerprint": "<sha256>"}],
      "lifecycle_events": [{"event": "activated", "timestamp": "<ISO 8601>", "actor": "investigator", "reason": "Captured at acquisition"}]
    }
  ],
  "provenance_manifest": {
    "status": "unsigned|signed|signing_failed",
    "generated_at": "<ISO 8601>",
    "signing": {"profile": "noosphere-c2pa", "receipt_path": "..."},
    "case_artifacts": [{"kind": "findings", "path": "data/findings.json", "sha256": "..."}],
    "claims": [{"finding_id": "F1", "support_type": "direct", "evidence_refs": ["E1"]}],
    "sources": [{"evidence_id": "E1", "source_url": "...", "sha256": "...", "human_verification_required": false}]
  },
  "fact_check_summary": {
    "total_claims": N,
    "verified": N,
    "unverified": N,
    "disputed": N,
    "false": N
  },
  "cycles": N,
  "existing_feedback": null
}
```

Join each finding with its matching fact-check claim by `finding_id`. Include the complete `expressions` array from `data/source-expressions.json` as `source_expressions`; the template joins every active, superseded, and withdrawn expression to each authoritative `finding_links[].finding_id`. Preserve the investigator's `grounding`, the fact-checker's `grounding_assessment`, source `local_file` fields, and `evidence_bundle_refs`. For legacy cases without the expression artifact, use an empty array and retain the existing finding-level review. If `data/provenance-manifest.json` exists, include it as `provenance_manifest`; if it does not exist, set `provenance_manifest: null` so the template can show that signing has not been generated yet.

### 4. Inject payload into the template

The template contains a single marker:

```html
<script id="investigation-data" type="application/json">
/*INVESTIGATION_DATA*/
</script>
```

Serialize the payload for an HTML script-data context before replacing the marker. After JSON serialization, replace every literal `<` with `\u003c`, U+2028 with `\u2028`, and U+2029 with `\u2029`. This prevents exact source text such as `</script><script>…` from terminating the inert JSON element. Do not build payload JSON by string concatenation.

Replace `/*INVESTIGATION_DATA*/` with that safe JSON payload. Use `edit-file` with `old="/*INVESTIGATION_DATA*/"` and `new=<safe-json-payload>`.

### 5. Write the artifact

```
write-file("{CASE_DIR}/review.html", <populated template>)
```

### 6. Report to user

```
"Review artifact written to {CASE_DIR}/review.html.

Open it in any browser to inspect findings and submit feedback.
If you submit feedback, save the exported review-feedback.json
into {CASE_DIR}/data/ and re-run /spotlight to process it.

Or proceed to ingestion now — review is optional."
```

---

## Mode B — `process` Steps

### 1. Detect feedback

```
list-files("{CASE_DIR}/data/review-feedback.json")
list-files("{CASE_DIR}/data/review-feedback-processed.json")
```

If `review-feedback.json` exists AND `review-feedback-processed.json` does NOT exist (or is older than the feedback file) → proceed. Otherwise skip.

### 2. Read feedback

```
read-file("{CASE_DIR}/data/review-feedback.json")
```

Validate it against the schema in `references/feedback-schema.md`. Required fields: `schema_version`, `project`, `submitted_at`, and at least one of `findings_feedback[]`, `expressions_feedback[]`, `general_feedback`, `missing_angles`.

For each expression-targeted entry, resolve both `expression_id` and the authoritative `finding_links[].finding_id` pair in the current source-expression artifact. If either target is missing, log a warning naming both IDs and skip that entry; never retarget feedback heuristically. Accept only the documented category enum.

### 3. Build investigator spawn prompt

Compose a focused spawn prompt. For each finding with feedback:

```
Finding {id}: {claim}
Current verdict: {verdict}

Editorial feedback:
  - Challenge: {challenge}
  - Deeper verification requested: {deeper_verification}
  - Alternative framing suggested: {alternative_framing}

Action: address this feedback — seek additional evidence for the
challenge, pursue the deeper verification, consider whether the
alternative framing is supported by sources. Update findings.json
with any new evidence. If the verdict should change, say so
explicitly in the cycle notes.
```

For `general_feedback` and `missing_angles`, add a "general directives" section to the prompt.

For each valid expression-targeted entry, add:

```
Source expression {expression_id} linked to finding {finding_id}
Category: {category}
Reviewer note: {comment}

Action: inspect the canonical anchor and original evidence artifact. Do not
mutate an existing expression core or finding link in place. If its passage,
locator, attribution, relation, or source is wrong or stale, withdraw or
supersede the old expression and create a corrected expression under the
source-expression contract. Record changed and superseded expression IDs and
the affected finding IDs in the cycle notes. This annotation does not change a
verdict.
```

### 4. Spawn investigator in EXECUTION mode

```
handle = spawn-agent(
  agent_id: "investigator",
  prompt: "<spawn prompt from step 3, wrapped in EXECUTION-mode template>
MODE: EXECUTION
PROJECT: {project}
VAULT_PATH: {vault_path or 'none'}
CYCLE: <current cycle + 1>

This cycle addresses editorial feedback submitted through the review
artifact. Read {CASE_DIR}/data/review-feedback.json for the
full feedback. Focus narrowly on the items listed above.

Read methodology from {CASE_DIR}/data/methodology.json.
Write merged findings to {CASE_DIR}/data/findings.json.
Read and, when required, append lifecycle/corrected records to
{CASE_DIR}/data/source-expressions.json without mutating existing cores or links.
Append to {CASE_DIR}/data/investigation-log.json with focus='review-feedback'.",
  config: { iteration_limit: 80 }
)
wait-agent(handle)
```

### 5. Validate expression changes

Run the case validator after the investigator completes and before re-fact-check:

```
execute-shell("python3 scripts/validate-case.py {CASE_DIR}")
```

If validation fails, do not regenerate review and do not ask the fact-checker to consume invalid state. Return the named expression defects to the investigator for repair. Reviewer annotations never write verdicts directly.

### 6. Spawn fact-checker (re-verify affected claims)

Re-verify every finding targeted by valid expression feedback, plus findings whose feedback requested deeper verification or whose evidence was updated. The fact-checker is the only actor in this loop that may change a verdict:

```
handle = spawn-agent(
  agent_id: "fact-checker",
  prompt: "PROJECT: {project}
CYCLE: <current cycle>

Independently re-fact-check findings affected by editorial or source-expression
feedback. Specifically: {list of affected F-IDs}. Inspect the current
findings.json, source-expressions.json, and canonical anchors and apply SIFT per
the usual methodology. Do not inherit a verdict from the feedback annotation.

Write to {CASE_DIR}/data/fact-check.json (merge with existing).",
  config: { iteration_limit: 50 }
)
wait-agent(handle)
```

### 7. Validate re-fact-checked state

Run `python3 scripts/validate-case.py {CASE_DIR}` again after fact-checking. Do not write the processed marker or regenerate review unless the final findings, expressions, and verdict references validate together.

### 8. Write the processed marker

```
write-file("{CASE_DIR}/data/review-feedback-processed.json",
  '{"schema_version": "1.0", "processed_at": "<ISO 8601>", "feedback_file": "review-feedback.json", "feedback_submitted_at": "<ISO 8601 from feedback>", "cycles_added": 1, "findings_updated": [<ids>], "expressions_updated": [<ids>], "expressions_superseded": [<ids>]}')
```

### 9. Regenerate the review artifact

Re-enter Mode A (generate) to produce a fresh `review.html` reflecting the updated findings.

### 10. Report to user

```
"Feedback processed. {N} findings updated.

Regenerated review.html reflects the new state. Submit more
feedback, proceed to ingestion, or stop here — your call."
```

---


## Feedback Schema

The `review-feedback.json` schema is documented in `references/feedback-schema.md`.

Key invariants:

- `schema_version: "1.0"` required
- `project` must match the active case
- `findings_feedback[].finding_id` must reference an existing finding ID
- `expressions_feedback[].expression_id` and `.finding_id` must resolve to a current authoritative expression link
- `expressions_feedback[].category` is one of `omitted_context`, `attribution_error`, `wrong_relation`, `mistranscription`, `bad_locator`, `stale_source`, `other`
- Expression feedback is an annotation routed through validation and independent re-fact-check; it never changes a verdict directly
- Feedback comments and existing finding fields remain free-form; only the expression category uses a fixed enum

---

## The HTML Template

The self-contained template lives at `references/template.html`. Characteristics:

- Single file, inline CSS and JS, no external assets, no CDN
- Renders summary + findings + per-claim verdicts in a clean two-column layout
- Renders grounding granularity per finding: support type, source role, confidence cap, checked elements, missing assumptions, and misgrounding risk (contradiction-search outcome is rolled into the grounding rationale)
- Renders active and historical source expressions as an exact expression → finding → verdict chain, including source/locator, attribution/language, relation, hashes, lifecycle, and grounding
- Renders case-level provenance/C2PA state from `data/provenance-manifest.json`, including signing status, artifacts, source hashes, evidence refs, and whether human verification is still required
- Per-finding feedback form: `challenge`, `deeper_verification`, `alternative_framing`
- Per-expression feedback form: category plus optional note, bound to both expression and finding IDs
- Overall form: `general_feedback`, `missing_angles`
- Submit button serializes feedback into a Blob and triggers download via `<a download>`
- Dark-mode readable, no JavaScript framework dependencies
- Works offline; works in pi's embedded browser; works in any recent Chrome / Firefox / Safari

The template has exactly one substitution marker: `/*INVESTIGATION_DATA*/` inside a `<script type="application/json">` tag. Skill execution replaces this marker with the payload JSON from Mode A step 3.

---

## File Locations

```
Reads from:
  {CASE_DIR}/data/findings.json
  {CASE_DIR}/data/fact-check.json
  {CASE_DIR}/data/source-expressions.json     (optional for legacy cases)
  {CASE_DIR}/data/summary.json              (optional)
  {CASE_DIR}/summary.md                      (optional)
  {CASE_DIR}/data/review-feedback.json      (Mode B only)
  {CASE_DIR}/data/review-feedback-processed.json  (Mode B only — existence check)
  skills/review/references/template.html

Writes to:
  {CASE_DIR}/review.html
  {CASE_DIR}/data/review-feedback-processed.json  (Mode B)
  {CASE_DIR}/data/findings.json              (Mode B, via spawned investigator)
  {CASE_DIR}/data/source-expressions.json    (Mode B, via spawned investigator when required)
  {CASE_DIR}/data/fact-check.json            (Mode B, via spawned fact-checker)
  {CASE_DIR}/data/investigation-log.json     (Mode B, appended)
```

---

## Sensitive Mode

In sensitive mode:

- Mode A still functions fully — HTML generation is local-only, no network calls required
- Mode B's investigator re-spawn runs in sensitive mode (no `fetch`/`search`), so the investigator can only address feedback using pre-scraped research in `{CASE_DIR}/research/`
- If feedback requests evidence the investigator cannot gather without network access, the cycle log explicitly records "sensitive-mode constrained — could not pursue {specific item}"
