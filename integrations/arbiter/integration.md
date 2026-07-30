# Arbiter — Social-Media Case Studies

**What:** Arbiter is a social-intelligence platform that collects and analyzes social-media activity around events, narratives, people, and organizations. It exposes a browsable menu of **already-collected, already-analyzed case studies** — a bounded corpus (one query, a set of platforms, a date window) with entity/stance analysis, a hierarchical theme tree, actor and community mapping, engagement metrics, and archived copies of posts that may since have been deleted at the origin. The menu is the curated set **plus every completed case study created with the calling API key**; both kinds answer every `/topics/{id}/…` endpoint identically. When the menu has no match, an approved key can create a new user-owned study from a reviewed search plan.

**When to use:**

- The claim under investigation concerns an event, narrative, person, or organization Arbiter has already covered
- You need per-entity stance analysis, theme clustering, or actor/community structure that plain social search cannot produce
- You need to cite a social post that may have been deleted at the origin — Arbiter serves its archived snapshot
- You want a fully-collected corpus over a declared date window rather than an ad-hoc search sample

**When NOT to use:**

- Free-text or cross-study search — there is none. Every read is scoped to one study id or one post id, and `/topics` is the only discovery mechanism.
- Per-post sentiment or language: declared in the schema, never populated.
- Evidence outside a study's declared `window` — `since`/`until` are silently clamped to it, so you can narrow but never widen.
- Live/breaking activity. A study covers what was collected, not what is happening now.
- Anything in sensitive mode — see § Sensitive mode.

**Access:** Self-serve. Sign in to Arbiter, open the user profile → **API Keys** tab, and create a key. The plaintext is shown once; an account holds one active key at a time. Set `ARBITER_API_KEY` and `ARBITER_API_BASE` in `.env`.

**Full API reference:** `docs/arbiter-api.md` — parameters, response fields, the error table, credit rules, pagination, timestamp zones, and the case-study state machine. Machine-readable contract: `GET <ARBITER_API_BASE>/openapi.json` (unauthenticated).

## Environment

`ARBITER_API_BASE` is **deployment-specific**, and the key and the base URL must name the **same** deployment — a key is registered against one deployment's database and will not resolve against another. Create the key on the deployment you point at. **Include the `/api/v1` suffix**; without it every request 404s.

| Deployment | `ARBITER_API_BASE` |
|---|---|
| Production | `https://arbiter.simppl.org/api/v1` |
| Staging | `https://arbiter-staging.simppl.org/api/v1` |
| Local dev | `http://localhost:3000/api/v1` |

If `/topics` returns 404 for every request, the base URL is wrong or the deployment does not serve this API.

## Verb calls

Arbiter is a bearer-token REST API over HTTPS. `invoke-skill("shell-safety")` before curl calls. Validate **every** interpolated id before it is embedded in a URL — `execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id or post_id>"')` — not only where the examples below show it. Never inline free text (queries, titles, agent questions) into a shell string; write it to a file first with `write-file` and pass the file. Responses are JSON with `snake_case` fields.

Every saved response goes to `{CASE_DIR}/research/arbiter-<type>-<slug>-<timestamp>.json`, unmodified.

### 1. Browse the case-study menu — free

```
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics?limit=100" \
  -o {CASE_DIR}/research/arbiter-topics-menu-<timestamp>.json')
```

