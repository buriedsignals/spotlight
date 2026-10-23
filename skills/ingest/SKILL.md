---
name: ingest
description: Retired 1.0 ingest. A findings contract 1.0 case is no longer ingested; it is activated or continued as a follow-up 1.1 case.
version: "1.0"
invocable_by: [orchestrator]
requires: []
---

# Ingest (retired)

Spotlight 1.0 ingested a legacy case (findings contract `1.0`) by committing a
signed batch to a local SQLite graph and projecting it into vault notes. That
graph is retired: Engine's update exports any old graph into the wiki
(`spotlight/legacy-graph/`) and deletes it, and nothing writes it any more.

An activated case (`1.1`) never comes here: in solo mode `phase-ingest` runs
`scripts/wiki-destination.py`, and a newsroom-mode case writes nothing locally.

When `phase-ingest` reaches this skill for a `1.0` case, write nothing. Tell
the member:

> "This case predates Batch 1.1 and can no longer be ingested. To keep its
> findings, I can check whether it can be activated, or start a follow-up case
> from its brief that reuses its research and evidence."

Then offer, in this order:

1. `scripts/spotlight-python scripts/migrate-source-expressions.py {CASE_DIR}`
   as a dry run, and `--apply` only when the dry run says the case is eligible
   and the member agrees. An activated case is ingested through
   `phase-ingest` as usual.
2. Otherwise a follow-up 1.1 case from the old brief, with the old
   `research/` and `evidence/` available as local sources.

Normalized propositions, source positions, the old `partially_verified` and
`mischaracterized` verdicts, a finding's `disputed` confidence, flag
dispositions, events and story arcs cannot be migrated mechanically; a person
decides them in the follow-up case.
