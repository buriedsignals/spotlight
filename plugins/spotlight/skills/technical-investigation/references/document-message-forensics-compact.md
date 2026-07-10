# Document and Message Forensics — Compact

Use only local documents and complete raw email source supplied or lawfully acquired for the case.

1. Preserve an immutable original; record filename, size, cryptographic hash, source, and collection UTC.
2. Work on a copy. Do not execute macros, scripts, links, embedded objects, attachments, or remote resources.
3. Extract container/file metadata separately from visible content. Record exact field, value, tool/version, and artifact.
4. For email, preserve raw headers and body; unfold headers and read `Received` hops chronologically from the earliest trusted boundary. Record SPF/DKIM/DMARC results as server assertions, not sender identity proof.
5. Normalize time zones while retaining original strings. Flag malformed, missing, or contradictory fields.
6. Metadata is a lead: it can be edited, stripped, inherited from templates, or inserted by relays.

Invoke `epistemic-grounding` before making authorship, origin, or delivery claims.
