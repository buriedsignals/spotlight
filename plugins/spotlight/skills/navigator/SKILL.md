---
name: navigator
description: Route investigative research to Navigator when it needs OSINT tool discovery or reproducible structured public records. Use for finding investigative tools, company registries, court records, procurement, sanctions or PEP screening, campaign finance, legislation, registries, and entity resolution.
---

# Navigator

Navigator is a member service with two modes behind one account connection:

- **OSINT tool discovery** finds the right investigative tool or technique.
- **Data Navigator** finds and queries structured public-record sources for
  Lab members.

Use the Navigator CLI first. It keeps authentication and any bring-your-own
source keys out of the agent transcript. Do not construct direct upstream API
calls. Navigator API calls are a fallback only when the CLI is unavailable and
the runtime has injected an authenticated credential.

## When to Use Navigator

Use **OSINT tool discovery** for a technique or service you do not already
know: domains, usernames, images, maps, archives, networks, verification,
geolocation, company intelligence, or country-specific investigative tools.

Use **Data Navigator** for structured public records: company filings, court
records, procurement/contracts, sanctions or PEP data, campaign finance,
lobbying, legislation, registries, and entity records.

Use both when structured findings need a follow-up investigative technique.
Use ordinary web research for narrative reporting, current articles, source
pages, and unsupported datasets. Navigator does not replace browser, search,
or archival work.

In a sensitive or offline investigation, do not contact Navigator unless the
operator explicitly authorizes network access. Record the skip and use local
research material or the available offline tool catalogue instead.

## Connection and Capability Checks

First check whether this installation is connected:

```bash
navigator auth status
```

If the command is unavailable or reports no verified connection, stop before
calling Navigator and tell the operator to run `mycroft-navigator` (Mycroft) or
`spotlight-navigator` (Spotlight). The base product and this skill remain usable
for discovery even while Navigator is locked.

The connection flow stores a revocable Navigator PAT in the OS keychain. Never
ask the user to paste that token into a prompt, file, argv, or command. Pro
members receive OSINT tool discovery. Data Navigator is a Lab-only tool; explain
that limitation without treating the whole Pro connection as broken.

## CLI Workflow

### OSINT tools

```bash
navigator tools find "company registry Norway" --json
navigator tools show <tool-id> --json
```

Inspect the returned tool record before using it. Save the tool identifier,
retrieval time, and any documented limitations with the research plan.

### Structured data

```bash
navigator data find "Norway companies" --json
navigator data show no/brreg/enheter
navigator query no/brreg/enheter --input '{"navn":"Equinor","size":5}' --out research/companies.json
```

Always run `navigator data show <source-id>` before a query. It supplies the
current source-specific playbook, input schema, auth requirements, rate limits,
and caveats. Use `--out` for multi-record results.

## Evidence Rules

- Preserve source IDs, non-secret query parameters, timestamps, source URLs,
  warnings, and output paths.
- Treat sanctions, PEP, identity, and entity-resolution matches as leads or
  records to verify—not conclusions about a person or organization.
- Cite the underlying source URL when reporting a result. Navigator's response
  is a retrieval trail, not a substitute for verification.
- If a source needs a BYO key, tell the user the `navigator keys set <name>`
  command. Keys remain in the OS keychain; never request the key value.

## Recovery

- CLI unavailable: use permitted web or local research tools; use Navigator's
  authenticated API only where the runtime explicitly supports that fallback.
- Disconnected, expired, or revoked: ask the operator to use the installed
  product's Navigator reconnect command. Do not request a credential value.
- Data not entitled: state that Data Navigator requires the Lab tier, continue
  with OSINT tools and normal research, and do not claim Data Navigator ran.
