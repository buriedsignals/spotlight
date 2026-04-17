# Junkipedia — Narrative & Misinformation Tracking

**What:** A misinformation database aggregating social media content across platforms. Researchers, journalists, and advocacy orgs use it to monitor narrative spread, track disinformation campaigns, and search historical social posts that may no longer be live on the origin platform.

**When to use:**

- You're investigating a viral claim and need to find its earliest amplifications across platforms
- You need to search for content that has since been deleted from its origin platform
- You want to see which outlets / accounts have amplified a specific narrative
- You're tracking a disinformation operation and need cross-platform pattern data

**Access:** Application-based. Request API access via https://www.junkipedia.org/. Once approved, set `JUNKIPEDIA_API_KEY` in `.env`.

**Docs:** https://docs.junkipedia.org/reference-material/api

## Verb calls

Junkipedia is REST API over HTTPS. Use `execute-shell` with curl.

### Search for tracked content

```
execute-shell('curl -s -H "Authorization: Bearer $JUNKIPEDIA_API_KEY" \
  "https://api.junkipedia.org/api/v1/posts?q=<query>&limit=50" \
  -o cases/{project}/research/junkipedia-<slug>.json')
```

### Search by narrative / issue

Junkipedia tags content under "issues" (narratives). Find posts tagged with a specific narrative:

```
execute-shell('curl -s -H "Authorization: Bearer $JUNKIPEDIA_API_KEY" \
  "https://api.junkipedia.org/api/v1/issues/<issue_id>/posts?limit=100" \
  -o cases/{project}/research/junkipedia-issue-<id>.json')
```

### List issues the platform tracks

```
execute-shell('curl -s -H "Authorization: Bearer $JUNKIPEDIA_API_KEY" \
  "https://api.junkipedia.org/api/v1/issues" \
  -o cases/{project}/research/junkipedia-issues.json')
```

Refer to the official docs for the current endpoint catalog — the exact paths above may change as the platform evolves. Re-verify against https://docs.junkipedia.org/reference-material/api before relying on any specific endpoint.

## Output handling

Junkipedia returns JSON posts with `url`, `platform`, `author`, `timestamp`, `text`, and `issue_tags`. Feed these into `findings.json` as sources:

```json
{
  "url": "https://twitter.com/user/status/...",
  "type": "social_media",
  "platform": "X",
  "accessed": "ISO 8601",
  "access_method": "archive_copy",
  "access_notes": "Retrieved via Junkipedia — post may no longer be live on origin platform",
  "authenticity_flags": ["junkipedia-tagged-disinformation"]
}
```

The `access_method` for Junkipedia-retrieved content is typically `archive_copy` — Junkipedia archives posts at ingest time, so you're reading their snapshot rather than the live origin. Note this explicitly in `access_notes`.

## Combining with social-media-intelligence skill

The `social-media-intelligence` skill's coordination-detection and narrative-tracking workflows pair directly with Junkipedia. When an agent is working narrative spread:

1. `invoke-skill("social-media-intelligence")` — loads the methodology
2. Query Junkipedia for early instances of the claim
3. Check for coordination patterns per the SMI checklists (timing bursts, identical content across accounts, etc.)
4. Archive every source before citing (`invoke-skill("web-archiving")`) — Junkipedia's archive is supplementary, not primary

## Sensitive mode

Junkipedia requires remote API access, so it's blocked in sensitive mode (the adapter strips `fetch`/`search`, and `execute-shell("curl ...")` against remote hosts is guarded at the skill layer). If pre-archived Junkipedia responses exist in `cases/{project}/research/`, agents can read those directly via `read-file`.
