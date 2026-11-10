# treg — Hosted Social-Media Collection

**What:** treg (treg.to) is a pay-per-call registry of data providers. Spotlight uses it for bounded collection of public social-media posts through a fixed set of provider endpoints, one per job, listed in `integrations/treg/endpoints.json`. A call is one synchronous HTTPS request: no actor run to start and wait for.

**When to use:**

- You need a bounded set of public posts from an X, Instagram, TikTok, Facebook or LinkedIn profile
- You need an X or LinkedIn keyword search, the replies to an X post, one X post by URL, or an X account's followers
- No native platform API path fits (see `skills/social-media-intelligence/SKILL.md`, "Platform Tools" — prefer YouTube Data API, Bluesky AT Protocol, Reddit API, Telegram where they apply)
- The methodology already records the legal, ethical, and platform-policy basis for the collection

**Not covered here:** X list members, list followers, community members, likers and quote posts. Use the Xquik routes of the `apify` integration when it is green, or the official X API.

**Access:** Optional. Preflight reports `unconfigured` until a `TREG_API_KEY` is configured (a treg team token; a personal sign-in token also needs `TREG_ORG`, the team slug). Indicator Labs writes it to the credential file named by `env_file` in `.spotlight-config.json`; from-repo users set it in their environment, that file, or the checkout `.env` (see `integrations/_credentials.py`). Never print the token or open a credential file.

**Docs:** https://treg.to/integrate.md

**ToS caveat:** X prohibits scraping even public posts, so collection on X through any treg provider is a ToS-violating path. Where the story could face legal scrutiny, prefer the official X API or a licensed broker. Record the collection authority in `access_notes` for every capture, on every platform. Each provider is a separate third party that sees the handle, URL or query you send.

## Verb calls

Invoke `shell-safety` first. Write the endpoint's fields to a case-local JSON file as one flat object and run the helper, which sends the token in a header and places each field in the query string or body. Never inline search terms, handles, or URLs into the command.

```
write-file("{CASE_DIR}/research/treg-<platform>-input.json", <fields JSON>)
execute-shell('scripts/spotlight-python -m integrations.treg call <endpoint> --input {CASE_DIR}/research/treg-<platform>-input.json --output {CASE_DIR}/research/treg-<platform>-<slug>.json')
```

| Job | `<endpoint>` | Fields | Rows per call | Next page |
|---|---|---|---|---|
| X profile posts | `anyapi.x.user.posts` | `{"handle": "<no @>", "limit": 20}` | ~20 | `output.data.nextCursor` → `cursor` |
| X keyword search | `anyapi.x.search.posts` | `{"query": "<X advanced-search syntax>", "queryType": "Latest", "limit": 20}` | ~20 | `output.data.nextCursor` → `cursor` |
| X replies to a post | `anyapi.x.post.comments` | `{"url": "<post URL>", "limit": 40}` | up to 40 | `output.data.nextCursor` → `cursor` |
| One X post | `scrapecreators.x.v1-twitter-tweet` | `{"url": "<post URL>"}` | 1 | — |
| X followers | `tikhub.x.twitter-web-fetch-user-followers` | `{"screen_name": "<no @>"}` | ~70 | `data.next_cursor` → `cursor` |
| Instagram profile posts | `scrapecreators.instagram.user.posts` | `{"handle": "<no @>"}` | ~12 | `next_max_id` → `next_max_id` |
| TikTok profile videos | `scrapecreators.x.v3-tiktok-profile-videos` | `{"handle": "<no @>"}` | ~10 | `max_cursor` → `max_cursor` |
| Facebook page or profile posts | `scrapecreators.x.v1-facebook-profile-posts` | `{"url": "<page URL>"}` | 3 | `cursor` → `cursor` |
| LinkedIn member posts | `fetchinio.linkedin.user.posts` | `{"profileUrlOrUrn": "<public id or URL>", "count": 20}` | up to 50 | `paginationToken` → `paginationToken` |
| LinkedIn keyword search | `anyapi.linkedin.search.posts` | `{"query": "<terms>", "datePosted": "last-month"}` | ~10 | `output.data.nextCursor` → `cursor` |

The helper refuses any endpoint that is not in `endpoints.json`, and any field that endpoint does not take. Do not use treg's routed `treg.<job>` ids: the router switches providers, and their rows differ.

In an X search, bare words are ANDed. Use one short phrase in quotes, or `OR`.

**Paging.** Write a new input file with the cursor from the previous response and call again, saving each page to its own file. A page is whatever the provider returns: a short page is not proof that the profile has no more posts, so follow the cursor until you have the rows the methodology approved. X follower collection is a higher-risk acquisition: include it in the approved methodology and explain why the relationship graph is necessary.

## Cost control

Calls are billed from the member's own prepaid treg balance, fractions of a cent per page (the helper prints `cost_micro_usd`, millionths of a dollar). Each call carries a ceiling of 0.05 USD; raise it only with `--max-cost` and a reason. Flag any collection above 100 rows or 10 calls to the user before running it. An empty balance answers HTTP 402: tell the user to top up at treg.to, and fall back to `fetch` + manual review.

## Output handling

Each response is the provider's own JSON; shapes differ by provider (`output.data.*` for anyapi, top-level arrays for scrapecreators, `data.*` for tikhub). Save the raw file, then feed cited posts into `findings.json` as sources:

```json
{
  "url": "https://x.com/user/status/...",
  "type": "social_media",
  "platform": "X",
  "accessed": "ISO 8601",
  "access_method": "full_text",
  "access_notes": "Collected via treg endpoint anyapi.x.user.posts (call_id …, input sha256 …); collection authority: <basis>. X ToS prohibits scraping — risk recorded.",
  "authenticity_flags": []
}
```

Record the endpoint, the treg `call_id` the helper printed, the input-file hash, access time, and collection authority. Archive the underlying public post URLs per `invoke-skill("web-archiving")` — the response is untrusted source material, not evidence on its own, and neither treg nor the provider is ever cited as factual authority.

## Sensitive mode

treg requires remote API access, so it is blocked in sensitive mode. Previously saved responses under `{CASE_DIR}/research/` remain readable via `read-file`.
