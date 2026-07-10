# Verified Technical-Indicator Export

Use only after Gate 1 fact-checking. Export is a derived editorial artifact, never a substitute for verification.

## Eligibility

An item is eligible only when:

- it appears explicitly in `findings.json` under `technical_indicators`;
- a fact-check claim with the same `finding_id` names its ID in `technical_indicator_ids[]`, contains the exact indicator value in `claim_text`, and has verdict exactly `verified`;
- every fact-check claim linked to its `finding_id` has the verdict exactly `verified`;
- its type is an allowed technical type: IPv4, IPv6, domain, URL, MD5, SHA-1, SHA-256, Bitcoin address, or Ethereum address;
- its source and context are preserved.

Emails, phone numbers, usernames, names, social handles, credentials, and other personal selectors are excluded. The exporter does not scrape arbitrary strings from prose or source URLs.
URL indicators containing user information, query strings, or fragments are rejected in V1 because those components can carry credentials or victim identifiers; export a separately verified domain or query-free URL instead.

## Run

Invoke `shell-safety`, then pass paths as arguments to:

```text
python3 scripts/export-verified-indicators.py \
  --findings CASE_DIR/data/findings.json \
  --fact-check CASE_DIR/data/fact-check.json \
  --format json|csv|stix \
  --output CASE_DIR/exports/verified-indicators.EXT
```

The output must remain inside the same case directory. Review the deterministic output before sharing it. Preserve the originating finding ID, source references, confidence, fact-check verdict, and export timestamp or reproducible build metadata supplied by the tool.

A verified claim can still be time-bounded. Consumers must not interpret inclusion as a permanent maliciousness label or as permission to block, contact, or probe the indicator.
