# Arbiter API Reference

Arbiter is a social-media case-study platform. A *case study* is a bounded corpus — one search query, a set of platforms, a date window — that has been collected, de-duplicated, indexed and analysed. Data Navigator's hosted partner source exposes this API to trusted Spotlight operators for browsing existing studies, creating new ones, and asking the case-study-scoped agent without disclosing the partner key to the local runtime. It is administrator-only because the upstream key shares account-owned studies and credits.

Arbiter is first-party, so there is no vendor documentation site to defer to: this file is the upstream interface contract (what the endpoints accept and return). `integrations/arbiter/integration.md` is the agent-facing Data Navigator contract (how Spotlight drives the flow).

**`GET <base>/openapi.json` is the machine-readable contract** — OpenAPI 3.1, unauthenticated, `max-age=300`, and authoritative for shapes. Fetch it rather than hardcoding a schema. It is not listed in its own `paths`, so a generated client will not include the operation. § *Divergences* records where observed behaviour differs from the published document.

```
                    ┌─────────────── READ SURFACE ───────────────┐
GET /topics ──▶ pick an id ──┬──▶ /topics/{id}/posts      (metered)
                             ├──▶ /topics/{id}/entities   (free)
                             ├──▶ /topics/{id}/themes     (free)
                             ├──▶ /topics/{id}/report     (free)
                             ├──▶ /topics/{id}/agent      (metered)
                             └──▶ any post id ──▶ /posts/{id} (metered)

                    ┌─────────────── WRITE SURFACE ──────────────┐
POST /case-studies ──▶ pending ──▶ POST /{id}/search-plan ──▶ review phrases
   (free)                            (25 credits, ~minutes)
                                          │
                        POST /{id}/finalize ──▶ processing ──▶ completed
                          (100 credits)          │  poll GET /{id}/progress (free)
                                                 └──▶ failed (terminal, unrecoverable)
                                                          │
                            once `completed`, the study is readable by id
                            on the READ surface above ────┘
```

## At a glance

| Item | Value |
| --- | --- |
| Base URL | deployment-specific, with the `/api/v1` suffix — e.g. `https://arbiter.simppl.org/api/v1` |
| Auth | `Authorization: Bearer <api-key>` on every endpoint except `GET /openapi.json` |
| Content type | `application/json` only, request and response |
| Version | `1.0.0`, path-versioned as `/api/v1`; additive optional fields only within v1 |
| Caching | Every authenticated response is `Cache-Control: no-store`. No `ETag`, no `If-None-Match`. |
| Hosted configuration | Data Navigator resolves the partner key as `arbiter` (`NAVIGATOR_KEY_ARBITER`; legacy server fallback `ARBITER_API_KEY`) and optionally reads `ARBITER_API_BASE`. The source is administrator-only while this key shares one account-owned study namespace and credit pool. |

**Forward compatibility:** ignore unknown response fields and never use a strict/closed-world deserializer. No schema sets `additionalProperties: false`, `required` lists are deliberately short, and optional fields may appear at any time.

**Scope:** the 14 operations enumerated in the OpenAPI document. The `/api/v1` prefix also hosts an undocumented job surface whose error body is a *flat* `{"error": "<string>"}` rather than the nested envelope below — do not write one error handler for the whole prefix, it will crash on `error.code` being undefined.

## Endpoint catalog

| # | Method & path | Credits | Purpose |
| --- | --- | --- | --- |
| 1 | `GET /topics` | free | List readable case studies. The only discovery mechanism. |
| 2 | `GET /topics/{id}/posts` | `max(10, 2 × items)` | Paged raw post corpus, newest first. |
| 3 | `GET /topics/{id}/entities` | free | Entity + stance analysis. |
| 4 | `GET /topics/{id}/themes` | free | Hierarchical theme tree with per-theme stats. |
| 5 | `GET /topics/{id}/report` | free | Consolidated actors, themes, communities, engagement timeline. |
| 6 | `GET /topics/{id}/agent/questions` | free | Curated sample questions for the agent. |
| 7 | `POST /topics/{id}/agent` | **25** | Ask the case-study agent one question. Synchronous. |
| 8 | `GET /posts/{postId}` | **10** | Resolve any cited post id to its full record. |
| 9 | `GET /usage` | free | Balance, effective rates, period counters, advertised limits. |
| 10 | `POST /case-studies` | free | Create a pending study. |
| 11 | `GET /case-studies/{id}` | free | Status and immutable scope. |
| 12 | `POST /case-studies/{id}/search-plan` | **25** | Generate the reviewable search plan. |
| 13 | `POST /case-studies/{id}/finalize` | **100** | Commit reviewed phrases, start collection. |
| 14 | `GET /case-studies/{id}/progress` | free | Poll plan, collection and analysis progress. |

## Authentication

Send the key as a bearer token on every call. The header must literally begin with `Bearer ` and the remainder must be non-empty after trimming.

**Every** credential problem — missing header, wrong prefix, empty token, unknown, expired, or revoked key — returns the identical `401` with nothing to distinguish them, and no `X-RateLimit-*` headers:

```json
{"error":{"code":"unauthorized","message":"Invalid or expired API key.","request_id":"req_…"}}
```

