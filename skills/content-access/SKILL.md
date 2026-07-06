---
name: content-access
description: Work through the legal access hierarchy for paywalled or restricted sources before marking them inaccessible and downgrading confidence
version: "1.0"
invocable_by: [investigator, fact-checker]
requires: [web-archiving, shell-safety]
env_vars: [CORE_API_KEY, OPENALEX_API_KEY]
attribution: "Adapted from jamditis/claude-skills-journalism (https://github.com/jamditis/claude-skills-journalism). Original author: Joe Amditis. MIT License."
---

# Content Access

When a source is behind a paywall or access barrier, work through this hierarchy before marking it inaccessible. A paywalled source should not default to `low` confidence — it should default to an access attempt.

## Decision Tree

Try each step in order. Stop as soon as you have the full text.

Before any `execute-shell` command that uses a DOI, URL, query, timestamp, filename, or path, invoke `shell-safety` and validate the value with `scripts/spotlight_safe.py`. Prefer `--data-urlencode` or serialized JSON over interpolating values into shell strings.

External-access boundary: Unpaywall, CORE, Semantic Scholar, web archives, and
similar services may receive only the minimum DOI, title/query, URL, or
timestamp needed to retrieve the source. Do not send case notes, raw source
dumps, unpublished allegations, private source material, credentials, vault
contents, or unnecessary personal data. In sensitive mode, remote access
services are disabled unless the user explicitly approves an override.

### 1. Free Version Search

Many paywalled articles have freely distributed copies. Search for them:

```
search: query="{exact title}" filetype:pdf
search: query="{exact title}" site:{author-institution}.edu
search: query="{exact title}" site:researchgate.net
search: query="{exact title}" site:academia.edu
```

### 2. Unpaywall (Academic Papers with DOI, Optional Integration)

Unpaywall is optional. Before using it, confirm the integration is green:

```
execute-shell: python3 integrations/preflight.py --json
```

Use Unpaywall only when `unpaywall` is green. It requires `$UNPAYWALL_EMAIL` in `.env`; the email is a fair-use identifier, not a notification target. If the integration is red or missing, skip to step 3.

```
execute-shell: python3 scripts/spotlight_safe.py validate-doi "{DOI}"
execute-shell: curl --get "https://api.unpaywall.org/v2/{DOI}" --data-urlencode "email=$UNPAYWALL_EMAIL"
```

Parse the response for `best_oa_location.url` — if present, it's the legal open-access copy. Also check `oa_locations[]` for mirrors.

If a URL is found, fetch the full text:

```
fetch: url={oa_location_url}, output_path={CASE_DIR}/research/{filename}.md
```

### 3. CORE (295M Open Access Papers)

```
execute-shell: curl --get "https://api.core.ac.uk/v3/search/works" \
  --data-urlencode "q={query}" \
  --data-urlencode "limit=5" \
  -H "Authorization: Bearer ${CORE_API_KEY}"
```

Check `results[].downloadUrl` for PDF links.

### 4. Semantic Scholar

Unkeyed access now sits behind a low shared rate limit and is unreliable for batch lookups. For more than ad-hoc calls, request a free key at semanticscholar.org/product/api and send it as `x-api-key`; on 429, move to step 5 rather than retrying.

```
execute-shell: curl --get "https://api.semanticscholar.org/graph/v1/paper/search" \
  --data-urlencode "query={query}" \
  --data-urlencode "fields=title,openAccessPdf,externalIds" \
  --data-urlencode "limit=5"
```

Check `openAccessPdf.url`.

### 4b. OpenAlex (250M+ scholarly works)

The de-facto open scholarly backbone since Microsoft Academic Graph was retired. API-key-required since 2026-02-13 (credit-based rate model — verify at docs.openalex.org). Skip if `$OPENALEX_API_KEY` is unset.

execute-shell: curl --get "https://api.openalex.org/works" \
  --data-urlencode "search={query}" \
  --data-urlencode "per-page=5" \
  -H "Authorization: Bearer ${OPENALEX_API_KEY}"

Check `results[].open_access.oa_url` and `oa_status` (gold/green/hybrid/bronze/closed).

### 5. Archive Copy

Check if an archived version is accessible before the paywall went up:

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl --get "http://web.archive.org/cdx/search/cdx" \
  --data-urlencode "url={URL}" \
  --data-urlencode "output=json" \
  --data-urlencode "limit=3" \
  --data-urlencode "fl=timestamp,statuscode"
```

```
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
execute-shell: curl -sI "https://archive.ph/newest/{URL}" | grep -i '^location:'

