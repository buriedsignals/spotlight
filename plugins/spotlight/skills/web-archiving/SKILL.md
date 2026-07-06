---
name: web-archiving
description: Archive evidence before it disappears — first-party snapshot (MHTML + screenshot, content-addressed) by default, plus third-party archives, with chain of custody for editorial accountability and legal defensibility
version: "1.1"
invocable_by: [investigator, fact-checker]
requires: [shell-safety]
env_vars: []
attribution: "Methodology inspired by OpenSanctions evidence-preservation practice. Full Buried Signals rewrite; no upstream text remains."
---

# Web Archiving

> Methodology inspired by [OpenSanctions](https://www.opensanctions.org/) evidence-preservation practice.

Archive evidence sources as you find them, before they disappear. This skill is for investigators and fact-checkers who need to preserve sources for editorial accountability, legal defensibility, and reproducibility.

## When to Archive

- **Before citing any URL** — Archive immediately on discovery. Sources vanish. A finding without an archived copy is a finding that can be challenged.
- **When a source is the only evidence for a claim** — Capture a first-party snapshot (Tier 0) AND a third-party copy (Wayback or Archive.today).
- **When a page is JS-rendered, bot-blocking, or likely to change/vanish** — the first-party snapshot (Tier 0) captures what Wayback/Archive.today often miss.
- **When a page has changed during the investigation window** — Archive each version with a timestamp note.

## Archive Service Hierarchy

Capture a **first-party snapshot (Tier 0) by default**, then a third-party copy for independent
verifiability. Tier 0 never *replaces* the third-party tiers — it is a higher-fidelity, self-held
record; independent verifiability still comes from Tier 1/2.

Before any `execute-shell` call that uses a URL, timestamp, filename, or path, invoke `shell-safety` and validate values with `scripts/spotlight_safe.py`. Use `curl --get --data-urlencode` for URL parameters; do not interpolate untrusted values into shell strings.

### 0. First-party snapshot — DEFAULT (dev-browser)

Capture the page as a content-addressed **MHTML archive + full-page screenshot + response
headers** using the local `dev-browser` CLI. Full recipe: `references/capture-dev-browser.md`;
record schema: `references/snapshot-record.md`. In brief:

1. `execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"`
2. Write + run the capture script (`dev-browser --headless --timeout 90 run …`) → JSON with the
   temp MHTML/PNG paths, status, headers, and an `access_wall` flag.
3. Hash each artifact (`spotlight_safe.py sha256`) and copy it to
   `{CASE_DIR}/evidence/snapshots/<sha>.{mhtml,png}` (filename == hash; identical bytes dedup).
4. Record it as an `evidence-bundle.json` item (`acquisition_method: dev_browser`).

**Guards:**
- **Sensitive mode** — this skill is disabled in sensitive mode and `dev-browser` must not hit
  live external sites without explicit egress approval. Skip Tier 0; for surveilled targets a
  third-party Wayback save (issued from Internet-Archive infra, not your IP) is the safer lead.
- **Access wall** — a no-session headless capture of a paywalled/authed page records the *wall*,
  not the content. If `access_wall` is true, mark the item `human_verification_required` and do
  not present it as content evidence.
- **No dev-browser?** Degrade to a firecrawl scrape (raw HTML + full-page screenshot), record
  `acquisition_method: firecrawl`, no MHTML — then continue to Tier 1.

  ```
  fetch: url={URL}, output_path={CASE_DIR}/evidence/snapshots/{slug}.html
  ```

### 1. Wayback Machine (Internet Archive)

Check if already archived:

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl -s --get "https://archive.org/wayback/available" --data-urlencode "url={URL}"
```

Submit for archiving:

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl -s -I "https://web.archive.org/save/{URL}"
```

The response `Location` header contains the new snapshot URL.

Find all snapshots (CDX API):

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl --get "http://web.archive.org/cdx/search/cdx" \
  --data-urlencode "url={URL}" \
  --data-urlencode "output=json" \
  --data-urlencode "limit=5" \
  --data-urlencode "fl=timestamp,statuscode,original"
```

### 2. Archive.today

Submit via form:

```
execute-shell: curl -s -L -X POST "https://archive.ph/submit/" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "url={URL}" \
  -d "anyway=1"
```

Follow redirects — the final URL is the archived copy.

Check for existing copy:

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl -s "https://archive.ph/newest/" --get --data-urlencode "url={URL}"
```

> The old standalone "local markdown scrape" tier is removed — it is subsumed by the Tier-0
> first-party snapshot (and its firecrawl fallback), which is higher fidelity.

## Evidence Storage

- **Tier-0 snapshot artifacts** (content-addressed): `{CASE_DIR}/evidence/snapshots/<sha>.{mhtml,png,html}`.
- **Chain-of-custody / third-party copies**: `{CASE_DIR}/research/archived/`,
  naming `{domain}-{slug}-archived-{YYYYMMDD}.md`.

Examples:

```
cases/project-name/evidence/snapshots/9f2c…ab.mhtml
cases/project-name/research/archived/reuters-ukraine-ceasefire-archived-20260315.md
```

## Chain of Custody Block

Embed this header in every archived file. It is the provenance record.

```
---
archived_at: 2026-03-15T14:22:00Z
original_url: https://example.com/article/path
capturer: dev_browser                                  # Tier 0: dev_browser | firecrawl
snapshot_sha256: 9f2c…ab                               # Tier-0 MHTML artifact (content-addressed)
snapshot_path: evidence/snapshots/9f2c…ab.mhtml
access_wall: false                                     # true ⇒ wall captured, not content
archive_url: https://web.archive.org/web/20260315142200/https://example.com/article/path
archive_service: Wayback Machine                       # third-party (Tier 1/2), when obtained
archived_by: investigator | fact-checker
case: {project}
---
```

Without this block, the file is not a valid archived source — it is just a local copy.

Write the archived file with the chain of custody block:

```
write-file: path={CASE_DIR}/research/archived/{domain}-{slug}-archived-{YYYYMMDD}.md, content=<chain of custody block + page content>
```

## Dead Pages

If the original URL returns 404 or is otherwise gone, check Wayback Machine before marking the source as unavailable:

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl --get "http://web.archive.org/cdx/search/cdx" \
  --data-urlencode "url={URL}" \
  --data-urlencode "output=json" \
  --data-urlencode "limit=1" \
  --data-urlencode "fl=timestamp,statuscode"
```

If a snapshot exists, retrieve it:

```
execute-shell: python3 scripts/spotlight_safe.py validate-timestamp "{TIMESTAMP}"
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
fetch: url=https://web.archive.org/web/{TIMESTAMP}/{URL}, output_path={CASE_DIR}/research/archived/{filename}.md
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
  "local_file": "{CASE_DIR}/research/archived/example-article-archived-20260315.md"
}
```

If the page could not be archived, set `archive_url` to `null` and note why in the finding's `confidence_rationale`.

### Evidence bundle (Tier-0 snapshots)

Each Tier-0 snapshot is also recorded as an `evidence-bundle.json` item so its hashes are carried
into the C2PA-signed `provenance-manifest.json`. Map onto **existing** schema fields (see
`references/snapshot-record.md`) — do not invent new keys:

```json
{
  "id": "snap-9f2cab01",
  "query_or_task": "web-archiving Tier-0 snapshot: https://example.com/article",
  "acquisition_method": "dev_browser",
  "source_url": "https://example.com/article",
  "accessed": "2026-03-15T14:22:00Z",
  "sha256": "9f2c…ab",
  "raw_path": "evidence/snapshots/9f2c…ab.mhtml",
  "screenshot_path": "evidence/snapshots/7a10…cd.png",
  "extraction_confidence": "high",
  "human_verification_required": false,
  "notes": "MHTML+screenshot; condition=load; screenshot_sha256=7a10…cd"
}
```

Run `scripts/validate-case.py` after writing. `build-provenance-manifest.py` hashes the files at
`raw_path`/`screenshot_path`, so both artifacts are tamper-evident in the signed manifest.

## Rules

- **First-party snapshot (Tier 0) is the default.** Then a third-party copy (Tier 1/2) for
  independent verifiability. For surveilled/hostile targets, lead with Tier 1 (third-party) —
  a self-capture exposes your own IP/fingerprint to the target.
- **Content-addressing is mandatory.** A Tier-0 artifact's stored filename equals the SHA-256 of
  its bytes; identical bytes dedup to one file.
- **Never store an access wall as evidence.** Flag `access_wall` + `human_verification_required`.
- **Sensitive mode:** no live Tier-0 capture without explicit egress approval (skill is disabled there).
- **Archive before you cite.** Not after.
- **Never delete archived files.** Even if the finding they support is later disproven — keep the file. It documents what existed at the time.
- **One URL = one archive attempt per service.** Don't spam archive services.
- **Timestamping is mandatory.** The investigation-log.json already tracks access time per source — ensure it matches the archived copy's timestamp.

---

## Credits

Methodology inspired by **OpenSanctions** evidence-preservation practice. An early version of this skill was adapted from Joe Amditis's `web-archiving` skill (MIT); the current version is a full rewrite with no upstream text remaining (verified by diff, 2026-07-06).
