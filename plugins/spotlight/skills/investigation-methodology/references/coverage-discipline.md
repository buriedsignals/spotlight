# Investigation Coverage Discipline

A coverage ledger makes the investigation's search space visible. It does not score quality or replace claim-level grounding.

## Build the Ledger from the Brief

Turn each material question into relevant research paths. Do not use a universal checklist or add intrusive paths merely to increase a count. Each row should contain:

| Field | Meaning |
|---|---|
| question | Investigation question the path could answer |
| path | Bounded source or method |
| priority | Why this path matters now |
| status | `planned`, `observed`, `null`, `skipped`, or `blocked` |
| result_refs | Finding, artifact, or research-note references |
| reason | Required for `skipped` and `blocked`; useful for `null` |
| limits | Visibility, date, jurisdiction, access, or method constraints |
| next_check | A proportionate follow-up, if any |

## Status Semantics

- `planned`: relevant but not yet attempted.
- `observed`: produced one or more source-linked observations, whether supportive or contradictory.
- `null`: completed within the stated scope and returned no relevant observation.
- `skipped`: deliberately not attempted; record why, such as irrelevance, disproportionality, ethics, OPSEC, duplication, or lack of authorization.
- `blocked`: relevant but could not be completed; record the access, legal, technical, cost, or time constraint.

A null result is not evidence that the subject or event does not exist unless the searched source is demonstrably complete for the exact claim, place, and time. A blocked or skipped path is not silently equivalent to null.

## Review Before a Gate

1. Confirm every material brief question has at least one relevant path or an explicit scope decision.
2. Link observed paths to artifacts/findings and ensure null paths record the actual scope searched.
   In an explicitly enabled source-expression pilot or activated case, also
   link each relied-on observed passage to its case-local expression. Preserve
   original-language text and explicit support, contradiction, or context
   polarity. A missing or unavailable anchor remains a gap; never fabricate one
   to make a coverage row look complete.
3. Review planned and blocked paths by expected evidentiary value, not by how easy they are to complete.
4. Carry material gaps into `gaps` and the limitations section.
5. Do not calculate a completion percentage or use path count to raise a claim's confidence. Invoke `epistemic-grounding` for confidence.

## Safety Boundary

Coverage never obliges the investigator to use breach data, leaked credentials, active scanning, pretexting, target contact, paid services, or any method outside the brief. Mark such a path skipped with the governing reason if it is relevant to explaining the investigation's limit.

Adapted from the coverage-matrix and verification-checklist concepts in [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo / chongluadao.vn (GitHub: 7onez; MIT License with upstream Ethical Use Addendum), with numerical coverage scoring and unsafe discovery paths removed.
