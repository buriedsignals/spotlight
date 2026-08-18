# Reviewed knowledge graph

## Claim-note retirement activation

Standalone claim-note creation remains the default. Spotlight suppresses only
new claim notes for a graph-enabled case when
`scripts/validate-install-config.py` validates the configured typed activation
receipt and all four workflow migrations. The gate also verifies the exact
reviewed batch in SQLite, its active case policy, and a completed local
projection receipt. Missing or invalid state leaves legacy ingest unchanged.
Existing claim notes are never rewritten or removed by activation.

Local activation binds the configured Open Knowledge workspace, graph path,
namespaces, provider policy, and workflow-migration receipt:

```sh
python3 scripts/validate-install-config.py --config .spotlight-config.json \
  --issue-local
```

Startup and operator recovery use the same serialized queue hook. Spotlight
discovers case IDs from reviewed batches under the configured cases root:

```sh
python3 scripts/knowledge_projection.py --database /path/to/knowledge.sqlite \
  --root /path/to/OpenKnowledge --drain-mode startup \
  --cases-root /path/to/spotlight/cases \
  --activation /path/to/OpenKnowledge/.knowledge-workspace/spotlight-activation.json
```

Use `--drain-mode operator` for an explicit retry. Both modes reconcile an
abandoned running job to failed before retrying it through the ordinary worker.

Spotlight's Knowledge Destination Port stores reviewed connections from exact
source expressions to atomic claims, canonical events, and editorial story
arcs. It is additive: committing a knowledge batch never edits
`findings.json`, `source-expressions.json`, activated case hashes, or existing
vault claim notes.

```text
source expression refs
        ↕
canonical claim version
        ↕  claim_event_membership
canonical event version
        ↕  event_story_arc_membership
editorial story-arc version
```