A *valid* key that is over its window returns `429`, not `401`. If credential verification is itself unavailable you get `503` — retryable, not a credential fault.

**Getting a key:** sign in, open the user profile page → **API Keys** tab, create a key (optionally naming it, 1–60 characters), and copy the plaintext immediately — it is shown exactly once. An account holds **one active key at a time**: *regenerate* revokes the old key and mints a replacement; *delete* revokes with no replacement. A key created that way is automatically linked to the Arbiter account, and **`POST /topics/{id}/agent` and the whole write surface require a linked key** — a manually provisioned key may not be linked and returns `403`. Every other read endpoint works with an unlinked key.

**A key is bound to one deployment.** It is registered against that deployment's database and will not resolve against another, so the key and `ARBITER_API_BASE` must name the same deployment.

## Errors

Every non-2xx response on the 14 documented operations uses one envelope:

```json
{
  "error": {
    "code": "insufficient_credits",
    "message": "Insufficient credits for a metered query.",
    "request_id": "req_1f2e3d4c5b6a7988990a1b2c",
    "credits_balance": 4,
    "minimum_required": 10
  }
}
```

`code`, `message` and `request_id` are always present. `request_id` is `req_` + 24 lowercase hex characters and also appears on success as `meta.request_id`; log it on every call and quote it verbatim in support requests — it is the only handle correlating a call to Arbiter's records, and it is **not** exposed as a response header.

**Branch on `code`, never on `message`.** Messages are stable-ish English but are not part of the versioned contract.

| `code` | HTTP | Cause | Remedy |
| --- | --- | --- | --- |
| `invalid_request` | 400 | Malformed parameter, cursor, or body. | Fix the request. Never retry unchanged. |
| `unauthorized` | 401 | Missing/malformed/unknown/expired/revoked key. | Check header format; mint a new key. |
| `insufficient_credits` | 402 | Balance below the operation's minimum; no billing project on the key; or (agent/finalize) the linked account is out of its own credits. | Top up and retry. If billing is not configured, retrying will not help. |
| `forbidden_scope` | 403 | Authenticated but not permitted: study outside read scope, out-of-scope post, unlinked key on the agent, or a write against a study the account does not own. | Do not retry. Use an id from `GET /topics`. |
| `not_found` | 404 | Id not in the readable universe; or on `GET /usage`, no billing project. | Do not retry the same id. Re-list. |
| `rate_limited` | 429 | Per-minute window exhausted, **or** a concurrency conflict. | Back off and retry. |
| `internal` | 503 | Transient upstream unavailability. | Retry with exponential backoff. |

**There is no 500 and no 502** — all internal failures surface as `503 internal` so callers read them as retryable. No 4xx outside `{400, 401, 402, 403, 404, 429}` occurs.

**Conditional extra fields, all optional even when the code matches:** `credits_balance` and `minimum_required` (numbers) appear on the balance-too-low variant of `402` only — the "no billing project" and "linked account out of credits" variants carry neither. `unavailable_months` (`["YYYY-MM", …]`) appears on the `400` from `POST /case-studies` when `reddit` is requested for an uncovered month, and is **not declared in the published spec**.

**Two distinct meanings of 404 on the read surface.** (a) `"No such case study."` — the id is not in this key's read scope; deliberately does not distinguish "does not exist" from "exists but is private to someone else". (b) `"No entity analysis is available for this case study and platform."` (and the `themes` / `report` equivalents) — the study is readable, but that analysis does not exist for the requested `platform`; retry with `?platform=global` or another platform from the report's `platforms` list. Separately, a **just-completed** study can `404` briefly on `/topics/{id}/*` because the readable set is served from a short-lived cache — wait ~15 seconds and retry once before treating it as permanent.

## Rate limiting

| Header | Meaning |
| --- | --- |
| `X-RateLimit-Limit` | The key's per-window request limit. |
| `X-RateLimit-Remaining` | Requests left in the window, floored at `0`. |
| `X-RateLimit-Reset` | When the window resets — **either** absolute epoch-milliseconds **or** a relative duration in milliseconds. |
| `Retry-After` | Seconds to wait. `429` only, and not on every `429`. |

**None of these is guaranteed.** They are emitted only when a limit context exists for the key. The spec declares them on 2xx only, but they appear on most post-authentication responses. Do not build a client that requires them. For `X-RateLimit-Reset`, disambiguate defensively: if the value exceeds the current epoch-ms, treat it as absolute; otherwise as a relative duration.

**Default allowance:** a self-serve key gets **30 requests per 60 seconds**, sliding, per key. Read the advertised limit at `GET /usage` → `limits.requests_per_minute` (`null` when none is advertised, which does **not** mean unlimited).

**Two distinct 429 causes.** (a) The key's per-minute window is exhausted — `Rate limit exceeded for this API key…`, with `Retry-After` sent only sometimes. (b) A concurrent operation on the **same study**: an agent turn already running, or a plan/finalize already running — `Retry-After: 30` always. Treat `Retry-After` as advisory: honour it when present, otherwise fall back to exponential backoff. The concurrency gates are per case study, not per key, and are best-effort — two truly simultaneous calls can occasionally both proceed, so serialize your own calls per study rather than relying on the 429.

