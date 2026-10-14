# Noosphere C2PA Integration

Use this integration only after Spotlight has passed Gate 1. It packages the completed case into a C2PA-ready provenance manifest and can optionally submit that manifest to a Noosphere signer.

## Contract

Signing requires a Noosphere API key, sent as the `X-API-Key` header with the `sign` scope. The signer also uses a signing credential configured on the Noosphere side.

Environment:

- `NOOSPHERE_C2PA_URL` — signer endpoint, e.g. `https://platform.noosphere.tech/api/provenance/sign`
- `NOOSPHERE_PROVENANCE_API_KEY` — Noosphere signing API key (sent as `X-API-Key`, only to `https://platform.noosphere.tech`). Required to sign; read from the environment only.
- `NOOSPHERE_C2PA_CREDENTIAL_ID` — signer credential id, if Noosphere exposes multiple credentials (optional)

Preflight reports this integration as `unconfigured` until both
`NOOSPHERE_C2PA_URL` and `NOOSPHERE_PROVENANCE_API_KEY` are set. This does not
affect local unsigned manifest generation.

## Build Manifest

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR}")
```

Output:

`{CASE_DIR}/data/provenance-manifest.json`

## Sign Manifest

If `NOOSPHERE_C2PA_URL` is configured:

```text
execute-shell("python3 scripts/build-provenance-manifest.py {CASE_DIR} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\" --artifact review.html")
```

The helper checks the response against the Noosphere contract and the manifest
it sent (see `skills/provenance-signing/SKILL.md`), then saves an accepted
receipt to:

`{CASE_DIR}/data/provenance-signing-receipt.json`

An accepted receipt is verified locally with `c2patool` (0.27.22 tested)
against `c2pa-trust-list.pem`. Only a `Trusted` result marks the package
`signed`; otherwise it is recorded as `receipt_status: received_unverified` and
stays `unsigned`.

`c2pa-trust-list.pem` is the official C2PA trust list, copied unchanged from
`c2pa-org/conformance-public` `trust-list/C2PA-TRUST-LIST.pem` at commit
`70ec46e16962b81e70795fcb50a05e0128329a20` (2026-08-13). Noosphere's
development CA is not on it, so receipts signed under that CA stay unverified.
Update the file from the same source when the list changes.

## Editorial Boundary

C2PA signing makes the package tamper-evident. It does not certify that claims are true. Spotlight's truth standard remains the evidence bundle, independent fact-checking, and Gate 1 editorial review.
