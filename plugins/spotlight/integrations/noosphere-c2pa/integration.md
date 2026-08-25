# Noosphere C2PA Integration

Use this integration only after Spotlight has passed Gate 1. It packages the completed case into a C2PA-ready provenance manifest and can optionally submit that manifest to a Noosphere signer.

## Contract

Signing requires a Noosphere API key, sent as the `X-API-Key` header with the `sign` scope. The signer also uses a signing credential configured on the Noosphere side.

Environment:

- `NOOSPHERE_C2PA_URL` — signer endpoint, e.g. `https://platform.noosphere.tech/api/provenance/sign`
- `NOOSPHERE_PROVENANCE_API_KEY` — Noosphere signing API key (sent as `X-API-Key`). Required to sign.
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

The helper saves the receipt to:

`{CASE_DIR}/data/provenance-signing-receipt.json`

## Editorial Boundary

C2PA signing makes the package tamper-evident. It does not certify that claims are true. Spotlight's truth standard remains the evidence bundle, independent fact-checking, and Gate 1 editorial review.
