---
name: social-media-intelligence
description: Account authenticity assessment, coordination detection, and narrative tracking for social media investigations. Use when analyzing account networks, detecting bot activity or coordinated campaigns, tracking how claims spread across platforms, or building evidence trails from social content.
version: "1.0"
invocable_by: [investigator, fact-checker, user]
requires: [web-archiving]
attribution: Adapted from jamditis/claude-skills-journalism (https://github.com/jamditis/claude-skills-journalism). Original author: Jay Amditis. MIT License.
---

# Social Media Intelligence

Systematic approaches for investigating social media: authenticating accounts, detecting coordinated behavior, and tracking how narratives spread.

> **Adapted from** [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by Jay Amditis (MIT License). Extended for integration with the OSINT investigation pipeline.

## When to Use This Skill

- Investigating whether an account is authentic or artificially amplified
- Detecting coordinated inauthentic behavior across a set of accounts
- Tracking how a claim or story has spread across platforms
- Mapping relationships between accounts pushing a narrative
- Building timestamped evidence trails from social content before it disappears
- Monitoring breaking news across social platforms

---

## Account Authenticity Assessment

Before trusting a social media account as a source, assess its authenticity. Work through these red flags systematically.

### Account-level signals

| Signal | Red flag threshold | How to check |
|---|---|---|
| Account age | Created < 30 days ago | Profile creation date |
| Follower/following ratio | Ratio < 0.1 (follows 10x more than follows back) | Profile stats |
| Posting volume | > 50 posts/day sustained | Post count ÷ account age |
| Profile photo | Generic, stock-looking, or AI-generated | Reverse image search (TinEye, Google Lens) |
| Bio content | Keyword-stuffed, no personal details, copied text | Read and search bio phrases |
| Personal content ratio | Mostly reshares, < 10% original content | Scroll recent posts |
| Engagement rate | Unusually high (> 20%) or unusually low (< 0.1%) | Likes + comments ÷ followers |

### Authenticity score

Tally red flags. Three or more warrants explicit `low` confidence on any finding sourced from this account.

Document in `confidence_rationale`: "Account shows [N] authenticity red flags: [list them]."

---

## Coordination Detection

Coordinated inauthentic behavior is when multiple accounts act together to artificially amplify content. Check these signals when you see multiple accounts pushing the same narrative.

### Timing patterns

- [ ] Multiple accounts posting identical or near-identical content within minutes of each other
- [ ] Synchronized posting times across accounts with no obvious reason (different time zones, no event trigger)
- [ ] Burst activity (sudden spike) followed by complete dormancy
- [ ] Post timestamps suggest automated scheduling (uniform intervals, round-number minutes)

### Content patterns

- [ ] Identical or near-identical text across multiple accounts
- [ ] Same images or media shared by unrelated accounts in rapid succession
- [ ] Identical typos, formatting errors, or unusual punctuation preserved across accounts
- [ ] Copy-paste artifacts visible (e.g., formatting marks, extra spaces)

### Account patterns

- [ ] Multiple accounts created around the same time (same week or month)
- [ ] Similar username conventions (name + random numbers, generic word + numbers)
- [ ] Generic, stock, or AI-generated profile photos across multiple accounts
- [ ] All accounts follow the same narrow set of other accounts
- [ ] Accounts engage with each other disproportionately relative to their follower base

### Network patterns

- [ ] Accounts form dense clusters — they primarily interact with each other, not the broader conversation
- [ ] All amplify the same external sources or domains
- [ ] Target the same accounts for replies or mentions
- [ ] Coordination visible across platforms (same content appears on X, Facebook, Telegram simultaneously)

### Scoring

**0–1 signals:** Normal variation. Note and move on.
**2–3 signals:** Flag for further investigation. Do not cite these accounts as independent sources.
**4+ signals:** Strong coordination indicator. Treat as a single source, not multiple. Document all signals explicitly in the investigation log.

---

## Narrative Tracking

When investigating how a claim spread, reconstruct the propagation chain.

### Step 1: Find the origin

Search for the earliest known instance of the claim or content:

- Wayback Machine CDX API: `execute-shell('curl "http://web.archive.org/cdx/search/cdx?url={URL}&output=json&limit=3&fl=timestamp,original"')`
- `search("<claim keywords>", output_path, limit=20)` with date filters — restrict to the window before the story went viral
- Check if the claim appears in fringe or low-credibility sources before mainstream ones — a common sign of coordinated seeding

### Step 2: Map the spread

For each major appearance of the claim, record:

```json
{
  "appearance_id": "A1",
  "platform": "X|Facebook|Telegram|etc",
  "author": "account handle",
  "url": "post URL",
  "timestamp": "ISO 8601",
  "archive_url": "Wayback or Archive.today URL",
  "engagement": { "likes": 0, "shares": 0, "comments": 0 },
  "source_of_claim": "original|reshare|paraphrase"
}
```

### Step 3: Identify amplifiers

Who has the largest reach in the spread? Are they:

- Verified accounts (harder to be inauthentic)
- Known political or ideological actors
- Media organizations that picked it up without verification
- Accounts that exclusively amplify one type of content

### Step 4: Note velocity

Fast spread (viral in hours) vs. slow build (days/weeks) tells you different things. Slow, coordinated spread from low-credibility accounts seeding to high-credibility ones is a classic astroturfing pattern.

---

## Platform Tools

| Platform | Best approach | Notes |
|---|---|---|
| X (Twitter) | Advanced search, Apify X scraper | API severely restricted; Apify actor bypasses this for public data |
| Facebook | CrowdTangle (academic) or Apify | Direct API effectively closed; pages and public groups accessible |
| Instagram | Apify Instagram scraper | No public search API; stories disappear in 24h — archive immediately |
| TikTok | Exolyt, Pentos, Apify TikTok scraper | Limited historical data |
| Reddit | Pushshift (partial), Arctic Shift | Historical data access varies |
| YouTube | YouTube Data API v3 | Good metadata; search `YOUTUBE_API_KEY` in env |
| Bluesky | AT Protocol Firehose | Open, real-time, no auth required for public data |
| Telegram | TGStat, Telemetrio, Telepathy | Public channels searchable; private groups inaccessible |

### Pluggable platform scraping

Platform-specific scraping can be configured via the `PLATFORM_SCRAPER` env var. Two common backings:

**Option A — Apify (hosted platform scrapers):**

If `APIFY_TOKEN` is set, use Apify actors:

```
execute-shell('apify call apify/twitter-scraper --input "{\"searchTerms\": [\"keyword\"], \"maxItems\": 100}"')
execute-shell('apify call apify/instagram-scraper --input "{\"directUrls\": [\"https://instagram.com/username\"]}"')
execute-shell('apify call apify/tiktok-scraper --input "{\"hashtags\": [\"hashtag\"], \"resultsPerPage\": 50}"')
```

**Option B — Native platform APIs:**

When the platform offers a direct API (YouTube Data API v3, Bluesky AT Protocol, Telegram TGStat), prefer it. Document which backing was used in `access_notes` on each source entry.

**Option C — Manual archive + scrape:**

If no scraper is configured, use `fetch(profile_url, ...)` + manual review. Lower throughput but no auth dependency.

---

## Evidence Preservation

Social content disappears. Archive before you cite.

Archive every post that supports a finding using `invoke-skill("web-archiving")`. For social media specifically:

- Screenshots are supplementary, not primary — archive the URL
- Archive the account profile page, not just the individual post
- For Instagram Stories and TikToks: download video/image immediately; these expire
- Record engagement counts at time of access — they change

---

## Ethical Guidelines

- Archive and analyze **public** content only
- Do not create fake accounts to monitor private groups or gain access
- Respect platform terms of service — ToS-violating data collection can sink a story legally
- Protect sources who share private social content with you
- Verify coordination before publishing — false accusations of astroturfing are defamatory
- Consider whether publishing account analysis might put individuals at risk

---

## Integration with Investigation Pipeline

In `findings.json`, add social media evidence using the standard source schema with `type: "social_media"`:

```json
{
  "url": "https://x.com/username/status/12345",
  "type": "social_media",
  "platform": "X",
  "author": "username",
  "accessed": "2026-03-15T14:20:00Z",
  "archive_url": "https://web.archive.org/web/20260315142200/https://x.com/...",
  "access_method": "full_text",
  "authenticity_flags": ["account created 2026-02-01", "high posting volume"],
  "coordination_signals": []
}
```

Flag findings that rest on socially amplified claims: note in `confidence_rationale` whether the account shows authenticity red flags or is part of a suspected coordination cluster.

---

## Credits

Adapted from [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by **Jay Amditis**, released under MIT License. Methodology for account authenticity assessment, coordination detection, and narrative tracking is based on his original `social-media-intelligence` skill, adapted here for integration with the Spotlight investigation pipeline.
