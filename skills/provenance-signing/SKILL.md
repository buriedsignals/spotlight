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
- `data/source-expressions.json`, for activated cases
- `data/evidence-bundle.json`
- `data/investigation-log.json`
- `review.html`, when present
- finalized report artifacts, when present

Legacy cases keep the generated manifest at:

`{CASE_DIR}/data/provenance-manifest.json`

For an activated findings contract (`schema_version: "1.1"`), that path is a
derived current pointer. The immutable manifest bytes are stored at:

`{CASE_DIR}/data/provenance-manifests/PM-<revision-hash>.json`

Each revision records its input-set hash and parent revision/input-set hashes.
Claim entries include the exact source-expression IDs, expression/finding/link
fingerprints, relation, anchor hash, and original artifact hash used by the
fact-check at that revision. This proves integrity and traceability, not that
the passage semantically entails the finding.

## Credentials Boundary

Signing needs a Noosphere API key with the `sign` scope, sent as the `X-API-Key`
header from `NOOSPHERE_PROVENANCE_API_KEY`. The signing credential itself is
configured on the Noosphere side and never reaches the case directory.

Model this distinction explicitly:

- `requires_api_key: true`
- `requires_signing_credential: true`
- `credential_id`: optional identifier passed to the signer

Never store private keys, certificates, API keys, bearer tokens, or signing secrets in the case directory.

## Build Only

For a local unsigned package:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

For a legacy case this writes the existing unsigned 1.0 manifest. For an
activated case it creates one immutable unsigned revision and advances the
current pointer. Repeating the command with unchanged inputs reuses the same
revision.

Before delivering or signing an existing manifest, check that findings,
fact-check, source expressions, review, and report inputs have not changed:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --check-current")
```

The command exits non-zero and marks an activated pointer `stale` on any input
or revision-byte mismatch. Rebuild to create a child revision; never edit a
revision file in place.

## Optional Noosphere Signing

If a Noosphere C2PA signer endpoint is available:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\" --artifact review.html")
```

The signing request carries the `X-API-Key` header and this body:

```json
{
  "profile": "spotlight",
  "artifact_path": "review.html",
  "provenance_manifest": {},
  "credential_id": "optional signer credential id"
}
```

The response follows the Noosphere provenance contract
(`https://platform.noosphere.tech/openapi.yaml`): it returns the signed
`record_b64`, its `content_hash`, the C2PA sidecar as `c2pa_manifest_b64`, and
echoes `profile` and `input_set_hash` unchanged.
For activated cases receipts are immutable and content-addressed under:

`{CASE_DIR}/data/provenance-signing-receipts/`

The current pointer records the applicable receipt. A repeated signing command
for an already signed revision does not call the signer again. Legacy cases
retain `data/provenance-signing-receipt.json` for compatibility.

## Report Language

In summaries and HTML reports, describe the result carefully:

- Correct: "The investigation package and verification trail were signed and can be checked for later tampering."
- Incorrect: "C2PA proves the investigation is true."

Truth still depends on Spotlight evidence, independent fact-checking, and editorial review.

## Failure Handling

If signing fails, keep the unsigned immutable revision and continue the review
flow. Activated cases append a deduplicated failure record under
`data/provenance-signing-attempts/`; retrying never overwrites a revision,
receipt, or earlier attempt. Report:

- endpoint attempted,
- whether a credential id was provided,
- exact error string from the current pointer,
- that the case remains editorially reviewable but unsigned.

If a signing retry succeeds, the pointer advances to the immutable receipt and
prior failed-attempt records remain historical evidence. If case inputs change
after signing, the signed revision and receipt remain untouched; the current
pointer becomes stale until a new child revision is built.
