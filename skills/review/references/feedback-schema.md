# Review Feedback Schema

Schema for `cases/{project}/data/review-feedback.json` — the file produced by the review HTML's submit action and consumed by the review skill's Mode B (process).

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
| `general_feedback` | No | Free text |
| `missing_angles` | No | Free text |
| `ingest_preference` | No | Hint to the orchestrator about next step. Default if omitted: `proceed` (offer ingestion after processing) |

---

## Validation Rules

1. At least one of `findings_feedback` (non-empty), `general_feedback` (non-empty), or `missing_angles` (non-empty) MUST be present. Otherwise the feedback file carries no actionable content and should be rejected.

2. `findings_feedback[].finding_id` must exist in the current `cases/{project}/data/findings.json`. If a referenced ID has been removed (e.g., finding was retracted in a prior cycle), the skill logs a warning and skips that feedback entry.

3. All feedback fields are free-form text. The review skill passes them verbatim into the investigator spawn prompt.

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
  "general_feedback": "Overall strong but the Denmark-specific sourcing is thin. Can we get more Danish primary sources?",
  "missing_angles": "We haven't looked at the Netherlands angle — they've taken a parallel position. Worth a mention in limitations at minimum.",
  "ingest_preference": "hold"
}
```

---

## Processing Marker

After the review skill's Mode B processes a feedback file, it writes `cases/{project}/data/review-feedback-processed.json`:

```json
{
  "schema_version": "1.0",
  "processed_at": "<ISO 8601>",
  "feedback_file": "review-feedback.json",
  "feedback_submitted_at": "<ISO 8601 from the feedback file>",
  "cycles_added": 1,
  "findings_updated": ["F3", "F7"],
  "new_findings": [],
  "retracted_findings": [],
  "notes": "Summary of what changed in response to the feedback"
}
```

The existence of this marker (with `feedback_submitted_at` ≥ feedback file's `submitted_at`) prevents reprocessing the same feedback. A new `review-feedback.json` with a newer `submitted_at` triggers another Mode B cycle.

---

## Resubmission

Users may submit feedback multiple times across cycles. Each submission:

1. Overwrites `cases/{project}/data/review-feedback.json` (the review HTML's download always uses this filename)
2. Triggers a fresh Mode B cycle
3. Results in a new `review-feedback-processed.json` (overwrites previous)

Prior feedback is preserved in the investigation-log.json — each Mode B cycle appends an entry with `focus: "review-feedback"` and the full feedback payload.