## Credits and metering

| Operation | Charge |
| --- | --- |
| `GET /topics/{id}/posts` | `max(minimum_query_credits, per_result_credits × items_returned)` — default **`max(10, 2 × items)`** |
| `GET /posts/{postId}` | one result ⇒ **10** (the floor) |
| `POST /topics/{id}/agent` | flat **25** |
| `POST /case-studies/{id}/search-plan` | flat **25** |
| `POST /case-studies/{id}/finalize` | flat **100** |
| Everything else | **free**; `meta.credits_charged` is literally `0` |

`items_returned` is the length of `items[]` **after** page de-duplication, not the requested `limit`. Read effective rates from `GET /usage` → `rates` rather than hardcoding 2/10 — they can be provisioned per key. Note the gap: `rates` exposes only the per-result read rates, so the flat agent rate is not programmatically discoverable.

- **The 10-credit floor makes small pages expensive.** A page of 1 costs the same as a page of 5: at `limit=100` the marginal cost is 2 credits/post, at `limit=1` effectively 10 — a 10× penalty. Request large pages.
- **A failed request never charges.** Every 4xx and 5xx costs 0. The balance is checked *before* any work, so a `402` is free; the debit is attempted only after a `200` has been assembled.
- **`meta.credits_charged` is authoritative.** Reconcile against it, not your own arithmetic. It can be `0` on a `200` for three reasons: the operation is free; it was an idempotent replay; or a transient billing hiccup occurred after the payload was built — in which case you keep the data free rather than losing the response. No caller-supplied idempotency keys are accepted anywhere, so a *retry* is always a new billable request.
- **Two separate pools gate the write surface.** `finalize` checks the API project's credits (100) *and* the linked account's own product credits (which must be ≥ 1000). A fully funded key can still be blocked by the underlying account.
- **Short debits.** When the balance is nearly exhausted a debit can be clamped to whatever remains, and the pre-check reserves nothing, so concurrent requests can each pass it and collectively settle short. A `meta.credits_charged` below the expected charge is a **top-up signal**, not a discount, and the next call will `402`. Do not fire many metered requests concurrently on a nearly-empty balance.

## Pagination

Only two endpoints paginate, with the same convention.

| Endpoint | Cursor kind | Params | Page size | Total |
| --- | --- | --- | --- | --- |
| `GET /topics` | Opaque, positional | `limit`, `cursor`, `sort` | `1..100`, default 25 | `meta.total_topics` — exact |
| `GET /topics/{id}/posts` | Opaque, keyset | `limit`, `cursor` (+ filters) | `1..100`, default 25 | `meta.total_in_topic` — upper bound only |

- **Cursors are opaque.** Pass back exactly the `next_cursor` string; never construct, decode, mutate, or persist one across schema changes. An unrecognised cursor is `400`. Cursors are not interchangeable between endpoints.
- `next_cursor` is `string | null`; **`null` means last page.** Loop until it is `null`.
- **`limit` rejects out-of-range values with `400`; it does not clamp.** `0`, negatives, `101+`, and non-integers all fail.

Two gotchas specific to `/posts`: a **short page is not the end** (`items.length` can be below `limit` while `next_cursor` is non-null, because pages are de-duplicated after retrieval), and **the final page hands you one more cursor** — following it returns `items: []` with `next_cursor: null` and is still charged the 10-credit floor. Stop when `items` is empty *or* `next_cursor` is `null`, and budget for one possible extra billed request. `meta.total_in_topic` may slightly exceed the number of unique posts; use it as a progress indicator, never as a termination condition.

`GET /topics` paging is positional over a set re-derived per request, so items can shift between pages if the menu changes. The menu is small — fetch it in one call with `limit=100`.

## Platform enums — the asymmetry

Three different platform lists exist on this API. Mixing them up is a `400`.

| Context | Count | Values |
| --- | --- | --- |
| `platforms=` filter on `/topics/{id}/posts` | **10** | `twitter`, `youtube`, `bluesky`, `reddit`, `instagram`, `facebook`, `tiktok`, `linkedin`, **`google_news`**, `fourchan` |
| `platform=` on `/entities`, `/themes`, `/report` | **11** | the 10 above plus **`global`** (the default) |
| `platforms` on `POST /case-studies` (and analysis rows on finalize) | **9** | the 10 minus **`google_news`** |

So `google_news` is readable but not creatable, and `global` is a read-side aggregate that is never valid as a collection target and is never listed in a report's `platforms[]` even though it is always requestable. Filter values are trimmed and lowercased, so `Twitter , reddit` is accepted; an unknown slug or an empty list is `400`.

## Identifiers

Two different id shapes travel on this API. A client that validates both against one pattern will reject valid ids.

| Id | Shape | Examples |
| --- | --- | --- |
| **Case-study id** — `topics[].id`, the `{id}` in every `/topics/{id}/*` path, and `case_study_id` on the write surface | **32 lowercase alphanumeric characters** (`[a-z0-9]{32}`), server-generated | `9f3c1b7a20d84e6fa15c8b0d4e7f2a13` (shape only) |
| **Post id** — `items[].id`, `sample_post_ids[]`, `themes[].top_posts[].id`, `claims[].post_id`, and the `{postId}` on `GET /posts/{postId}` | **Platform-native, 1–512 characters**: letters in either case, digits, and `_`, `-`, `.`, `~`, `%`. Percent-encoding is accepted, so a `%`-bearing id is passed through as-is. | `IDfIYCNsmMI`, `dZj9yXtff_U`, `S-VgRXOzibQ` |

