---
name: provenance-signing
description: Build a Spotlight provenance manifest and optionally hand it to Noosphere C2PA signing before the final review/report artifact is delivered.
version: "1.0"
invocable_by: [orchestrator, user]
requires: []
---

# Provenance Signing

This skill creates a portable provenance package for a completed Spotlight case. It does not replace Spotlight verification. It makes the case artifacts tamper-evident and gives Noosphere C2PA a clear signing contract.

Use it after Gate 1 readiness criteria pass and before the final HTML review/report artifact is delivered.

## What Gets Signed

Spotlight signs the investigation package, not isolated web fetches:

- `summary.md`
- `data/findings.json`
- `data/fact-check.json`
- `data/evidence-bundle.json`
- `data/investigation-log.json`
- `review.html`, when present

The generated manifest is:

`cases/{project}/data/provenance-manifest.json`

## No API Key Boundary

Noosphere C2PA does not need a third-party API key for this contract. It does need a signing credential configured wherever Noosphere runs the signer.

Model this distinction explicitly:

- `requires_api_key: false`
- `requires_signing_credential: true`
- `credential_id`: optional identifier passed to the signer

Never store private keys, certificates, bearer tokens, or signing secrets in the case directory.

## Build Only

For a local unsigned package:

```text
execute-shell("python3 scripts/build-provenance-manifest.py cases/{project}")
```

This writes `data/provenance-manifest.json` with `status: unsigned`.

## Optional Noosphere Signing

If a Noosphere C2PA signer endpoint is available:

```text
execute-shell("python3 scripts/build-provenance-manifest.py cases/{project} --sign-endpoint http://localhost:5002/api/spotlight/provenance/sign --credential-id <credential-id> --artifact review.html")
```

The signing request body is:

```json
{
  "artifact_path": "review.html",
  "provenance_manifest": {},
  "credential_id": "optional signer credential id"
}
```

Expected successful response is any JSON receipt Noosphere chooses to return. Save it as:

`cases/{project}/data/provenance-signing-receipt.json`

## Report Language

In summaries and HTML reports, describe the result carefully:

- Correct: "The investigation package and verification trail were signed and can be checked for later tampering."
- Incorrect: "C2PA proves the investigation is true."

Truth still depends on Spotlight evidence, independent fact-checking, and editorial review.

## Failure Handling

If signing fails, keep the unsigned manifest and continue the review flow. Report:

- endpoint attempted,
- whether a credential id was provided,
- exact error string from `data/provenance-manifest.json`,
- that the case remains editorially reviewable but unsigned.
