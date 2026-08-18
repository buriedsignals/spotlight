# Sensitivity — local-only investigations and durable knowledge

Local knowledge activation does not create an isolation boundary. A
`local_conformance` receipt can enable the local graph/projection workflow but
cannot assert multi-user confidentiality or authorization isolation. Spotlight
and Open Knowledge run with the current user's filesystem access.

Status: current behavior plus a deliberately unimplemented design boundary.

## Current behavior

Spotlight is an OSINT investigation orchestrator for open material. When
`sensitive: true` is active, the runtime adapter removes live `fetch` and
`search` access. The case must then work only from material already present in
the case workspace and the configured local knowledge project.

This mode reduces accidental network disclosure. It is not a confidentiality
boundary against an agent that can read local files or execute shell commands.

Spotlight uses OpenKnowledge for durable local knowledge:

- the CLI initializes and maintains the project;
- the MCP server supplies runtime search, read, and approved write operations;
- Markdown remains portable and inspectable on disk;
- case workspaces remain outside the durable knowledge project.

The public installer initializes the configured vault with:

```bash
open-knowledge --cwd "$SPOTLIGHT_VAULT_PATH" init --mcp --local-only
```

Engine-managed installs perform the equivalent operation through sealed,
transactional OpenKnowledge steps.

## What sensitive mode does not provide

Sensitive mode does not provide encryption, operating-system access control,
anonymization, redaction, declassification workflow, or protection from a
frontier runtime that can already read the same local files. Source-expression
snapshots improve auditability but do not change that boundary.

For source-protected documents, unpublished identities, off-record interview
notes, or material with a stronger threat model, use an operational boundary
that actually enforces separation: a distinct machine, OS account, encrypted
volume with independent access control, or an offline environment.

## Separate sensitive knowledge project

A second OpenKnowledge project is a possible future ingest target, but it is
not implemented by Spotlight today. If implemented, it must meet all of these
conditions:

- explicit user opt-in and an explicit destination for every sensitive write;
- no automatic links or writes from the sensitive project into the default
  project;
- separate MCP registration and clear runtime routing;
- no claim that project separation alone prevents a shell-capable runtime from
  reading both projects;
- tests covering configuration, ingestion, doctor output, and uninstall;
- documentation that keeps operational isolation as the real security
  boundary.

Until those conditions are implemented and reviewed, do not rely on a
"sensitive vault" setting or command. Preload approved local material into the
case, enable sensitive mode, and run the case inside the operational boundary
appropriate to the reporting risk.

## Source expressions

Source expressions preserve exact quoted text, attribution, case-local paths,
and hashes. They improve evidence auditability and can make a case artifact
more revealing. In sensitive mode they may be created only from already
present case-local material. Missing text must remain an explicit evidence gap;
it must never be reconstructed from memory.

## Related documentation

- [Runtime wiring](runtimes.md)
- [Investigation pipeline](investigating.md)
- [Structure and Knowledge Workspace Port](structure.md)
- [Disclaimer and scope limits](../DISCLAIMER.md)