Post ids are minted by the **source platform**, not by Arbiter, so they are not slugs: leading uppercase, mixed case, and embedded `_` or `-` are all ordinary, and a lowercase-slug validator rejects most real ids. `integrations/arbiter/run_id.py validate "<post_id>"` implements the accepting pattern — `^[A-Za-z0-9][A-Za-z0-9._~%-]{0,511}$` — and is what a client should use for post ids; keep a lowercase-slug check for case-study ids only. `Topic.slug` is a display aid and never an identifier at all (§ *Divergences*).

## Timestamps

> **Post publication time is UTC. Arbiter record timestamps are IST (UTC+05:30).**

Anything describing *when content was published or collected in the world* is UTC: `items[].timestamp`, `claims[].published_at`, `engagement_timeline.points[].date` (a UTC calendar day, `YYYY-MM-DD`), `window.from`/`.to`, `last_updated`, `generated_at`, and the `since`/`until` request params (send `Z`-suffixed UTC).

Anything describing *when an Arbiter record was created or touched* is IST: `created_at` on `GET /case-studies/{id}` and every `updated_at` on `/progress`. Parse those defensively — treat an offset-less value as `+05:30` — and never compare them naively against a UTC clock. `GET /usage` daily/monthly counters also roll over on **IST** boundaries, so reconciling against UTC-day billing shows a 5h30m offset. On the write surface, `date_range.from`/`.to` are plain dates with no zone.

## Read endpoints

### `GET /topics`

The discovery menu. **There is no free-text search** — this is the only way to enumerate readable studies. Params: `limit` (`1..100`, default 25), `cursor`, `sort` (`recent` default, `title`, `post_count`). Response: `items[]`, `next_cursor`, `meta.total_topics`.

Each topic carries `id` (32 lowercase alphanumeric characters, which feeds every `/topics/{id}/*` call — see § *Identifiers*), `slug` (a display aid derived from the title — **never an identifier**, and also the single element of every post's `tags`), `title`, `description` (the study's **search query text**, not prose), `platforms`, `window.from`/`.to` (declared collection bounds, UTC), `post_count` (distinct posts), `starred` (`true` = curated, `false` = created with this key — provenance, not capability), and `last_updated` (UTC).

Empty studies are omitted, so every item has `post_count > 0` — but a `post_count` can occasionally be stale; `/topics/{id}/posts` is authoritative. **A study omitted from this menu stays addressable by id.** `sort=recent` is not a global recency merge (curated first, newest-created, then your own); sort client-side on `last_updated` for true global ordering. `sort=title` ascends, `sort=post_count` descends. The menu is bounded to at most 100 curated plus 100 of your own studies. Errors: `400`, `401`, `429`, `503` only — never `402`, `403`, or `404`.

### `GET /topics/{id}/posts`

