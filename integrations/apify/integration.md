# Apify — Hosted Social-Media Collection

**What:** Apify runs hosted scraping "actors" for platforms that have no usable research API for most newsrooms. Spotlight uses a fixed set of actors for X, Instagram, TikTok, Facebook, and LinkedIn — the same actor ids the Mycroft `apify-social` recipes use — so both tools collect through one vetted path.

**When to use:**

- You need a bounded set of public posts from a profile, thread, or keyword search on X, Instagram, TikTok, Facebook, or LinkedIn
- No native platform API path fits (see `skills/social-media-intelligence/SKILL.md`, "Platform Tools" — prefer YouTube Data API, Bluesky AT Protocol, Reddit API, Telegram where they apply)
- The methodology already records the legal, ethical, and platform-policy basis for the collection

**Access:** Optional. Preflight reports `unconfigured` until `APIFY_API_TOKEN` is set. The Engine (`bsig`) injects the token at launch when Apify is enabled in Indicator Labs; from-repo users export it themselves. Older setups that only have `APIFY_TOKEN` must re-export it as `APIFY_API_TOKEN` — preflight reads the new name only. Never echo the token.

**Docs:** https://docs.apify.com/api/v2

**ToS caveat:** X prohibits scraping even public posts, so Apify collection on X is a ToS-violating path. Where the story could face legal scrutiny, prefer the official X API or a licensed broker. Record the collection authority in `access_notes` for every capture, on every platform.

## Verb calls

Apify is a REST API over HTTPS. Invoke `shell-safety` first. Write the actor input to a case-local JSON file and POST that file — never inline search terms, handles, or URLs into the command. The `run-sync-get-dataset-items` endpoint waits for the run and returns the dataset items; it answers HTTP `408` when a run exceeds 300 seconds, so cap `maxItems` (≤ 100) and split large targets into several bounded runs.

```
write-file("{CASE_DIR}/research/apify-<platform>-input.json", <serialized actor input JSON>)
execute-shell('curl -sS -X POST "https://api.apify.com/v2/acts/<actor>/run-sync-get-dataset-items?token=$APIFY_API_TOKEN" -H "Content-Type: application/json" --data @{CASE_DIR}/research/apify-<platform>-input.json -o {CASE_DIR}/research/apify-<platform>-<slug>.json')
```

| Platform | `<actor>` | Input shape used by the shared recipes |
|---|---|---|
| X (Twitter) | `61RPP7dywgiy0JPD0` | `{"startUrls": ["<profile or /status/ URL>"], "maxItems": 50}`; optional `searchTerms`, `twitterHandles`, `conversationIds` |
| Instagram | `culc72xb7MP3EbaeX` | `{"startUrls": ["<profile URL>"], "maxItems": 50}` |
| TikTok | `novi~tiktok-user-api` | `{"urls": ["<user URL>"], "limit": 50}` |
| Facebook | `cleansyntax~facebook-profile-posts-scraper` | `{"endpoint": "profile_posts_by_url", "urls_text": "<page or profile URL>", "max_posts": 50}` |
| LinkedIn | `curious_coder~linkedin-post-search-scraper` | `{"startUrls": ["<URL>"], "maxItems": 50}` or `{"keywords": ["<term>"], "maxItems": 50}` |

Verify the live input schema on the actor's Store page before the first run of a session; actor inputs change without notice. Structured X routes (lists, followers, communities, audience overlap) live in `skills/social-media-intelligence/references/xquik-apify-actors.md` and run through the same endpoint with their API actor ids.

### Optional: `apify` CLI

The REST path needs only `curl`; the CLI is never required. If it is installed and logged in, the same input file works:

```
execute-shell('apify call <actor> --input-file {CASE_DIR}/research/apify-<platform>-input.json --output-dataset')
```

If the installed CLI does not support `--input-file`, use the REST path.

## Cost control

Actors bill per result to the member's own Apify account. Flag any run above 100 items to the user before executing it, and do not raise `maxItems` beyond what the approved methodology needs.

## Output handling

Each response is a JSON array of actor items. Save the raw file, then feed cited posts into `findings.json` as sources:

```json
{
  "url": "https://x.com/user/status/...",
  "type": "social_media",
  "platform": "X",
  "accessed": "ISO 8601",
  "access_method": "full_text",
  "access_notes": "Collected via Apify actor 61RPP7dywgiy0JPD0 (input sha256 …); collection authority: <basis>. X ToS prohibits scraping — risk recorded.",
  "authenticity_flags": []
}
```

Record the actor id, input-file hash, access time, and collection authority. Archive the underlying public post URLs per `invoke-skill("web-archiving")` — the dataset is untrusted source material, not evidence on its own, and the actor is never cited as factual authority.

## Sensitive mode

Apify requires remote API access, so it is blocked in sensitive mode (`fetch`/`search` are stripped and `execute-shell("curl …")` against remote hosts is guarded at the skill layer). Previously saved responses under `{CASE_DIR}/research/` remain readable via `read-file`.