A Location pointing at a snapshot (no `/newest/` in it) is the hit; no Location or a `/newest/` Location means no snapshot or CAPTCHA rate-limiting — do not retry in a loop.
```

Early snapshots often predate paywall implementation. Archive.today caveat (2026): the FBI subpoenaed its registrar in Oct 2025 and Wikipedia stopped accepting it as a citation source in Feb 2026. Prefer Wayback snapshots for anything cited in `findings.json`; use archive.ph only as fallback and record that in `access_notes`. If a pre-paywall snapshot is found, retrieve it:

```
execute-shell: python3 scripts/spotlight_safe.py validate-timestamp "{TIMESTAMP}"
execute-shell: python3 scripts/spotlight_safe.py validate-url "{URL}"
fetch: url=https://web.archive.org/web/{TIMESTAMP}/{URL}, output_path={CASE_DIR}/research/archived/{filename}.md
```

### 6. Google Scholar

Search the title in Google Scholar. Look for `[PDF]` links next to results — these are often author-posted copies on institutional servers.

```
search: query="{exact title}" site:scholar.google.com
```

### 7. Reader Mode (Metered Paywalls)

For publications with metered paywalls (free article limits). Note: FT, Bloomberg, NYT, and WSJ are hard-paywalled — reader mode reveals nothing there; go to step 5 or 8 instead. For metered outlets:

- Safari: `Cmd-Shift-R` or View > Show Reader
- Firefox: `F9` or the reader icon in the address bar
- Edge: reader icon in address bar

Works only on metered paywalls, not hard paywalls. This is a manual step — note in `access_notes` if reader mode was used.

### 8. Author Contact (Last Resort)

Email the author directly. Most authors are willing to share their work:

> Subject: Research access request — [{Title}]
>
> I'm a journalist investigating [{topic}] and would like to access your paper "{Title}" (published {year}). Would you be able to share a copy? I'm happy to credit your work in my reporting.

Note the outreach in `access_notes` and set `access_method` to `author_request_pending` until a response is received.

## Do Not

- Use browser extension bypasses (Bypass Paywalls Clean, Unpaywall browser extension in bypass mode)
- Use Sci-Hub, LibGen, Anna's Archive, or paywall-redirector services (12ft.io, removepaywall.com) — same legal and reputational exposure
- Scrape paywalled pages by rotating user agents or sessions — ToS violation
- Pretend to be an institutional user to access licensed databases

## Source Metadata

Add `access_method` to every source entry in `findings.json` and `fact-check.json`:

```json
{
  "url": "https://example.com/paywalled-article",
  "type": "news",
  "accessed": "2026-03-15T14:20:00Z",
  "access_method": "archive_copy",
  "access_notes": "Retrieved via Wayback Machine snapshot from 2024-11-02"
}
```

Valid values for `access_method`:

- `full_text` — Complete article accessed directly (no barrier)
- `open_access` — Legal open-access copy found via Unpaywall/CORE/author
- `free_version` — Freely distributed copy found via search (PDF, institutional mirror)
- `archive_copy` — Accessed via Wayback Machine or Archive.today
- `google_scholar_pdf` — PDF found via Google Scholar institutional link
- `reader_mode` — Retrieved via browser reader mode on metered paywall
- `author_provided` — Author shared copy in response to request
- `author_request_pending` — Author contact sent, awaiting response
- `abstract_only` — Only abstract/summary available
- `partial_text` — Some content accessible (e.g., first paragraphs before hard paywall)
- `cached_copy` — Found in search engine cache (Google, Bing)
- `institutional_access` — Accessed through legitimate institutional subscription
- `preprint` — Preprint version found (arXiv, SSRN, bioRxiv, etc.)
- `inaccessible` — Could not access after full hierarchy attempt

## Confidence Implications

| access_method | Confidence cap |
|---|---|
| `full_text` / `open_access` / `author_provided` / `institutional_access` | No cap |
| `free_version` / `google_scholar_pdf` / `preprint` | No cap (but note version date — may differ from published version) |
| `archive_copy` / `cached_copy` | No cap (but note snapshot date) |
| `reader_mode` / `partial_text` | `medium` at best — may be missing key content |
| `abstract_only` | `medium` at best — abstract may omit key findings |
| `author_request_pending` | `low` — pending, do not cite as verified |
| `inaccessible` | `low` — cite the source but flag that content was not verified |

A finding that rests on an `inaccessible` source must say so explicitly in `confidence_rationale`. Do not inflate confidence because the source sounds authoritative if you have not read it.

## Rules

- **Attempt access before downgrading.** Work through at least the first five steps before marking a source `inaccessible`.
- **Record what you tried.** Note attempted access methods in `access_notes` so the next cycle doesn't repeat the effort.
- **Abstract != full text.** Never cite a finding as supported by a paper you only read the abstract of without flagging it.
- **Date-check archived copies.** An archived copy from 5 years ago may not reflect the current version of a document. Note the snapshot date.

---

## Deliberate divergences from upstream

- Condensed to an executable decision tree wired to `spotlight_safe.py` shell-safety validation and `{CASE_DIR}` outputs, replacing upstream's Python library code.
- Adds the `access_method` vocabulary and confidence-cap table feeding `findings.json`/`fact-check.json` — no upstream equivalent.
- Adds an external-service data-minimization boundary and sensitive-mode gate absent upstream.
- Drops upstream's VPN/geo-blocking, library-database, and US fair-use material as out of pipeline scope.

Reconciled against upstream @ `2097d218` (2026-07-06).

## Credits

Adapted from [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by **Joe Amditis**, released under MIT License.
