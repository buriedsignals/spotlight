# Infrastructure History — Compact

Build a dated timeline, not a present-tense ownership claim.

1. Define the subject and relevant date range.
2. Collect passive DNS, certificate-transparency, RDAP/WHOIS, network, archive, and historical HTTP observations only as needed.
3. Record `valid_from`, `valid_to` or observation time, collection UTC, source URL, access method, and artifact for every row.
4. Keep `observed` separate from `inferred`. Shared IPs, nameservers, registrars, certificates, analytics IDs, and header similarity are pivots—not proof of common control.
5. Seek changes and contradictions; preserve gaps as `null`, `skipped`, or `blocked` with reasons.
6. Ground any finding to the relevant historical moment, not today's state.

```text
time window | subject | record type | observed value | source | artifact
possible pivot | alternative explanation | confidence cap | next check
```

WARNING: Do not scan, enumerate services, brute-force subdomains, or contact the live host. Use passive and archived observations.
