# Platform-Specific Investigation Techniques

Techniques for extracting data from specific platforms. Each section has step-by-step methodology with inline tool references and OPSEC warnings.

---

## TikTok

### Profile Viewing — OPSEC

WARNING: TikTok shows users who visited their profile. Go to "inbox" to see "XXX and many others checked your profile yesterday." There is no way to opt out. **Create a dedicated research account** with no identifying information before investigating TikTok profiles.

WARNING: The password recovery trick (entering a phone number or email on the "forgot password" screen) confirms whether an account exists — but **it sends a reset email/SMS to the target**, alerting them to your investigation. Do not use this casually.

### Timestamp Extraction

TikTok shows limited time information. You cannot see when an account was created. Trending videos may be weeks or months old. Each video shows the upload day next to the username, but not the exact time.

**To extract exact upload timestamps:**

1. Open the video on the **web version** of TikTok (not mobile)
2. Copy the video URL and open it in a new tab
3. Right-click on the video and select **Inspect** (opens DevTools)
4. Search the HTML for `UploadDate`
5. Copy the timestamp value — it will be in **Zulu time** (UTC)
6. Convert to local time using **TimeAndDate.com** (timeanddate.com)

### Profile Picture Extraction

1. Navigate to the target profile on web
2. Right-click the profile picture → **Inspect Element**
3. In the HTML, find the image URL (look for the `<img>` tag)
4. Right-click → **Copy** → **Copy Element**
5. Paste into the address bar
6. Delete everything before `https` and after `.jpeg` or `.jpg`
7. The resulting URL loads the full-resolution profile picture

Example CDN URL format: `https://p16.muscdn.com/img/musically-maliva-obj/[ID]~c5_100x100.jpeg`

### Video Download

**Method 1 — Browser (web version):** Right-click video → Inspect Element → find `src=` attribute → open in new tab → Save Video As

**Method 2 — In-app:** Share → Save Video

**Method 3 — External tool:** ttdown.org

### Archiving TikTok Videos

1. Copy the video URL
2. Go to **Archive.org** (archive.org)
3. Use "Save Page Now" to archive

The web version of TikTok works with Wayback Machine. Mobile-only content does not.

### Cross-Platform Search for TikTok Content

People share TikTok videos on other platforms. Search for TikTok URLs on those platforms to find content and context.

**On Twitter/X:**
- Search `tiktok.com [keyword]` — finds tweets containing TikTok links about your topic
- Better than searching `#tiktok [keyword]` because it catches direct shares

**On Facebook:**
- Search `tiktok.com [keyword]` or `m.tiktok.com [keyword]` → filter to Videos
- Also try `vm.tiktok.com [keyword]` (the share URL variant)

**On LinkedIn:**
- Search `m.tiktok.com [keyword]` or `tiktok.com [keyword]` → filter to Content

**Cross-platform watermark identification:** TikTok overlays the username as a watermark on every video. When a TikTok video is re-shared on Twitter, Facebook, or WhatsApp, the watermark often remains visible — identifying the original TikTok account even when shared without attribution.

### Google Dorks for TikTok

**Find user accounts:**
```
inurl:https://m.tiktok.com/h5/share/usr/ filetype:html
```

**Find accounts with partial username match:**
```
inurl:https://m.tiktok.com/h5/share/usr/ filetype:html [partial-username]
```
Use case: you know someone uses "mutz" in usernames on other platforms but don't know their TikTok handle.

**Find TikTok videos mentioning specific text:**
```
site:tiktok.com intext:"[search term]"
```

**Find what others say about a specific profile:**
```
site:tiktok.com intext:@username -intitle:"username"
```
This surfaces comments and mentions of the target on other users' videos.

### URL Manipulation for Search

**Hashtag search (no account needed):**
```
tiktok.com/tag/KEYWORD
```
Replace KEYWORD with any term. Works in browser without login.

### Additional Tool

**OSINT Combine TikTok Quick Search** (osintcombine.com/tiktok-quick-search) — aggregates multiple TikTok search methods. Compare its results against manual searches.

---

## Instagram

### Full-Resolution Image Extraction

Instagram compresses images for display. Two methods to get the original resolution:

**Method 1 — /media/?size=l trick:**

1. Get the Instagram post URL (e.g., `https://www.instagram.com/p/Bw2ScdvpKW9/`)
2. Remove anything after the post ID
3. Append `/media/?size=l` (lowercase L)
4. Result: `https://www.instagram.com/p/Bw2ScdvpKW9/media/?size=l`
5. This loads the highest resolution version of the posted image

