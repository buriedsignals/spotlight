---
name: navigator
description: Route investigative research to Navigator when it needs OSINT tool discovery. Use for finding investigative tools and techniques for domains, usernames, images, maps, archives, networks, verification, geolocation, company intelligence, and country-specific research.
version: "1.0"
invocable_by: [investigator, fact-checker, orchestrator, user]
requires: []
---

# Navigator

Navigator is a member service for OSINT tool discovery behind one account
connection: it finds the right investigative tool or technique.

Use the Navigator CLI first. It keeps authentication out of the agent
transcript. Do not construct direct upstream API calls. Navigator API calls are
a fallback only when the CLI is unavailable and the runtime has injected an
authenticated credential.

## When to Use Navigator

Use **OSINT tool discovery** for a technique or service you do not already
know: domains, usernames, images, maps, archives, networks, verification,
geolocation, company intelligence, or country-specific investigative tools.

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
ask the user to paste that token into a prompt, file, argv, or command.

## CLI Workflow

### OSINT tools

```bash
navigator tools find "company registry Norway" --json
navigator tools show <tool-id> --json
```

Inspect the returned tool record before using it. Save the tool identifier,
retrieval time, and any documented limitations with the research plan.


## Evidence Rules

- Preserve source IDs, non-secret query parameters, timestamps, source URLs,
  warnings, and output paths.
- Treat sanctions, PEP, identity, and entity-resolution matches as leads or
  records to verify—not conclusions about a person or organization.
- Cite the underlying source URL when reporting a result. Navigator's response
  is a retrieval trail, not a substitute for verification.

## Recovery

- CLI unavailable: use permitted web or local research tools; use Navigator's
  authenticated API only where the runtime explicitly supports that fallback.
- Disconnected, expired, or revoked: ask the operator to use the installed
  product's Navigator reconnect command. Do not request a credential value.
