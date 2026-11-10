# ThinkPol — Reddit Archive Search

**What:** ThinkPol keeps a searchable archive of Reddit submissions and comments, including content that was later removed or deleted. Search is exhaustive keyword or exact-phrase matching bounded by a time window, with no relevance ranking. Hydration routes read one account's history, one thread, or a subreddit's context.

**When to use:**

- A lead or claim may have been discussed on Reddit and you need who said what, and when
- An exact quotation has to be traced to its Reddit origin (`mode: "phrase"`)
- A Reddit post or comment has been removed or deleted at origin
- You need one account's posting history or a whole thread, in order
- You want to see when a term spiked before choosing a search window (`/v3/search/popularity`)

**When not to use:** live Reddit state (votes, current flair, whether something is still up — check the `comment_url` / `submission_url` with `fetch`), or any platform other than Reddit.

**Access:** Optional. Preflight reports `unconfigured` until a `THINKPOL_API_KEY` is configured. Indicator Labs writes it to the credential file named by `env_file` in `.spotlight-config.json`; from-repo users set it in their environment, that file, or the checkout `.env` (see `integrations/_credentials.py`). Never print the key or open a credential file.

**Docs:** https://api.think-pol.com/antechamber/docs

## Verb calls

Invoke `shell-safety` first. Write the parameters to a case-local JSON file and run the helper, which sends the key in an `Authorization` header. Never inline search terms, usernames, or ids into the command: `{name}` parts of a route are filled from the params file.

```
write-file("{CASE_DIR}/research/thinkpol-<slug>-params.json", <params JSON>)
execute-shell('scripts/spotlight-python -m integrations.thinkpol get <route> --params {CASE_DIR}/research/thinkpol-<slug>-params.json --output {CASE_DIR}/research/thinkpol-<slug>.json')
```

| Need | `<route>` | Params file |
|---|---|---|
| Search posts and comments | `/v3/search` | `{"terms": ["<phrase>"], "mode": "phrase", "type": "comment", "from": <unix>, "to": <unix>, "order": "desc", "limit": 50}`. `terms` is a list: every value must match the same item. `mode` is `word` (default) or `phrase`; `type` is `comment` or `submission`, omit for both. |
| Volume over time for one word | `/v3/search/popularity` | `{"terms": ["<one word>"], "resolution": "month", "buckets": 24}`. Counts only, never evidence. |
| An account's submissions | `/v2/user/{username}/posts` | `{"username": "<name>", "limit": 100}` |
| An account's comments / replies | `/v2/user/{username}/comments`, `/v2/user/{username}/subcomments` | `{"username": "<name>", "limit": 100}` |
| One submission with recent comments | `/v2/posts/{id}` | `{"id": "<submission id>"}` |
| A submission's comments | `/v2/posts/{id}/comments` | `{"id": "<submission id>", "limit": 100}` |
| One comment / its replies | `/v2/comments/{id}`, `/v2/subcomments/{id}` | `{"id": "<comment id>"}` |
| Subreddit context | `/v2/subreddit/{subreddit_name}` | `{"subreddit_name": "<name>"}` |
| Live Reddit profile | `/v3/user/{username}` | `{"username": "<name>"}` |

Other supported routes: `/v3/user/search`, `/v2/subreddit/search`, `/v2/subreddit/{subreddit_name}/users`, `/v2/posts`. The stream routes (`/v1/streams`) register webhooks and are not reachable through the helper.

**Paging.** `/v3/search` returns `after` and `has_more`: pass `after` back in the next params file. The user and thread lists return `last` and `has_more`: pass it back as `last`.

## Cost control

Every request draws on the key's query quota. Check what is left before a paged collection, and tell the user before any plan that needs more than 20 requests:

```
execute-shell('scripts/spotlight-python -m integrations.thinkpol quota')
```

Narrow with `from` / `to`, `type` and phrase mode before paging.

## Output handling

A search response holds `submissions` and `comments` arrays; each record carries `id`, `author` (lowercased), `subreddit`, `created_utc`, the text, and a `comment_url` or `submission_url`. Save the raw file, then feed cited records into `findings.json` as sources:

```json
{
  "url": "https://www.reddit.com/r/<subreddit>/comments/<submission_id>/comment/<id>",
  "type": "social_media",
  "platform": "Reddit",
  "accessed": "ISO 8601",
  "access_method": "full_text",
  "access_notes": "Retrieved from the ThinkPol archive (route /v3/search, params sha256 …). Origin checked on <date>: live | removed | deleted.",
  "authenticity_flags": []
}
```

- Attribute the observation to ThinkPol. The archive is not independently complete: an empty result is not proof that nothing was posted.
- `fetch` the origin URL and record whether the item is still live. For a live item, archive it per `invoke-skill("web-archiving")`. For a removed or deleted one, the archive copy is the only capture: say so, and seek independent corroboration before any consequential claim rests on it.
- A username is not an identity. Treat account history as a lead; do not name a person from it without independent evidence.
- ThinkPol's terms forbid redistribution or resale of the data: quote what the story needs, do not publish the dump.

## Sensitive mode

ThinkPol requires remote API access, so it is blocked in sensitive mode. Previously saved responses under `{CASE_DIR}/research/` remain readable via `read-file`.