**Method 2 — Inspect Element:**

1. Open the post in a browser
2. Right-click the image → **Inspect**
3. In the Elements panel, find the `<img>` tag
4. Look for the `srcset` attribute — it contains multiple resolution URLs
5. Right-click the highest-resolution URL → **Open in new tab**
6. The CDN URL (e.g., `scontent-arn2-1.cdninstagram.com/vp/...`) serves the full image

### Finding Private Account Activity

Even if a target's Instagram account is private, their **comments on public accounts** are searchable via Google:

```
"@username" site:instagram.com -site:instagram.com/username
```

This returns mentions of the target username on Instagram pages that are NOT their own profile — catching comments, tags, and story mentions on other people's public posts.

---

## Twitter/X

### Original Resolution Image Extraction

Twitter compresses uploaded images for display. To get the original:

1. Right-click the image in a tweet → **Copy Image Address**
2. The URL will look like: `https://pbs.twimg.com/media/[ID]?format=jpg&name=medium`
3. Replace `medium` with `orig`: `https://pbs.twimg.com/media/[ID]?format=jpg&name=orig`
4. This loads the original resolution as uploaded

**Only works for images uploaded directly to Twitter** — not for images embedded via link preview.

**Why this matters:** At `:medium` resolution, text in photos (name cards, documents, signs) may be unreadable. At `:orig`, they become legible. This can reveal names, addresses, and other details invisible at default resolution.

---

## WordPress

### User Enumeration via REST API

A large number of sites use WordPress. The REST API often exposes registered users even when the site's interface does not.

**Basic user list:**
```
https://[domain]/wp-json/wp/v2/users/
```
Returns JSON with user ID, name, slug, link, and avatar URL. Only shows the first 10 users (alphabetical).

**Pagination for more users:**
```
https://[domain]/wp-json/wp/v2/users/?per_page=100&page=1
```
Change `page=1` to `page=2`, `page=3`, etc. for sites with more than 100 registered users.

**Administrator identification:**
```
https://[domain]/wp-json/wp/v2/users/1
```
User ID `1` is typically the site administrator — the person who set up the WordPress installation.

**What the JSON reveals:**
- User ID, display name, and URL slug
- Author page URL (may reveal content they posted)
- Avatar hash (links to Gravatar, which can reveal other accounts using the same email)
- WordPress locale setting (e.g., `ru_RU` for Russian)
- Installed plugins (visible in response headers)

Not all WordPress sites expose this endpoint — some disable it via security plugins. But many do not.

### Case Study: vaccine.wiki

An anti-vaccine conspiracy site emerged during the coronavirus outbreak. Applying the WordPress user trick:

1. Navigated to `vaccine.wiki/wp-json/wp/v2/users/`
2. JSON response revealed user slug: `stanislav-artiuschikgmail-com`
3. **Derived email**: `stanislav.artiuschik@gmail.com` (from the slug)
4. Locale: `ru_RU` (Russian)
5. Used **Facebook Email Reverse Lookup** (osint.support) with the email
6. Found Facebook profile: Stanislav Artiuschik — posting anti-vaccine content in Russian, linking back to vaccine.wiki

The site administrator failed to change the default WordPress slug, which exposed their email address directly in the API response.

### Case Study: Middle America Project

In 2019, a think tank called "Middle America Project" appeared to criticize progressive Democrats without disclosing funders or founders.

1. `middleamericaproject.com/wp-json/wp/v2/users/` revealed multiple users with joke slugs (`al-sharpton`, `yo-mtv-raps`, `Goku`)
2. One user: **"Marsel Gray"** — searched `marselgray.com`, found personal site: web developer at **NJI Media**, Alexandria, VA
3. GitHub profile (`marselgray`) found: 60 repos, organization @njimedia
4. Critical finding: repo `bravenewgreenworld/index.html` contained commented-out HTML criticizing Alexandria Ocasio-Cortez and the Green New Deal
5. Administrator (`/users/1`): slug `sonthonax` — Googled `"sonthonax" web design`
6. Found LinkedIn: **Sonthonax Sonny Vernard**, Chief Website Designer at **Saguaro Strategies**
7. Saguaro Strategies listed Middle America Project as a client alongside Democratic campaign clients
8. FEC records: Saguaro Strategies received **$1.83M** in 2018 from political campaigns (Katie Hill, Stanton for Congress, Katie Porter, Gallego for Arizona)

This investigation chain — from WordPress API → developer name → GitHub repos → political consulting firm → FEC filings — was triggered entirely by the exposed user list.

For alternative tool recommendations: `/osint` → Domain Investigation category.