The contract adapts the sentence-to-event model in Sannuta Raghu's
[News Atom](https://newsatom.xyz/) and its
[published schema](https://newsatom.xyz/schema). News Atom treats the sentence
as the atom and provides organisation-local `event_id` values. Spotlight adds
the independently assessable claim and story-arc layers.

## Authority and files

The normative batch schema is
[`schemas/knowledge-batch.schema.json`](../schemas/knowledge-batch.schema.json).
The local adapter is
[`scripts/knowledge_destination.py`](../scripts/knowledge_destination.py).
It is the canonical graph for a single-operator local install. Open Knowledge
owns the readable Markdown workspace, indexing, and embeddings; Spotlight owns
the reviewed graph and the rebuildable projection.

A reviewed batch contains:

- canonical, versioned claims with an immutable origin mapping from
  `(project, finding_id, finding_fingerprint)`;
- composite source-expression references preserving the case namespace and
  expression fingerprint;
- canonical events whose identity core records actors, action, object, place,
  and time;
- editorial story arcs;
- first-class, versioned `claim_event_membership` and
  `event_story_arc_membership` records;
- attributable review decisions and provenance;
- an idempotency key for safe replay.

Membership arrays on claims, events, or arcs are deliberately absent. The two
relation tables are the single source of membership truth. Backlinks and search
indexes are derived projections.

## Review and commit

Stage the canonical parsed package against the real case first. Duplicate JSON
keys, inputs above 8 MiB, unresolved case references, and paths outside the
declared roots are rejected:

```bash
python3 scripts/knowledge_destination.py stage \
  --case-root /resolved/cases \
  --case-dir example-case \
  --destination-id newsroom:example \
  data/knowledge-batch.json > data/review-manifest.json
```

The review manifest contains the canonical payload hash, verified source-case
artifact hashes, additions, exclusions, candidate links, and `not_applicable`
dispositions. A trusted operator creates a detached receipt and signs it with
an SSH key whose public key is present in the destination's `allowed_signers`
file. First provision the local database identity:

```bash
python3 scripts/knowledge_destination.py init-local \
  --workspace-root /resolved/OpenKnowledge \
  --db .knowledge-workspace/spotlight.sqlite \
  --destination-id newsroom:example
```

Then create and sign the receipt:

```bash
python3 scripts/knowledge_destination.py approval \
  --manifest data/review-manifest.json \
  --reviewer-id journalist:alice \
  --approved-at 2026-08-18T10:05:00Z > data/approval.json

ssh-keygen -Y sign -f /secure/reviewer-key \
  -n spotlight-knowledge-batch-v1 data/approval.json

python3 scripts/knowledge_destination.py commit \
  --workspace-root /resolved/OpenKnowledge \
  --case-root /resolved/cases \
  --case-dir example-case \
  --db .knowledge-workspace/spotlight.sqlite \
  --expected-sha256 <staged-sha256> \
  --manifest data/review-manifest.json \
  --approval-receipt data/approval.json \
  --approval-signature data/approval.json.sig \
  --allowed-signers trust/allowed_signers \
  --project-after-commit \
  --activation .knowledge-workspace/spotlight-activation.json \
  data/knowledge-batch.json
```

The signature is verified with the fixed
`spotlight-knowledge-batch-v1` namespace and binds reviewer, destination,
payload, and review-manifest hashes. Its signature and signer-policy snapshot
are persisted and rechecked by `verify`. This proves conformance history but
does not make same-user local policy a multi-user authorization boundary. SQLite commits the
batch and outbox intent in one transaction; `--project-after-commit` renders
the corresponding investigation/story Markdown through the local projection
journal. Open Knowledge then observes those files and refreshes its own index.
Replaying
the same idempotency key and payload is a no-op; reusing the key for changed
content fails. A dangling endpoint, conflicting immutable version, missing
prior version, or incomplete declared link rolls back the entire batch.

Candidate object or relation versions may be created by an agent, but they do not appear in canonical
traversal until a journalist-approved membership version exists. Rejection or
supersession creates a new append-only relation version; it never rewrites the
old decision.

The Draft-07 schema requires a supersession pointer on every version after
version 1. Equality between sibling numeric fields is not expressible in this
schema dialect, so the runtime additionally requires
`supersedes_version == version - 1` and rejects gaps.

## Confirming the chain

Traverse from a claim to its approved events and story arcs:

```bash
python3 scripts/knowledge_destination.py traverse \
  --workspace-root /resolved/private-workspace \
  --db knowledge/spotlight.sqlite \
  --claim-id claim:night-discharge:001
```

Traverse in reverse from a story arc to events, claims, and source-expression
references:

```bash
python3 scripts/knowledge_destination.py traverse \
  --workspace-root /resolved/private-workspace \
  --db knowledge/spotlight.sqlite \
  --story-arc-id story-arc:after-dark-river-pollution
```

Candidate object and relation versions are hidden by default.
`--include-candidates` exposes the candidate endpoint chain for a review
interface without making it canonical.

Use the coverage report to distinguish fully linked, event-only, pending, and
reviewed-not-applicable claims:

```bash
python3 scripts/knowledge_destination.py coverage \
  --workspace-root /resolved/private-workspace \
  --db knowledge/spotlight.sqlite
```

An `event_link_disposition` or `story_link_disposition` of `linked` requires an
approved relation. `not_applicable` requires its own human decision. This keeps
coverage measurable without manufacturing events or story arcs for claims that
do not describe one.

Traversal and coverage default to 200 records and accept bounded `--limit`
(maximum 1,000) and `--offset` arguments. Traversal also accepts
`--nested-offset` for each returned event's story/claim collection. Responses
include page metadata. Coverage aggregation runs in SQLite and only the
requested claim page is materialized in Python.

## Local storage safeguards

The reference database and newly created parent directory are owner-only
(`0600` and `0700`), symlinks and escaped roots are rejected, and SQLite uses a
five-second busy timeout. The database carries an explicit physical schema
version, destination identity, canonical batch payload, review manifest, and
approval receipt plus signature evidence. Read commands use read-only SQLite,
reject unknown or altered schema objects, and reject permissive file modes.
Back up a quiescent database with SQLite's online backup API or
`VACUUM INTO`; do not copy a live database file directly.

## Compatibility rules

- Do not add canonical IDs to the current source-expression `finding_link`;
  its exact field set participates in immutable link fingerprints.
- Do not reinterpret case activation or source-expression ingestion
  `event_id` values as editorial event identities. Editorial IDs always use
  the `event:` namespace.
- Findings 1.0, activated 1.1 cases, and expression-less legacy claims remain
  valid.
- Backfills must begin as candidate records with visible ambiguous skips. They
  must not infer a canonical merge from text similarity or embeddings.
- Never treat `--unsafe-local-reference-commit` as multi-user journalist
  authorization. It is an explicit acknowledgement of the same-user local
  operating boundary.

## Knowledge Workspace capability boundary

Projection uses four strict Draft-07 contracts:

- `projection-manifest.schema.json` binds one destination generation to the
  exact graph receipt and record versions, signed-case provenance and artifact
  hashes, signed case-policy revision, desired-set hash, and managed page hashes;
- `projection-job.schema.json` is the deliberately minimal outbox record and
  carries no page body or journal checkpoint;
- `case-policy-receipt.schema.json` is the signed, versioned source of case
  classification, allowed destinations, and provider policy; and
- `knowledge-workspace-package.schema.json` defines managed-block upsert and
  managed-page removal inputs, exact local preconditions, and final receipts.

Spotlight owns this projection transaction locally. It validates ownership
markers, takes a workspace-scoped inter-process lock, rechecks expected hashes,
writes through an atomic replacement, journals recovery, and binds completion
to a content-free receipt. Open Knowledge is not asked to approve the write: it
observes the resulting Markdown and refreshes its own full-text and BGE-M3
indexes.

Search and reads go directly to Open Knowledge. `query-vault` then compares
managed results with the current SQLite projection head and excludes stale or
foreign-case pages. Exact IDs and relationship traversal never depend on
semantic ranking.

Engine remains the installer, updater, configuration writer, and doctor. It is
not in the Spotlight → Open Knowledge runtime data path.
