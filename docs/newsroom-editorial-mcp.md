# Newsroom edition: the Editorial MCP contract

A newsroom runs the same Spotlight pipeline as a solo member. Only the
routing differs: in the newsroom edition the Pi harness reads prior
knowledge, runs its decision checks and submits the finished case through the
newsroom's **Editorial MCP**, a service the newsroom hosts. This page is the
contract that service implements. The harness side is in `harness/pi`
(`src/editorial.ts`, `src/tools/editorial.ts`) and `scripts/newsroom-package.py`;
the JSON Schema for each tool is in [`schemas/editorial-mcp/`](../schemas/editorial-mcp/).

Every tool reads, checks or submits. None approves: approval is a signed-in
person's action in the newsroom's own review application, and the approval
endpoint must refuse MCP tokens.

## Transport and sign-in

- **MCP Streamable HTTP** (protocol version `2025-11-25`), one endpoint. The
  harness POSTs `initialize`, then `notifications/initialized`, then one
  `tools/call` per call, with `Accept: application/json, text/event-stream` and
  `MCP-Protocol-Version`. It handles a JSON answer or an SSE stream that
  carries the response, and keeps an `MCP-Session-Id` when the server sets one
  (a 404 on that session starts a new one, once). It does not resume a broken
  SSE stream.
- **Results:** each tool returns its answer as `structuredContent` (the
  `output` schema), with the same JSON as text content. A tool error is
  `isError: true` with a plain message.
