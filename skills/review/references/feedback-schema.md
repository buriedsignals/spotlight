# Review Feedback Schema

Schema for `{CASE_DIR}/data/review-feedback.json` — the file produced by the review HTML's submit action and consumed by the review skill's Mode B (process).

---

## Schema

```json
{
  "schema_version": "1.0",
  "project": "<slug>",
  "submitted_at": "<ISO 8601>",
  "reviewer": "<optional — name or handle of the reviewer>",
  "findings_feedback": [
    {
      "finding_id": "F1",
      "challenge": "Free text — why this claim is wrong, weak, or contested",
      "deeper_verification": "Free text — what further verification is needed",
      "alternative_framing": "Free text — how else this could be framed"
    }
  ],
  "expressions_feedback": [
    {
      "expression_id": "SX1",
      "finding_id": "F1",
      "category": "omitted_context",
      "comment": "Free text — what is wrong or what should be re-verified"
    }
  ],
  "general_feedback": "Free text — overall investigation feedback",
  "missing_angles": "Free text — angles the investigation didn't pursue",
  "ingest_preference": "proceed | hold | cancel"
}
```

---

## Field Reference

| Field | Required | Notes |
|---|---|---|
| `schema_version` | Yes | Must be `"1.0"` |
| `project` | Yes | Must match the active case slug |
| `submitted_at` | Yes | ISO 8601 timestamp when feedback was submitted in the browser |
| `reviewer` | No | Free text identifier; preserved in investigation-log for audit trail |
| `findings_feedback` | No | Array of per-finding feedback entries. If omitted/empty, feedback is general-only |
| `findings_feedback[].finding_id` | Yes (if entry exists) | Must reference an existing finding in current `findings.json` |
| `findings_feedback[].challenge` | No | May be empty string |
| `findings_feedback[].deeper_verification` | No | May be empty string |
| `findings_feedback[].alternative_framing` | No | May be empty string |
| `expressions_feedback` | No | Array of source-expression annotations. Existing finding-level feedback remains valid without it |
| `expressions_feedback[].expression_id` | Yes (if entry exists) | Must reference an expression in current `source-expressions.json` |
| `expressions_feedback[].finding_id` | Yes (if entry exists) | Must reference an authoritative finding link on that expression |
| `expressions_feedback[].category` | Yes (if entry exists) | `omitted_context`, `attribution_error`, `wrong_relation`, `mistranscription`, `bad_locator`, `stale_source`, or `other` |
| `expressions_feedback[].comment` | No | Optional free-text explanation or requested verification |
| `general_feedback` | No | Free text |
| `missing_angles` | No | Free text |
| `ingest_preference` | No | Hint to the orchestrator about next step. Default if omitted: `proceed` (offer ingestion after processing) |

---

## Validation Rules

1. At least one of `findings_feedback` (non-empty), `expressions_feedback` (non-empty), `general_feedback` (non-empty), or `missing_angles` (non-empty) MUST be present. Otherwise the feedback file carries no actionable content and should be rejected.

2. `findings_feedback[].finding_id` must exist in the current `{CASE_DIR}/data/findings.json`. If a referenced ID has been removed (e.g., finding was retracted in a prior cycle), the skill logs a warning and skips that feedback entry.

3. Feedback comments and existing finding-level fields are free-form text and are passed verbatim into the investigator prompt. Expression categories are restricted to the documented enum.

4. Each expression feedback pair must resolve exactly: `expression_id` must exist and that expression must contain a `finding_links[]` entry with the supplied `finding_id`. A dangling expression or finding target is logged by both IDs and skipped; it is never guessed or retargeted.

5. Expression feedback is an annotation, not a verdict mutation. Every valid target enters source-expression validation and independent re-fact-check before a refreshed review is generated. Only the fact-checker may change the verdict.

---

## Example

```json
{
  "schema_version": "1.0",
  "project": "chat-control-denmark",
  "submitted_at": "2026-04-17T14:30:00Z",
  "reviewer": "Tom V.",
  "findings_feedback": [
    {
      "finding_id": "F3",
      "challenge": "The contract award claim relies on a single registry entry. I'm skeptical — can we find the actual signed contract document?",
      "deeper_verification": "Check sam.gov for the procurement action. Also look for the contracting officer's name in the filing.",
      "alternative_framing": ""
    },
    {
      "finding_id": "F7",
      "challenge": "",
      "deeper_verification": "Need a second source for the minister's statement — the one Danish outlet cited may have mistranslated.",
      "alternative_framing": "Could this be framed as a policy signal rather than a firm decision?"
    }
  ],
  "expressions_feedback": [
    {
      "expression_id": "SX4",
      "finding_id": "F7",
      "category": "attribution_error",
      "comment": "The quoted sentence appears to be the journalist's narration, not the minister's statement. Check the original-language anchor."
    }
  ],
  "general_feedback": "Overall strong but the Denmark-specific sourcing is thin. Can we get more Danish primary sources?",
  "missing_angles": "We haven't looked at the Netherlands angle — they've taken a parallel position. Worth a mention in limitations at minimum.",
  "ingest_preference": "hold"
}
```

---

## Processing Marker

After the review skill's Mode B processes a feedback file, it writes `{CASE_DIR}/data/review-feedback-processed.json`:

```json
{
  "schema_version": "1.0",
  "processed_at": "<ISO 8601>",
  "feedback_file": "review-feedback.json",
  "feedback_submitted_at": "<ISO 8601 from the feedback file>",
  "cycles_added": 1,
  "findings_updated": ["F3", "F7"],
  "expressions_updated": ["SX4-corrected"],
  "expressions_superseded": ["SX4"],
  "new_findings": [],
  "retracted_findings": [],
  "notes": "Summary of what changed in response to the feedback"
}
```

The existence of this marker (with `feedback_submitted_at` ≥ feedback file's `submitted_at`) prevents reprocessing the same feedback. A new `review-feedback.json` with a newer `submitted_at` triggers another Mode B cycle.

---

## Resubmission

Users may submit feedback multiple times across cycles. Each submission:

1. Overwrites `{CASE_DIR}/data/review-feedback.json` (the review HTML's download always uses this filename)
2. Triggers a fresh Mode B cycle
3. Results in a new `review-feedback-processed.json` (overwrites previous)

Prior feedback is preserved in the investigation-log.json — each Mode B cycle appends an entry with `focus: "review-feedback"` and the full feedback payload. The processed marker records expression IDs changed or superseded so the regenerated review can expose the full lifecycle history.
