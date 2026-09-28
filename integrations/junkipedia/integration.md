# Junkipedia — Narrative & Misinformation Tracking

**What:** A misinformation database aggregating social media content across platforms. Researchers, journalists, and advocacy orgs use it to monitor narrative spread, track disinformation campaigns, and search historical social posts that may no longer be live on the origin platform.

**When to use:**

- You're investigating a viral claim and need to find its earliest amplifications across platforms
- You need to search for content that has since been deleted from its origin platform
- You want to see which outlets / accounts have amplified a specific narrative
- You're tracking a disinformation operation and need cross-platform pattern data

**Access:** Application-based. Request API access via https://www.junkipedia.org/. Once approved, add the key in Indicator Labs, or set `JUNKIPEDIA_API_KEY` in your environment, the file named by `env_file` in `.spotlight-config.json`, or the checkout `.env`. Never print the key or open a credential file.

**Docs:** https://docs.junkipedia.org/reference-material/api

## Verb calls

Junkipedia is REST API over HTTPS. Invoke `shell-safety` first. The helper sends the key in a header, URL-encodes every parameter and accepts only the paths below; validate output paths.

### Search for tracked content

```
execute-shell('python3 -m integrations.junkipedia get posts --param "q=<query>" --param limit=50 --output {CASE_DIR}/research/junkipedia-<slug>.json')
```

### Search by narrative / issue

Junkipedia tags content under "issues" (narratives). Find posts tagged with a specific narrative:

```
execute-shell('python3 scripts/spotlight_safe.py validate-slug "<issue_id>"')
execute-shell('python3 -m integrations.junkipedia get issues/<issue_id>/posts --param limit=100 --output {CASE_DIR}/research/junkipedia-issue-<id>.json')
```

### List issues the platform tracks

```
execute-shell('python3 -m integrations.junkipedia get issues --output {CASE_DIR}/research/junkipedia-issues.json')
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

Junkipedia requires remote API access, so it's blocked in sensitive mode (the adapter strips `fetch`/`search`, and remote calls are guarded at the skill layer). If pre-archived Junkipedia responses exist in `{CASE_DIR}/research/`, agents can read those directly via `read-file`.
