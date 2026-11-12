# Newsroom edition: the Editorial MCP contract

A newsroom runs the same Spotlight pipeline as a solo member. Only the
routing differs: in the newsroom edition the Pi harness registers the case,
reads prior knowledge and submits the approved batch through the newsroom's
**Editorial MCP**, a service the newsroom hosts. This page is what the
harness expects of that service. The harness side is in `harness/pi`
(`src/editorial.ts`, `src/tools/editorial.ts`), `scripts/newsroom-package.py`
and `scripts/newsroom_batch.py`.

Every tool reads or submits. None approves: approval is a signed-in person's
action on the newsroom's own review page, and that page's approval must
refuse MCP tokens.

The service's `tools/list` is the authority for the tools it has built and
for their input schemas. The harness reads it at the start and calls only
what it lists. The JSON Schemas in
[`schemas/editorial-mcp/`](../schemas/editorial-mcp/) cover the read tools,
which the harness defines.

## Transport and sign-in

- **MCP Streamable HTTP** (protocol version `2025-11-25`), one endpoint. The
  harness POSTs `initialize`, then `notifications/initialized`, then
  `tools/list` once and one `tools/call` per call, with
  `Accept: application/json, text/event-stream` and `MCP-Protocol-Version`. It
  handles a JSON answer or an SSE stream that carries the response, and keeps
  an `MCP-Session-Id` when the server sets one (a 404 on that session starts
  a new one, once). It does not resume a broken SSE stream.
- **Results:** each tool returns its answer as `structuredContent`, with the
  same JSON as text content. A tool that returns a plain value wraps it as
  `{result: ...}`. A tool error is `isError: true` with a plain message.
- **Token:** `Authorization: Bearer <token>`. The harness never stores it in
  the case, never gives it to a model process (no route's `key_env` may name
  it), and redacts it from logs, errors and every answer before anything is
  recorded or shown to a model. A token shorter than 16 characters is refused
  at start.
- **Revocation:** answer **401 or 403** to a revoked or expired token. The
  harness stops with "the newsroom sign-in is no longer valid … sign in
  again" (exit code 4), with no fallback. It opens a session at every start,
  so a resumed case stops before any work.
- **Redirects** are never followed: a 3xx answer is refused.
- **Unavailable service:** a 3xx, any other HTTP error, a JSON-RPC error or an
  unreadable answer stops the harness with a plain message (exit code 5); a
  newsroom case does not go on without the newsroom's service. A tool's own
  `isError` answer (such as an unknown `record_id`) is not a stop: the agent
  reads it.
- **Untrusted data:** every answer reaches an agent between markers with a
  random tag, as data, never as instructions.

## Launch contract (environment)

| Variable | Meaning |
|---|---|
| `SPOTLIGHT_EDITION=newsroom` | Turns the newsroom path on. |
| `SPOTLIGHT_ROUTES` | The newsroom's model routes (see `harness/pi/README.md`, Routes). |
| `SPOTLIGHT_EDITORIAL_MCP_URL` | The Editorial MCP endpoint: https, or http on a loopback address. Required. |
| `SPOTLIGHT_EDITORIAL_MCP_TOKEN` | The access token. Required. |
| `SPOTLIGHT_CASE_TITLE` | The case name sent to `register_case` (default: the case folder name). |
| `SPOTLIGHT_CONFIG` | The install config; its `knowledge_destination.mode` must be `newsroom`. |

The edition and the install config must agree: a newsroom install refuses
the solo edition and a solo install refuses the newsroom edition, and a case
registered with a newsroom never starts as a solo case.

**Allowlist.** The origins of the routes and of the Editorial MCP are the only
places a request carrying a newsroom token may go. Any other host is refused
before the request leaves. The research lanes' public web fetches carry no
token and are unaffected.

## Tools

| Tool | When | Called by |
|---|---|---|
| `create_id` | Case start and Submit | The harness, once per case and once per batch |
| `register_case`, `get_case_info` | Case start | The harness |
| `search_knowledge`, `get_record`, `traverse`, `coverage` | Brief, Methodology, Investigation cycles, the prior-knowledge research lane | Agents (investigator, fact-checker), the lane runner |
| `start_case_review` | Ingest, after the member requested it | The harness |
| `get_knowledge_batch_info` | Right after a stored batch, and when a submission resumes | The harness |

Every id is the service's. The harness keeps each id in the case before it
uses it, so a lost answer never leaves the service holding a case or a batch
the case does not know.

### `create_id`, `register_case`, `get_case_info`

- `create_id {}` → `{result: <uuid>}`.
- `register_case {case_id, case_name}` → the case, with its `id`. Owner and
  organization come from the sign-in. A `case_id` the service already holds
  is refused.
- `get_case_info {case_id}` → the case, with its `id`; a tool error when the
  service holds no such case for this organization.

The harness calls `create_id`, stores the id with the Editorial MCP's origin
in `data/editorial-case.json`, calls `register_case`, then marks the
reference confirmed. A case that restarts with an unconfirmed reference asks
`get_case_info` first and registers only if the service does not hold the
case. A confirmed case calls none of these again. A case folder whose stored
reference names another case, or another endpoint, does not start.

