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

- `cases/{project}/data/findings.json`
- `cases/{project}/data/fact-check.json`
- `cases/{project}/data/summary.json` (optional)
- `cases/{project}/summary.md` (optional)

Outputs:

- `cases/{project}/review.html` — self-contained review artifact

### Mode B — `process`

Triggered when `cases/{project}/data/review-feedback.json` exists and has not yet been processed (no matching `data/review-feedback-processed.json` marker).

Inputs:

- `cases/{project}/data/review-feedback.json`
- `cases/{project}/data/findings.json` (current state)
- `cases/{project}/data/fact-check.json` (current state)

Outputs:

- Targeted investigator spawn prompt (constructed by the skill)
- Updated `cases/{project}/data/findings.json` (after investigator runs)
- Updated `cases/{project}/data/fact-check.json` (after fact-checker runs)
- Regenerated `cases/{project}/review.html` (Mode A runs automatically after processing)
- `cases/{project}/data/review-feedback-processed.json` — marker file recording when feedback was processed

---

## Mode A — `generate` Steps

### 1. Read case files

```
read-file("cases/{project}/data/findings.json")
read-file("cases/{project}/data/fact-check.json")
read-file("cases/{project}/data/summary.json")      # may not exist
read-file("cases/{project}/summary.md")              # may not exist
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
      "perspective": "...",
      "sources": [{"url": "...", "type": "...", "archive_url": "...", "access_method": "..."}],
      "verdict": {
        "verdict": "verified|unverified|disputed|false",
        "confidence": "high|medium|low",
        "evidence_for": [{"description": "...", "source": "...", "source_type": "primary|secondary"}],
        "evidence_against": [{"description": "...", "source": "..."}],
        "notes": "..."
      }
    }
  ],
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

Join each finding with its matching fact-check claim by `finding_id`.

### 4. Inject payload into the template

The template contains a single marker:

```html
<script id="investigation-data" type="application/json">
/*INVESTIGATION_DATA*/
</script>
```

Replace `/*INVESTIGATION_DATA*/` with the JSON payload from step 3. Use `edit-file` with `old="/*INVESTIGATION_DATA*/"` and `new=<json-payload>`.

### 5. Write the artifact

```
write-file("cases/{project}/review.html", <populated template>)
```

### 6. Report to user

```
"Review artifact written to cases/{project}/review.html.

Open it in any browser to inspect findings and submit feedback.
If you submit feedback, save the exported review-feedback.json
into cases/{project}/data/ and re-run /spotlight to process it.

Or proceed to ingestion now — review is optional."
```

---

## Mode B — `process` Steps

### 1. Detect feedback

```
list-files("cases/{project}/data/review-feedback.json")
list-files("cases/{project}/data/review-feedback-processed.json")
```

If `review-feedback.json` exists AND `review-feedback-processed.json` does NOT exist (or is older than the feedback file) → proceed. Otherwise skip.

### 2. Read feedback

```
read-file("cases/{project}/data/review-feedback.json")
```

Validate it against the schema in `references/feedback-schema.md`. Required fields: `schema_version`, `project`, `submitted_at`, and at least one of `findings_feedback[]`, `general_feedback`, `missing_angles`.

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
artifact. Read cases/{project}/data/review-feedback.json for the
full feedback. Focus narrowly on the items listed above.

Read methodology from cases/{project}/data/methodology.json.
Write merged findings to cases/{project}/data/findings.json.
Append to cases/{project}/data/investigation-log.json with focus='review-feedback'.",
  config: { iteration_limit: 80 }
)
wait-agent(handle)
```

### 5. Spawn fact-checker (re-verify affected claims)

Only for findings whose feedback requested deeper verification OR where the investigator updated evidence:

