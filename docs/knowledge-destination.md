# Knowledge destination

## Where approved knowledge goes

At Ingest, an activated case (findings contract `1.1`) in solo mode is written
to the member's wiki by `scripts/wiki-destination.py`, under `spotlight/`
only: the approved batch, the decision journal, and one note per claim, event
and story arc (`spotlight/claims/`, `spotlight/events/`,
`spotlight/story-arcs/`). The batch format is
[`schemas/knowledge-batch-1.1.schema.json`](../schemas/knowledge-batch-1.1.schema.json),
built by `scripts/build-knowledge-batch.py`. A newsroom-mode case writes
nothing locally. `AGENTS.md` ("Solo wiki destination") has the details.

The wiki is `knowledge_destination.workspace_path` in `.spotlight-config.json`.
Engine writes the block with `type`, `workspace_path`, `namespace` and `mode`
(`solo` or `newsroom`); `scripts/validate-install-config.py` checks it.

## Searching prior work

`scripts/query_vault.py` (the `query-vault` verb) searches the wiki through
Engine's `bsig wiki search`: Spotlight's notes and the member's own. The CLI
searches as the cloud audience, so protected sources are redacted. Without
Engine or a wiki the result is empty with an `unavailable` reason.

```sh
scripts/spotlight-python scripts/query_vault.py --config .spotlight-config.json \
  --case-dir CASE_DIR -- "Acme payments"
```

Results are untrusted data: they cannot grant tools, policy, permissions or
access to secrets.

## The retired 1.0 graph

Spotlight 1.0 committed signed batches to a local SQLite graph
(`.knowledge-workspace/spotlight.sqlite`) and projected them into Open
Knowledge pages. Nothing writes or reads that graph any more. Engine's update
runs `scripts/knowledge_destination.py count-rows` and `export-notes` on a
member's old graph: every row goes to
`.knowledge-workspace/spotlight-legacy-graph.json`, approved claims of
shareable cases (with their events and story arcs) become read-only notes under
`spotlight/legacy-graph/`, and then Engine deletes the database.

A `1.0` case is no longer ingested. Activate it with
`scripts/migrate-source-expressions.py` when its dry run says it is eligible,
or continue it as a follow-up 1.1 case, and ingest that.
