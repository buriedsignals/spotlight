---
name: editorial-review
description: Generate a self-contained HTML review artifact after the Investigation gate and export structured feedback to the Investigation gate owner. No server required.
version: "1.0"
invocable_by: [orchestrator, user]
requires: []
---

# Editorial Review — Post-Investigation-gate Review Artifact

Generate one self-contained HTML file that the journalist can open in any
browser to inspect findings and verdicts and export structured feedback. The
Investigation gate owner validates that feedback, converts it to targeted instructions, and
records `requestFollowUp` through `spotlight_transition`. This skill never
spawns agents or derives phase state from feedback-file presence.

**No server required.** The HTML is fully self-contained (inline CSS + JS).
Feedback is exported as a downloadable `review-feedback.json` file.

---

## Generate

The Investigation gate owner invokes this skill only when the resolver returns
`resume_at: review`.

Inputs:

- `{CASE_DIR}/data/findings.json`
- `{CASE_DIR}/data/fact-check.json`
- `{CASE_DIR}/data/source-expressions.json` (optional for legacy cases; required by activated cases)
- `{CASE_DIR}/data/summary.json` (optional)
- `{CASE_DIR}/data/provenance-manifest.json` (optional)
- `{CASE_DIR}/summary.md` (optional)

Output:

- `{CASE_DIR}/review.html` — self-contained review artifact

## Generate Steps

### 1. Build the artifact

```
execute-shell("scripts/spotlight-python scripts/build-review-html.py {CASE_DIR}")
```

The script is deterministic and invents nothing. It reads the inputs above
and `skills/editorial-review/references/template.html`, builds the injection
payload below, serializes it for the HTML script-data context, and writes
`{CASE_DIR}/review.html`. It prints the written path, its size and its
SHA-256. A missing or unreadable `findings.json` or `fact-check.json` stops it
with a message on stderr and exit code 1: fix the case file and run it again.
Do not hand-build the payload or edit the template instead.

The Pi harness has no shell: its `build_review_html` tool runs the same script.

### 2. What the script builds

The payload has the shape expected by the template (see `references/feedback-schema.md`):

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

Each finding is copied whole from `data/findings.json` (a list of evidence
strings is joined into paragraphs) and joined with its fact-check claim by
`finding_id` as `verdict`, so the investigator's `grounding`, the
fact-checker's `grounding_assessment`, source `local_file` fields, and
`evidence_bundle_refs` are preserved. The complete `expressions` array from
`data/source-expressions.json` becomes `source_expressions`; the template joins
every active, superseded, and withdrawn expression to each authoritative
`finding_links[].finding_id`. A legacy case without the expression artifact
gets an empty array and keeps the finding-level review. `provenance_manifest` is
`data/provenance-manifest.json`, or `null` when it does not exist yet, so the
template shows that signing has not been generated. The summary comes from
`data/summary.json` (`title` as the headline, `overview` or `summary`,
`conclusions` or `key_conclusions`, `limitations`, `scope`), falling back to
`summary.md` for the overview. `fact_check_summary` counts the fact-check
verdicts, and `cycles` is `summary.json` `cycles` or the latest recorded cycle.

The template contains a single marker:

```html
<script id="investigation-data" type="application/json">
/*INVESTIGATION_DATA*/
</script>
```

The script serializes the payload for an HTML script-data context before
replacing the marker: after JSON serialization, every literal `<` becomes
`\u003c`, U+2028 becomes `\u2028`, and U+2029 becomes `\u2029`. This prevents
exact source text such as `</script><script>…` from terminating the inert JSON
element.

### 3. Report to user

```
"Review artifact written to {CASE_DIR}/review.html.

Open it in any browser to inspect findings and export structured feedback.
Return the exported review-feedback.json to the Investigation gate owner for validation
and a targeted follow-up transition, or proceed to the public report."
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
- Before a follow-up transition, the Investigation gate owner runs `scripts/spotlight-python scripts/validate-case.py {CASE_DIR}` to validate the referenced case and expression state.
- The independent fact-checker is the only actor in this loop that may change a verdict.
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

The template has exactly one substitution marker: `/*INVESTIGATION_DATA*/` inside a `<script type="application/json">` tag. `scripts/build-review-html.py` replaces this marker with the payload JSON described in generate step 2.

---

## File Locations

```
Runs:
  scripts/build-review-html.py

Reads from:
  {CASE_DIR}/data/findings.json
  {CASE_DIR}/data/fact-check.json
  {CASE_DIR}/data/source-expressions.json     (optional for legacy cases)
  {CASE_DIR}/data/summary.json                (optional)
  {CASE_DIR}/data/provenance-manifest.json    (optional)
  {CASE_DIR}/summary.md                       (optional)
  skills/editorial-review/references/template.html

Writes to:
  {CASE_DIR}/review.html
```

---

## Sensitive Mode

Generation is local-only and works unchanged in sensitive mode. Any targeted
follow-up requested from exported feedback returns through the Investigation gate owner and
the normal execution phase, where sensitive-mode egress restrictions apply.
