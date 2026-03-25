# Search Operators for OSINT

Advanced search techniques for finding exposed documents, hidden pages, and platform-specific content. These operators work across Google, Bing, Yandex, and DuckDuckGo — try the same search on multiple engines, as each indexes different content.

---

## Core Operators

### Quotation Marks `""`

Forces exact-match results. Without quotes, search engines return results for individual words in any order.

- `"Homer Simpson"` — returns only Homer Simpson results, not the Greek poet Homer
- `"First Last"` — essential for name searches to avoid false matches
- `"First * Last"` — wildcard for middle name (see below)

### Minus Sign `-`

Excludes a term from results. No space between `-` and the excluded word.

- `Homer -Greece -Greek -Poet -Iliad -Odyssey` — removes all Greek poet results
- `"John Smith" -linkedin -facebook` — excludes major platforms to find niche results

### Wildcard `*`

Substitutes an unknown variable — a single character, word, or phrase.

- `"First * Last"` — finds results that include the subject's middle name (which you may not know)
- `"got drunk and bought a * last night"` — fills in the blank with any word or phrase
- Useful for partial dates of birth if you experiment with date formats

### site:

Restricts results to a specific domain or domain extension.

- `site:bellingcat.com` — only results from Bellingcat
- `site:.gov` — only government sites
- `site:.edu` — only educational institutions
- `site:instagram.com` — only Instagram pages

### filetype:

Restricts results to a specific file format.

- `filetype:pdf` — PDF documents
- `filetype:xls` or `filetype:xlsx` — Excel spreadsheets
- `filetype:doc` or `filetype:docx` — Word documents
- `filetype:ppt` — PowerPoint presentations

### intitle:

Finds pages with a specific word in the HTML page title.

- `intitle:"Index of"` — finds open/unsecured file directories (their titles contain "Index of")
- `intitle:"login" site:example.com` — finds login pages on a specific domain

---

## OSINT Search Patterns

### Finding Exposed Documents

Combine `filetype:` and `site:` to find documents indexed by search engines that site administrators may not realize are public.

**Exposed confidential documents:**
```
"Private & confidential" filetype:pdf site:.gov
```
```
"Private & confidential" filetype:pdf site:.edu
```

**Internal-only presentations:**
```
"For internal use only" site:prezi.com
```
```
"For internal use only" site:scribd.com
```

**Default passwords:**
```
"Your default password is" site:.edu
```
```
"Your default password is" site:.gov
```
```
"Your default password is" site:docs.google.com
```

**Open file directories on institutional domains:**
```
intitle:"Index of" "employee training" site:.gov
```
```
intitle:"Index of" "employee training" site:.edu
```

**Tip:** Run these searches on DuckDuckGo and Yandex as well as Google — each indexes different content.

### Combining Operators

Stack operators for precision. Example workflow:

```
California education spending 2017 filetype:xls
```
Returns Excel files about California education spending.

```
California education spending 2017 filetype:xls -private
```
Same search, excluding private school results.

```
California education spending 2017 filetype:xls site:.gov -private
```
Only government-hosted Excel files, excluding private schools.

### Instagram Account Investigation

Find a private account's public activity (comments, tags, mentions on other people's posts):

```
"@username" site:instagram.com -site:instagram.com/username
```

This searches Instagram for mentions of the username on pages that are NOT their own profile. Returns comments they left on public posts, stories they were tagged in, and @-mentions by others.

### Finding Google Documents

```
site:docs.google.com "[search term]"
```
Searches publicly-shared Google Docs. Many users do not realize their documents are indexed.

### Non-Paywalled Newspaper Archives

Many OCR-scanned newspaper editions exist on outside domains without paywalls:

```
site:etypeservices.com "First Last"
```
```
site:issuu.com "First Last"
```

### Google Hacking Database

For an extensive catalog of advanced dork patterns organized by category (footholds, sensitive directories, vulnerable files, error messages):

**Exploit-DB Google Hacking Database** (exploit-db.com/google-hacking-database)

---

## Search Engine Variety

Different engines index different content and rank results differently. Always try multiple:

1. **Google** — largest index, best for general searches
2. **Yandex** — strong for Russian/Eastern European content, sometimes indexes pages Google misses
3. **Bing** — independent index, sometimes surfaces results Google buries
4. **DuckDuckGo** — uses Bing's index but with different ranking; no filter bubble

**Exercise:** Search your own name or your target's name across all four engines and compare results. You will find different content on each.

---

## Google Advanced Search Tool

For those who prefer a GUI over memorizing operators:

1. Go to google.com
2. Click **Settings** → **Advanced Search**
3. Fill in fields: all these words, exact phrase, any of these words, none of these words, numbers ranging from
4. Narrow by: language, region, last update, site/domain, file type, usage rights

**Additional resources:**
- google.com/insidesearch — search tips
- powersearchingwithgoogle.com — self-paced advanced courses

For tool alternatives and deeper search capabilities: `/osint` → Domain Investigation or People Search categories.
For country-specific search tools: OSINT Navigator (navigator.indicator.media).