Paged post corpus, strictly newest-first by publish time. Params: `limit`, `cursor`, `platforms` (CSV, the 10-value list), `since`, `until` (ISO-8601, or `YYYY-MM-DD` widened to the day's bounds). **`since`/`until` are silently clamped to the study's declared window** — you can narrow but never widen, with no error or flag. An inverted range yields an empty page, not a `400`.

Response: `topic_id`, `items[]`, `next_cursor`, `meta.total_in_topic`.

| Post field | Presence | Notes |
| --- | --- | --- |
| `id` | always | The platform's own post id, **not** a slug — mixed case and `_ - . ~ %` all occur (§ *Identifiers*). Feeds `GET /posts/{id}`. |
| `url` | always | Origin URL; `""` when unknown. May no longer be live. |
| `platform`, `author`, `text` | always | `"unknown"` / `""` fallbacks when absent. |
| `timestamp` | always | UTC; `""` when the record has no publish time. |
| `title` | conditional | **Omitted** (not null) when absent. |
| `tags` | always | Exactly one element: the owning study's `slug`. |
| `engagement` | conditional | **Whole object omitted** when the platform reports no metrics. `likes`, `shares`, `comments`, `views` are each independently omitted. |
| `archived` | always | Constant `true` — retrievable from Arbiter even if deleted at origin. |

**Declared but never populated:** `language`, `sentiment` (`{label, score}`), `collected_at`. `sentiment` is the trap — you cannot build a per-post sentiment feature from this surface. A study with no recorded corpus returns `items: []`, `total_in_topic: 0`, and is still charged the floor.

### `GET /topics/{id}/entities` · `/themes` · `/report`

All three are free, unpaged, and take only `platform` (default `global`, the 11-value enum). All three return `topic_id`, the normalized `platform`, a study-level `generated_at`, and `meta`.

**Entities** — `entities[]` with `text`, `label` (e.g. `ORG`, `PERSON`), `stance_score`, `mention_count`, optional `narrative`, and `sample_post_ids[]`. `stance_score` is `-1..1` rounded to 2 decimals, computed as `(positive − negative) / total mentions`, and is **`0` when unscored** — indistinguishable from genuinely neutral. `sample_post_ids` is capped at 5 and feeds `GET /posts/{id}`. No ordering is guaranteed.

**Themes** — `themes[]` (the root's *children*; the root is surfaced separately as `root_theme` and never as a tree element), plus `total_themes`, `theme_levels`, `total_posts`. Each recursive node: `theme`, `level`, `post_count`, `engagement_total`, optional `sentiment_distribution` / `emotion_distribution` (open maps, omitted entirely when absent), `sample_post_ids[]` (cap 5, resolvable), `top_posts[]` (cap 3: `id`, `text`, `engagement`, `author`, `url`), `children[]` (empty array at leaves, never null). Silent caps with no truncation flag: depth 6, total nodes 500. `total_themes` counts the full hierarchy and **can exceed the node count you receive** — compare the two to detect pruning. Resolution of `top_posts[].id` via `GET /posts/{id}` is not contractually promised (unlike `sample_post_ids`).

**Report** — the widest single response: `platforms[]`, `title`, `root_theme`, `top_actors[]`, `themes[]`, `communities[]`, `cross_theme_actors[]`, optional `engagement_timeline`, and `sections`.

- **`sections` is `{actors, themes, engagement}` booleans — check it before reading an empty array as "no signal".** A missing analysis yields a `200` with that section empty and its flag `false`. `404` occurs only when neither actors nor themes exist for the platform.
- `top_actors[]` — cap 20, highest total engagement first: `actor`, optional `group` / `dominant_theme` / `narrative`, an `engagement` block (`total_posts`, `total_engagement`, `avg_engagement_per_post`, five-field `breakdown`), `claims[]` (cap 5), `active_themes[]` (cap 10). `claims[].post_id` feeds `GET /posts/{id}`; `claims[].text` is hard-truncated at 500 characters with no flag.
- `themes[]` here is a **thinner** schema than `/themes` — `top_actors[]` (cap 5) but no `sample_post_ids`, `top_posts`, or distributions.
- `communities[]` and `cross_theme_actors[]` — cap 20 each. `basis` is `group` or `shared_theme` **and both can coexist in one report**; `actor_count`, the totals, and `theme_count` (always ≥ 2) are computed over all members, so they can exceed the capped `actors[]` / `themes[]` (cap 10).
- `engagement_timeline` (only when `sections.engagement`) — `points[]` ascending by `date`, capped at 400, plus `total_interactions`, `average_interactions`, and an optional `story` narrative (truncated at 4000 chars). When the cap engages, `total_interactions` may exceed the sum of `points`. **Bucket granularity varies** — derive the interval from consecutive `date` values rather than assuming days.

### `GET /topics/{id}/agent/questions` · `POST /topics/{id}/agent`

`questions` is free with no parameters, returning `topic_id` and `questions[]` of `question`, `category`, `why_this_helps` — currently six items identical for every study, but neither the length nor the `category` values are pinned. Treat it as illustrative and variable-length with `category` an open string.

The agent call body is `{"question": "<string>"}` — trimmed, then **1–2000 characters**; whitespace-only fails and extra properties are ignored. Response: `topic_id`, `question` (the trimmed form), `answer` (markdown, tables preserved), `run_id` (correlation only), `meta`.

**Synchronous:** tens of seconds typical, up to **10 minutes** before `503`. Set a client timeout of at least ~10 minutes and do not retry aggressively; there is no token streaming. Beyond the key's 25-credit charge the *linked account* spends its own agent quota, so a `402` naming the linked account is possible even with a healthy `GET /usage` balance (that variant carries no balance fields). An unlinked key gets an agent-only `403`, not retryable. Send the user's question verbatim — Arbiter only `.trim()`s it and does its own prompt construction, so client-side rewriting degrades the answer. Two behaviours you do not control: prior turns on the same study and key are carried as context, and an identical earlier question can replay a cached answer while still charging.

### `GET /posts/{postId}` · `GET /usage`

`postId` is trimmed, **1–512 characters**, percent-encoding accepted, and is the platform-native id described in § *Identifiers* — letters in either case, digits, and `_ - . ~ %`, so `IDfIYCNsmMI` and `dZj9yXtff_U` are both ordinary values and a lowercase-slug check would wrongly reject them. There are no query parameters, so you cannot narrow by study or platform. Returns `{item, meta}` where `item` is exactly the Post schema above. Charged the **10-credit floor** on success; `400`/`403`/`404` are free, but the balance pre-check runs first, so a broke key gets `402` even for an id that would have `404`'d. `403` means the post belongs to a *curated* study this key may not read; `404` means it was not found in the curated set — the probe never inspects private studies, so this split cannot be used to detect posts in private data.

`GET /usage` is free with no parameters: `credits_balance`, `rates.per_result_credits` / `.minimum_query_credits` (your **effective** rates), `period.daily` and `period.monthly` counters (`queries`, `results_returned`, `credits_used`, on IST boundaries), and `limits.requests_per_minute` (number or `null`). Counters cover metered **read** queries only — agent questions draw on the same balance but do not appear in `period.*.queries`.

## Write surface

Four steps — create → plan → review → finalize — then poll. Nothing is charged until the plan step.

### `POST /case-studies` — free

| Field | Req | Constraints |
| --- | --- | --- |
| `search_query` | yes | trimmed, **1–500** chars |
| `platforms` | yes | **1–9** items, unique, from the 9-value creation enum |
| `date_range.from` | yes | ISO-8601 **with explicit offset or `Z`** — a bare `2026-06-01T00:00:00` is rejected |
| `date_range.to` | yes | same; **strictly after** `from`; **not in the future** |
| `title` | no | trimmed, 1–200 chars; derived from the query when omitted |

Extra properties are rejected, and **every** body failure collapses into one generic message — there is no per-field detail on create. Policy errors arrive *after* body validation and are how you discover the account's tier: `403` "not approved for case-study creation"; `400` for platform-count, window-span, or in-flight-study caps; `400` + `error.unavailable_months` when `reddit` has no data for a selected month; `503` when platform availability is temporarily unknown (only when `reddit` is requested). Span rule: `floor((to − from) / 86400000) + 1` inclusive days must be ≤ the tier cap.

**Success `201`:** `case_study_id`, `status: "pending"`, `url`, `meta`. **Not idempotent, and no idempotency key is supported anywhere on this surface** — every accepted call creates a new study, so a retry after a client timeout can leave a duplicate. Pending duplicates cost nothing but you must track the returned id yourself; recommended client timeout 30 s. Two observable side effects: an omitted `title` may be replaced by a generated one within seconds, and a supplied `title` is accepted to 200 characters but **stored truncated to 160**.

### `POST /case-studies/{id}/search-plan` — 25 credits

**Body must be exactly `{}`.** Requires the study to be `pending` with no run linked. **Re-generating while still pending is permitted and charges again**, overwriting the previous plan.

Route budget **800 s**, internal soft timeout 600 s (then `503`), so set the client timeout above 800 s. Fire this on one connection and **poll `/progress` on another** — the plan's steps and streaming `display_text` update live while this request blocks.

Response: `case_study_id`, `plan`, `meta`, where `plan` carries `summary` (plain text, HTML stripped), `sources[]` (`{title (nullable), url}`, de-duplicated by URL, empty array when unavailable), **`search_phrases[]`** (the reviewable list you edit and send to `/finalize`, de-duplicated case-insensitively), `entities[]`, `key_phrases[]`, `hashtags[]`, and echoes of the scope (`start_date`, `end_date`, `platforms[]`). No count bounds are enforced on the returned arrays — validate before finalizing.

**Retry safety is the sharpest edge here.** Billing dedupe is keyed to a *server-generated* id, so every client retry is a new charge: two successful calls cost 50. A `503` charged nothing and is safe to retry, but a retry after a *client-side* timeout on a call that actually succeeded regenerates the plan and charges again. **Prefer polling `/progress` until `plan_ready` is `true` over re-calling this endpoint.**

### `POST /case-studies/{id}/finalize` — 100 credits

| Field | Req | Constraints |
| --- | --- | --- |
| `search_phrases` | yes | **1–50** items, each trimmed **1–200** chars, unique |
| `final_entities` | no (`[]`) | **0–200** items, each trimmed **1–500** chars, unique |

Unlike create, size violations name the limit. **The 50-phrase cap is a protocol ceiling, not the effective limit** — the real limit is the account tier's (**15** on research/enterprise, **10** on free and journalist), and exceeding it is a `400`. Note the interaction: the route de-duplicates exactly (`"AI"` and `"ai"` both survive) but the policy layer de-duplicates case-insensitively, so two case variants count as one against the tier limit.

Requires `pending` **with a saved plan**; otherwise `400` — which is also what a duplicate finalize returns. Success `200`: `case_study_id`, `status: "processing"`, `url`, `meta`. Recommended client timeout 60 s.

**Retry safety:** effectively idempotent by rejection — once the study leaves `pending`, a second finalize returns `400` and charges nothing, so you cannot double-charge a study. One recovery hazard: if the call is interrupted *after* the transition to `processing` but *before* the run dispatches, a retry hits the `400` and the study sits in `processing` with nothing behind it. There is no cancel, resume, or delete; a 12-hour stale-run backstop eventually flips it to `failed`. **Remedy: after a finalize whose response you never received, poll `/progress`; if `collection.has_activity` stays `false` and `updated_at` does not advance, treat the study as lost and create a new one.**

### `GET /case-studies/{id}` — free

Returns `case_study_id`, `title` (nullable), `status`, `url`, `created_at` (**IST**), `platforms[]` (normalized), `date_range` (echoed verbatim), `search_query` (trimmed), `meta`. Fully idempotent and safe to poll; use it for coarse status and `/progress` for narration. A malformed, nonexistent, or someone-else's id all return the identical `404` — you cannot distinguish them.

### `GET /case-studies/{id}/progress` — free

Idempotent and safe to poll continuously, **including while `/search-plan` or `/finalize` is blocking.** This is the only completion signal: there are no webhooks. Top level: `case_study_id`, `status`, `plan_ready`, `updated_at`, `steps[]`, `collection`, `analysis`, `meta`.

**`steps[]`** — planning narration with `id`, `title`, `status` (`pending` | `thinking` | `typing` | `complete` | `waitingApproval`), and `display_text`. Ordinary ids are `query-display`, `summary`, `categories`, `timeline`, `documents`, `key-search-phrases`; a **7th step can appear** (`insufficient-prompt-scope`) when the grounded scope came out weak, and `summary` then lands on `waitingApproval`. Treat `steps` as an ordered, variable-length list keyed by `id`, not a fixed six. **`display_text` is an escape-sanitized HTML fragment, not plain text** — `<p>` with `<b>`, `<i>`, `<a href>`, and `""` is a legitimate value. Render it through a sanitizing HTML renderer, never raw. The `summary` step rewrites it on every model chunk while `typing`, so polling it streams the grounded summary instead of waiting out the blocking `/search-plan` response.

**`collection`** — `total_posts`, `has_activity`, `platforms[]`, `stages[]`. `platforms[]` has one row per selected platform, always present, synthesized and zeroed before collection starts (`platform`, `posts`, `status` ∈ `pending`/`active`/`complete`, nullable `message`, nullable IST `updated_at`). `has_activity` is `false` until the collector reports its first row of any kind. `stages[]` is pipeline narration whose stage ids are **camelCase** while the rest of the wire is snake_case (`existingDataCheck`, `collection`, `normalization`, `rerank`, `embeddings`, `nlp`, `indexing`, `ingestion`, `scopeMaterialization`, `agentReady`); it grows over the run, so treat it as a keyed set, not a fixed array.

**`analysis`** — `modules[]` is **empty until finalize**, then seeded with five rows per selected platform (`engagement`, `actors`, `domain-analysis`, `external-links`, `topic-map`); each row has `platform`, `module`, and `status` ∈ `queued`/`running`/`ready`/`failed`. Once the study is `completed`, any still-unresolved module is reported as `ready`. `activity[]` is ordered narration, **newest last**, each with `key`, `status`, `phase`, `platform`, `module`, plain-text `message`, and `posts`; the terminal line is one of `analysis-run:completed` | `:partial` | `:failed`.

**Telling the states apart.** `has_activity` and `plan_ready` exist precisely because the platform and stage rows are synthesized and always present — without them, "not started" and "started, found nothing" are byte-identical.

| Situation | Signature of the progress payload |
| --- | --- |
| Plan not generated yet | `plan_ready: false`; steps `thinking`/`typing`; `has_activity: false`; all platforms `pending`; `total_posts: 0`; `modules: []` |
| Awaiting your finalize | `status: "pending"` **and** `plan_ready: true`; steps `complete` (or `summary` on `waitingApproval`) |
| Collecting, nothing found yet | `status: "processing"`, `plan_ready: true`, **`has_activity: true`**, ≥1 platform `active`, `total_posts: 0` |
| Stalled | `status: "processing"` but `updated_at` does not advance across polls. **This is the only stall signal.** Poll every 15–30 s; treat several minutes of no advance as stalled. |
| Failed | `status: "failed"`. Terminal — stop polling. |

## Lifecycle

```
(none) ──POST /case-studies──▶ pending
pending ──POST /{id}/search-plan (0..n times, charges each)──▶ pending
pending ──POST /{id}/finalize──▶ processing
processing ──all required modules resolved──▶ completed
processing ──orchestration failure──▶ failed
processing ──12h with no activity──▶ failed
completed / failed ──(terminal, no transitions)
```

In `pending` you may re-plan (repeatedly, charging each time), finalize once a plan is saved, and `GET` status/progress. In `processing` only the two `GET`s are permitted: re-finalize and re-plan both return `400`, and there is no cancel, edit, or restart endpoint. In `completed` the corpus becomes readable via `/topics/{id}/*`. `failed` has no recovery path.

**`completed` is derived, not stored:** it flips the moment every required module for every selected platform has *resolved*, where resolved means `ready` **or** `failed` — so a study can report `completed` while some `analysis.modules[].status` is `failed`. Do not read `completed` as "everything succeeded"; inspect `modules[]`. Conversely `failed` on the wire means at least one required module never resolved. Required modules: `engagement`, `actors`, `domain-analysis`, `external-links`, `topic-map` per social platform; `news` alone for a news platform; `group-invite-links` only if a row exists.

**There is no recovery from `failed` through the API** — no delete, cancel, retry, or resume. A failed study remains readable as a status record but is *not* readable via `/topics/{id}/*`, so it carries no evidence base. The remedy is to create a new study; keep the `url` and the failing call's `request_id` for support.

**When your study becomes readable:** only once `status` is `completed`. Before that, `/topics/{id}/*` behaves as if the id is out of scope. After that, those paths resolve fresh per request and are readable **immediately** — your own studies need no curation. The `GET /topics` *listing* has a ~10-second cache and covers at most the 100 most recent of your studies, omitting zero-post studies entirely. **Poll `/progress` until `completed`, then go straight to `/topics/{id}/*` by id; do not gate on the study appearing in `GET /topics`.**

**Tier limits** are properties of the linked account, not the key, and nothing exposes which tier you are on — you discover a limit by receiving its `400`.

| Tier | Platforms | Span (inclusive days) | Phrases | Concurrent running |
| --- | --- | --- | --- | --- |
| Free | exactly 1 | 15 | 10 | 3 |
| Journalist | 1–3 | 42 | 10 | 3 |
| Research / Flex | 1–3 | 56 | 15 | 3 |
| Enterprise | 1–3 | 56 | 15 | 3 |

Also gated: the account must be email-verified and access-approved, and must hold ≥ 1000 of its own product credits at finalize time (independent of the API project's 100-credit charge). **The schema ceilings are not the policy limits** — `platforms: maxItems 9` and `search_phrases: maxItems 50` are protocol bounds well above every real tier, so a 4-platform request is schema-valid and then rejected. Build any picker to the tier limit, not the schema.

## What this API does not do

**No streaming and no webhooks, anywhere.** Every response is a single buffered JSON body — the agent returns the completed answer, so configure long client *and proxy* read timeouts — and polling `/progress` is the only completion signal (it consumes the per-minute allowance but costs 0 credits).

Also absent: full-text or cross-topic search (`/posts` filters only by `platforms`, `since`, `until`, and every read is scoped to one study or post id); date ranges outside a study's declared window; bulk export, CSV/NDJSON, or sparse fieldsets; per-post sentiment, `language`, and `collected_at` (declared, never populated); any `PATCH`/`PUT`/`DELETE`, so no study can be edited, cancelled, or deleted; access to others' private studies; non-query case-study creation; conditional requests or caching (`ETag`, `Last-Modified` — everything is `no-store`); caller-supplied idempotency keys; a request-id *header* (read it from the body); a `500` status (all internal faults are `503 internal`, so treat them as retryable); any dry-run or test mode (a `finalize` spends 100 real credits and starts a real run); and any credit-reservation model.

## Divergences from the published spec

The OpenAPI document is authoritative for *shapes*; these are the places where observed behaviour differs or the spec is silent. Build against this list.

- `limit` on both paged endpoints **rejects** out-of-range values with `400`; the `minimum`/`maximum` reads like clamping and is not.
- The 11-value `?platform=` enum and the 10-slug `platforms=` enum are both enforced with `400`, but neither is declared as an enum.
- `Topic.entity_count`, `Post.language`, `.sentiment`, and `.collected_at` are declared and **never returned**.
- `error.unavailable_months` is emitted on the Reddit-availability `400` but not declared in the envelope, so a strict validator rejects a valid response.
- `title` on create is accepted to 200 characters and **stored truncated to 160**.
- `progress.updated_at` is typed nullable but is always present in practice — use `has_activity` / `plan_ready` for "has anything happened", not `null`. `plan_ready`, `collection.has_activity` and `steps[].display_text` are likewise optional in the spec and always emitted today.
- `steps[].display_text` is described as `<p>` + `<a>` but also contains `<b>` and `<i>`; sanitize rather than allow-listing tags. `stages[].status` / `.stage` and `activity[].status` are open strings — tolerate unknown values.
- On `/posts`, a non-null `next_cursor` can point at an empty, still-billed page, and a short page is not the end. Neither is documented.
- `GET /openapi.json` is absent from the spec's own `paths`, and its unauthenticated status is undocumented.
- `/usage` `rates` exposes only the per-result read rates; the flat agent rate is not programmatically discoverable.
- `sort=recent` is curated-first then your-own, each newest-created — not a merged global recency.
- Rate-limit headers are declared on 2xx only, appear on most post-authentication responses, and none is guaranteed.

Also unpublished as guarantees: typical wall-clock time for a plan or a full run, and the default request timeout for routes without an explicit budget. The client timeouts recommended above are conservative suggestions, not measured figures.

**Not identifiers despite looking like them:** `Topic.slug` (a display aid that also appears in `Post.tags`), `top_actors[].actor` and every actor name in `communities`/`themes` (labels), `run_id` (correlation only), and `themes[].top_posts[].id` (a post id, but resolution is not promised). Cursors are contractual only on the endpoint that issued them.

## Evidence handling and sensitive mode

Arbiter serves archived snapshots, so posts remain retrievable after deletion at origin. Cite the origin `url` as the source with `access_method: "archive_copy"`, say so in `access_notes`, and record the Arbiter case-study id for reproducibility. Two integrity notes belong in findings: a study's `window` is a **declared** bound, so absence of posts outside it is not evidence of absence of activity; and `stance_score` is `0` both for genuinely neutral and for unscored entities, so `0` is not a measured neutral. Everything the API returns is a lead — nothing from it may be recorded as a `verified`, `confirmed`, or `publishable` status.

Arbiter requires remote API access, so it is **unavailable in sensitive mode**. Previously saved responses under `{CASE_DIR}/research/` remain readable, and the offline renderers in `integrations/arbiter/` still work on them.

## See also

- `integrations/arbiter/integration.md` — the agent-facing behaviour contract: exact verb calls, the review flow, evidence handling.
- `docs/integrations.md` — operator overview of all integrations.
- `GET <ARBITER_API_BASE>/openapi.json` — machine-readable OpenAPI 3.1.
