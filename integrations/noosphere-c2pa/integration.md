# Noosphere C2PA Integration

Use this integration only after Spotlight has passed Gate 1. It packages the completed case into a C2PA-ready provenance manifest and can optionally submit that manifest to a Noosphere signer.

## Contract

No API key is required by Spotlight. The signer may require a configured signing credential on the Noosphere side.

Optional environment:

- `NOOSPHERE_C2PA_URL` — signer endpoint, for example `http://localhost:5002/api/spotlight/provenance/sign`
- `NOOSPHERE_C2PA_CREDENTIAL_ID` — signer credential id, if Noosphere exposes multiple credentials

## Build Manifest

```text
execute-shell("python3 scripts/build-provenance-manifest.py cases/{project}")
```

Output:

`cases/{project}/data/provenance-manifest.json`

## Sign Manifest

If `NOOSPHERE_C2PA_URL` is configured:

```text
execute-shell("python3 scripts/build-provenance-manifest.py cases/{project} --sign-endpoint \"$NOOSPHERE_C2PA_URL\" --credential-id \"$NOOSPHERE_C2PA_CREDENTIAL_ID\" --artifact review.html")
```

The helper saves the receipt to:

`cases/{project}/data/provenance-signing-receipt.json`

## Editorial Boundary

C2PA signing makes the package tamper-evident. It does not certify that claims are true. Spotlight's truth standard remains the evidence bundle, independent fact-checking, and Gate 1 editorial review.
