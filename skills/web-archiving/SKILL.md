---
name: web-archiving
description: Archive evidence before it disappears — structured preservation with chain of custody for editorial accountability and legal defensibility
version: "1.0"
invocable_by: [investigator, fact-checker]
requires: []
env_vars: []
attribution: Adapted from jamditis/claude-skills-journalism (https://github.com/jamditis/claude-skills-journalism). Original author: Jay Amditis. MIT License.
---

# Web Archiving

> **Adapted from** [jamditis/claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by Jay Amditis (MIT License).

Archive evidence sources as you find them, before they disappear. This skill is for investigators and fact-checkers who need to preserve sources for editorial accountability, legal defensibility, and reproducibility.

## When to Archive

- **Before citing any URL** — Archive immediately on discovery. Sources vanish. A finding without an archived copy is a finding that can be challenged.
- **When a source is the only evidence for a claim** — Archive it twice (Wayback + Archive.today).
- **When a source is from a hostile, unstable, or government-controlled domain** — Archive before the site detects your activity.
- **When a page has changed during the investigation window** — Archive each version with a timestamp note.

## Archive Service Hierarchy

Try in order. Stop when you have a confirmed archived copy.

### 1. Wayback Machine (Internet Archive)

Check if already archived:

```
execute-shell: curl -s "https://archive.org/wayback/available?url={URL}" | jq '.archived_snapshots.closest'
```

Submit for archiving:

```
execute-shell: curl -s -I "https://web.archive.org/save/{URL}"
```

The response `Location` header contains the new snapshot URL.

Find all snapshots (CDX API):

```
execute-shell: curl "http://web.archive.org/cdx/search/cdx?url={URL}&output=json&limit=5&fl=timestamp,statuscode,original"
```

### 2. Archive.today

Submit via form:

```
execute-shell: curl -s -L -X POST "https://archive.ph/submit/" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "url={URL}" \
  -d "anyway=1"
```

Follow redirects — the final URL is the archived copy.

Check for existing copy:

```
execute-shell: curl -s "https://archive.ph/newest/{URL}"
```

### 3. Local Scrape (Fallback)

When archive services are rate-limited or unavailable, scrape directly and save to the evidence store:

```
fetch: url={URL}, output_path=cases/{project}/research/archived/{domain}-{slug}-archived-{YYYYMMDD}.md
```

Treat local scrapes as lower-confidence preservation — they are not independently verifiable.

## Evidence Storage

All archived copies go to `cases/{project}/research/archived/`.

Naming convention: `{domain}-{slug}-archived-{YYYYMMDD}.md`

Examples:

```
cases/project-name/research/archived/reuters-ukraine-ceasefire-archived-20260315.md
cases/project-name/research/archived/companyreg-gov-uk-filing-archived-20260315.md
```

## Chain of Custody Block

Embed this header in every archived file. It is the provenance record.

```
---
archived_at: 2026-03-15T14:22:00Z
original_url: https://example.com/article/path
archive_url: https://web.archive.org/web/20260315142200/https://example.com/article/path
archive_service: Wayback Machine
archived_by: investigator | fact-checker
case: {project}
---
```

Without this block, the file is not a valid archived source — it is just a local copy.

Write the archived file with the chain of custody block:

```
write-file: path=cases/{project}/research/archived/{domain}-{slug}-archived-{YYYYMMDD}.md, content=<chain of custody block + page content>
```

## Dead Pages

If the original URL returns 404 or is otherwise gone, check Wayback Machine before marking the source as unavailable:

```
execute-shell: curl "http://web.archive.org/cdx/search/cdx?url={URL}&output=json&limit=1&fl=timestamp,statuscode"
```

If a snapshot exists, retrieve it:

```
execute-shell: curl "https://web.archive.org/web/{TIMESTAMP}/{URL}" -o cases/{project}/research/archived/{filename}.md
```

Only mark a source as `unavailable` after checking all three services.

## Integration with Findings

Add `archive_url` to every source entry in `findings.json` and `fact-check.json`:

```json
{
  "url": "https://example.com/article",
  "type": "news",
  "accessed": "2026-03-15T14:20:00Z",
  "archive_url": "https://web.archive.org/web/20260315142200/https://example.com/article",
  "local_file": "cases/{project}/research/archived/example-article-archived-20260315.md"
}
```

If the page could not be archived, set `archive_url` to `null` and note why in the finding's `confidence_rationale`.

## Rules

- **Archive before you cite.** Not after.
- **Never delete archived files.** Even if the finding they support is later disproven — keep the file. It documents what existed at the time.
- **One URL = one archive attempt per service.** Don't spam archive services.
- **Timestamping is mandatory.** The investigation-log.json already tracks access time per source — ensure it matches the archived copy's timestamp.

---

## Credits

Adapted from [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by **Jay Amditis**, released under MIT License.
