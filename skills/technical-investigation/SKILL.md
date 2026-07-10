---
name: technical-investigation
description: Passive technical methods for indicators, infrastructure history, local files, email headers, public GitHub, and verified export.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
requires: [epistemic-grounding, web-archiving, shell-safety]
attribution: "Adapted from 7onez/cti-expert (https://github.com/7onez/cti-expert), by Hieu Ngo / chongluadao.vn. MIT License with upstream Ethical Use Addendum."
---

# Technical Investigation

Use passive, source-linked technical observations as leads in a Spotlight investigation. This skill does not assign a risk score, identify a person from infrastructure alone, or turn provider labels into verified findings.

> **Adapted from** [CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo / chongluadao.vn (GitHub: 7onez; MIT License with upstream Ethical Use Addendum). Spotlight preserves the useful investigative methods while deliberately excluding the upstream runtime, installers, credential and breach workflows, active scanning, identity merging, and automatic trust scoring.

## Route One Task at a Time

| Need | Reference |
|---|---|
| Triage a domain, IP, URL, or file hash using passive sources | `references/indicator-triage.md` |
| Reconstruct DNS, certificate, registration, or HTTP history | `references/infrastructure-history.md` |
| Examine local document metadata or raw email headers | `references/document-message-forensics.md` |
| Trace public GitHub repository, commit, fork, or release history | `references/github-public.md` |
| Export fact-checked technical indicators after Gate 1 | `references/verified-indicator-export.md` |

### Model-tier loading

- `12b`: read this root and exactly one matching `*-compact.md` reference. Do not load the expanded counterpart in the same cycle.
- `26b` or `31b`: read the matching expanded reference, but only for the active task.
- `frontier`, `api`, Codex Desktop, or Claude Desktop with no smaller tier configured: read the matching expanded reference.
- Verified export has one short reference for every tier and is only available after fact-checking.

The tier changes context density, not evidence standards, tool authority, or safety boundaries.

## Record the Selected Method

When this skill is selected during methodology, include `technical-investigation` in `skills_invoked[]` and append one object per selected method to `method_provenance[]`:

```json
{
  "skill_id": "technical-investigation",
  "method": "indicator-triage|infrastructure-history|document-message-forensics|github-public|verified-indicator-export",
  "upstream": "7onez/cti-expert",
  "active_sha": "f9ecc9b0258caff78d26c0b779d1687f4431749f",
  "reference": "the one tier-selected reference filename"
}
```

This records method provenance, not evidence for a factual claim.

## Non-negotiable Method

1. Preserve the exact input and classify its type. Never silently normalize away path, port, case, or query-string evidence.
2. Prefer passive records, public APIs, archives, and local artifacts. Do not visit a suspicious URL in a normal browser, probe ports, enumerate services, validate credentials, or interact with a target account.
3. Record each observation with source URL or local artifact, collection time, observed time when supplied, access method, and provider wording. Archive public evidence when permitted.
4. Separate observation from inference. Shared hosting, reused certificates, repository metadata, and provider classifications are pivots—not proof of common control, authorship, maliciousness, or identity.
5. Record attempted, observed, null, skipped, and blocked paths. A null result is not exculpatory; a skipped path must include a reason.
6. Invoke `epistemic-grounding` before promoting a technical lead into `findings.json`. Search for contradictions and preserve unresolved conflicts.
7. Put shell parameters through `shell-safety`. Keep artifacts inside the case directory and never auto-install a dependency.
8. During fact-checking, name every assessed explicit indicator in the claim's `technical_indicator_ids[]` and include its exact value in `claim_text`. Unlisted or non-verified indicators cannot export.

## Related Skills

- `invoke-skill("web-archiving")` to preserve public technical evidence.
- `invoke-skill("content-access")` when a relevant public source is inaccessible.
- `invoke-skill("follow-the-money")` when a public blockchain trail becomes a financial-flow question.
- `invoke-skill("investigate")` for the investigation-wide coverage ledger.

## Credits

Method selection and adaptation are documented in `references/source-map.json`. CTI Expert remains an independent upstream project; Spotlight ships reviewed adaptations, not its executable runtime.
