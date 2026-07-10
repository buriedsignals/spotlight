# CTI Expert upstream review policy

CTI Expert is source material, never a runtime dependency. The daily workflow
records the latest observed upstream revision in one durable review issue;
`seen_sha` records the last revision a maintainer acknowledged. Spotlight ships
only adaptations reviewed at `active_sha`.

## Allowlisted subjects

- passive domain, IP, URL, hash, DNS, certificate, WHOIS/RDAP, and HTTP-header
  methodology;
- local document metadata and raw email-header analysis;
- public GitHub profile, repository, commit, fork, tag, and release history;
- public-ledger blockchain tracing as a lead-generation method;
- attempted/skipped/null-path and contradiction discipline;
- technical-indicator export concepts.

## Promotion blockers

- missing or changed license;
- new executable, binary, archive, symlink, dependency, install command, remote
  script, external host, or destructive operation;
- autonomous scope expansion, auto-approval, credential use, or active scanning;
- breach, stealer-log, secret, face, phone, Wi-Fi, darknet, or private-person
  collection;
- automatic identity merging or scores that imply verification/publication;
- compact-reference or manifest token-budget regression;
- missing attribution, tests, or plugin regeneration.

## Review flow

1. Inspect the daily `CTI Expert upstream review pending` issue, or run
   `python3 scripts/check-cti-expert-upstream.py --json --strict`.
2. If upstream moved, fetch the reported commit into maintainer quarantine; do
   not execute it or place it under `skills/`.
3. Diff only paths named in `source-map.json`, plus the upstream license.
4. Rewrite accepted changes into Spotlight's contracts and record deliberate
   divergences.
5. Run the CTI, manifest, schema, safety, and plugin-distribution checks.
6. Use `--update-seen` when acknowledging the observed revision, whether it is
   accepted or deliberately skipped. Advance `active_sha` only in a reviewed
   change that adapts accepted material.

An upstream change may remain unpromoted indefinitely when it is irrelevant or
unsafe. `seen_sha != active_sha` is review state, not an installation failure.
