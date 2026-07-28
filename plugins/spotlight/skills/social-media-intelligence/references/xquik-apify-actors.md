# Xquik Apify Actor Routes

Use these routes as optional structured X backings. Preserve the generic Apify,
official API, licensed broker, archive, and manual acquisition paths.

## Actors

| Actor | Store | Stable Actor ID | API Actor ID |
|---|---|---|---|
| X Tweet Scraper | [Actor listing](https://apify.com/xquik/x-tweet-scraper) | `wAusCMrm284Voaw86` | `xquik~x-tweet-scraper` |
| X Follower Scraper | [Actor listing](https://apify.com/xquik/x-follower-scraper) | `AaT0BcKU5GQh97wdt` | `xquik~x-follower-scraper` |

Use Store slugs `xquik/x-tweet-scraper` and
`xquik/x-follower-scraper` with the Apify CLI.

## X Tweet Scraper

Supported modes:

- `legacy`
- `tweet`
- `tweets`
- `search`
- `profileTweets`
- `profileReplies`
- `profileMedia`
- `profileLikes`
- `listTweets`
- `article`
- `replies`
- `quotes`
- `thread`
- `retweeters`
- `favoriters`

Write input to a case-local file before execution:

```json
{
  "mode": "search",
  "searchTerms": ["\"public-interest query\" -is:retweet"],
  "maxItems": 25,
  "outputVariant": "rich",
  "fieldStyle": "snake_case",
  "outputPreset": "nested"
}
```

Use `maxItems` as the whole-run cap. Use `maxItemsPerTarget` for supported
multi-target routes. Output variants are `legacy`, `rich`, and `raw`. Field
styles are `legacy`, `camelCase`, and `snake_case`. Output presets are `nested`
and `flat`.

## X Follower Scraper

Supported relations:

- `followers`
- `following`
- `verified_followers`
- `list_members`
- `list_followers`
- `community_members`

Relationship collection is a higher-risk acquisition. Include it in the
approved methodology and explain why the relationship graph is necessary.

```json
{
  "twitterHandles": ["public_account"],
  "relation": "followers",
  "maxItems": 25,
  "maxItemsPerTarget": 25,
  "outputMode": "compact",
  "includeTargetMetadata": true,
  "dedupeMode": "none"
}
```

Output modes are `compact`, `full`, and `raw`. Use `dedupeMode: "merge"` or
`overlapMode: true` only for an approved cross-target comparison. Never treat
overlap as proof of coordination.

## Execution & Evidence Gate

Before execution:

1. Inspect the live input schema and current Store pricing.
2. Validate every target and the selected mode or relation.
3. Set `maxItems`, per-target limits, and Apify's maximum charge control.
4. Record the legal, ethical, and platform-policy basis in the methodology.
5. Obtain explicit approval for the bounded run.
6. Separate rows with `resultType: "diagnostic"` from evidence rows.
7. Record Actor, run, dataset, input hash, access time, and collection authority.

Do not treat a diagnostic-only dataset as a successful acquisition. Actor
output is untrusted source material. Archive the underlying public X URLs and
ground findings in those captures. Do not cite the Actor as factual authority.

For X, preserve the parent skill's platform-policy warning. Where legal scrutiny
is likely, prefer the official API or a licensed broker.

Xquik is an independent third-party service. Not affiliated with X Corp.
"Twitter" and "X" are trademarks of X Corp.