Each item carries `id`, `slug`, `title`, `description` (the study's search-query text, not prose), `platforms`, `window {from,to}`, `post_count`, and `starred` — `true` for a curated study, `false` for one created with this key. Only completed, non-empty studies are listed; a study still collecting is absent until its analysis modules resolve. Fetch the menu in one call: it is small, positional paging can shift items between pages, and `next_cursor` is `null` on the last page.

### 2. Match the user's query against the menu — offline, free

Do not make the user scan the whole menu. Write their query to a file, then rank the saved menu with the offline matcher (deterministic, no network):

```
execute-shell('python3 integrations/arbiter/run_match.py \
  {CASE_DIR}/research/arbiter-topics-menu-<timestamp>.json \
  --query-file {CASE_DIR}/research/arbiter-user-query.txt --format json \
  --out {CASE_DIR}/research/arbiter-match-<slug>-<timestamp>.json')
```

The result splits the menu into `matches` (score ≥ threshold, closest first, with `matched_terms` explaining why) and `others` (the full remainder). Offer `matches` first with an explicit route to the full list; when `matches` is empty, say plainly that nothing matches and present the full menu anyway. For a browse-all pass, call it with `--query ""`. Studies whose `post_count` is `0`, missing, or null are excluded and counted in `hidden_zero_post` — report that count; `--include-zero-post` is a diagnostic escape hatch only. The matcher is a lexical first pass: re-order or annotate its output with your own judgment, but never hide a positive-post study the user could have wanted.

### 3. Pull the chosen study's posts — metered

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id>"')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/posts?limit=100" \
  -o {CASE_DIR}/research/arbiter-posts-<slug>-<timestamp>.json')
```

**Cost: `max(10, 2 × items_returned)`.** The 10-credit floor makes small pages expensive — a page of 1 costs the same as a page of 5, so always request `limit=100`. Optional narrowing: `platforms=twitter,reddit,google_news`, `since=`/`until=` (ISO-8601, clamped to the study's `window`), `cursor=` from a prior `next_cursor`. `limit` maxes at 100 and **rejects** out-of-range values with 400 rather than clamping.

**Platform enum asymmetry:** this filter accepts **ten** platforms — `twitter`, `youtube`, `bluesky`, `reddit`, `instagram`, `facebook`, `tiktok`, `linkedin`, `google_news`, `fourchan`. Case-study *creation* (§7) accepts only **nine** — the same list without `google_news`. `global` is valid on `?platform=` for entities/themes/report, never on this filter.

Paginate until `next_cursor` is `null` **or** `items` is empty: a short page is not the end, and the final cursor can point at an empty page that is still charged the floor.

### 4. Entity and stance analysis — free

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id>"')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/entities" \
  -o {CASE_DIR}/research/arbiter-entities-<slug>-<timestamp>.json')
```

Returns `entities[{text, label, stance_score, mention_count, narrative?, sample_post_ids}]`. `stance_score` is `-1..1` and is `0` both for a genuinely neutral entity and for an unscored one — never report `0` as a measured neutral. `sample_post_ids` is capped at 5 and resolves via §6; pull the underlying posts before an entity claim reaches findings.

### 5. Hierarchical theme analysis — free

`platform` defaults to `global` (all platforms).

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id>"')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/themes" \
  -o {CASE_DIR}/research/arbiter-themes-<slug>-<timestamp>.json')
```

Each node carries `theme`, `level`, `post_count`, `engagement_total`, optional sentiment/emotion distributions, `sample_post_ids` (cap 5, resolvable), `top_posts` (cap 3), and `children`. A 404 means no theme analysis exists **for that platform** — retry with `?platform=<p>` for each entry in the study's `platforms`, or continue without themes.

Render it locally (no network, so this also works in sensitive mode on an already-saved response):

```
execute-shell('python3 integrations/arbiter/run_themes.py \
  {CASE_DIR}/research/arbiter-themes-<slug>-<timestamp>.json')
```

`--format markdown --out <path>.md` instead produces a self-contained vault note (frontmatter, a mermaid hierarchy diagram, per-theme evidence links). During ingestion (`invoke-skill("ingest")`), copy it into the vault beside the investigation notes and link it from the case index.

### 6. Resolve a specific post — metered

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<post_id>"')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/posts/<post_id>" \
  -o {CASE_DIR}/research/arbiter-post-<post_id>-<timestamp>.json')
```

**Cost: 10 credits** (one result, so the floor). Resolves any post in a study this key can read. Post fields: `id`, `url` (may be dead), `platform`, `author`, `timestamp` (UTC), `text`, `title?`, `tags`, `engagement?` (whole object omitted when the platform reports no metrics), and `archived: true`.

### 7. Consolidated case-study report — free

One call for the whole analyzed surface instead of assembling it yourself. `platform` defaults to `global`.

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id>"')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/report" \
  -o {CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.json')
```

Sections: `top_actors[{actor, group?, dominant_theme?, narrative?, engagement, claims[{post_id, text, url, published_at, engagement}], active_themes}]`, `themes[]` (a thinner tree carrying per-node `top_actors`), `communities[{name, basis: group|shared_theme, actor_count, total_posts, total_engagement, actors, themes}]`, `cross_theme_actors[{actor, themes, theme_count, post_count}]`, and — when the platform has an engagement analysis — `engagement_timeline{points[{date, interactions}], total_interactions, average_interactions, story?}`. Check the `sections {actors, themes, engagement}` booleans before reading an empty array as "no signal": a `false` section is absent analysis, not an error. `platforms[]` lists the per-platform report targets (`global` is never listed but is always requestable). A 404 means neither actors nor themes exist for that platform — retry per platform. Every `claims[].post_id` resolves via §6; `claims[].text` is hard-truncated at 500 characters. Render it locally (offline, sensitive-mode safe), or emit a vault note:

```
execute-shell('python3 integrations/arbiter/run_report.py \
  {CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.json')
execute-shell('python3 integrations/arbiter/run_report.py \
  {CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.json \
  --format markdown --out {CASE_DIR}/research/arbiter-report-<slug>-<timestamp>.md')
```

### 8. Ask the Arbiter case-study agent — 25 credits, synchronous

Arbiter ships a per-study analysis agent with direct access to the corpus, theme statistics, actor metrics, temporal analysis, and claims extraction. Fetch its sample questions first (free, no parameters):

```
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/agent/questions" \
  -o {CASE_DIR}/research/arbiter-agent-questions-<slug>-<timestamp>.json')
```

Returns `questions[{question, category, why_this_helps}]` — treat the list as illustrative and variable-length. Offer them as choices; a free-text question is equally valid. Write the body to a file (`{"question": "<the exact question>"}`, JSON-escaped) — never inline it, since an apostrophe breaks the quoting. Send the question **verbatim**: the selected sample byte-for-byte or the user's own text, never expanded, rephrased, blended, or given extra formatting instructions. Arbiter does its own prompt construction; rewriting the question degrades the answer.

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<topic_id>"')
execute-shell('curl -s --max-time 700 -X POST \
  -H "Authorization: Bearer $ARBITER_API_KEY" -H "Content-Type: application/json" \
  --data @{CASE_DIR}/research/arbiter-agent-question-<n>.json \
  "${ARBITER_API_BASE%/}/topics/<topic_id>/agent" \
  -o {CASE_DIR}/research/arbiter-agent-<slug>-<timestamp>.json')
```

The call **blocks until the agent finishes** — tens of seconds typically, up to ~10 minutes — so always pass a long `--max-time`. Run it in the **foreground and wait for it**; dispatching it detached and moving on loses the answer. If the runtime caps how long a command may run, raise the cap to its maximum for this call and size `--max-time` to return just inside it. A call the runtime killed early is unrecoverable — there is no retrieval endpoint for a finished agent run — so treat it as failed, wait ~30 seconds for the study's active-run slot to clear, and re-ask only with consent (it charges again). If the command was instead moved to the background, poll the `-o` output file until it contains a JSON envelope, then continue. The response carries `answer` (markdown), `run_id`, and `meta.credits_charged`. Ask one question at a time per study; a concurrent question returns 429 with `Retry-After: 30`. Reproduce the `answer` markdown in full and unmodified, tables included, **before** offering any follow-up action — and deliver it as a plain reply with **no tool or verb call attached to the same message**, ending the turn on the answer text. Combining the answer with a follow-up prompt in one message reliably drops the answer into invisible reasoning; an answer that never appears in the visible reply is an unanswered question.

An agent answer is **tool-generated analysis, not primary evidence.** Cite it with `access_method: "tool_output"`, name the tool and the `run_id`, and check any specific claim against the underlying posts (§3/§6) before it enters findings.

### 9. Check the balance — free

```
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/usage" \
  -o {CASE_DIR}/research/arbiter-usage-<timestamp>.json')
```

Returns `credits_balance`, your **effective** `rates` (read these rather than hardcoding 2/10), IST-day and IST-month counters, and `limits.requests_per_minute` (`null` does not mean unlimited).

## Creating a new case study

Use creation only after the user explicitly chooses it — either as a direct option or after §2 found no suitable study. It produces a real Arbiter study owned by the account linked to the key, and it is not an ad-hoc search: the user reviews a grounded search plan before collection starts. Requires a **linked, approved** key. Write artifacts to `{ARTIFACT_DIR}={CASE_DIR}/research` when a case workspace exists; before one exists use a fixed temporary directory and retain the files when the investigation starts. Never write them into the vault. `run_create.py` is offline and stdlib-only; it keeps queries, titles, and phrases out of shell strings.

**Step 1 — collect inputs and create (free).** Ask for the query, the platforms (from the nine creation platforms in §3 — all of them offered in one round, a closed set), and the date window (a preset, or a custom range whose bounds are each validated with `spotlight_safe.py validate-timestamp`). Write the query verbatim to `arbiter-create-query.txt` and any title to `arbiter-create-title.txt`, then build the body offline:

```
execute-shell('python3 integrations/arbiter/run_create.py build-create \
  --query-file {ARTIFACT_DIR}/arbiter-create-query.txt \
  --platforms <validated-comma-separated-platforms> \
  --from <validated-ISO-from> --to <validated-ISO-to> \
  [--title-file {ARTIFACT_DIR}/arbiter-create-title.txt] \
  --out {ARTIFACT_DIR}/arbiter-create-body.json')
execute-shell('curl -s -D {ARTIFACT_DIR}/arbiter-create-headers.txt \
  --max-time 120 -X POST \
  -H "Authorization: Bearer $ARBITER_API_KEY" -H "Content-Type: application/json" \
  --data @{ARTIFACT_DIR}/arbiter-create-body.json \
  "${ARBITER_API_BASE%/}/case-studies" \
  -o {ARTIFACT_DIR}/arbiter-create-response.json')
```

Save headers as well as the body on every write call so `Retry-After` stays available. On 201, read `case_study_id`, `status: "pending"`, and `url`, and validate the id before it goes into a later URL. **This endpoint is not idempotent and accepts no idempotency key** — a retry after a client timeout can leave a duplicate pending study, so record the returned id. Account tier caps platform count, window span, and in-flight studies; those limits surface only as 400s with an explicit message.

**Step 2 — generate the search plan (25 credits).** Body must be exactly `{}`. Start **one** POST with a client timeout above the route's 800-second budget, and poll `/progress` on a separate call while it blocks — if the runtime can run a shell command detached, poll in the foreground; otherwise poll after the POST returns. Never start a second search-plan POST for the same study: each successful call charges again and overwrites the previous plan.

```
execute-shell('curl -s -D {ARTIFACT_DIR}/arbiter-search-plan-headers.txt \
  --max-time 840 -X POST \
  -H "Authorization: Bearer $ARBITER_API_KEY" -H "Content-Type: application/json" \
  --data @{ARTIFACT_DIR}/arbiter-search-plan-body.json \
  "${ARBITER_API_BASE%/}/case-studies/<id>/search-plan" \
  -o {ARTIFACT_DIR}/arbiter-search-plan-<id>.json')
execute-shell('curl -s -H "Authorization: Bearer $ARBITER_API_KEY" \
  "${ARBITER_API_BASE%/}/case-studies/<id>/progress" \
  -o {ARTIFACT_DIR}/arbiter-progress-<id>.json')
execute-shell('python3 integrations/arbiter/run_create.py progress-summary \
  --progress-file {ARTIFACT_DIR}/arbiter-progress-<id>.json --phase plan')
```

`--phase plan` reports plan-step transitions and the `updated_at` age without the zeroed collection headline — collection has not started. Show its output after every poll rather than waiting silently, and never invent a step that is not in the payload.

**Step 3 — review the phrases.** Render the plan and show the numbered phrases to the user as visible text:

```
execute-shell('python3 integrations/arbiter/run_create.py plan-summary \
  --plan-file {ARTIFACT_DIR}/arbiter-search-plan-<id>.json \
  [--removed <accumulated-removed-numbers>]')
```

Phrase numbers are the plan's original positions and never shift, so the block can be re-rendered after each round and the numbers still mean the same phrases. For a removal or addition round, build the options file and present its `options[]`, accumulating selected `original_number` values — never parse labels, never renumber:

```
execute-shell('python3 integrations/arbiter/run_create.py plan-options \
  --mode remove --plan-file {ARTIFACT_DIR}/arbiter-search-plan-<id>.json \
  [--removed <accumulated-removed-numbers>] \
  --out {ARTIFACT_DIR}/arbiter-plan-options-<id>-remove.json')
```

For additions, generate candidate phrases from the plan's summary/categories/entities, write them one per line to a suggestions file, and pass `--mode add --suggestions-file <path>`. State explicitly that suggestions are Spotlight-generated and are **not** phrases Arbiter returned — the emitted `source.disclosure` carries that wording. Additions are an open set: accept user-written phrases too, merging them into `arbiter-added-phrases-<id>.txt` (one per line, exact-deduplicated, first occurrence wins). Never pass the suggestions file to `build-finalize`.

**Step 4 — finalize (100 credits).** Build the body offline; the helper carries the plan's entities into `final_entities`, keeps the surviving phrases, appends the file-backed additions, and drops exact duplicates:

```
execute-shell('python3 integrations/arbiter/run_create.py build-finalize \
  --plan-file {ARTIFACT_DIR}/arbiter-search-plan-<id>.json \
  --remove <accumulated-removed-numbers> \
  [--phrases-file {ARTIFACT_DIR}/arbiter-added-phrases-<id>.txt] \
  --out {ARTIFACT_DIR}/arbiter-finalize-<id>-body.json')
```

Pass `--remove` with the dropped numbers (omit it when the user kept everything) so the request can never drift from the numbering shown; `--keep` still accepts an explicit keep list. POST it once to `${ARBITER_API_BASE%/}/case-studies/<id>/finalize` with `--max-time 60`, saving headers. On success `status` becomes `processing`; report `meta.credits_charged`. Finalize is idempotent by rejection — a second attempt returns 400 and charges nothing — so **never re-finalize because a progress poll looks slow.** The protocol ceiling is 50 phrases; the effective limit is the account tier's (10 or 15), and exceeding it is a 400.

**Step 5 — poll to completion.** Reuse the progress GET roughly every 15 seconds with `--phase collection`, showing the full block each time — total and per-platform post counts, active stage messages, analysis narration, activity age — and call out modules newly `ready` or `failed`.

- `processing` → keep polling.
- `processing` but `updated_at` stops advancing → the run is stalled even though the status does not change; `progress-summary` flags this after ten minutes. Report how long it has been frozen and ask whether to keep watching, open the study in Arbiter for collector detail the API does not expose, start a fresh study (charged as new), or stop and keep the artifacts. **Never repeat a metered call to "retry" a stalled run.**
- `completed` → stop polling. The study is immediately readable by id on every `/topics/{id}/…` endpoint with `starred: false`; nothing needs curating. It may be missing from `GET /topics` for ~10 seconds, so do not gate on the listing.
- `failed` → terminal and unrecoverable through the API: no cancel, resume, or delete, and a failed study is never queryable via `/topics/{id}/…`, so it carries no evidence base. Preserve every artifact, report which platforms collected and which stage stopped, and offer only: start a fresh study, re-run the same scope from the saved create body (same charge), open it in Arbiter, use an existing study from §1 instead, or stop. Do **not** finalize again.

`completed` is derived from every required module having *resolved* — `ready` **or** `failed`. Inspect `analysis.modules[]`; `completed` does not mean everything succeeded.

## Quotas and errors

Every documented error is `{error:{code, message, request_id}}`. **Branch on `code`, never on `message`**, and log `request_id` — it is the only handle that correlates a call to Arbiter's records, and it is in the body, not a header.

| Code | HTTP | Cause | Response |
|---|---|---|---|
| `invalid_request` | 400 | Malformed parameter, cursor, or body; or a tier limit (platforms, span, phrases, in-flight studies). | Fix the request. Never retry unchanged. |
| `unauthorized` | 401 | Missing, malformed, unknown, expired, or revoked key — all indistinguishable. | Check the `Bearer ` prefix; mint a new key. Carries no rate-limit headers. |
| `insufficient_credits` | 402 | Balance below the operation's minimum, no billing project, or (agent/finalize) the linked account is out of its own credits. | Report and continue with other sources. The optional `credits_balance` / `minimum_required` fields appear only on the balance variant. |
| `forbidden_scope` | 403 | Authenticated but not permitted: study outside read scope, out-of-scope post, an unlinked key on the agent, or a write against a study the account does not own. | Do not retry. If the message says the key is not linked to a user account, the user must mint a key from the profile → API Keys tab. |
| `not_found` | 404 | Two distinct causes — see below. | Depends on the cause. |
| `rate_limited` | 429 | Two distinct causes — see below. | Back off and retry. |
| `internal` | 503 | Transient upstream unavailability. There is no 500 and no 502. | Retry with backoff. |

**Two 404s.** (a) `"No such case study."` — the id is not in this key's read scope; do not retry the same id, re-list instead. (b) `"No … analysis is available for this case study and platform."` — the study is readable but that module does not exist for the requested `platform`; retry with `?platform=global` or another entry from the report's `platforms`. Separately, a **just-completed** study can 404 briefly on `/topics/{id}/…` because the readable set is served from a short cache — wait ~15 seconds and retry once before concluding anything.

**Two 429s.** (a) The key's per-minute window is exhausted (default 30 requests / 60 seconds, sliding) — `Retry-After` is sent only sometimes. (b) A concurrent operation on the *same study*: an agent turn already running, or a plan/finalize already running — `Retry-After: 30` always. The concurrency gates are per study and best-effort, so serialize your own calls per study rather than relying on the 429.

**Rate-limit headers are optional.** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, and `Retry-After` are emitted only when a limit context exists for the key. Never require them; honour `Retry-After` when present and fall back to exponential backoff otherwise. `X-RateLimit-Reset` may be absolute epoch-milliseconds *or* a relative duration — if it exceeds the current epoch-ms, read it as absolute.

**Credits.** A failed request never charges: every 4xx and 5xx costs 0. `meta.credits_charged` is authoritative — reconcile against it, not your own arithmetic — and **it can legitimately be `0` on a 200**: the operation was free, it was an idempotent replay, or a transient billing hiccup occurred after the payload was built. A charge *below* the expected amount means the balance was nearly exhausted and the debit was clamped; treat it as a top-up signal, and do not fire metered calls concurrently on a near-empty balance. No endpoint accepts a caller-supplied idempotency key, so a retried metered read is a new billable request.

## Evidence handling

Save every response **verbatim** under `{CASE_DIR}/research/arbiter-<type>-<slug>-<timestamp>.json` before citing anything from it, and record the exact endpoint and parameters so the retrieval is reproducible. Arbiter snapshots posts at collection time, so you are reading its archive rather than the live origin. Cite the **origin post URL** as the source, with `access_method: "archive_copy"`:

```json
{
  "url": "https://twitter.com/user/status/...",
  "type": "social_media",
  "platform": "X",
  "accessed": "ISO 8601",
  "access_method": "archive_copy",
  "access_notes": "Retrieved via Arbiter case study <topic_id> (post <post_id>) — post may no longer be live at origin"
}
```

Archive the underlying origin URLs per `invoke-skill("web-archiving")` where they are still live — Arbiter's copy is supplementary, not primary.

**Everything Arbiter returns is a lead.** Posts, entity stances, themes, actor rankings, and agent answers are inputs to investigation, not conclusions. Never write a `verified`, `confirmed`, or `publishable` status from Arbiter output; verification happens in the fact-checking pass against the underlying material. Two integrity notes to carry into findings: a study's `window` is a **declared** bound, so absence of posts outside it is not evidence of absence of activity in the world; and `stance_score` is `0` for unscored entities as well as neutral ones.

## Combining with the social-media-intelligence skill

`invoke-skill("social-media-intelligence")` for the methodology, browse the menu (§1/§2) and pull the matching study's posts and entities, use `stance_score` and `mention_count` to prioritize which narratives to fact-check, resolve `sample_post_ids` for the evidence trail, then archive every source before citing (`invoke-skill("web-archiving")`).

## Sensitive mode

**Arbiter is a remote API integration and is unusable in sensitive mode.** When `sensitive: true`, the adapter strips the `fetch` and `search` verbs and `execute-shell("curl …")` against remote hosts is guarded at the skill layer — no `/topics`, `/posts`, `/report`, agent, or create call can run, and no new evidence can be pulled. Treat the integration as unavailable and note it at Gate 1 as a sensitive-mode constraint.

The offline renderers keep working on responses saved during an earlier non-sensitive session, because they read local files and never touch the network: `run_match.py`, `run_themes.py`, `run_report.py`, and every `run_create.py` subcommand. Read previously saved responses under `{CASE_DIR}/research/` with `read-file`, or re-render them locally.
