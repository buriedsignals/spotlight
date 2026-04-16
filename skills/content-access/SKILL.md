---
name: content-access
description: Work through the legal access hierarchy for paywalled or restricted sources before marking them inaccessible and downgrading confidence
version: "1.0"
invocable_by: [investigator, fact-checker]
requires: [web-archiving]
env_vars: [CORE_API_KEY]
attribution: Adapted from jamditis/claude-skills-journalism (https://github.com/jamditis/claude-skills-journalism). Original author: Jay Amditis. MIT License.
---

# Content Access

When a source is behind a paywall or access barrier, work through this hierarchy before marking it inaccessible. A paywalled source should not default to `low` confidence — it should default to an access attempt.

## Decision Tree

Try each step in order. Stop as soon as you have the full text.

### 1. Free Version Search

Many paywalled articles have freely distributed copies. Search for them:

```
search: query="{exact title}" filetype:pdf
search: query="{exact title}" site:{author-institution}.edu
search: query="{exact title}" site:researchgate.net
search: query="{exact title}" site:academia.edu
```

### 2. Unpaywall (Academic Papers with DOI)

```
execute-shell: curl "https://api.unpaywall.org/v2/{DOI}?email=research@buriedsignals.com"
```

Parse the response for `best_oa_location.url` — if present, it's the legal open-access copy. Also check `oa_locations[]` for mirrors.

If a URL is found, fetch the full text:

```
fetch: url={oa_location_url}, output_path=cases/{project}/research/{filename}.md
```

### 3. CORE (295M Open Access Papers)

```
execute-shell: curl "https://api.core.ac.uk/v3/search/works?q={query}&limit=5" \
  -H "Authorization: Bearer ${CORE_API_KEY}"
```

Check `results[].downloadUrl` for PDF links.

### 4. Semantic Scholar

```
execute-shell: curl "https://api.semanticscholar.org/graph/v1/paper/search?query={query}&fields=title,openAccessPdf,externalIds&limit=5"
```

Check `openAccessPdf.url`.

### 5. Archive Copy

Check if an archived version is accessible before the paywall went up:

```
execute-shell: curl "http://web.archive.org/cdx/search/cdx?url={URL}&output=json&limit=3&fl=timestamp,statuscode"
```

```
execute-shell: curl -s "https://archive.ph/newest/{URL}"
```

Early snapshots often predate paywall implementation. If a pre-paywall snapshot is found, retrieve it:

```
execute-shell: curl "https://web.archive.org/web/{TIMESTAMP}/{URL}" -o cases/{project}/research/archived/{filename}.md
```

### 6. Google Scholar

Search the title in Google Scholar. Look for `[PDF]` links next to results — these are often author-posted copies on institutional servers.

```
search: query="{exact title}" site:scholar.google.com
```

### 7. Reader Mode (Metered Paywalls)

For publications with free article limits (e.g., Financial Times, Bloomberg):

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
- Use Sci-Hub or similar sites for current journalism work — legal and reputational risk
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

## Credits

Adapted from [claude-skills-journalism](https://github.com/jamditis/claude-skills-journalism) by **Jay Amditis**, released under MIT License.