```
handle = spawn-agent(
  agent_id: "fact-checker",
  prompt: "PROJECT: {project}
CYCLE: <current cycle>

Re-fact-check findings that were updated in response to editorial
feedback. Specifically: {list of affected F-IDs}. Read the current
findings.json and apply SIFT per the usual methodology.

Write to cases/{project}/data/fact-check.json (merge with existing).",
  config: { iteration_limit: 50 }
)
wait-agent(handle)
```

### 6. Write the processed marker

```
write-file("cases/{project}/data/review-feedback-processed.json",
  '{"processed_at": "<ISO 8601>", "feedback_file": "review-feedback.json", "cycles_added": 1, "findings_updated": [<ids>]}')
```

### 7. Regenerate the review artifact

Re-enter Mode A (generate) to produce a fresh `review.html` reflecting the updated findings.

### 8. Report to user

```
"Feedback processed. {N} findings updated.

Regenerated review.html reflects the new state. Submit more
feedback, proceed to ingestion, or stop here — your call."
```

---

## Integration with the Orchestrator

### Phase 4 end (Gate 1 approved)

The orchestrator's Phase 4, after the user approves the findings and `summary.md` is written, includes:

```
5. invoke-skill("review")  # Mode A auto — generates review.html
6. Offer the user: "Review artifact ready. Inspect and submit feedback,
   or proceed to ingestion?"
```

### Phase 0 resume check

When resuming an investigation, the orchestrator's Phase 0 should add a step (between 7 and 8):

```
Check for cases/{project}/data/review-feedback.json:
  - If exists AND review-feedback-processed.json missing/older:
    invoke-skill("review")  # Mode B auto — processes feedback
  - Otherwise: proceed normally
```

### Direct user invocation

The user can call this skill at any time to regenerate the review artifact (useful mid-investigation to see current state) or to force-process pending feedback.

---

## Feedback Schema

The `review-feedback.json` schema is documented in `references/feedback-schema.md`.

Key invariants:

- `schema_version: "1.0"` required
- `project` must match the active case
- `findings_feedback[].finding_id` must reference an existing finding ID
- All feedback text is free-form — no enum constraints on sentiment or category

---

## The HTML Template

The self-contained template lives at `references/template.html`. Characteristics:

- Single file, inline CSS and JS, no external assets, no CDN
- Renders summary + findings + per-claim verdicts in a clean two-column layout
- Per-finding feedback form: `challenge`, `deeper_verification`, `alternative_framing`
- Overall form: `general_feedback`, `missing_angles`
- Submit button serializes feedback into a Blob and triggers download via `<a download>`
- Dark-mode readable, no JavaScript framework dependencies
- Works offline; works in pi's embedded browser; works in any recent Chrome / Firefox / Safari

The template has exactly one substitution marker: `/*INVESTIGATION_DATA*/` inside a `<script type="application/json">` tag. Skill execution replaces this marker with the payload JSON from Mode A step 3.

---

## File Locations

```
Reads from:
  cases/{project}/data/findings.json
  cases/{project}/data/fact-check.json
  cases/{project}/data/summary.json              (optional)
  cases/{project}/summary.md                      (optional)
  cases/{project}/data/review-feedback.json      (Mode B only)
  cases/{project}/data/review-feedback-processed.json  (Mode B only — existence check)
  skills/review/references/template.html

Writes to:
  cases/{project}/review.html
  cases/{project}/data/review-feedback-processed.json  (Mode B)
  cases/{project}/data/findings.json              (Mode B, via spawned investigator)
  cases/{project}/data/fact-check.json            (Mode B, via spawned fact-checker)
  cases/{project}/data/investigation-log.json     (Mode B, appended)
```

---

## Sensitive Mode

In sensitive mode:

- Mode A still functions fully — HTML generation is local-only, no network calls required
- Mode B's investigator re-spawn runs in sensitive mode (no `fetch`/`search`), so the investigator can only address feedback using pre-scraped research in `cases/{project}/research/`
- If feedback requests evidence the investigator cannot gather without network access, the cycle log explicitly records "sensitive-mode constrained — could not pursue {specific item}"
