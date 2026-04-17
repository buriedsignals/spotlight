# Archiving & Evidence Recovery

How to preserve evidence before it disappears and recover content that has already been deleted. **Archive first, investigate second** — originals may be removed once an investigation becomes known.

---

## Web Search Caches

Search engines cache copies of pages. When the original is changed or removed, the cache may still hold the old version.

### Google Cache

**Method 1 — Via search results:** Search for the URL in Google → click the downward arrow next to the result → click "Cached"

**Method 2 — Direct syntax:**
```
cache:example.com/page
```
Type this directly in the Google search bar. Replace with the exact URL.

**Limitation:** Google refreshes its cache approximately every week. After that, the old version is replaced.

### Bing and Yandex Caches

Both Bing and Yandex maintain independent caches that refresh on **different schedules** from Google. If Google's cache has already refreshed, Bing or Yandex may still hold the older version.

Access: Same downward-arrow method on search results.

**Always check all three cache sources** — Google, Bing, and Yandex — for deleted or modified content.

---

## Archiving Tools

### Archive.today (archive.is)

- URL: **archive.today** (also archive.is, archive.ph)
- Takes a snapshot of any webpage — the snapshot is permanent even if the original disappears
- Saves both text and graphical copies
- Provides a short, permanent link to the archived version
- Has a bookmarklet for one-click archiving
- **Uses a virtual Facebook identity** to access Facebook pages (critical for archiving Facebook content)
- Content CAN be taken down for violations, but slowly
- **Banned in some countries**
- Must be archived **manually** — no automatic crawling

### Wayback Machine (archive.org)

- URL: **archive.org**
- 350+ billion web pages archived
- **Government funded** — more likely to persist long-term
- Has both automatic crawling AND manual "Save Page Now"
- "Save Page Now" only works for sites that **allow crawlers**
- Will take down content more readily than archive.today — and when removed, **it is gone forever**
- Provides a calendar view showing all captures with dates

### Neither Works for Instagram

Neither archive.today nor the Wayback Machine can reliably archive Instagram content. Screenshot and download Instagram content directly.

---

## Wayback Machine Wildcard Searches

Use wildcards to find ALL archived pages under a URL path:

**Twitter example — find all archived tweets from an account:**
```
http://twitter.com/realdonaldtrump/status/*
```
The `*` matches any tweet ID. This returns every archived tweet from that account.

**Key principle:** Understand URL structures for each platform:
- Twitter: `twitter.com/username/status/[tweet-id]`
- Facebook: `facebook.com/[username-or-id]/posts/[post-id]`
- YouTube: `youtube.com/watch?v=[video-id]`

Construct wildcard searches using these patterns to enumerate all archived content for any account.

---

## Deleted YouTube Video Recovery

**Step 1:** Search for the video **title** and/or the **video ID** (the alphanumeric string after `v=` in the URL)

**Step 2:** Search for the video ID on archive sites:
- Wayback Machine: `web.archive.org/web/*/youtube.com/watch?v=[VIDEO-ID]`
- Archive.today: search for the YouTube URL

**Step 3:** Crowdsource — ask publicly if anyone saved or re-uploaded the video. YouTube videos are frequently downloaded and mirrored.

---

## High-Resolution Image Extraction

Social media platforms compress uploaded images. These techniques retrieve the original resolution.

### Instagram — /media/?size=l

1. Get the post URL: `https://www.instagram.com/p/[POST-ID]/`
2. Remove anything after the post ID
3. Append `/media/?size=l` (lowercase L)
4. Result: `https://www.instagram.com/p/[POST-ID]/media/?size=l`
5. Loads the highest resolution image posted

### Instagram — Inspect Element

1. Open the post in browser → right-click image → **Inspect**
2. Find the `<img>` tag in Elements panel
3. Look for the `srcset` attribute — contains multiple resolution URLs
4. Open the largest URL in a new tab

### Twitter/X — :orig

1. Right-click image → **Copy Image Address**
2. URL format: `https://pbs.twimg.com/media/[ID]?format=jpg&name=medium`
3. Replace `medium` with `orig`
4. Result: `https://pbs.twimg.com/media/[ID]?format=jpg&name=orig`

Only works for images **uploaded directly to Twitter**, not link previews.

**Why this matters:** At `:medium`, text on documents and name cards is unreadable. At `:orig`, it becomes legible — revealing names, addresses, and details invisible at default resolution.

### Facebook

Download directly — Facebook does not compress in the same way. Right-click → Save Image.

---

## Video Download Tools

| Platform | Tool | URL |
|---|---|---|
| Facebook | Fbdown.net | fbdown.net (also Chrome extension) |
| YouTube | y2mate.com | y2mate.com — or insert `pp` after "youtube" in any URL |
| Twitter/X | TwitterVideoDownloader | twittervideodownloader.com |
| Instagram | Blastup | blastup.com/instagram-downloader |
| Instagram Stories | storiesig.com | storiesig.com (enter username) |
| TikTok | ttdown.org | ttdown.org |

**YouTube speed trick:** Change `youtube.com/watch?v=ID` to `youtubepp.com/watch?v=ID` — redirects to download page.

---

## Google News Archive (Pre-2003)

For historical research reaching back before online news:

### Articles from 2003 to Present

1. Go to **news.google.com**
2. Enter your search query
3. Click **Recent** → select **Archives** from the dropdown
4. Results ranked by significance, going back to ~2003
5. For a specific date range: click **Custom range** and set From/To years

### Scanned Newspapers (Pre-2003)

Google has scanned physical newspaper pages going back decades:

```
site:google.com/newspapers [search terms]
```

Example: `site:google.com/newspapers "NASA putting man on Mars"` returns scanned newspaper articles from the 1980s-1990s with the actual physical page layout visible.

**Limitation:** Google Web Search custom date ranges do not go before 1970.

For tool alternatives (archiving, evidence preservation): `/osint` → Web Archives category.
For specialized preservation tools: OSINT Navigator.
