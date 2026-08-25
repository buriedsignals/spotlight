---
name: social-media-intelligence
description: Account authenticity assessment, coordination detection, and narrative tracking for social media investigations. Use when analyzing account networks, detecting bot activity or coordinated campaigns, tracking how claims spread across platforms, or building evidence trails from social content.
version: "1.0"
invocable_by: [investigator, fact-checker, user]
requires: [web-archiving]
attribution: "Adapted from jamditis/claude-skills-journalism (https://github.com/jamditis/claude-skills-journalism). Original author: Joe Amditis. MIT License."
---

# Social Media Intelligence

Systematic approaches for investigating social media: authenticating accounts, detecting coordinated behavior, and tracking how narratives spread.

> **Adapted from** [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by Joe Amditis (MIT License). Extended for integration with the OSINT investigation pipeline.

Before running any social-media scraper or CLI with keywords, handles, URLs, dates, or output paths, invoke `shell-safety`. Do not interpolate search text into shell commands; pass it through JSON, stdin, a temp file, or a helper that uses argv safely.

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

- Wayback Machine CDX API: validate `{URL}` with `scripts/spotlight_safe.py`, then use `curl --get ... --data-urlencode "url={URL}" --data-urlencode "output=json" --data-urlencode "limit=3" --data-urlencode "fl=timestamp,original"`
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

Status as of 2026 — platform APIs change fast; verify pricing/access before designing collection around any one path.

| Platform | Best approach | Notes |
|---|---|---|
| X (Twitter) | Official API (pay-per-use since Feb 2026, replaced Basic/Pro tiers); X Pro Search (Premium+); Apify X scraper | Free research tier ended 2023. Post-2023 ToS prohibits scraping even public posts — Apify collection is a ToS-violating path; record the risk in `access_notes` |
| Facebook / Instagram | Meta Content Library + Library API (research); Junkipedia (free); Apify | CrowdTangle shut down Aug 14, 2024 — it no longer exists. MCL applications go through Meta's portal directly (since Dec 2025); eligibility favors academic/nonprofit affiliation |
| TikTok | Research API (developers.tiktok.com — academic/nonprofit; EU via DSA Art. 40); Exolyt, Pentos, Apify | Limited historical data; Commercial Content endpoints expanded 2026 |
| Reddit | Reddit API (free, non-commercial); Arctic Shift (Pushshift successor, free dumps) | Pushshift is moderator-only since 2023 — not a research path |
| YouTube | YouTube Data API v3 | 10,000 units/day default; each search costs 100 units (~100 searches/day) |
| Bluesky | Jetstream (filtered JSON over WebSocket, no auth) or raw AT Protocol firehose | Jetstream ~850 MB/day filtered; raw firehose 4-8 GB/hour |
| Threads | Meta Content Library (research) | Public-profile discovery threshold lowered to 100 followers, Mar 2026 |
| Mastodon / Fediverse | Per-instance public-timeline API; search.noc.social | Many instances set DISALLOW_UNAUTHENTICATED_API_ACCESS; cross-instance search is fragmented |
| Telegram | Bot API + MTProto; t.me/s/<channel> previews; TGStat, Telemetrio, Telegago, Telepathy, TelegramDB | Public channels legal to collect in most jurisdictions; private groups off-limits |

### EU DSA Article 40 access

EU-based researchers have stronger platform-data access rights on TikTok and Meta platforms under the Digital Services Act than researchers elsewhere. Non-EU journalists may qualify through an EU institutional partner (university or vetted research nonprofit). A real path, not a workaround.

### Pluggable platform scraping

Platform-specific scraping can be configured via the `PLATFORM_SCRAPER` env var. Two common backings:

**Option A — Apify (hosted platform scrapers):**

If `APIFY_TOKEN` is set, use Apify actors:

```
write-file("{CASE_DIR}/research/apify-twitter-input.json", <serialized actor input JSON>)
execute-shell('apify call apify/twitter-scraper --input-file {CASE_DIR}/research/apify-twitter-input.json')
write-file("{CASE_DIR}/research/apify-instagram-input.json", <serialized actor input JSON>)
execute-shell('apify call apify/instagram-scraper --input-file {CASE_DIR}/research/apify-instagram-input.json')
write-file("{CASE_DIR}/research/apify-tiktok-input.json", <serialized actor input JSON>)
execute-shell('apify call apify/tiktok-scraper --input-file {CASE_DIR}/research/apify-tiktok-input.json')
```

Keep the generic X route above available. For structured X posts,
conversations, lists, engagement accounts, followers, following, communities,
or audience overlap, read `references/xquik-apify-actors.md`. It defines
bounded routes through both Xquik Actors and the required evidence handling.

If the installed Apify CLI does not support `--input-file`, use a local wrapper that reads JSON from a file and passes it through argv/subprocess without invoking a shell. Do not inline search terms or direct URLs into a shell command.

For X specifically, Apify collection violates the post-2023 ToS even for public posts. Where the story could face legal scrutiny, prefer the official API or a licensed broker, and record the collection authority in `access_notes` for every capture.

**Option B — Native platform APIs:**

When the platform offers a direct API (YouTube Data API v3, Bluesky AT Protocol, Telegram TGStat), prefer it. Document which backing was used in `access_notes` on each source entry.

**Option C — Manual archive + scrape:**

If no scraper is configured, use `fetch(profile_url, ...)` + manual review. Lower throughput but no auth dependency.

---

## External Tooling (status: 2026)

Verify operational status before citing any tool in reporting.

- Botometer X — archival only since June 2023; cannot score accounts created or active after May 31, 2023
- CooRTweet (R) — coordinated-behavior analysis; successor to CooRnet (discontinued Aug 2024)
- Hoaxy2 — claim-spread visualization; now covers Mastodon search and Bluesky real-time
- Bot Sentinel — offline through 2025, relaunch announced 2026; verify before citing
- InVID-WeVerify plugin — maintained under EU vera.ai; beta synthetic-image and voice-clone detectors added 2025
- Bellingcat Online Investigation Toolkit — bellingcat.gitbook.io/toolkit (continuously updated)
- WITNESS Deepfakes Rapid Response Force — pairs reporters with media-forensics experts on deadline
- Note: Stanford Internet Observatory dismantled June 2024; First Draft closed 2022 (successor: Information Futures Lab, Brown SPH)

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

## Deliberate divergences from upstream

- Upstream's Python monitoring/network-analysis library code replaced with checklist-driven manual assessment and explicit scoring thresholds tied to confidence grading.
- Evidence flows into the `findings.json` source schema (`type: "social_media"`, `authenticity_flags`, `coordination_signals`) with `confidence_rationale` tie-ins — no upstream equivalent.
- Pluggable scraper backends (Apify / native API / manual) via `PLATFORM_SCRAPER` under shell-safety discipline; Apify remains a deliberate collection path with the ToS risk documented rather than avoided.
- Archiving mechanics deferred to the `web-archiving` skill instead of inline wrappers.

Reconciled against upstream @ `2097d218` (2026-07-06).

## Credits

Adapted from [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by **Joe Amditis**, released under MIT License. Methodology for account authenticity assessment, coordination detection, and narrative tracking is based on his original `social-media-intelligence` skill, adapted here for integration with the Spotlight investigation pipeline.
