# Passive Infrastructure History

## Question First

State the historical question and date range before collecting: for example, “Which addresses did this domain resolve to during the week the document was published?” A current lookup cannot establish a historical state.

## Evidence Lanes

Use the smallest useful set of passive lanes:

| Lane | Observation | Important limit |
|---|---|---|
| Passive DNS | Dated A, AAAA, MX, NS, CNAME, or TXT relationships | Provider visibility is incomplete; absence is not proof |
| Certificate transparency | Logged certificate names, issuer, and validity window | A certificate can cover unrelated tenants and does not prove control |
| RDAP/WHOIS | Registration and registrar fields as observed | Privacy proxies, redaction, reseller data, and later changes limit attribution |
| Network registration | ASN/prefix holder for an address and date | Network ownership is not site ownership |
| Web archives/passive HTTP datasets | Archived body, redirect, title, or header observations | Capture time may differ from event time; headers are easy to copy |

WARNING: Do not port-scan, fingerprint a live service, brute-force names, request sensitive paths, bypass access controls, or send direct probes to the target. If a direct retrieval becomes editorially necessary, it is outside this passive method and requires explicit approval and an OPSEC plan.

## Timeline Workflow

1. Preserve the original domain, URL, or IP and any claimed event time.
2. Collect dated observations. Record source URL, collection UTC, source-reported observation time, record type, value, TTL/validity when available, access method, and local artifact.
3. Normalize time zones while retaining the source timestamp. Distinguish `observed_at`, `valid_from`, `valid_to`, and `collected_at`.
4. Sort observations chronologically. Mark gaps and conflicting records instead of interpolating them.
5. Generate pivots only when they answer a case question. Check whether the pivot is common across unrelated tenants before treating it as meaningful.
6. Seek a disconfirming source. A historical archive, later registration record, or unrelated tenant may explain an apparent link.
7. Invoke `epistemic-grounding` and word the claim to the time-bounded observation.

## Attribution Discipline

These are weak or conditional signals unless corroborated:

- shared IP address, CDN, cloud tenant, registrar, nameserver, or certificate authority;
- overlapping certificate subject-alternative names;
- similar titles, favicons, headers, analytics IDs, templates, or technologies;
- a registrant string that is redacted, proxied, stale, or self-asserted.

Do not turn infrastructure co-occurrence into a person or organization identity. A defensible output is a dated relationship graph whose edges link to preserved observations and state alternative explanations.

## Coverage Ledger

For each relevant lane record `observed`, `null`, `skipped`, or `blocked`, with the reason and provider coverage limitation. A complete investigation can legitimately contain gaps; hiding them makes a timeline look more certain than it is.