### `search_knowledge`, `get_record`, `traverse`, `coverage`

Approved knowledge only. Candidates are never returned.

- `search_knowledge {query, kinds?, limit?, cursor?}` → `{records: [{record_id, kind, title?, summary?, organization?, updated_at?}], next_cursor?}`
- `get_record {record_id}` → `{record}`
- `traverse {record_id, depth?}` → `{root, nodes, edges: [{from, to, relation}]}`
- `coverage {record_id}` → `{record_id, coverage}`

Agents treat every record as data and as a lead to re-check, never as a
confirmed fact. One of these the service does not list is answered by the
harness as unavailable and never called.

### `start_case_review`

In: `{request}`, the batch as the service's own input.

- The batch is drafted on the journalist's computer, on the newsroom's own
  model route: the analyst proposes it, the harness builds it and the member
  reviews it on the ingest page, as in the solo edition.
- `request.case_id` is the registered case and `request.batch_id` comes from
  `create_id`. A new record carries a `client_id` (its Batch 1.1 id) at
  version 1; a new version of a record the service holds carries that
  record's `existing_server_id`. Each reference names its target's type and
  one of the same two ids. `scripts/newsroom_batch.py` does the conversion
  from [Batch 1.1](../schemas/knowledge-batch-1.1.schema.json).
- Evidence travels as records (its reference and sha256), not as files.
- Two things in a Batch 1.1 stay in the case: a claim's `origin` beyond its
  `finding_id`, and an assessment's `flags`.

Out, one of:

- An error result (`isError: true`) whose structured content is
  `{errors: [...]}`: the service refused the batch and stored nothing. The
  case stays at Ingest; the harness records the reasons in
  `data/newsroom-submission.json`, tells the member and shows them on the
  ingest page.
- `{batch, assigned_ids: [{type, client_id, uuid}], review_url?, ...}`: the
  batch is stored. `review_url` is the member's: the harness opens it in the
  member's browser (only on one of the newsroom's endpoints) and never
  returns it to a model or keeps it in the case. The harness keeps
  `assigned_ids` in `data/newsroom-ids.json` for the case's next batch.

The service runs its decision checks on the stored batch and shows their
flags on the review page. The harness calls nothing for them.

**Crash safety.** The service stores a `batch_id` at most once. Before
calling `start_case_review` the harness records the intent (`state:
"pending"`, the batch id and the sha256 of the request) in
`data/newsroom-submission.json`, under the case lock and only after the
member requested ingestion on the ingest page. A start that finds a pending
intent asks `get_knowledge_batch_info` whether the service holds that batch,
sends it only if not, and records the answer before anything else runs. A
different batch is refused while one is pending.

### `get_knowledge_batch_info`

In: `{batch_id}`. Out: the batch's `id` and `status`, one of `submitted`,
`reviewed`, `approved`; a tool error when the service holds no such batch.

After a stored batch the harness records the receipts in the case, without
the review address: `receipts/submission-<batch_id>.json` and
`receipts/review-status-<batch_id>.json`. It then records the completed
ingest in `data/ingestion.json` (destination `newsroom`), which the Ingest
phase seals. `scripts/newsroom-package.py` writes all of these under the
case lock; no model process can write `receipts/`, `data/editorial-case.json`
or `data/newsroom-*.json`.

## A runtime without the Pi launcher

A subscription desktop app that runs the skills can work a newsroom case
too. Its install config says `knowledge_destination.mode: "newsroom"`, and
two things change at Ingest:

- The resolver's status carries the newsroom steps in a `newsroom_edition`
  field. It appears only on a newsroom-mode install, so the skills stay the
  solo steps and a solo install reads nothing more.
- After the member requested ingestion, the agent runs
  `node harness/pi/src/newsroom-submit.ts {CASE_DIR}`. It registers the case
  if needed, sends the approved batch and records the receipts with the same
  code the launcher runs. The batch goes from the journalist's computer to
  the newsroom's service and does not pass through a model.

The command reads `SPOTLIGHT_EDITORIAL_MCP_URL` and
`SPOTLIGHT_EDITORIAL_MCP_TOKEN`. It prints the outcome without the review
address, and exits 0 (submitted), 3 (the newsroom refused the batch), 4 (the
sign-in is no longer valid), 5 (the service could not be used) or 2.

The limits of this runtime are its own. The investigation and the analyst's
proposal run on the app's model, which the newsroom does not operate. An
agent with a shell can read what a shell can read, the sign-in token
included, so the token and the review address are kept from the model by
convention there, not by construction. The Pi harness, where no model
process has a shell or the token, is the runtime for a case that needs
those guarantees. The read tools (`search_knowledge` and the others) are
not wired into this runtime.

## What stays out of open-source Spotlight

- **Journalist input is not sent yet.** Open-source Spotlight registers a
  case's material (interviews, source emails, notes, documents) and takes
  passages and claims from it (`scripts/intake.py`), for the solo edition's
  wiki. In the newsroom edition that material stays in the case: the batch
  builder leaves it out until the newsroom's service accepts
  `journalist_inputs`. It never goes to a third-party model route.
- The review of a stored batch, its approval, publication and corrections
  happen in the newsroom's own service and applications.
