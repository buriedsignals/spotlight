# Noosphere signing fixtures, development CA

Supplied by Noosphere on 2026-10-05 (contract 1.3.0, issue #51), signed by
`platform.noosphere.tech` from the frozen Spotlight and Mycroft requests.
`request.json` and `response.json` are the exact bodies; `certificate-chain.pem`
is the sidecar's chain (leaf and the development subordinate CA).

They carry the **development** grade: production still signs under Noosphere's
development CA. `tests/provenance-manifest-check.py` uses them to prove the
record checks and the c2patool step. Replace them with `production-v1` when
Noosphere reissues under the production CA.