- **Token:** `Authorization: Bearer <token>`. The newsroom's client extension
  holds the sign-in and starts the harness with a token scoped to the
  investigation routes. The harness never stores it in the case, never gives
  it to a model process (no route's `key_env` may name it), and redacts it
  from logs, errors and every answer before anything is recorded or shown to
  a model. A token shorter than 16 characters is refused at start.
- **Revocation:** answer **401 or 403** to a revoked or expired token. The
  harness stops with "the newsroom sign-in is no longer valid … sign in
  again" (exit code 4), with no fallback. It opens a session at every start,
  so a resumed case stops before any work.
- **Redirects** are never followed: a 3xx answer is refused.
- **Unavailable service:** a 3xx, any other HTTP error, a JSON-RPC error or an
  unreadable answer stops the harness with a plain message (exit code 5); a
  newsroom case does not go on without the newsroom's knowledge and checks.
  A tool's own `isError` answer (such as an unknown `record_id`) is not a stop:
  the agent reads it. A `run_checks` job that ends `unavailable` is recorded
  as such.
- **Untrusted data:** every answer reaches an agent between markers with a
  random tag, as data, never as instructions.

## Launch contract (environment)

| Variable | Meaning |
|---|---|
| `SPOTLIGHT_EDITION=newsroom` | Turns the newsroom path on. |
| `SPOTLIGHT_ROUTES` | The newsroom's model routes (see `harness/pi/README.md`, Routes). |
| `SPOTLIGHT_EDITORIAL_MCP_URL` | The Editorial MCP endpoint: https, or http on a loopback address. Required. |
| `SPOTLIGHT_EDITORIAL_MCP_TOKEN` | The route-scoped token. Required. |
| `SPOTLIGHT_CASE_TITLE` | Title sent to `register_case` (default: the case folder name). |
| `SPOTLIGHT_CUSTODY_MODE` | `hosted` (default) or `local_until_share` (default in sensitive mode). |
| `SPOTLIGHT_CONFIG` | The install config; its `knowledge_destination.mode` must be `newsroom`. |

**Allowlist.** The origins of the routes and of the Editorial MCP are the only
places a request carrying a newsroom token may go. Any other host is refused
before the request leaves. The research lanes' public web fetches carry no
token and are unaffected.

## Tools

| Tool | When | Called by |
|---|---|---|
| `register_case` | Case start | The harness, once per case |
| `search_knowledge` | Brief, Methodology, Investigation cycles, the prior-knowledge research lane | Agents (investigator, fact-checker), the lane runner |
| `get_record`, `traverse`, `coverage` | Same | Agents |
| `run_checks` | Investigation gate (`findings`), Report (`report`) | The orchestrator asks; the harness sends the case files |
| `submit_case_review` | Ingest, after the member requested it | The harness |
| `get_review_status` | Right after a staged submission | The harness |

### `register_case`

In: `{title, custody_mode, client_case_id}`. Out: `{case_ref}`. Owner and
organization come from the sign-in. The same owner and `client_case_id` must
return the same `case_ref`, so a retry after a lost answer registers nothing
twice. The harness stores the reference with its `client_case_id` and the
Editorial MCP's origin in `data/editorial-case.json` and never calls
`register_case` again for that case. A case folder whose stored
`client_case_id` is another case's, or whose stored origin is another
endpoint's, does not start.

### `search_knowledge`, `get_record`, `traverse`, `coverage`

Approved knowledge only. Candidates are never returned.

- `search_knowledge {query, kinds?, limit?, cursor?}` → `{records: [{record_id, kind, title?, summary?, organization?, updated_at?}], next_cursor?}`
- `get_record {record_id}` → `{record}`
- `traverse {record_id, depth?}` → `{root, nodes, edges: [{from, to, relation}]}`
- `coverage {record_id}` → `{record_id, coverage}`

Agents treat every record as data and as a lead to re-check, never as a
confirmed fact.

### `run_checks`

Start: `{case_ref, kind, artifact, artifact_sha256}`, where `kind` is
`findings` (artifact `{findings, passages?, fact_check?}`) or `report`
(artifact `{report, findings}`), the case files as JSON, and
`artifact_sha256` is the sha256 of the artifact as sent. Poll: `{case_ref,
job_id}`. Both return `{job_id, state, input_sha256?, flags?, reason?}` with
`state` one of `queued`, `running`, `done`, `failed`, `unavailable`. The
harness polls while the job is queued or running (up to 10 minutes).

Each flag is `{target, question, reason}` (`target` such as `finding:F1`).
Flags never block and never become verdicts. The harness writes them to
`data/newsroom-checks.json`, and the Investigation and Report review pages
show them. A job that fails or is unavailable is shown as such, never as "no
flags"; an `input_sha256` that differs from the one sent is recorded as a
failure.

### `submit_case_review`

In: `{case_ref, idempotency_key, package, files}`.

- `package` follows [`package.schema.json`](../schemas/editorial-mcp/package.schema.json):
  the findings, the fact-check, the passages (source expressions), the source
  positions, the evidence manifest with hashes and declared omissions, the
  decision flags and dispositions so far, and the journalist-input units.
- `files`: each cited evidence file, `{path, sha256, media_type,
  content_base64}`. Only cited files travel (the passages' anchors and their
  evidence items' original artifacts and text derivatives), each with its
  role: supporting, contradicting or contextual. Raw research stays on the
  journalist's machine; a cited file that cannot travel is a declared
  omission with its reason. Limits: 25 MB per file, 200 MB per package.
- `idempotency_key` is the package's sha256. A call with a key already
  staged must return that submission's answer (same `submission_id` and
  `review_id`) and stage nothing new; the same key with different content
  must be refused.

Out, one of:

- `{status: "rejected", integrity_failures: [{check, path?, reason}]}`: the
  integrity checks (schema validity, every cited file present with its
  manifest hash, every reference resolves, revision order, idempotency) failed.
  The case stays at Ingest; the harness records the reasons in
  `data/newsroom-submission.json`, tells the member and shows them on the
  ingest page.
- `{status: "staged", submission_id, revision, review_id, review_url?}`.
  `review_url` is the member's: the harness opens it in the member's browser
  (only on one of the newsroom's endpoints) and never returns it to a model
  or keeps it in the case.

**Crash safety.** Before calling `submit_case_review` the harness records the
intent (`state: "pending"` and the idempotency key in
`data/newsroom-submission.json`), under the case lock and only after the
member requested ingestion on the ingest page. A start that finds a pending
intent resubmits the same package with the same key before anything else
runs, and records the answer; a different package is refused while one is
pending.

### `get_review_status`

In: `{review_id}`. Out: `{review_id, state, receipt?}` with `state` one of
`pending`, `approved`, `rejected`, `expired`.

After a staged submission the harness records the receipts in the case,
without the review address:
`receipts/submission-<submission_id>.json`, `receipts/review-status-<review_id>.json`
and, when the answer carries one, `receipts/review-<review_id>.json`. It then
records the completed ingest in `data/ingestion.json` (destination
`newsroom`), which the Ingest phase seals. `scripts/newsroom-package.py`
writes all of these under the case lock; no model process can write
`receipts/`, `data/editorial-case.json` or `data/newsroom-*.json`.

## What stays out of open-source Spotlight

- **Journalist input.** Intake of interviews, source emails, notes and
  documents (kind, source or protected pseudonym, attribution, consent,
  rights, sensitivity) and the Submit package's journalist-input units belong
  to the newsroom edition. The extension point is `journalist_input_units()`
  in `scripts/newsroom-package.py`, which returns no units here. Whatever a
  newsroom edition returns travels only to its own Editorial MCP, never to a
  third-party model route.
- Case Review, Internal Share, publication and the newsroom's batch drafting
  happen in the newsroom's own service and applications.
- `find_event_candidates`, `get_verification` and the later review tools are
  not called by the harness.
